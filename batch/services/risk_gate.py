"""주문 전 위험 점검 — 2부 배분과 **따로** 한 번 더 막는 관문 (docs/paper.md 11장, docs/infra.md 25.1451).

2026-10-15 사용자 목표("노하우를 … 실제 매매프로그램으로 쓰는거야")의 안전장치. 배분 함수
(`report_picks.build_portfolio`)가 이미 상한을 지키지만, 실제 주문을 내는 프로그램에는 **배분 코드의 결함이
그대로 주문이 되지 않게** 독립된 점검이 있어야 한다. 지금은 모의 주문이 이 관문을 지나고, 걸린 것은 결정 원장
(`paper_decisions`, reason "위험 점검: …")에 남는다. 실주문을 여는 날(사용자 결정 뒤) 같은 관문을 그대로 쓴다.

순수 함수다 — DB 를 읽지 않는다. 같은 입력이면 같은 판정(`ref_floor` 만 거래소 달력을 읽는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

#: 오늘 하루 수익(입출금을 뺀 시간가중)이 이만큼(%) 넘게 빠졌으면 그날 **새 매수**를 멈춘다(매도는 둔다). 하루 −3% 는
#: 국내 지수로도 드문 날이다 — 계좌가 그만큼 빠졌으면 무언가(시세 오류·분할 미반영·쏠림)를 먼저 의심한다
#: `[확인필요: 운영 1년 뒤 걸린 날 수로 다시]`
#: 핵심 H 백테스트 빈도(`loss_gate`, 25.1552)는 수정주가·결함 없는 환경이라 운영 빈도의 하한일 뿐
#: 이 자리를 채우지 않는다
DAILY_LOSS_HALT_PCT = -3.0
#: 계좌·시장·하루 새 매수 주문 상한 — 2부 상위 다섯 × 3회차 = 15 의 두 배. 넘으면 배분 코드가 이상한 것이다
MAX_ORDERS_PER_DAY = 30
#: 상한 재검사의 여유 — 배분은 금액으로, 주문은 정수 주·예약 여유(수수료 1%·달러 환율 3%)로 내려 생기는
#: 차이까지는 막지 않는다. 큰 쪽: 고정 0.5%p 또는 상한의 5%(상한 15% 면 0.75%p — 고정값만이면 미국 15% 상한의
#: 정상 배분이 15.6% 로 막혔다, 25.1453)
CAP_TOLERANCE_PCT = 0.5
CAP_TOLERANCE_REL = 0.05
#: 매수 기준 종가가 오늘 앞 이 거래일 수(그 시장 달력)보다 묵었으면 막는다 — 평소 주엔 예전 잣대(달력일 7일)와 같다.
#: 예전엔 달력일로만 세어 긴 연휴 뒤 첫 아침(국내 2025-10-02 → 10-10 이 8일, 2017-09-29 → 10-10 이 11일)에 바로 앞
#: 거래일 종가까지 "묵음" 으로 읽혀 그날 매수가 모두 막혔다(25.1629, 위험 점검 감사 [필수] 2)
STALE_PRICE_SESSIONS = 5
#: 달력을 주지 않은 문맥(`ref_floor` 없음)의 옛 잣대 — 달력일
STALE_PRICE_DAYS = 7

Reason = tuple[str, str]  # (코드, 사람이 읽는 글)


@dataclass(frozen=True)
class Context:
    """한 계좌의 지금 — 원화."""

    equity_krw: float
    #: 오늘 하루 수익률(%) — **입출금을 뺀** 시간가중 지수로 낸 것. 첫날은 None (25.1453)
    day_return_pct: float | None
    cash_available_krw: float  # 현금 − 다른 미체결 매수 예약
    holding_krw: dict[int, float]  # 종목 → 보유 평가(원)
    sector_of: dict[int, str | None]
    max_stock_pct: float
    max_sector_pct: float
    today: str
    halted: bool = False  # 사람이 켠 멈춤 스위치
    frozen: frozenset[int] = field(default_factory=frozenset)
    #: 이 날보다 앞선 기준 종가는 묵음(`ref_floor`) — 비면 달력일 `STALE_PRICE_DAYS` 로
    ref_floor: str | None = None


@dataclass(frozen=True)
class Candidate:
    stock_id: int
    side: str  # buy · sell
    cost_krw: float  # 매수면 **낼 것으로 보는 값**(수수료·환율 여유 포함) — 종목·섹터 상한이 본다. 매도면 0
    ref_date: str | None = None  # 매수 기준 종가의 날짜
    key: Any = None  # 부르는 쪽이 다시 찾을 열쇠(회차 등)
    #: 현금 검사의 잣대 — **예약과 같은 값**. 시장가 매수(시가 일괄)는 틈 여유까지 넣어 `cost_krw` 보다 크다.
    #: 비면 `cost_krw` (25.1458 재검증: 둘을 한 값으로 두면 상한이 여유만큼 부풀어 C·R 이 막히거나,
    #: 현금 검사가 예약과 다른 잣대가 된다)
    cash_krw: float | None = None

    @property
    def cash_cost(self) -> float:
        return self.cost_krw if self.cash_krw is None else self.cash_krw


def _여유(상한: float) -> float:
    return max(CAP_TOLERANCE_PCT, 상한 * CAP_TOLERANCE_REL)


def _매수_까닭(c: Candidate, ctx: Context, 손실: float | None, 매수수: int, 쓴현금: float,
              종목합: dict[int, float], 섹터합: dict[str, float]) -> list[Reason]:  # fmt: skip
    까닭: list[Reason] = []
    if 손실 is not None and 손실 <= DAILY_LOSS_HALT_PCT:
        까닭.append(("daily_loss", f"오늘 계좌 {손실:+.1f}% — 하루 손실 한도 {DAILY_LOSS_HALT_PCT:g}% 넘음"))
    if 매수수 >= MAX_ORDERS_PER_DAY:
        까닭.append(("max_orders", f"하루 매수 주문 {MAX_ORDERS_PER_DAY}건 넘음"))
    if ctx.equity_krw > 0:
        종목뒤 = (종목합.get(c.stock_id, 0.0) + c.cost_krw) / ctx.equity_krw * 100
        if 종목뒤 > ctx.max_stock_pct + _여유(ctx.max_stock_pct):
            까닭.append(("stock_cap", f"사고 나면 종목 비중 {종목뒤:.1f}% > 상한 {ctx.max_stock_pct:g}%"))
        섹터 = ctx.sector_of.get(c.stock_id)
        섹터뒤 = (섹터합.get(str(섹터), 0.0) + c.cost_krw) / ctx.equity_krw * 100 if 섹터 else 0.0
        if 섹터뒤 > ctx.max_sector_pct + _여유(ctx.max_sector_pct):
            까닭.append(("sector_cap", f"사고 나면 {섹터} 비중 {섹터뒤:.1f}% > 상한 {ctx.max_sector_pct:g}%"))
    if 쓴현금 + c.cash_cost > ctx.cash_available_krw + 1:
        까닭.append(("cash", "현금(다른 미체결 매수 예약을 뺀) 넘음"))
    if not c.ref_date:
        까닭.append(("no_ref", "기준 종가 날짜 모름"))
    elif ctx.ref_floor is not None:
        if c.ref_date[:10] < ctx.ref_floor:
            까닭.append(("stale", f"기준 종가가 {c.ref_date} — {STALE_PRICE_SESSIONS}거래일 넘게 묵음"))
    elif (date.fromisoformat(ctx.today) - date.fromisoformat(c.ref_date[:10])).days > STALE_PRICE_DAYS:
        까닭.append(("stale", f"기준 종가가 {c.ref_date} — {STALE_PRICE_DAYS}일 넘게 묵음"))
    return 까닭


def gate(candidates: list[Candidate], ctx: Context) -> tuple[list[Candidate], list[tuple[Candidate, list[Reason]]]]:
    """(통과, [(막힌 것, [(코드, 까닭)])]). 들어온 순서대로 보며, 통과한 매수는 뒤 후보의 상한·현금에 더한다.

    매도는 멈춤 스위치와 기업행위 경계만 본다 — 위험을 줄이는 주문을 손실 한도로 막으면 거꾸로다
    """
    통과: list[Candidate] = []
    막힘: list[tuple[Candidate, list[Reason]]] = []
    손실 = ctx.day_return_pct
    종목합 = dict(ctx.holding_krw)
    섹터합: dict[str, float] = {}
    for sid, v in ctx.holding_krw.items():
        if ctx.sector_of.get(sid):
            섹터합[str(ctx.sector_of[sid])] = 섹터합.get(str(ctx.sector_of[sid]), 0.0) + v
    쓴현금, 매수수 = 0.0, 0
    for c in candidates:
        까닭: list[Reason] = []
        if ctx.halted:
            까닭.append(("halt", "멈춤 스위치가 켜져 있음"))
        if c.stock_id in ctx.frozen:
            까닭.append(("frozen", "기업행위 경계(분할·병합 의심)"))
        if c.side == "buy":
            까닭 += _매수_까닭(c, ctx, 손실, 매수수, 쓴현금, 종목합, 섹터합)
        if 까닭:
            막힘.append((c, 까닭))
            continue
        통과.append(c)
        if c.side == "buy":
            매수수 += 1
            쓴현금 += c.cash_cost
            종목합[c.stock_id] = 종목합.get(c.stock_id, 0.0) + c.cost_krw
            if ctx.sector_of.get(c.stock_id):
                k = str(ctx.sector_of[c.stock_id])
                섹터합[k] = 섹터합.get(k, 0.0) + c.cost_krw
    return 통과, 막힘


def ref_floor(market: str, today: date) -> str:
    """오늘 앞 `STALE_PRICE_SESSIONS` 번째 거래일 — 기준 종가가 이보다 앞서면 묵음. 달력만 읽는다(DB 아님)."""
    from batch.core import calendar as cal

    d = today
    for _ in range(STALE_PRICE_SESSIONS):
        d = cal.previous_session(market, d)
    return d.isoformat()


def summary(막힘: list[tuple[Candidate, list[Reason]]]) -> dict[str, Any]:
    """실행 기록용 — 까닭 코드별 건수."""
    out: dict[str, int] = {}
    for _c, 까닭 in 막힘:
        for r in 까닭:
            out[r[0]] = out.get(r[0], 0) + 1
    return {"blocked": len(막힘), "by_reason": out}
