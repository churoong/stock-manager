"""모의 투자 계좌의 순수 계산 (docs/paper.md, docs/infra.md 25.1444).

DB 를 읽지 않는다 — 장부(입금·체결)와 그날 시세를 받아 체결 판정·보유·현금·검산·시간가중 수익·그림자 기준을 낸다.
배치(`jobs/paper.py`)가 읽고 적는다. 같은 입력이면 같은 결과다(복기의 전제, docs/paper.md 0장).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from batch.core import markets
from batch.notify.formatter import won_short

#: 계좌 규칙별로 **따라 파는** 매도 플래그 사유 (docs/paper.md 1장). None 은 모든 플래그
SELL_RULES: dict[str, frozenset[str] | None] = {
    "all": None,
    "core": frozenset({"손절", "목표도달", "기간초과"}),
    # D — 기간초과 하나만. 손절·목표·황색 모두 들고 간다 (docs/paper.md 2.2, 25.1458)
    "time": frozenset({"기간초과"}),
    # 판 2 투자사 헌장 (docs/company.md 1장, 25.1462) — 헌장 마이그레이션보다 **먼저** 배포한다
    # (모르는 원칙이면 `sells_for` 가 멈춘다)
    # A 가치: 손실이 커지거나 회사가 나빠지면 판다
    "value": frozenset({"손절", "목표도달", "재무악화", "점검"}),
    # C 모멘텀: 목표에서 팔지 않고 손실·악재에는 바로 나온다
    "momentum": frozenset({"손절", "기간초과", "재무악화", "점검", "감성급락"}),
    # G 추세 익절 (docs/paper.md 2.2, 25.1481): B(core)와 매도 하나만 다르다 — 목표도달에서 팔지 않고,
    # 목표에 한 번 닿은 뒤 고점 대비 손절폭만큼 빠지면(`TRAIL_CODE`, `trend_exit`) 전량. 손절·기간초과는 B 와 같다
    "trend": frozenset({"손절", "기간초과", "추적손절"}),
    # S 스윙 (docs/paper.md 16.3.1, 25.1871): 엔진의 매도 플래그를 쓰지 않고 `shadow.close_exit`(`swing_close_v1`)
    # 하나로 판다. 까닭 코드가 `손절` 이면 "손절 보유의 남은 회차 거둠" 길을 타므로 이름을 따로 둔다
    "swing_close": frozenset({"스윙손절", "스윙목표", "스윙기한"}),
    # T 당일 단타 (docs/paper.md 16.3.2, 25.1883): 매수와 함께 쓴 종가 짝 매도(`단타종가`)로 판다. 매도 플래그는 보지
    # 않고, 짝을 잃은 보유만 다음 장 시가에(`단타잔여`)
    "day_close": frozenset({"단타종가", "단타잔여"}),
}
#: 추세 익절(G)의 매도 사유 코드 — 매도 플래그(`sell_flags.evaluate`)가 아니라 모의 엔진이 낸다
TRAIL_CODE = "추적손절"
#: 계좌 **화면 이름** — 사람에게 보이는 곳(리포트·텔레그램·웹)은 알파벳 대신 계좌 특성 이름을 쓴다(2026-10-28 사용자
#: 지시 "각 가상계좌는 알파벳이 아닌 계좌특성을 이름으로 바꿔", docs/paper.md 1장, 25.1889). 내부 식별자(장부·결정 원장·
#: KIS 베끼기의 `account_id`)는 그대로 — 바꾸면 기록 수천 행과 H 베끼기 연결이 끊기고 마이그레이션이 든다.
#: 정의처는 여기 하나(웹 사본 `web/lib/accountNames.ts` 는 시험이 대조)
ACCOUNT_NAMES: dict[str, str] = {
    "A": "가치", "B": "기준", "C": "모멘텀", "D": "장기보유", "E": "안정", "G": "추세익절", "H": "지주사",
    "R1": "무작위1", "R2": "무작위2", "S": "스윙", "T": "단타",
}


def account_name(aid: str) -> str:
    """계좌 화면 이름 — 모르는 계좌는 식별자 그대로"""
    return ACCOUNT_NAMES.get(str(aid), str(aid))


#: 따로 재는 실험 계좌 (docs/paper.md 16.3.1, 25.1872) — 상황실 순위표 공통 구간·챔피언 후보에 넣지 않고, 결정 원장
#: 성과 채우기는 따로 한 바퀴(기존 계좌의 하루 상한을 나눠 쓰지 않게). 정의처는 여기 하나(`desk.SEPARATE_IDS`)
SEPARATE_IDS = ("S", "T")
#: 같은 날 사고파는 계좌 — 당일 단타 T (docs/paper.md 16.3.2, 25.1881). 한 날 안의 체결·판정 순서에서 이 계좌의 매도만
#: 그날 매수 **뒤**로 간다(`side_rank`). 행은 0092(2026-10-29 개설) — 이 정렬이 행보다 먼저 main 에 들어갔다
SAME_DAY_IDS: tuple[str, ...] = ("T",)


def side_rank(account_id: str, side: str) -> int:
    """한 날 안의 순서 — 매도 0 · 매수 1, **같은 날 매매 계좌의 매도는 2**(그날 산 것을 그날 판다).

    다른 계좌는 예전(매도 먼저 — 그날 판 돈과 자리를 그날 매수가 쓴다)과 바이트 그대로다. 같은 날 사고파는 T 의 체결을
    예전 순서로 다시 읽으면 매도가 매수보다 먼저 와 `positions` 가 "보유 없는 매도" 로 던지고, 그 호출이 계좌별 보호
    밖이라 단계 전체와 그 뒤 KIS 베끼기가 멈췄다(16.3.2 설계 검토)"""
    if side != "sell":
        return 1
    return 2 if account_id in SAME_DAY_IDS else 0


def fill_key(f: Fill) -> tuple[str, int, int]:
    """체결 장부를 다시 읽는 순서 — 체결일, `side_rank`, 주문 번호. `positions`·`realized` 가 같이 쓴다"""
    return (f.fill_date, side_rank(f.account_id, f.side), f.order_id)


#: 스윙(S)의 매도 규칙 이름과 `close_exit` 까닭 → 사유 코드 (16.3.1, 25.1871)
SWING_RULE = "swing_close"
SWING_CODES = {"stop": "스윙손절", "target": "스윙목표", "time": "스윙기한"}
#: 당일 단타(T)의 매도 규칙 이름·사유 코드 (16.3.2, 25.1883) — `손절` 과 이름이 같으면 "손절 보유의 남은 회차 거둠"
#: 길을 탄다
DAY_RULE = "day_close"
DAY_CLOSE_CODE = "단타종가"
DAY_LEFT_CODE = "단타잔여"
#: T 가 늦은 실행(장 열린 뒤·장 끝난 뒤)에 진입하지 않은 결정의 까닭 머리 — 같은 종가에 사고파는 비용뿐인 거래라
#: 견줄 새 후보에서 뺀다(`NONCOMPARABLE_SKIP_PREFIXES`)
DAY_LATE_PREFIX = "단타 진입 없음: "


def trend_exit(
    cost_per_share: float, peak_close: float | None, last_close: float | None, target_pct: float, stop_pct: float,
    rights_scale: float = 1.0,
) -> bool:
    """추세 익절(G)의 판정 — 첫 매수 뒤 종가 고점이 **목표에 한 번 닿았고**(원가 × (1 + 목표%)),
    지금 종가가 고점 × (1 + 손절%) 아래면 판다. 손절폭·목표는 그 보유 기간의 설정(`horizon_targets`, 기본 단기 −7/+10).

    목표에 닿은 뒤에만 켜지므로 파는 자리는 늘 원가 위다
    (단기 1.10 × 0.93 = +2.3%, 중기 1.25 × 0.85 = +6.3%, 장기 +12.5%) —
    목표 절반에서 켜는 초안은 원가 아래에서 팔아 "손절 좁히기" 와 섞였다(25.1481 설계 검증 [필수] 4).
    유상증자 권리락이 있으면 원가처럼 고점에도 계수를 곱한다 — 권리락 전 고점이 거짓 하락을 만들지 않게(검증 [권장] 5,
    고점이 권리락 뒤였다면 덜 민감해지는 쪽 = 덜 판다)
    """
    if not peak_close or not last_close or cost_per_share <= 0:
        return False
    배 = rights_scale if rights_scale != 1.0 and rights_scale > 0 else 1.0
    원가, 고점 = cost_per_share * 배, peak_close * 배
    # 경계(정확히 +10%·−7%)가 부동소수 찌꺼기로 빗나가지 않게 소수 여섯째 자리에서 — 손절 판정(`sell_flags`)과 같은 처리
    if round(고점 / 원가 - 1, 6) < round(target_pct / 100, 6):
        return False
    return round(last_close / 고점 - 1, 6) <= round(stop_pct / 100, 6)


#: 시장가 매수(시가 일괄, C·R)의 예약·크기 잣대 = 기준가 × (1 + 이 값) (docs/paper.md 2.1.1, 25.1458).
#: 하룻밤 틈(갭)의 대부분을 덮자는 값이고 실측 근거는 없다 [확인필요]. 넘게 뛰면 체결 때 현금 검사가 그 주문을 취소한다
OPEN_BUY_BUFFER = 0.10

#: 짝 비교 — (앞, 뒤, 그 차이가 재는 것). 판 2(투자사, docs/company.md 1장, 25.1468)부터는 회사 대 기준(B)과
#: 기준 대 무작위다.
#: 판 1 의 "한 번에 하나만 다른 짝"(A−B 황색 매도 등)은 회사가 여러 점 달라져 끝났다
PAIRS: tuple[tuple[str, str, str], ...] = (
    ("A", "B", "가치투자사 대 기준"),
    ("C", "B", "모멘텀투자사 대 기준"),
    ("D", "B", "장기보유투자사 대 기준"),
    ("E", "B", "안정투자사 대 기준"),
    ("G", "B", "목표 뒤 추적 매도 대 목표에서 전량 매도"),
    ("H", "G", "성적 가중 합의 선정 대 점수 상위 5 (매도는 둘 다 추적)"),
    ("B", "R1", "기준 대 무작위"),
    ("B", "R2", "기준 대 무작위"),
)

#: 장부로 다시 낸 현금과 어제 + 오늘 움직임이 이 만큼(원) 넘게 다르면 검산 실패 (docs/paper.md 3장 "1원이라도")
RECON_TOLERANCE_KRW = 1.0

#: 미체결 매수를 현금에서 미리 잡을 때 얹는 수수료 여유와 달러 주문의 환율 여유 (docs/paper.md 2.1 예약분)
FEE_BUFFER = 0.01
FX_BUFFER = 0.03

#: 판 뒤 같은 종목을 다시 사지 않는 거래일 수 (docs/paper.md 2.1, 규칙서 판 1)
REBUY_COOLDOWN_SESSIONS = 5

#: 정지로 미룬 매도를 마지막 종가로 청산하기까지의 거래일 (docs/paper.md 2.3, 25.696 과 같은 모양)
HALT_LIQUIDATE_SESSIONS = 20

#: 입금 한 번의 범위(원) — 웹과 같은 값 (docs/paper.md 5장)
DEPOSIT_MIN_KRW = 10_000
DEPOSIT_MAX_KRW = 1_000_000_000


@dataclass(frozen=True)
class CashFlow:
    id: int
    flow_date: str
    amount_krw: float
    kind: str  # deposit · cancel
    cancels_id: int | None = None


@dataclass(frozen=True)
class Costs:
    """한 시장의 비용률(%). 슬리피지는 편도, 체결가에 불리하게 얹는다 (docs/paper.md 2.3)."""

    buy_fee_pct: float
    sell_fee_pct: float
    sell_tax_pct: float
    slippage_pct: float


@dataclass(frozen=True)
class Order:
    id: int
    account_id: str
    stock_id: int
    side: str  # buy · sell
    order_type: str  # limit · market
    limit_price: float | None
    quantity: int
    currency: str
    fill_session: str
    horizon: str | None = None
    score_at_order: float | None = None
    score_as_of: str | None = None
    fill_basis: str = "open"  # open · close (장 열린 뒤 낸 주문)
    ref_price: float | None = None  # 시장가 매수의 기준가 — 예약 잣대 (2.1.1)
    #: 미룬 횟수 — 미룬 주문은 다음 장엔 개장 전에 이미 있던 주문이라 종가 잣대(`close`)라도 시가로 판정한다(25.1621).
    #: 저장된 `fill_basis` 는 바꾸지 않는다 — KIS 비교는 날마다의 베낀 줄을 그 칸으로 가르는데, 바꾸면 첫날(종가 판정)
    #: 줄까지 시가 표본에 들어가 괴리 판정이 거꾸로 섰다(25.1620 반박 [필수] 1)
    deferrals: int = 0

    @property
    def basis(self) -> str:
        """그 장의 판정 잣대 — 장 열린 뒤 낸 주문도 미뤄졌으면 시가"""
        return "open" if self.deferrals else self.fill_basis


@dataclass(frozen=True)
class Bar:
    date: str
    open: float | None
    close: float
    volume: float | None = None
    change_pct: float | None = None  # 그날 등락률(%) — 국내 가격제한폭의 기준가를 되살린다(`limit_locked`, 25.1553)


#: 국내 하루 가격제한폭(±30%, 2015-06-15 부터) — 시장가 주문의 체결 시점 가격이 그 끝이면 찼다고 보지 않는다(25.1553).
#: 정의처는 `core/markets.KR_DAILY_LIMIT` 하나(25.1753·25.1755, 중복 규칙 감사 ⑦)
KR_PRICE_LIMIT = markets.KR_DAILY_LIMIT
#: 하한가로 갇힌 시장가 매도의 까닭 끝 — 부르는 쪽이 이것을 보고 취소가 아니라 다음 장으로 미룬다
LOCKED_SELL = "하한가 — 시장가 매도가 찼다고 보지 않는다"
#: 시가가 비어 시장가를 그날 종가로 판정한 까닭 글 — 성과 분해 진입 칸이 이 글로 "종가 판정" 을 가른다
#: (`jobs/paper.is_close_judged`, 25.1783·25.1786 — 글자가 바뀌면 종가 판정 체결이 진입 평균에 조용히 다시 섞인다)
NO_OPEN_CLOSE_NOTE = "시가 없음 — 종가"


def limit_locked(order: Order, bar: Bar, price: float) -> bool:
    """국내 시장가 주문의 체결 시점 가격이 가격제한폭 끝이었나 — 매수는 상한가 이상, 매도는 하한가 이하
    (docs/paper.md 2.3).

    상한가로 열면(또는 장 열린 뒤 낸 주문이 상한가로 닫으면) 사려는 쪽이 넘쳐 시장가 매수가 찼다고 볼 수 없다 — 예전에는
    그 값에 다 찬 것으로 적어 C·R(시가 일괄)이 실제로는 못 샀을 종목을 샀다(25.1549 감사). 하한가 매도도 같다.
    기준가 = 종가 ÷ (1 + 등락률), 상한가는 기준가 × 1.3 을 호가 단위로 내림·하한가는 × 0.7 을 올림(거래소 방식).
    등락률은 소수 둘째 자리라 기준가가 조금 어긋나 끝 바로 한 단위 안쪽도 끝으로 볼 수 있다(보수 쪽 — 덜 산다).
    등락률이 없으면(대체 출처) 모른다 — 예전처럼 찬다. 미국은 가격제한이 없다
    """
    if order.currency != "KRW" or order.order_type != "market" or bar.change_pct is None:
        return False
    if bar.close <= 0 or price <= 0:
        return False
    if bar.change_pct <= -100 or abs(bar.change_pct) > KR_PRICE_LIMIT * 100 + 0.5:
        # 등락률이 제한폭 밖이면 그날은 가격제한이 없는 날이다(정리매매·기준가 재설정) — 끝이 없다(25.1560,
        # 25.1553 반박 [권고]: 정리매매 매도를 하한가로 보고 20거래일 미뤘다)
        return False
    기준 = bar.close / (1 + bar.change_pct / 100)
    if order.side == "buy":
        끝 = 기준 * (1 + KR_PRICE_LIMIT)
        단위 = _tick(끝)
        return price >= math.floor(round(끝 / 단위, 6)) * 단위
    끝 = 기준 * (1 - KR_PRICE_LIMIT)
    단위 = _tick(끝)
    return price <= math.ceil(round(끝 / 단위, 6)) * 단위


def _tick(price: float) -> float:
    from batch.services.signals import tick_size

    return tick_size(price, "KRW") or 1.0


@dataclass(frozen=True)
class Fill:
    order_id: int
    account_id: str
    stock_id: int
    side: str
    fill_date: str
    price: float  # 슬리피지 전
    quantity: int
    currency: str
    fx_rate: float  # 1 통화단위당 원
    fee: float
    tax: float
    slippage: float
    cash_krw: float  # 매수 음수 · 매도 양수
    horizon: str | None = None
    score_at_trade: float | None = None
    snapshot_as_of: str | None = None


@dataclass
class Position:
    stock_id: int
    currency: str
    quantity: int = 0
    cost: float = 0.0  # 종목 통화, 매수 비용·슬리피지 포함
    cost_krw: float = 0.0
    first_buy_date: str = ""
    horizon: str | None = None
    score_at_buy: float | None = None
    score_at_buy_date: str | None = None


@dataclass
class Valuation:
    invested_krw: float
    cash_krw: float
    holdings_krw: float
    equity_krw: float
    pnl_krw: float
    pnl_pct: float | None
    unknown: list[dict[str, Any]] = field(default_factory=list)
    #: 보유마다 [{stock_id, quantity, currency, cost_krw, value_krw, price_date}] — `paper_daily.positions_json`
    rows: list[dict[str, Any]] = field(default_factory=list)


def _원(v: float) -> float:
    """원 단위 반올림 — 장부의 원화 값은 정수 원으로 맞춘다(소수 찌꺼기로 검산이 1원 어긋나지 않게)."""
    return float(round(v))


def invested(flows: list[CashFlow]) -> float:
    """총 투자금액 = 입금 장부의 합(취소 행은 음수로 들어 있다)."""
    return float(sum(f.amount_krw for f in flows))


def reserved_by_stock(open_buys: list[Order], fx_now: dict[str, float | None]) -> dict[int, float] | None:
    """종목마다 미체결 매수가 묶은 원화(`reserved_krw` 와 같은 식) — 종목·섹터 상한에 보유처럼 센다(25.1549).
    달러 주문인데 환율을 모르면 None"""
    out: dict[int, float] = {}
    for o in open_buys:
        if o.side != "buy":
            continue
        x = reserved_krw([o], fx_now)
        if x is None:
            return None
        out[o.stock_id] = out.get(o.stock_id, 0.0) + x
    return out


def reserved_krw(open_buys: list[Order], fx_now: dict[str, float | None]) -> float | None:
    """미체결 매수가 묶어 둔 현금(원) — 수수료 여유, 달러는 환율 여유까지 (docs/paper.md 2.1, 검증 [필수] 2).

    달러 주문인데 환율을 모르면 None — 모르는 예약을 0 으로 치면 같은 현금을 두 번 쓴다.
    부르는 쪽은 그날 매수를 하지 않는다
    """
    합 = 0.0
    for o in open_buys:
        if o.side != "buy":
            continue
        단가 = buy_unit(o)
        if 단가 is None:
            continue
        금액 = 단가 * o.quantity * (1 + FEE_BUFFER)
        if o.currency != "KRW":
            환율 = fx_now.get(o.currency)
            if not 환율:
                return None
            금액 *= 환율 * (1 + FX_BUFFER)
        합 += 금액
    return _원(합)


def buy_unit(order: Order) -> float | None:
    """매수 주문 한 주의 예약 단가(종목 통화) — 지정가는 그 값, 시장가는 기준가 × (1 + `OPEN_BUY_BUFFER`) (2.1.1).

    시장가인데 기준가가 없으면 None — 예약에서 빠지면 같은 현금을 두 번 쓰므로 `size_buys` 는 그런 주문을
    내지 않는다
    """
    if order.order_type == "market":
        return order.ref_price * (1 + OPEN_BUY_BUFFER) if order.ref_price and order.ref_price > 0 else None
    return order.limit_price


def cancellable(flows: list[CashFlow], flow_id: int, available_by_account: dict[str, float]) -> str | None:
    """그 입금을 취소할 수 있나 — 못 하면 까닭, 되면 None (docs/paper.md 5장).

    이미 취소된 행·취소 행 자체·없는 행은 안 된다. `available_by_account` 는 계좌마다 (현금 − 미체결 매수 예약) —
    그 돈으로 이미 사서 **어느 계좌든** 모자라면 막는다(입금 한 번이 모든 계좌에 들어갔으니 취소도 모든 계좌에서 빠진다)
    """
    원래 = next((f for f in flows if f.id == flow_id), None)
    if 원래 is None:
        return "없는 입금입니다"
    if 원래.kind != "deposit":
        return "취소 행은 다시 취소할 수 없습니다"
    if any(f.cancels_id == flow_id for f in flows):
        return "이미 취소한 입금입니다"
    모자람 = sorted(a for a, c in available_by_account.items() if c < 원래.amount_krw)
    if 모자람:
        return f"그 돈으로 이미 사서 현금이 모자랍니다 (계좌: {', '.join(account_name(a) for a in 모자람)})"
    return None


def halted(bar: Bar | None) -> bool:
    """그날 정지였나 — 행이 없거나 거래량 0·시가 ≤ 0 (docs/paper.md 2.3, 검증 [필수] 5).

    정지한 날 종가가 전일 값 그대로 실리면 지정가 매수가 거짓으로 찬다. 거래량을 모르면(None) 시가만 본다
    """
    if bar is None:
        return True
    if bar.volume is not None and bar.volume <= 0:
        return True
    # 종가 ≤ 0 도 — 시가·거래량이 비고 종가 0 인 행은 "시가 없음 — 종가" 로 0 원에 찼다고 판정했는데 체결 행은 가격
    # CHECK 에 걸려 `INSERT OR IGNORE` 로 조용히 빠지고 주문만 찬 것으로 닫혔다(25.1713, 체결 판정 감사 [참고] 3)
    if bar.close is not None and bar.close <= 0:
        return True
    return bar.open is not None and bar.open <= 0


def judge(order: Order, bar: Bar | None) -> tuple[float | None, str]:
    """체결가(슬리피지 전)와 까닭. 가격이 None 이면 안 찼다 (docs/paper.md 2.3).

    매수(지정가): 그날 **종가 ≤ 회차 가격**이면 회차 가격에 찬다 — 장중 저가로 찼을 몫은 놓친다(보수 쪽).
    매도(시장가): 그날 시가(`fill_basis='open'`), 장 열린 뒤 낸 주문은 종가. 시가가 없으면 종가.
    정지한 날은 None — 매수는 부르는 쪽이 취소, 매도는 미룬다. 국내 시장가가 가격제한폭 끝이면(`limit_locked`) None —
    매수는 취소, 매도는 미룬다(25.1553)
    """
    if halted(bar):
        return None, "정지(그날 시세 없음·거래량 0)"
    assert bar is not None
    if order.side == "buy" and order.order_type == "market":
        # 시가 일괄 매수 (2.1.1) — 매도와 같은 잣대로 시가, 장 열린 뒤 낸 주문은 종가
        if order.basis == "close":
            가격, 까닭 = bar.close, "장 열린 뒤 낸 주문 — 종가"
        elif bar.open is not None and bar.open > 0:
            가격, 까닭 = bar.open, "시가"
        else:
            가격, 까닭 = bar.close, NO_OPEN_CLOSE_NOTE
        if limit_locked(order, bar, 가격):
            return None, f"{까닭.split(' — ')[-1]} 상한가 — 시장가 매수가 찼다고 보지 않는다"
        return 가격, 까닭
    if order.side == "buy":
        if order.limit_price is None or order.limit_price <= 0:
            return None, "지정가 없음"
        if bar.close <= order.limit_price:
            return order.limit_price, "종가 ≤ 회차 가격"
        # `.10g` — `:g`(유효숫자 6자리)는 백만 원 넘는 가격을 "1.294e+06" 으로 적어 결정 원장·화면에서 읽을 수 없었다
        # (25.1640, 10-22 운영 점검). 그보다 작은 값의 글은 예전과 같다
        return None, f"종가 {bar.close:.10g} > 회차 가격 {order.limit_price:.10g}"
    if order.basis == "close":
        가격, 까닭 = bar.close, "장 열린 뒤 낸 주문 — 종가"
    elif bar.open is not None and bar.open > 0:
        가격, 까닭 = bar.open, "시가"
    else:
        가격, 까닭 = bar.close, NO_OPEN_CLOSE_NOTE
    if limit_locked(order, bar, 가격):
        return None, f"{까닭.split(' — ')[-1]} {LOCKED_SELL}"
    return 가격, 까닭


def make_fill(order: Order, price: float, fill_date: str, fx_rate: float, costs: Costs) -> Fill:
    """체결 한 건의 비용과 현금 영향.

    지정가 매수는 그 값보다 비싸게 찰 수 없어 **슬리피지를 얹지 않는다**(검증 권장).
    시장가는 불리한 쪽으로 — 매도는 싸게, 매수(시가 일괄, 2.1.1)는 비싸게
    """
    gross = price * order.quantity
    slip = 0.0 if order.side == "buy" and order.order_type != "market" else gross * costs.slippage_pct / 100
    if order.side == "buy":
        fee = gross * costs.buy_fee_pct / 100
        tax = 0.0
        cash = -(gross + slip + fee) * fx_rate
    else:
        fee = gross * costs.sell_fee_pct / 100
        tax = gross * costs.sell_tax_pct / 100
        cash = (gross - slip - fee - tax) * fx_rate
    return Fill(
        order_id=order.id, account_id=order.account_id, stock_id=order.stock_id, side=order.side,
        fill_date=fill_date, price=price, quantity=order.quantity, currency=order.currency, fx_rate=fx_rate,
        fee=fee, tax=tax, slippage=slip, cash_krw=_원(cash), horizon=order.horizon,
        score_at_trade=order.score_at_order, snapshot_as_of=order.score_as_of,
    )  # fmt: skip


def cash(flows: list[CashFlow], fills: list[Fill]) -> float:
    """장부로 다시 낸 현금(원) = 입금 합 + 체결의 현금 영향 합."""
    return invested(flows) + float(sum(f.cash_krw for f in fills))


def positions(fills: list[Fill]) -> dict[int, Position]:
    """체결 장부에서 보유를 다시 낸다. 매도는 평균 원가로 덜어 낸다(수량 비례).

    다 팔면 지운다 — 다시 사면 첫 매수일·기간·점수 기준점이 새로 잡힌다(실제 보유의 25.286 과 같은 뜻)
    """
    out: dict[int, Position] = {}
    for f in sorted(fills, key=fill_key):
        p = out.get(f.stock_id)
        if f.side == "buy":
            if p is None:
                p = out[f.stock_id] = Position(
                    stock_id=f.stock_id, currency=f.currency, first_buy_date=f.fill_date, horizon=f.horizon,
                    score_at_buy=f.score_at_trade, score_at_buy_date=f.snapshot_as_of,
                )  # fmt: skip
            p.quantity += f.quantity
            p.cost += f.price * f.quantity + f.fee + f.slippage
            p.cost_krw += -f.cash_krw
            continue
        if p is None or p.quantity <= 0:
            raise ValueError(f"보유 없는 매도 체결입니다 (주문 {f.order_id}, 종목 {f.stock_id})")
        if f.quantity > p.quantity:
            raise ValueError(f"보유보다 많은 매도 체결입니다 (주문 {f.order_id}: {f.quantity} > {p.quantity})")
        남김 = (p.quantity - f.quantity) / p.quantity
        p.cost *= 남김
        p.cost_krw *= 남김
        p.quantity -= f.quantity
        if p.quantity == 0:
            del out[f.stock_id]
    return out


def realized(fills: list[Fill]) -> list[dict[str, Any]]:
    """매도 체결마다 실현손익(원) — 평균 원가 기준. 월간 리포트의 "가장 큰 이익·손실" 에 쓴다 (docs/paper.md 6장).

    해외 종목은 **환차손익 몫**(`fx_krw`)을 가른다 (25.1693, CLAUDE.md "해외 종목은 환차손익을 주가 손익과 분리") —
    원가를 원화와 현지 통화 둘로 쌓아(매수 비용 포함, 평균 원가), 실현손익 = 매도 대금(원) − 원화 원가 를
    주가 몫 = (현지 매도 대금 − 현지 원가) × 매도 환율, 환차 몫 = 현지 원가 × (매도 환율 − 매수 평균 환율) 로 나눈다.
    둘의 합은 실현손익과 같다(반올림 1원 안). 원화 종목의 환차 몫은 0
    """
    보유: dict[int, tuple[int, float, float]] = {}  # 수량, 원화 원가, 현지 통화 원가
    out: list[dict[str, Any]] = []
    for f in sorted(fills, key=fill_key):
        q, c, 현 = 보유.get(f.stock_id, (0, 0.0, 0.0))
        환율 = f.fx_rate if f.currency != "KRW" and f.fx_rate else 1.0
        if f.side == "buy":
            보유[f.stock_id] = (q + f.quantity, c - f.cash_krw, 현 - f.cash_krw / 환율)
            continue
        if q <= 0:
            continue
        원가 = c * f.quantity / q
        현지원가 = 현 * f.quantity / q
        손익 = _원(f.cash_krw - 원가)
        환차 = 0.0 if f.currency == "KRW" else _원(현지원가 * 환율 - 원가)
        out.append({"stock_id": f.stock_id, "fill_date": f.fill_date, "order_id": f.order_id,
                    "pnl_krw": 손익, "cost_krw": _원(원가), "fx_krw": 환차, "price_krw": 손익 - 환차})  # fmt: skip
        보유[f.stock_id] = (q - f.quantity, c - 원가, 현 - 현지원가)
    return out


def value(
    flows: list[CashFlow],
    fills: list[Fill],
    closes: dict[int, tuple[float, str] | None],
    fx_now: dict[str, float | None],
    frozen: set[int] | None = None,
) -> Valuation:
    """평가. `closes` 는 {종목: (마지막 종가, 날짜)}, `fx_now` 는 {통화: 원 환산률}.

    종가나 환율을 모르는 보유는 **원가로** 넣고 `unknown` 에 적는다 — 0 으로 넣으면 손익이 거짓으로 무너진다.
    `frozen`(기업행위 경계로 멈춘 종목, docs/paper.md 2.3)도 원가로 —
    분할 날 종가 × 분할 전 수량은 −75% 같은 거짓 값이다
    """
    현금 = cash(flows, fills)
    보유 = positions(fills)
    평가 = 0.0
    모름: list[dict[str, Any]] = []
    줄: list[dict[str, Any]] = []
    for sid, p in sorted(보유.items()):
        c = closes.get(sid)
        환율 = 1.0 if p.currency == "KRW" else fx_now.get(p.currency)
        까닭 = ("기업행위(멈춤)" if sid in (frozen or set()) else "종가 없음" if c is None
               else None if 환율 else f"{p.currency} 환율 없음")  # fmt: skip
        값 = p.cost_krw if 까닭 or c is None or not 환율 else c[0] * p.quantity * 환율
        if 까닭:
            모름.append({"stock_id": sid, "why": 까닭})
        평가 += 값
        줄.append({"stock_id": sid, "quantity": p.quantity, "currency": p.currency, "cost_krw": _원(p.cost_krw),
                  "value_krw": _원(값), "price_date": None if 까닭 or c is None else c[1],
                  "horizon": p.horizon,
                  # 화면의 보유내역 (25.1455) — 웹은 계산하지 않는다
                  "avg_price": p.cost / p.quantity if p.quantity else None,
                  "close": None if 까닭 or c is None else c[0], "first_buy_date": p.first_buy_date,
                  "unrealized_krw": _원(값 - p.cost_krw),
                  "unrealized_pct": (값 / p.cost_krw - 1) * 100 if p.cost_krw > 0 else None,
                  # 종목 통화 기준(환차 뺀) 평가손익 % 와 원가로 둔 까닭 — 원화 % 는 달러 종목이면 환차가 섞여
                  # 손절 판단과 부호가 갈렸고, 원가로 둔 줄은 "0원 · +0.0%" 로만 보였다
                  # (25.1604, 웹 화면 감사 [권고] 6·7)
                  "unrealized_local_pct": (c[0] / (p.cost / p.quantity) - 1) * 100
                  if not 까닭 and c is not None and p.quantity and p.cost > 0 else None,
                  "unknown_why": 까닭})  # fmt: skip
    입금 = invested(flows)
    총액 = 현금 + _원(평가)
    손익 = 총액 - 입금
    for r in 줄:
        r["weight_pct"] = r["value_krw"] / 총액 * 100 if 총액 > 0 else None
    return Valuation(
        invested_krw=입금, cash_krw=현금, holdings_krw=_원(평가), equity_krw=총액, pnl_krw=손익,
        pnl_pct=(손익 / 입금 * 100) if 입금 > 0 else None, unknown=모름, rows=줄,
    )  # fmt: skip


def reconcile(prev_cash: float | None, flows_today: float, fills_today: float, ledger_cash: float) -> tuple[bool, str]:
    """검산 — 어제 저장한 현금 + 오늘 움직임 = 장부로 다시 낸 현금 (docs/paper.md 3장).

    어제 행이 없으면(첫날) 장부만으로 맞다고 본다. 다르면 장부 행이 지워졌거나 고쳐졌다는 뜻이다
    """
    if prev_cash is None:
        return True, "첫 기록"
    기대 = prev_cash + flows_today + fills_today
    차 = ledger_cash - 기대
    if abs(차) > RECON_TOLERANCE_KRW:
        움직임 = flows_today + fills_today
        return False, f"장부 현금 {ledger_cash:,.0f}원 ≠ 어제 {prev_cash:,.0f} + 오늘 움직임 {움직임:+,.0f}"
    return True, "맞음"


def after(ts: object, base: object) -> bool:
    """`ts` 가 `base` 보다 뒤의 시각인가 — 웹(`…Z`)·배치(`…+00:00`)·공백 구분 모양을 모두 읽어 견준다 (25.1447).

    문자열로 견주면 모양이 다른 두 시각이 엇갈린다. **어느 하나라도 읽지 못하면 뒤가 아니다**(25.1677) — 예전엔
    문자열로 견줘, 손 SQL 로 `'2026/10/15 09:00'` 처럼 적힌 입금이 `'/'` > `'-'` 라 늘 "실행 시작 뒤" 로 걸러져 어느
    실행에도 들지 않았고(입금 장부 견줌도 같은 목록이라 "맞음" — 웹 합과 영구히 어긋남), `'nope'` 취소는 날마다
    "어제 뒤 새 입금" 으로 셌다. 모르는 시각은 이미 지난 것으로 세면 들어온 첫날 입금 장부 견줌이 한 번 잡는다
    """
    from datetime import UTC, datetime

    def 읽기(v: object) -> datetime | None:
        try:
            d = datetime.fromisoformat(str(v).strip().replace(" ", "T").replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=UTC)

    a, b = 읽기(ts), 읽기(base)
    return bool(a and b and a > b)


def twr_step(prev_index: float | None, prev_equity: float | None, equity: float, flow_since: float) -> float:
    """시간가중 수익 지수를 하루 잇는다 (docs/paper.md 3장).

    입금은 그날 끝(평가 직전)의 흐름으로 본다 — 그날 수익에 섞지 않는다:
    오늘 수익률 = (오늘 총액 − 새 입금) ÷ 어제 총액 − 1.
    어제 총액이 없거나 0 이면(첫 입금 날) 지수는 그대로 이어 간다(첫날 1)
    """
    if prev_index is None:
        return 1.0
    if not prev_equity or prev_equity <= 0:
        return prev_index
    return prev_index * (equity - flow_since) / prev_equity


def shadow(flows: list[CashFlow], level_of: dict[int, float | None], level_now: float | None) -> float | None:
    """같은 날 같은 돈을 기준 지수에 넣었다면 지금 얼마인가 — 원화 (docs/paper.md 4장).

    `level_of` 는 {입금 행 번호: 그 돈이 처음 투자 가능해진 장의 기준 원화 값 — S&P500 은 지수 × USDKRW}.
    취소 행은 **원래 입금이 산 단위를 되돌린다**(원래 행의 값으로) — 그날 값으로 팔면 기준에 가짜 손익이 생긴다.
    하나라도 모르면 None — 빠진 입금을 0 으로 치면 기준이 거짓으로 낮아진다
    """
    if level_now is None or level_now <= 0:
        return None
    단위 = 0.0
    for f in flows:
        lv = level_of.get(f.cancels_id if f.kind == "cancel" and f.cancels_id is not None else f.id)
        if lv is None or lv <= 0:
            return None
        단위 += f.amount_krw / lv
    return _원(단위 * level_now)


def sells_for(sell_rule: str, flag_codes: dict[int, set[str]]) -> dict[int, list[str]]:
    """그 계좌 규칙으로 팔 종목과 사유 (docs/paper.md 2.2)."""
    허용 = SELL_RULES[sell_rule]
    out: dict[int, list[str]] = {}
    for sid, codes in flag_codes.items():
        걸린 = sorted(codes if 허용 is None else codes & 허용)
        if 걸린:
            out[sid] = 걸린
    return out


def size_buys(
    allocations: list[dict[str, Any]], cash_available: float, fx_rate: float, sold_recently: set[int],
    currency: str = "KRW", entry: str = "tranche",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """2부 배분을 **정수 주 지정가 회차**로 바꾼다 (docs/paper.md 2.1).

    `entry='open'`(시가 일괄, 2.1.1)이면 회차 대신 배분 금액 전부를 한 시장가 주문(회차 1)으로 —
    수량은 금액 ÷ 기준가(`ref_price`) 내림, 현금은 기준가 × (1 + `OPEN_BUY_BUFFER`) 로 센다(예약과 같은 잣대).
    그 주문은 `limit_price` 가 None 이고 `ref_price` 가 있다.

    `allocations`: [{stock_id, amount(종목 통화), tranches:[{price_at_or_below, amount}], ref_price, ...}].
    순서는 `build_portfolio` 가 낸 그대로(같은 함수 원칙), 현금(원 → 종목 통화) 안에서만 낸다. 1주도 못 사는 회차는 내지
    않는다. 최근에 판 종목(`sold_recently`, 판 뒤 `REBUY_COOLDOWN_SESSIONS`)은 사지 않는다.
    (주문, 빠진 것) — 빠진 것은 까닭을 단다
    """
    if fx_rate <= 0:
        return [], [{"stock_id": a.get("stock_id"), "why": "환율 없음"} for a in allocations]
    # **예약과 같은 잣대로** 현금을 쓴다 (25.1447, 교차검증 [필수] 2) — 체결 때 남은 회차를 `reserved_krw`
    # (달러는 환율 여유 3% 까지)로 빼므로, 여유 없이 현금을 다 쓰면 가장 잘 차는 1회차가 "현금 부족" 으로 취소됐다
    여유 = (1 + FEE_BUFFER) * (1 if currency == "KRW" else 1 + FX_BUFFER)
    남은 = cash_available / fx_rate
    주문: list[dict[str, Any]] = []
    빠짐: list[dict[str, Any]] = []
    for a in allocations:
        sid = int(a["stock_id"])
        if sid in sold_recently:
            빠짐.append({"stock_id": sid, "why": f"판 뒤 {REBUY_COOLDOWN_SESSIONS}거래일 안"})
            continue
        if entry == "open":
            기준 = float(a.get("ref_price") or 0)
            금액 = float(a.get("amount") or 0)
            if 기준 <= 0 or 금액 <= 0:
                빠짐.append({"stock_id": sid, "tranche": 1, "why": "기준가 없음" if 기준 <= 0 else "배분 금액 없음"})
                continue
            단가 = 기준 * (1 + OPEN_BUY_BUFFER)
            수량 = int(min(금액 // 기준, 남은 / 여유 // 단가))
            if 수량 < 1:
                빠짐.append({"stock_id": sid, "tranche": 1, "why": "현금 부족" if 금액 >= 기준 else "배분 금액 < 1주"})
                continue
            남은 -= 수량 * 단가 * 여유
            주문.append({"stock_id": sid, "tranche": 1, "limit_price": None, "ref_price": 기준, "quantity": 수량})
            continue
        for i, t in enumerate(a.get("tranches") or [], start=1):
            가격 = float(t.get("price_at_or_below") or 0)
            금액 = float(t.get("amount") or 0)
            if 가격 <= 0 or 금액 <= 0:
                continue
            # 비용을 넉넉히 잡아 현금이 음수로 가지 않게 — 수수료 여유 (수수료율보다 크다)
            수량 = int(min(금액, 남은 / 여유) // 가격)
            if 수량 < 1:
                빠짐.append({"stock_id": sid, "tranche": i, "why": "현금 부족" if 금액 >= 가격 else "회차 금액 < 1주"})
                continue
            남은 -= 수량 * 가격 * 여유
            주문.append({"stock_id": sid, "tranche": i, "limit_price": 가격, "quantity": 수량})
    return 주문, 빠짐


def frozen_line(frozen: set[int], sticky: set[int]) -> str:
    """리포트 머리의 기업행위 멈춤 줄 — 경계 멈춤(모든 계좌)과 이어 멈춤(경계 전부터 든 계좌만)을 나눠 센다(25.1656).

    예전 "멈춘 종목 N개(기업행위 경계 — …)" 는 두 수를 합쳐, 이어 멈춤 종목을 다른 계좌가 사고판다는 것을 가렸다
    (25.1653 과 같은 결함 — 운용 화면만 고쳤었다, 교차검증 [참고]). 둘 다 멈춘 계좌에서는 원가로 평가한다(2.3)
    """
    경계 = len(frozen - sticky)
    이어 = len(frozen & sticky)
    조각 = ([f"기업행위 경계 {경계}개(모든 계좌)"] if 경계 else []) + (
        [f"이어 멈춤 {이어}개(경계 전부터 든 계좌만)"] if 이어 else [])  # fmt: skip
    return f"가상모의투자 멈춘 종목 — {' · '.join(조각)} — 멈춘 계좌는 원가로 평가(docs/paper.md 2.3)"


def random_order(pool: list[int], seed: str) -> list[int]:
    """무작위 대조(R1·R2)가 종목을 고르는 순서 — 고정 씨앗 (docs/paper.md 2.1.2, 25.1458).

    후보마다 `sha256(씨앗|후보)` 가 작은 순서다(랑데부 해싱). 목록 전체를 섞으면 유니버스에 한 종목만 더해져도 순열이
    통째로 바뀌어, 주간 유니버스가 갱신될 때마다 같은 신호 종목의 대체가 다른 종목으로 갔다(검증 [필수] 3 — R 만 새
    종목을 사며 투자 속도가 빨라진다). 이 순서는 고른 종목이 모집단에 남아 있는 한 그대로이고, 파이썬 판과도 무관하다
    """
    import hashlib

    def 열쇠(c: int) -> bytes:
        return hashlib.sha256(f"{seed}|{c}".encode()).digest()

    return sorted(set(pool), key=열쇠)


def _퍼센트(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _대비(v: Any, 입금: float) -> float | None:
    """평가 ÷ 입금 − 1 (%) — 같은 돈 흐름의 기준 지수와 견주는 잣대 (docs/paper.md 4장)."""
    return (float(v) / 입금 - 1) * 100 if v is not None and 입금 > 0 else None


def _범위(날들: list[str]) -> str:
    """정렬된 날짜 목록의 "처음~끝" — 하루면 그날 하나(25.1742 — "2026-10-22~2026-10-22" 가 나갔다)"""
    return 날들[0] if 날들[0] == 날들[-1] else f"{날들[0]}~{날들[-1]}"


def summary_lines(
    daily: dict[str, list[dict[str, Any]]],
    fills: dict[str, list[Fill]],
    week_start: str,
    month_start: str | None,
    names: dict[int, str],
    sectors: dict[int, str | None],
    limits: dict[str, Any],
    sell_rules: dict[str, str] | None = None,
    starts: dict[str, str] | None = None,
    twr_at: dict[tuple[str, str], float] | None = None,
    created: dict[int, str] | None = None,
) -> list[str]:
    """주간 요약의 모의 계좌 절 — 월 첫 요약이면 월간 복기까지 (docs/paper.md 6장). 순수 함수.

    `daily` 는 계좌마다 `paper_daily` 행(날짜 순, 월간이면 한 달 넘게), `limits` 는 {max_stock_pct, max_sector_pct,
    stop_pct: {기간: %}}. 기준 지수는 같은 돈 흐름이라 평가 ÷ 입금으로 견준다.
    `starts` = 계좌마다 첫 평가일, `twr_at` = {(계좌, 날짜): 그날 이하 마지막 시간가중 지수} — 짝의 시작일이
    다르면(늦게 연 계좌, 25.1481) 원화 차이 대신 **늦은 시작일부터의 공통 구간 시간가중 차이(%p)** 로 견준다
    """
    if not any(daily.values()):
        return []
    out = ["[가상모의투자]"]
    # 계좌마다 두 줄 — 평가·손익·시간가중 / 입금·같은 돈 지수·이번 주 체결(25.1727, 리포트 가독성 2026-10-22 사용자
    # 지시 — 예전엔 계좌마다 이 모두와 평가일·종가 범위가 한 줄이라 폰에서 아홉 계좌가 큰 문단이 됐다).
    # 평가일·종가 범위가 모든 계좌에 같으면 머리에 한 번, 다르면 그 계좌 둘째 줄 끝에
    def 종가날(행: dict[str, Any]) -> list[str]:
        보유 = json.loads(행.get("positions_json") or "[]")
        return sorted({str(p.get("price_date")) for p in 보유 if p.get("price_date")})

    기준들 = {aid: (str(행들[-1]["as_of"]), 종가날(행들[-1])) for aid, 행들 in daily.items() if 행들}
    평가일들 = {v[0] for v in 기준들.values()}
    공통평가 = next(iter(평가일들)) if len(평가일들) == 1 else None
    if 공통평가:
        모든종가 = sorted({d for v in 기준들.values() for d in v[1]})
        out[0] += f" {공통평가} 평가" + (f" (보유 종가 {_범위(모든종가)})" if 모든종가 else "")
    # 입금·같은 돈 지수가 여러 계좌에 같으면 머리 아래 한 줄로 모으고 계좌 줄에선 뺀다(25.1742 — 아홉 계좌 둘째
    # 줄마다 "입금 10억원 · 같은 돈 KOSPI +2.2% · S&P500 +0.7%" 가 되풀이됐다). 가장 많은 계좌의 값을 머리에, 다른
    # 계좌는 제 줄에. 계좌가 하나면 그 값을 머리에
    지수값: dict[str, tuple[str, str, str]] = {}
    for aid, 행들 in daily.items():
        if 행들:
            입 = float(행들[-1]["invested_krw"])
            지수값[aid] = (won_short(입), *(_퍼센트(_대비(행들[-1].get(k), 입)) for k in ("kospi_krw", "sp500_krw")))
    셈 = Counter(지수값.values())
    # 동률이면 이름순 첫 계좌가 든 값을 머리로 — 글자 사전순으로 고르면 "—"(지수 없음)가 "+" 보다 커 빈 값이 머리로
    # 올라갔다(25.1747, 25.1744 교차검증 [참고])
    첫계좌 = {v: min(a for a in 지수값 if 지수값[a] == v) for v in 셈}
    주된, 주된수 = min(셈.items(), key=lambda kv: (-kv[1], 첫계좌[kv[0]])) if 셈 else (None, 0)
    if 주된 is None or (주된수 < 2 and len(지수값) > 1):
        주된 = None
    else:
        주된계좌 = "·".join(account_name(a) for a in sorted(지수값) if 지수값[a] == 주된)
        누구 = "" if 주된수 == len(지수값) else f" ({주된계좌})"
        씩 = "씩" if len(지수값) > 1 else ""  # 계좌가 하나면 "씩" 이 어색하다(25.1747)
        out.append(f"  입금 {주된[0]}{씩} · 같은 돈 KOSPI {주된[1]} · S&P500 {주된[2]}{누구}")
    끝값: dict[str, float] = {}
    for aid in sorted(daily):
        nm = account_name(aid)  # 화면 이름(25.1893) — 사전 열쇠는 식별자 그대로
        행들 = daily[aid]
        if not 행들:
            continue
        끝 = 행들[-1]
        주초 = next((r for r in reversed(행들) if str(r["as_of"]) < week_start), None)
        입금 = float(끝["invested_krw"])
        총액 = float(끝["equity_krw"])
        끝값[aid] = 총액
        주간 = (float(끝["twr_index"]) / float(주초["twr_index"]) - 1) * 100 if 주초 and 주초["twr_index"] else None
        손익, 코스피, 미국 = (_대비(v, 입금) for v in (총액, 끝.get("kospi_krw"), 끝.get("sp500_krw")))
        # 체결은 다음 배치가 판정해 그날 행에 들어간다(금요일 장 → 월요일 행) — "이번 주" 는 주초 행 뒤에 장부에 든
        # 체결, 곧 [주초 행 날, 끝 행 날). 예전 `fill_date >= week_start` 는 금요일 장 체결을 어느 주에도 세지 않았는데
        # 그 체결은 이번 주 시간가중에는 들어 있었다(25.1609, 운용 요약 감사 [필수] 1)
        # 주초 행이 없으면(읽은 2주 안에 그 앞 행이 없다 — 배치 공백·새 계좌) 처음부터가 아니라 week_start 부터 —
        # 처음부터면 전체 이력을 이번 주로 셌다(25.1611, 25.1609 교차검증 [필수] 2)
        이번주 = 장부에_든(fills.get(aid, []), 주초["as_of"] if 주초 else week_start, 끝["as_of"], created,
                        주초.get("updated_at") if 주초 else None, 끝.get("updated_at"))  # fmt: skip
        보유 = json.loads(끝.get("positions_json") or "[]")
        날짜 = sorted({str(p.get("price_date")) for p in 보유 if p.get("price_date")})
        누적 = (float(끝["twr_index"]) - 1) * 100
        out.append(f"  {nm} 평가 {won_short(총액)} · 입금 대비 {_퍼센트(손익)} · 시간가중 이번 주 {_퍼센트(주간)}"
                   f" · 누적 {_퍼센트(누적)}")  # fmt: skip
        매수, 매도 = sum(f.side == "buy" for f in 이번주), sum(f.side == "sell" for f in 이번주)
        out.append(
            f"     이번 주 체결 매수 {매수}·매도 {매도}"
            + ("" if 지수값[aid] == 주된 else
               f" · 입금 {won_short(입금)} · 같은 돈 KOSPI {_퍼센트(코스피)} · S&P500 {_퍼센트(미국)}")
            + ("" if 공통평가 else f" ({끝['as_of']} 평가" + (f", 종가 {_범위(날짜)}" if 날짜 else "") + ")")
        )  # fmt: skip
    # 짝 비교 (25.1458) — 한 번에 하나만 다르게 지은 짝. 차이는 그 규칙을 따른 정책 전체의 효과다(docs/paper.md 1장).
    # **투자 비중을 함께 싣는다**(검증 [필수] 7) — E 는 처음 몇 달 B 의 두 배를 사고 D 는 거의 팔지 않아, 차이의 큰 몫이
    # 규칙이 아니라 "얼마나 시장에 들어가 있었나"(베타)일 수 있다
    비중: dict[str, float | None] = {}
    for aid, 행들 in daily.items():
        if 행들 and float(행들[-1]["equity_krw"] or 0) > 0:
            보유합 = sum(float(p.get("value_krw") or 0) for p in json.loads(행들[-1].get("positions_json") or "[]"))
            비중[aid] = 보유합 / float(행들[-1]["equity_krw"]) * 100
    for 앞, 뒤, 무엇 in PAIRS:
        if 앞 in 끝값 and 뒤 in 끝값:
            가, 나 = account_name(앞), account_name(뒤)  # 화면 이름(25.1893)
            덧 = (f"; 투자 비중 {가} {비중[앞]:.0f}% · {나} {비중[뒤]:.0f}%"
                  if 비중.get(앞) is not None and 비중.get(뒤) is not None else "")  # fmt: skip
            시작들 = {(starts or {}).get(앞), (starts or {}).get(뒤)}
            if len(시작들) > 1 and None not in 시작들:
                # 늦게 연 계좌 — 원화 차이는 기간이 달라 뜻이 없다. 늦은 시작일부터 같은 구간의 시간가중 차이로
                공통 = max(str(x) for x in 시작들)
                기 = {a: (twr_at or {}).get((a, 공통)) for a in (앞, 뒤)}
                끝지수 = {a: float(daily[a][-1]["twr_index"] or 0) for a in (앞, 뒤)}
                if all(기.values()):
                    차 = (끝지수[앞] / 기[앞] - 끝지수[뒤] / 기[뒤]) * 100  # type: ignore[operator]
                    out.append(f"  {가}−{나}: 시간가중 {차:+.1f}%p ({공통}부터 공통 구간 — {무엇}{덧})")
                continue
            out.append(f"  {가}−{나}: {끝값[앞] - 끝값[뒤]:+,.0f}원 ({무엇}{덧})")
    if month_start is None:
        return out
    # 그달 = 그달 행들에 장부로 든 것 — [그달 앞 마지막 행 날, 그달 마지막 행 날). 체결일로 자르면 그달 마지막 장의
    # 매매가 복기 때 아직 장부에 없고 다음 달 복기에선 체결일이 앞이라 빠져 영영 안 보였다. 위 끝도 없어 복기를 연
    # 이번 달 첫 며칠의 매매·규칙 점검 날이 섞였다(25.1609, 운용 요약 감사 [필수] 2·[권고])
    y, m = int(month_start[:4]), int(month_start[5:7])
    다음달 = f"{y + m // 12:04d}-{m % 12 + 1:02d}-01"
    out.append(f"[가상모의투자 월간 복기] {month_start} 부터 {다음달} 전까지")
    for aid in sorted(daily):
        nm = account_name(aid)  # 화면 이름(25.1893) — 사전 열쇠는 식별자 그대로
        앞행 = next((r for r in reversed(daily[aid]) if str(r["as_of"]) < month_start), None)
        끝행 = next((r for r in reversed(daily[aid]) if str(r["as_of"]) < 다음달), None)
        든것 = 장부에_든(fills.get(aid, []), 앞행["as_of"] if 앞행 else month_start,
                       끝행["as_of"] if 끝행 else month_start, created, 앞행.get("updated_at") if 앞행 else None,
                       끝행.get("updated_at") if 끝행 else None)  # fmt: skip
        든주문 = {f.order_id for f in 든것}
        실현 = [r for r in realized(fills.get(aid, [])) if r["order_id"] in 든주문]
        if 실현:
            좋은 = sorted(실현, key=lambda r: -r["pnl_krw"])[:3]
            나쁜 = sorted(실현, key=lambda r: r["pnl_krw"])[:3]
            def 글(r: dict[str, Any]) -> str:
                return f"{names.get(r['stock_id'], r['stock_id'])} {r['pnl_krw']:+,.0f}원"
            out.append(f"  {nm} 이익 셋: " + (", ".join(글(r) for r in 좋은 if r["pnl_krw"] > 0) or "없음"))
            out.append(f"  {nm} 손실 셋: " + (", ".join(글(r) for r in 나쁜 if r["pnl_krw"] < 0) or "없음"))
        else:
            out.append(f"  {nm}: 그달 판 종목 없음")
        종목넘음, 섹터넘음, 손절넘김 = set(), set(), set()
        for r in daily[aid]:
            if not month_start <= str(r["as_of"]) < 다음달 or not float(r["equity_krw"]):
                continue
            총 = float(r["equity_krw"])
            섹터합: dict[str, float] = {}
            for p in json.loads(r.get("positions_json") or "[]"):
                v = float(p.get("value_krw") or 0)
                if v / 총 * 100 > float(limits.get("max_stock_pct") or 100):
                    종목넘음.add(str(r["as_of"]))
                if sectors.get(int(p["stock_id"])):
                    섹터합[str(sectors[int(p["stock_id"])])] = 섹터합.get(str(sectors[int(p["stock_id"])]), 0.0) + v
                손절 = (limits.get("stop_pct") or {}).get(p.get("horizon") or "long")
                if 손절 is not None and (수익 := _종목통화_수익(p)) is not None and 수익 <= float(손절):
                    손절넘김.add(str(r["as_of"]))
            if any(합 / 총 * 100 > float(limits.get("max_sector_pct") or 100) for 합 in 섹터합.values()):
                섹터넘음.add(str(r["as_of"]))
        # 손절을 따르지 않는 계좌(D, 기간 보유)는 설계상 생긴다. 손절을 따르는 계좌도 판정이 종가 기준이라 생길 수 있다
        # (25.1458 재검증 — 예전 주석은 B 에만 붙어 같은 규칙의 C·E·R 에는 없었다)
        덧 = (" (설계상 — 손절을 따르지 않는 계좌)" if (sell_rules or {}).get(aid) == "time"
              else " (손절은 종가 판정이라 설계상 생길 수 있다)")  # fmt: skip
        if (sell_rules or {}).get(aid) == DAY_RULE:
            손절글 = "손절선 점검 해당 없음(당일 단타 — 그날 종가 매도)"
        elif (sell_rules or {}).get(aid) == SWING_RULE:
            # 스윙(S)의 손절선은 기간별 손절(단기 −7%, 원가)이 아니라 진입 시가 대비 −3% 종가(16.3.1) — 이 잣대로 세면
            # 틀린 수가 나온다(25.1873, 25.1871 교차검증 [권고])
            from batch.services.shadow import SWING_CLOSE

            아래 = f"{SWING_CLOSE.stop_pct * 100:g}".replace("-", "−")  # 다른 글과 같은 마이너스 기호(25.1876)
            손절글 = (f"손절선 점검 해당 없음(스윙 규칙 — 종가가 진입 시가 대비 {아래}% 이하면"
                    " 다음 장 시가 매도)")  # 값은 규칙 정의처에서(25.1874)
        else:
            손절글 = f"손절선 아래로 들고 간 날 {len(손절넘김)}{덧}"
        out.append(f"  {nm} 규칙 점검: 종목 상한 넘은 날 {len(종목넘음)} · 섹터 상한 넘은 날 {len(섹터넘음)}"
                   f" · {손절글}")
    return out


def 장부에_든(
    fills: list[Fill], after_row: Any, upto_row: Any, created: dict[int, str] | None = None,
    after_at: Any = None, upto_at: Any = None,
) -> list[Fill]:
    """`after_row` 날의 평가 행 뒤 ~ `upto_row` 날의 평가 행까지 장부에 든 체결.

    두 행의 기록 시각(`updated_at`)과 체결 행을 쓴 시각(`created`)을 알면 그것으로 — 같은 실행이 같은 시각으로 쓴다.
    체결일로 가르면 종목 행을 기다려(25.1713) 체결일 D 인 체결이 D+3 행에 든 것이 어느 주·달에도 안 셌다(25.1716, 평가
    감사 [권고] 1). 앞 행의 기록 시각을 모르면(계좌 첫 달·주) 아래는 체결일, 위는 끝 행의 기록 시각 — 위까지 체결일로
    자르면 끝 행 뒤에 늦게 든 체결이 첫 달(체결일)·다음 달(기록 시각) 둘 다에 셌다(25.1719, 25.1716 교차검증 [권고] 1).
    둘 다 모르면 예전처럼 체결일 — 행 X 에는 체결일 < X 인 체결이 든다. `after_row` 가 없으면 처음부터"""
    if created is not None and after_at and upto_at:
        return [f for f in fills if str(after_at) < created.get(f.order_id, "") <= str(upto_at)]
    아래, 위 = str(after_row) if after_row else "", str(upto_row)
    if created is not None and upto_at:
        return [f for f in fills if 아래 <= f.fill_date and created.get(f.order_id, "") <= str(upto_at)]
    return [f for f in fills if 아래 <= f.fill_date < 위]


def _종목통화_수익(p: dict[str, Any]) -> float | None:
    """월간 복기 손절 점검의 수익률(%) — 매도 엔진(`sell_flags.evaluate`)과 같은 **종목 통화** 잣대: 종가 ÷ 평균단가
    (수수료 포함). 예전에는 원화 평가 ÷ 원화 원가라 미국 보유는 환율만 움직여도 "손절선 아래로 들고 간 날" 이 생기거나
    빠졌다(25.1547 감사 남은 것, 25.1555). 종가·평균단가가 없는 옛 행·원가로 둔 보유(멈춤·종가 없음)는 원화 비율로 —
    원가로 둔 보유는 0% 라 걸리지 않는다. **예외**: 유상증자 권리락 보유는 엔진이 원가에 권리락 계수를 곱하는데
    여기서는 곱하지 않아 엔진이 팔지 않은 날도 위반으로 셀 수 있다(25.1560 반박 정정 — 예전 원화 비율도 같았다)
    """
    종가, 평단 = p.get("close"), p.get("avg_price")
    if 종가 is not None and 평단 is not None and float(평단) > 0:
        return (float(종가) / float(평단) - 1) * 100
    원가 = float(p.get("cost_krw") or 0)
    return (float(p.get("value_krw") or 0) / 원가 - 1) * 100 if 원가 > 0 else None


def window_change(rows: list[tuple[str, float]], start: str, end: str) -> tuple[float, str, str] | None:
    """시간가중 지수 계열의 [start, end] 변화율(%) — (변화, 쓴 시작일, 쓴 끝일). 날짜 순 `rows` = [(날짜, 지수)].

    시작은 start 이하의 마지막 행(없으면 start 이상의 첫 행), 끝은 end 이하의 마지막 행. 둘을 못 잡거나 끝이 시작보다
    앞이면 None
    """
    if not rows:
        return None
    앞 = [r for r in rows if r[0] <= start]
    시작 = 앞[-1] if 앞 else next((r for r in rows if r[0] >= start), None)
    뒤 = [r for r in rows if r[0] <= end]
    if 시작 is None or not 뒤 or 뒤[-1][0] < 시작[0] or not 시작[1]:
        return None
    return (float(뒤[-1][1]) / float(시작[1]) - 1) * 100, 시작[0], 뒤[-1][0]


def compare_real(
    paper: dict[str, list[tuple[str, float]]], real: list[tuple[str, float]],
) -> dict[str, Any] | None:
    """모의 계좌와 **실제 포트폴리오**의 시간가중 수익률을 같은 기간으로 견준다 (docs/paper.md 4장,
    2026-10-15 사용자 지시 "실제 투자랑도 비교하는거 그냥 키자").

    기간 = (모의 첫 기록·실제 첫 기록 중 늦은 날) ~ (둘의 마지막 기록 중 이른 날). 실제는 `portfolio_values.twr_index`
    (보유분만·배당 포함), 모의는 `paper_daily.twr_index`(현금 포함·배당 없음) — 같은 잣대가 아니라는 것을
    부르는 쪽이 함께 적는다.
    어느 한쪽이 없으면 None
    """
    모의 = {k: v for k, v in paper.items() if v}
    if not 모의 or not real:
        return None
    # 시작은 실제 첫 기록과 **가장 이른** 모의 첫 기록 가운데 늦은 날 — 가장 늦은 모의 계좌에 맞추면 계좌를
    # 하나 더할 때마다 모든 계좌의 비교 창이 그 날로 밀렸다(25.1481 검증 [필수] 11).
    # 늦게 연 계좌는 제 첫 기록부터(`paper_start`)
    시작 = max([real[0][0], min(v[0][0] for v in 모의.values())])
    끝 = min([real[-1][0], *(v[-1][0] for v in 모의.values())])
    if 끝 < 시작:
        return None
    실제 = window_change(real, 시작, 끝)
    if 실제 is None:
        return None
    out: dict[str, Any] = {"start": 실제[1], "end": 실제[2], "real_pct": 실제[0], "paper_pct": {}, "paper_start": {}}
    for aid, rows in sorted(모의.items()):
        w = window_change(rows, 시작, 끝)
        out["paper_pct"][aid] = None if w is None else w[0]
        if w is not None and w[1] > 실제[1]:
            out["paper_start"][aid] = w[1]
    return out


def compare_line(c: dict[str, Any] | None) -> str | None:
    """리포트·요약에 싣는 줄들(줄바꿈으로 이음). 견줄 수 없으면 None.

    머리 한 줄 + 들여 쓴 실제·가상·단서 줄(25.1721, 리포트 가독성 — 예전엔 한 줄에 아홉 계좌와 단서가 다 실렸다)
    """
    if not c:
        return None
    늦음 = c.get("paper_start") or {}
    모의 = " · ".join(f"{account_name(aid)} {_퍼센트(v)}" + (f"({늦음[aid]}부터)" if aid in 늦음 else "")
                     for aid, v in c["paper_pct"].items())  # fmt: skip
    return "\n".join([
        f"실제 투자와 비교 ({c['start']}~{c['end']}, 시간가중)", f"  실제 {_퍼센트(c['real_pct'])}", f"  가상 {모의}",
        "  (실제는 보유분만·배당 포함, 가상모의투자는 현금 포함·배당 없음)",
    ])  # fmt: skip


#: 결정 원장의 성과 창 — 기준 뒤 몇 번째 거래일(그 종목 시세 행) 종가인가 (docs/paper.md 9장)
DECISION_HORIZONS = (5, 20, 60)
#: 달력일로 이만큼 지나야 그 거래일 수가 찼을 수 있다 — 아직 못 찰 행은 읽지 않는다(읽기 줄이기). 휴일이 몰려도 넉넉하게
HORIZON_CALENDAR_DAYS = {5: 7, 20: 28, 60: 84}
#: 창 끝날의 지수 행이 아직 없으면(그날 지수 수집 실패) 이 달력일까지는 그 창을 채우지 않고 기다린다 —
#: "그날 이하 마지막" 지수는 하루 묵은 값이라, 채우면 COALESCE 때문에 영구히 남았다(25.1579, 위원회 감사 [참고]).
#: 지수는 다음 배치가 다시 받으므로 한 주(달력 7일 ≈ 5 거래일)면 넉넉하다. 그 뒤엔 묵은 값으로 채운다(영영 비우지 않게)
BENCH_WAIT_DAYS = 7
#: 이만큼 지나도 60 거래일 시세가 안 모이면(정지·상장폐지) 더 기다리지 않고 까닭을 적는다
OUTCOME_GIVE_UP_DAYS = 200


def outcomes(
    ref_close: float | None, after: list[tuple[str, float]], bench_ref: float | None, bench_on: dict[str, float | None],
) -> dict[str, float | None]:
    """결정 뒤 성과 — `after` 는 기준일 **뒤** 그 종목 종가 [(날짜, 종가)] 날짜 순,
    `bench_on` 은 {그 날짜: 지수(그날 이하 마지막)}.

    거래일 수가 아직 안 찬 창은 넣지 않는다(이미 채운 값을 덮지 않게 부르는 쪽이 COALESCE).
    지수를 모르면 지수 칸만 비운다
    """
    out: dict[str, float | None] = {}
    if not ref_close or ref_close <= 0:
        return out
    for n in DECISION_HORIZONS:
        if len(after) < n:
            continue
        날, 값 = after[n - 1]
        out[f"ret_{n}d"] = (float(값) / float(ref_close) - 1) * 100
        b = bench_on.get(날)
        out[f"bench_{n}d"] = (float(b) / float(bench_ref) - 1) * 100 if b and bench_ref else None
    return out


def due_horizons(decided_on: str, today: str, have: dict[str, Any]) -> list[int]:
    """오늘 채울 수 있을지 모르는(달력일이 지난) 아직 빈 창."""
    from datetime import date

    지난 = (date.fromisoformat(today) - date.fromisoformat(decided_on)).days
    return [n for n in DECISION_HORIZONS
            if have.get(f"ret_{n}d") is None and 지난 >= HORIZON_CALENDAR_DAYS[n]]  # fmt: skip


#: 성과 분해의 창 — 결정 원장의 20 거래일(단기 투자 기간의 중간쯤) 지수 대비 초과 (docs/paper.md 10장)
ATTRIBUTION_HORIZON = 20
#: 이보다 표본이 적은 칸은 "참고" 로만 — 몇 건의 운을 실력으로 읽지 않게
ATTRIBUTION_MIN_SAMPLE = 30
#: 같은 종목·같은 결정이 이 달력일 안에 되풀이되면 한 번으로 센다 — 신호가 이어지는 동안 날마다 한 줄씩 쌓여
#: 20 거래일 창이 겹친 표본이 되고, 종목 한두 개로 "참고" 문턱을 넘었다 (25.1453, 교차검증 [필수] 4).
#: 20 거래일 ≈ 28 달력일
ATTRIBUTION_EPISODE_DAYS = 28
#: 선정 비교("주문 낸 후보 vs 안 낸 후보")에 넣지 않는 안 낸 까닭 — 이미 들고 있는 종목(상한 도달 = 지난 승자,
#: 손절 보유 = 지난 패자)과 판 뒤 쉼은 새 후보와 다른 집단이다. 까닭별 줄에는 그대로 남긴다 (25.1453)
#: "열린 매수 주문 있음"(25.1549) — 앞 실행의 주문이 그대로 차는 사실상 산 종목이라 안 산 후보와 견주지 않는다(25.1550)
#: 매수 단계가 일찍 끝난 날(환율 모름·장부 현금 음수) 위원회·지주사가 고른 종목의 skip 까닭 머리(25.1622, jobs/paper)
BUY_STEP_SKIP_PREFIX = "매수 단계 건너뜀: "
#: 위험 점검이 **매도**를 막은 결정의 까닭 머리(jobs/paper) — 성과 분해가 막은 매수와 가른다(25.1688: 막은 매수는
#: 오르면, 막은 매도는 내리면 점검이 비용이라 한 평균에 섞으면 +8 과 −8 이 0 으로 지워졌다)
BLOCKED_SELL_PREFIX = "위험 점검(매도)"
NONCOMPARABLE_SKIP_PREFIXES = ("종목 비중 상한 도달", "보유 중 손절선 이탈", "판 뒤 ", "열린 매수 주문 있음",
                               BUY_STEP_SKIP_PREFIX, DAY_LATE_PREFIX)  # fmt: skip


def _초과(rows: list[dict[str, Any]]) -> dict[str, Any]:
    키, 기 = f"ret_{ATTRIBUTION_HORIZON}d", f"bench_{ATTRIBUTION_HORIZON}d"
    xs = [float(r[키]) - float(r[기]) for r in rows if r.get(키) is not None and r.get(기) is not None]
    if not xs:
        return {"n": 0, "avg": None, "hit": None}
    return {"n": len(xs), "avg": sum(xs) / len(xs), "hit": sum(x > 0 for x in xs) / len(xs) * 100}


def _에피소드(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 종목·같은 kind 가 `ATTRIBUTION_EPISODE_DAYS` 안에 되풀이되면 첫 줄만 (종목·날짜를 모르면 그대로).

    성과 분해가 따로 세는 갈래(막은 매수·막은 매도, 새 매수·추가 매수)는 **다른 갈래**로 묶는다(25.1696, 25.1688·1689
    교차검증 [권고]) — (종목, kind) 하나로 묶으면 막은 매도 뒤 28일 안 막은 매수가, 추가 매수 뒤 다시 산 새 매수가
    먼저 온 쪽에 묻혀 사라졌다(25.1630 이 skip 에서 고친 "거른 뒤 묶기" 와 같은 뜻)."""
    from datetime import date

    남김: list[dict[str, Any]] = []
    마지막: dict[tuple[Any, Any, str], date] = {}
    for r in sorted(rows, key=lambda x: (str(x.get("decided_on") or ""), str(x.get("stock_id")))):
        if r.get("stock_id") is None or not r.get("decided_on"):
            남김.append(r)
            continue
        갈래 = ("매도" if r.get("kind") == "blocked" and _막은매도(r)
                else "추가" if r.get("kind") == "buy" and _추가매수(r) else "")  # fmt: skip
        열쇠, 날 = (r["stock_id"], r.get("kind"), 갈래), date.fromisoformat(str(r["decided_on"])[:10])
        if 열쇠 in 마지막 and (날 - 마지막[열쇠]).days < ATTRIBUTION_EPISODE_DAYS:
            continue
        마지막[열쇠] = 날
        남김.append(r)
    return 남김


#: 가상 대조 (docs/company.md 6장, 25.1476) — 매수마다 같은 날·같은 시장 유니버스에서 무작위로 뽑는 대조 종목 수.
#: 20 이면 분위 한 칸이 5 — 한 매수로는 거칠지만 에피소드 30 개를 모으면 평균의 표준오차가 약 5 분위
VIRTUAL_CONTROLS = 20
#: 20 거래일 성과를 낸 대조가 이보다 적으면 분위를 내지 않는다 (상장폐지·긴 정지로 빠진 것이 많은 날)
VIRTUAL_MIN_CONTROLS = 10


def control_picks(universe: list[int], market: str, ref_date: str, exclude: int) -> list[int]:
    """가상 대조 종목 — `random_order(유니버스, "virtual|시장|기준일")` 앞에서 산 종목을 빼고 `VIRTUAL_CONTROLS` 개.

    같은 날 같은 시장의 매수는 모두 같은 대조군이다(산 종목만 빠진다) — 회사끼리 같은 잣대로 견준다. 재현 가능
    """
    return [c for c in random_order(universe, f"virtual|{market}|{ref_date}")[: VIRTUAL_CONTROLS + 1]
            if c != exclude][:VIRTUAL_CONTROLS]  # fmt: skip


def control_pct(buy_ret: float, control_rets: list[float]) -> float | None:
    """대조 수익 분포 안에서 매수의 분위(0~100) — (낮은 수 + 같은 수의 절반) / 대조 수. 대조가 모자라면 None."""
    if len(control_rets) < VIRTUAL_MIN_CONTROLS:
        return None
    낮음 = sum(r < buy_ret for r in control_rets)
    같음 = sum(r == buy_ret for r in control_rets)
    return (낮음 + 0.5 * 같음) / len(control_rets) * 100


def _가상(buys: list[dict[str, Any]]) -> dict[str, Any]:
    """에피소드 첫 매수의 가상 대조 분위 — 평균·위쪽 절반 비율. 무작위면 평균 50."""
    xs = [float(b["control_pct"]) for b in buys if b.get("control_pct") is not None]
    if not xs:
        return {"n": 0, "avg_pct": None, "top_half": None}
    return {"n": len(xs), "avg_pct": sum(xs) / len(xs), "top_half": sum(x > 50 for x in xs) / len(xs) * 100}


def _까닭_묶음(reason: str) -> str:
    """안 산 까닭을 묶는 열쇠 — 위원회 부결 사유의 숫자(표·찬성 수)를 N 으로. 숫자마다 다른 까닭으로 갈려
    상위 5칸이 "표 0.83 < 문턱 1"·"표 0.91 < 문턱 1" 같은 조각으로 찼다 (25.1472)"""
    import re

    return re.sub(r"[-+]?\d+(?:\.\d+)?", "N", reason) if reason.startswith("위원회") else reason


def _막은매도(d: dict[str, Any]) -> bool:
    return str(d.get("reason") or "").startswith(BLOCKED_SELL_PREFIX)


def attribution(
    decisions: list[dict[str, Any]], entries: list[tuple[float, float | None]], limit_fills: int = 0,
    close_fills: int = 0,
) -> dict[str, Any]:
    """성과 분해 — 어디가 돈을 버나 (docs/paper.md 10장, 25.1450). 순수 함수.

    모두 **20 거래일 뒤 지수 대비 초과(%p)**. 선정 = 산 후보 vs 안 산 후보, 비중 필터 = 안 산 까닭별,
    진입 = 체결가가 그날 종가보다 얼마나 쌌나(%)와 안 찬 회차의 그 뒤, 청산 = 판 뒤와 플래그를 따르지 않고 든 뒤
    (판 뒤가 음수면 판 것이 옳았다). `entries` 는 **시장가** 매수 체결마다 (체결가, 체결일 종가). 지정가 매수 체결은
    `limit_fills` 로 수만 받는다 — 체결 모형이 "종가 ≤ 회차 가격이면 회차 가격" 이라 체결가가 늘 종가 이상이어서 진입이
    실력과 상관없이 0 이하로 나왔다(25.1687, 결정 원장 감사 [필수] 2 — B·H 가 늘 "비쌌다"). 종가로 판정된 시장가 매수는
    `close_fills` 로 수만 — 체결가가 그날 종가라 늘 0 이다(25.1783)
    """
    원래 = decisions
    decisions = _에피소드(decisions)
    종류 = {k: [d for d in decisions if d.get("kind") == k]
            for k in ("buy", "skip", "missed", "sell", "hold_flag", "blocked")}  # fmt: skip
    # 이미 든 종목의 skip 도 "안 낸 새 후보" 가 아니다 — 다 투자한 계좌는 목표 비중 아래로 내려간 보유(대개 최근에 빠진
    # 종목)가 날마다 "투자 여력 부족" 으로 이 풀에 들었다(25.1600, 매도·성과 분해 감사 [권고] 3).
    # **거른 뒤 에피소드로 묶는다**(25.1630) — 예전엔 묶은 뒤 걸러, 에피소드 첫 줄이 견주지 않는 skip("판 뒤"·"상한
    # 도달"·이미 든 종목)이면 28일 안에 다시 새 후보가 된 같은 종목의 skip 까지 빠졌다(판 뒤 다시 산 줄은 buy 라 남아
    # 안 산 쪽만 빠짐 — 선정 비교가 한쪽으로 기움, 25.1601 [참고]). 까닭별 칸도 까닭마다 따로 묶는다
    # 날짜·종목 순 — 결정 원장 질의에 순서가 없어 까닭 묶음의 순서(n 이 같은 까닭의 상위 5)가 DB 가 돌려준 순서를 따랐다
    # (25.1630 교차검증 [권고] 1)
    원skip = sorted((d for d in 원래 if d.get("kind") == "skip"),
                   key=lambda x: (str(x.get("decided_on") or ""), str(x.get("stock_id"))))  # fmt: skip
    견줄 = _에피소드([d for d in 원skip if not str(d.get("reason")).startswith(NONCOMPARABLE_SKIP_PREFIXES)
                   and not _추가매수(d)])  # fmt: skip
    까닭: dict[str, list[dict[str, Any]]] = {}
    for d in 원skip:
        까닭.setdefault(_까닭_묶음(str(d.get("reason"))), []).append(d)
    까닭 = {k: _에피소드(v) for k, v in 까닭.items()}
    진입 = [(float(c) - float(p)) / float(p) * 100 for p, c in entries if c and p and p > 0]
    새매수 = [d for d in 종류["buy"] if not _추가매수(d)]
    return {
        "horizon": ATTRIBUTION_HORIZON,
        # 산 쪽도 **새** 후보만(25.1689, 결정 원장 감사 [권고]) — 안 산 쪽은 이미 든 종목을 빼는데 산 쪽엔 추가 매수가
        # 남아 한쪽만 뺐다(결정 단위 증거 `selection_evidence` 는 이미 양쪽 다 뺀다). 추가 매수는 따로 한 칸
        "selection": _초과(새매수),
        "add_on": _초과([d for d in 종류["buy"] if _추가매수(d)]),
        "skipped": _초과(견줄),
        "skip_by_reason": dict(sorted(((k, _초과(v)) for k, v in 까닭.items()),
                                      key=lambda kv: (-kv[1]["n"], kv[0]))[:5]),  # fmt: skip
        "entry": {"n": len(진입), "avg": sum(진입) / len(진입) if 진입 else None,
                  **({"limit_n": limit_fills} if limit_fills else {}),
                  **({"close_n": close_fills} if close_fills else {})},
        "missed": _초과(종류["missed"]),
        "sold": _초과(종류["sell"]),
        "held_flag": _초과(종류["hold_flag"]),
        # 막은 매수·막은 매도를 가른다(25.1688, 결정 원장 감사 [권고]) — 뜻이 반대다
        "blocked": _초과([d for d in 종류["blocked"] if not _막은매도(d)]),
        "blocked_sell": _초과([d for d in 종류["blocked"] if _막은매도(d)]),
        "virtual": _가상(새매수),
    }


# ── 결정 단위 선정 증거 (docs/paper.md 10.1, 25.1518) ─────────────────────────────────────────────

#: 같은 날 견줄 새 후보(안 산 것)가 이보다 적은 날은 뺀다 — 후보 둘 사이 분위는 0·50·100 뿐이라 거칠다
EVIDENCE_MIN_POOL = 3
#: 거래일 묶음(달력일) — 20 거래일 뒤 수익은 이웃한 날끼리 거의 다 겹쳐 하루를 표본 하나로 세면 표준오차를 크게 깎는다.
#: 기간(20 거래일 ≈ 28 달력일)만큼 묶는다
EVIDENCE_BLOCK_DAYS = 28
#: 하한을 내는 최소 묶음 수 — 자유도 3 아래면 t 분위 근사(Cornish–Fisher)가 크게 틀린다
EVIDENCE_MIN_BLOCKS = 4
#: 묶음 하나로 세려면 결정일이 이만큼 있어야 한다 — 앞뒤 끝 묶음이 하루치여도 같은 무게를 받아 "묶음 6개 ≈ 반년" 이
#: 석 달 남짓에 찼다(25.1523, 검토 5)
EVIDENCE_BLOCK_MIN_DAYS = 3
#: 한쪽 유의수준 — 하한 = 평균 − t(1−α, n−1) × 표준오차
EVIDENCE_ALPHA = 0.05


def t_quantile(p: float, df: int) -> float:
    """t 분포 분위 근사 — 정규 분위에 Cornish–Fisher 보정 네 항 (Abramowitz & Stegun 26.7.5). 자유도 3 이상에서
    t 표와 대조한 값은 시험에(tests/test_selection_evidence_1518.py). scipy 를 쓰지 않으려고"""
    from statistics import NormalDist

    z = NormalDist().inv_cdf(p)
    g1 = (z**3 + z) / 4
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96
    g3 = (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / 384
    g4 = (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / 92160
    return z + g1 / df + g2 / df**2 + g3 / df**3 + g4 / df**4


def block_bound(values: dict[int, float], alpha: float = EVIDENCE_ALPHA) -> dict[str, Any]:
    """묶음 값들 → {n_blocks, mean, se, lower}. 묶음이 `EVIDENCE_MIN_BLOCKS` 보다 적으면 lower 는 None"""
    xs = list(values.values())
    n = len(xs)
    if not n:
        return {"n_blocks": 0, "mean": None, "se": None, "lower": None}
    m = sum(xs) / n
    if n < 2:
        return {"n_blocks": n, "mean": m, "se": None, "lower": None}
    분산 = sum((x - m) ** 2 for x in xs) / (n - 1)
    # 이웃 묶음끼리 상관 보정(뉴이–웨스트 시차 1, 바틀렛 가중 1/2) — 28일 묶음과 20 거래일 수익 창이 거의 같은 길이라
    # 이웃 묶음 평균이 겹친다(ρ≈0.25 어림, 25.1523 검토 4). 더 커질 때만 쓴다(작아지는 쪽으로는 고치지 않는다)
    키 = sorted(values)
    짝 = [(values[a] - m) * (values[b] - m) for a, b in zip(키, 키[1:], strict=False) if b == a + 1]
    if 짝:
        분산 = max(분산, 분산 + 2 * 0.5 * sum(짝) / (n - 1))
    se = 분산**0.5 / n**0.5
    lower = m - t_quantile(1 - alpha, n - 1) * se if n >= EVIDENCE_MIN_BLOCKS else None
    return {"n_blocks": n, "mean": m, "se": se, "lower": lower}


def _묶음(day: str) -> int:
    from datetime import date

    return date.fromisoformat(day[:10]).toordinal() // EVIDENCE_BLOCK_DAYS


def _추가매수(d: dict[str, Any]) -> bool:
    """결정 때 이미 든 종목인가(`detail.held`, 매수 줄 25.1570·제외 skip 줄 25.1600 부터 적힌다 — 그 전 행은 모름이라
    예전처럼 센다). 산 줄이면 추가 매수, skip 줄이면 새 후보가 아닌 보유"""
    det = d.get("detail")
    if det is None:
        try:
            det = json.loads(d.get("detail_json") or "{}")
        except (TypeError, ValueError):
            det = {}
    return bool(isinstance(det, dict) and det.get("held"))


def selection_evidence(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """결정 단위 선정 증거 — 산 종목이 **같은 날 같은 시장의 안 산 새 후보** 가운데 몇 분위였나 (docs/paper.md 10.1).

    한 계좌의 평가 곡선 하나로는 반년에 연 수십 % 초과가 아니면 운과 실력을 못 가른다 — 결정마다 같은 날 후보와 견주면
    시장 전체·같은 날 공통 움직임이 빠져 잡음이 크게 준다.
    날마다 (산 종목 분위 평균) → `EVIDENCE_BLOCK_DAYS` 묶음 평균 → 묶음 평균들의 평균과 한쪽 하한.
    무작위로 골랐으면 50. 견줄 새 후보 = `NONCOMPARABLE_SKIP_PREFIXES` 를 뺀 skip.
    같이 20 거래일 지수 대비 초과(산 종목 평균)의 묶음 평균도 낸다 — 챔피언 비교(짝지은 묶음 차이)에 쓴다
    """
    키, 기 = f"ret_{ATTRIBUTION_HORIZON}d", f"bench_{ATTRIBUTION_HORIZON}d"
    날: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for d in decisions:
        if d.get(키) is None or not d.get("decided_on"):
            continue
        k = (str(d.get("market") or ""), str(d["decided_on"])[:10])
        if d.get("kind") == "buy" and _추가매수(d):
            # 상한까지 채우는 추가 매수는 새 선정이 아니다 — 같은 종목이 날마다 산 쪽을 차지했다(25.1570, 운용 감사
            # [확인필요] 6. 핵심 H 무작위 대조 분위가 25.1544 [권고] 4 에서 뺀 것과 같은 뜻)
            continue
        if d.get("kind") == "buy":
            날.setdefault(k, {"buy": [], "skip": []})["buy"].append(d)
        elif (d.get("kind") == "skip" and not str(d.get("reason")).startswith(NONCOMPARABLE_SKIP_PREFIXES)
              and not _추가매수(d)):  # 이미 든 종목의 skip 은 새 후보 풀 밖(25.1600)
            날.setdefault(k, {"buy": [], "skip": []})["skip"].append(d)
    분위묶음: dict[int, list[float]] = {}
    초과묶음: dict[int, list[float]] = {}
    # 묶음의 결정일 수는 **달력 날짜**로 센다 — (시장, 날짜)로 세면 두 시장 계좌(H)는 같은 날 국내·미국이 이틀이 됐다
    # (25.1570, 운용 감사 [권고] 1). 짝 차이(초과)도 같은 사흘 문턱(같은 감사 [권고] 2 — 하루치 끝 묶음이 같은 무게)
    분위날: dict[int, set[str]] = {}
    초과날: dict[int, set[str]] = {}
    쓴날 = 0
    # 같은 종목이 28일 안에 되풀이해 새 매수 결정이 되면 첫 결정만(25.1706, 25.1697 교차검증 [권고] 1) — 분할 지정가가
    # 안 차면 다음 날 같은 종목이 다시 "새 매수" 가 되어 몇 종목만으로 결정일 문턱·묶음 6개가 찼다. 연구실 가상 대조
    # (`_가상` — 에피소드)·핵심 백테스트(`core_evidence.first_buys`)와 같은 잣대. **챔피언 짝 차이(초과 묶음)에도**
    # 쓴다(25.1784, 결함 루프 [권고] — 25.1706 은 가상 대조에만 넣어 한 종목 되풀이로 챔피언 후보가 됐다). 분위 묶음은
    # 사전 등록 정의(관문 "선정 실력")라 그대로 둔다
    첫매수 = {id(b) for b in _에피소드([b for g in 날.values() for b in g["buy"]])}
    for (_m, day), g in sorted(날.items()):
        if not g["buy"]:
            continue
        for b in g["buy"]:
            if b.get(기) is not None and id(b) in 첫매수:
                초과묶음.setdefault(_묶음(day), []).append(float(b[키]) - float(b[기]))
                초과날.setdefault(_묶음(day), set()).add(day)
        풀 = [float(x[키]) for x in g["skip"]]
        if len(풀) < EVIDENCE_MIN_POOL:
            continue
        분위 = [(sum(r < float(b[키]) for r in 풀) + 0.5 * sum(r == float(b[키]) for r in 풀)) / len(풀) * 100
              for b in g["buy"]]  # fmt: skip
        분위묶음.setdefault(_묶음(day), []).append(sum(분위) / len(분위))
        분위날.setdefault(_묶음(day), set()).add(day)
        쓴날 += 1
    블록 = {k: sum(v) / len(v) for k, v in 분위묶음.items() if len(분위날[k]) >= EVIDENCE_BLOCK_MIN_DAYS}
    초과 = {k: sum(v) / len(v) for k, v in 초과묶음.items() if len(초과날[k]) >= EVIDENCE_BLOCK_MIN_DAYS}
    # **가상 대조 분위의 묶음 하한** (25.1697, H·R 감사 [필수]) — 산 새 종목이 같은 날·같은 시장 유니버스의 무작위
    # 20종목 안 몇 분위였나(`control_pct`, `fill_controls`). 진입 방식(분할 지정가·시가 일괄)·매도 규칙·현금 비중과
    # 무관하게 선정만 잰다 — 관문 "R 대비" 는 H 와 R 의 규칙이 달라(진입·매도·투자 속도) 하락장엔 현금만으로 통과하고
    # 상승장엔 실력이 있어도 떨어졌다. 같은 묶음·같은 하한 계산(무작위면 50)
    # 가상 대조도 위 `첫매수` 로(25.1706)
    대조묶음: dict[int, list[float]] = {}
    대조날: dict[int, set[str]] = {}
    for (_m, day), g in sorted(날.items()):
        xs = [float(b["control_pct"]) for b in g["buy"] if b.get("control_pct") is not None and id(b) in 첫매수]
        if xs:
            대조묶음.setdefault(_묶음(day), []).append(sum(xs) / len(xs))
            대조날.setdefault(_묶음(day), set()).add(day)
    대조 = {k: sum(v) / len(v) for k, v in 대조묶음.items() if len(대조날[k]) >= EVIDENCE_BLOCK_MIN_DAYS}
    return {"horizon": ATTRIBUTION_HORIZON, "days": 쓴날, **block_bound(블록),
            "blocks": {str(k): v for k, v in sorted(블록.items())},
            "excess_blocks": {str(k): v for k, v in sorted(초과.items())},
            "control": {**block_bound(대조), "blocks": {str(k): v for k, v in sorted(대조.items())}}}  # fmt: skip


def attribution_lines(aid: str, a: dict[str, Any] | None) -> list[str]:
    """주간 요약·리포트용 몇 줄. 표본이 `ATTRIBUTION_MIN_SAMPLE` 보다 적은 칸에는 "참고" 를 붙인다."""
    nm = account_name(aid)  # 화면 이름(25.1893)
    if not a:
        return []

    def 칸(s: dict[str, Any]) -> str:
        if not s.get("n"):
            return "표본 없음"
        # "이긴 비율" 이라 쓰면 판 뒤·막은 매도·안 찬 회차에서 규칙이 매번 틀린 경우가 "이긴 100%" 로 읽혔다 — 주어는
        # 칸의 종목(25.1804, 결함 루프 10-26). 옳고 그름은 칸마다 붙인 방향 글로
        비율 = f", 지수보다 오른 비율 {s['hit']:.0f}%" if s.get("hit") is not None else ""
        글 = f"{s['avg']:+.1f}%p (n={s['n']}{비율}"
        return 글 + (", 참고" if s["n"] < ATTRIBUTION_MIN_SAMPLE else "") + ")"

    줄 = [f"  {nm} 선정: 주문 낸 새 후보 {칸(a['selection'])} · 안 낸 새 후보 {칸(a['skipped'])}"
          + (f" · 이미 든 종목 추가 매수 {칸(a['add_on'])}" if (a.get("add_on") or {}).get("n") else "")
          + (" — 안 낸 새 후보가 지수보다 오르면 안 낸 것이 비용" if (a.get("skipped") or {}).get("n") else "")]
    if a.get("skip_by_reason"):
        줄.append(f"  {nm} 안 낸 까닭별: " + " · ".join(f"{k} {칸(v)}" for k, v in a["skip_by_reason"].items())
                 + " — 지수보다 오르면 그 까닭으로 안 낸 것이 비용")
    if (a.get("blocked") or {}).get("n"):
        줄.append(f"  {nm} 위험 점검이 막은 매수의 그 뒤: {칸(a['blocked'])} — 지수보다 오르면 막은 것이 비용")
    if (a.get("blocked_sell") or {}).get("n"):
        줄.append(f"  {nm} 위험 점검이 막은 매도의 그 뒤: {칸(a['blocked_sell'])} — 지수보다 내리면 막은 것이 비용")
    e = a.get("entry") or {}
    # 음수면 "−0.85% 쌌다" 가 아니라 "0.85% 비쌌다" — 부호가 뜻을 뒤집어 읽혔다(25.1644, 25.1630 감사 [권고])
    if not e.get("n"):
        진입 = "체결 표본 없음"
    elif round(e["avg"], 2) == 0:
        진입 = f"체결가가 그날 종가와 평균 같았다 (n={e['n']})"
    else:
        진입 = f"체결가가 그날 종가보다 평균 {abs(e['avg']):.2f}% {'쌌다' if e['avg'] > 0 else '비쌌다'} (n={e['n']})"
    # 재지 않는 체결 — 지정가(모형상 늘 종가 이상, 25.1687)·종가 판정 시장가(늘 0, 25.1783).
    # 진입 규칙은 안 찬 회차의 그 뒤로 본다
    안잼 = ", ".join(x for x in (
        f"지정가 체결 {e['limit_n']}건" if e.get("limit_n") else "",
        f"종가로 판정된 시장가 체결 {e['close_n']}건" if e.get("close_n") else "") if x)  # fmt: skip
    if 안잼:
        까닭표 = {(True, False): "모형상 늘 종가 이상", (False, True): "체결가가 그날 종가라 늘 0",
                (True, True): "지정가는 모형상 늘 종가 이상, 종가 판정은 늘 0"}
        까닭 = 까닭표[(bool(e.get("limit_n")), bool(e.get("close_n")))]
        글 = f"{안잼}은 재지 않음({까닭})"
        진입 = 글 if not e.get("n") else f"{진입}, {글}"
    놓침 = " — 지수보다 오르면 놓친 기회" if (a.get("missed") or {}).get("n") else ""  # 표본이 없으면 방향 글도 없다
    줄.append(f"  {nm} 진입: {진입} · 안 찬 회차의 그 뒤 {칸(a['missed'])}{놓침}")
    줄.append(f"  {nm} 청산: 판 뒤 {칸(a['sold'])} · 플래그를 따르지 않고 든 뒤 {칸(a['held_flag'])}"
             " — 판 뒤가 음수면 판 것이, 든 뒤가 양수면 든 것이 옳았다")
    v = a.get("virtual") or {}
    if v.get("n"):
        줄.append(f"  {nm} 가상 대조: 산 종목이 같은 날 무작위 {VIRTUAL_CONTROLS}종목 안 평균 {v['avg_pct']:.0f} 분위"
                 f" · 위쪽 절반 {v['top_half']:.0f}% (n={v['n']}"
                 + (", 참고" if v["n"] < ATTRIBUTION_MIN_SAMPLE else "") + ") — 무작위면 50")  # fmt: skip
    return 줄
