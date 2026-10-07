"""포트폴리오 계산 (docs/portfolio.md). 순수 함수만. 네트워크와 DB 를 모른다.

원본은 사용자가 입력한 trades · dividend_receipts 뿐이다. 여기서 나오는 것은 모두 파생이라
언제든 원본에서 다시 만들 수 있어야 한다.

환차손익 분리 (docs/portfolio.md 2장)
  원화 총손익 = 매도대금 × 매도환율 − 매수원가 × 매수환율
             = (매도대금 − 매수원가) × 매수환율        ← 주가 손익
             + 매도대금 × (매도환율 − 매수환율)        ← 환차손익
  두 줄의 합이 총손익과 **정확히** 같다(항등식). 테스트로 고정한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

#: 2 — 현금 상태 날을 지표에서 뺐다 (docs/infra.md 25.736). 지난 스냅샷의 지표와 견줄 때 판이 다르다
#: 3 — 그날 산 몫을 그날 판 왕복을 교체로 상계하지 않는다 (docs/infra.md 25.780·25.782). 그런 날이 있던 TWR 이 바뀐다
#: 4 — 일부만 판 묶음은 원가 전체(25.784), 전날 몫과 그날 묶음에 걸친 매도 줄의 대금은 매수 자금이 아니다(25.787)
#: 5 — 지급일 보유가 앞 6개월 최대의 절반 아래면 배당을 몫이 빠진 날에 붙인다 (docs/infra.md 25.828)
CALC_VERSION = 5

# 수량 비교 허용 오차. 소수 주식(미국)을 더하고 빼다 생기는 부동소수 찌꺼기
QTY_EPS = 1e-9


@dataclass(frozen=True)
class Trade:
    id: int
    stock_id: int
    side: str  # buy | sell
    trade_date: str
    price: float
    quantity: float
    currency: str
    fx_rate: float
    fee: float | None = None
    tax: float | None = None
    horizon: str | None = None


@dataclass(frozen=True)
class CostRates:
    """수수료·세율(%). None 은 사용자가 아직 정하지 않았다는 뜻이다(설정 화면 경고)."""

    buy_fee_pct: float | None = None
    sell_fee_pct: float | None = None
    sell_tax_pct: float | None = None  # 국내 증권거래세. 미국은 매매마다 붙는 세금이 없다
    #: 매도에 매매세가 **있는** 시장인가. 거짓이면(미국) 세금 칸이 비어도 0 이 **확정값**이다 — 추정이 아니다.
    #: 예전에는 `sell_tax_pct=None` 을 국내처럼 "모른다" 로 읽어 미국 매도가 전부 "일부 추정" 으로 찍혔다
    #: (docs/infra.md 25.285)
    has_sell_tax: bool = True
    #: 국내 매도세의 시행일별 법정 세율 ((시행일, %), …). 비었으면 `sell_tax_pct` 하나. **마지막(지금) 구간 앞의
    #: 매도**는 이 표로 추정한다 —
    #: 설정 세율은 사용자가 지금 치르는 값이라 2025 년(0.15%) 매도에 2026 년 세율을 물리면 실현손익이 틀린다
    #: (docs/infra.md 25.791, 25.783 과 같은 규칙)
    sell_tax_schedule: tuple[tuple[str, float], ...] = ()


@dataclass
class Lot:
    buy_trade_id: int
    sell_trade_id: int
    stock_id: int
    quantity: float
    buy_price: float
    sell_price: float
    currency: str
    buy_fx: float
    sell_fx: float
    cost: float
    proceeds: float
    realized_pnl: float
    realized_pnl_krw: float
    price_pnl_krw: float
    fx_pnl_krw: float
    fee_total: float
    tax_total: float
    cost_estimated: bool
    holding_days: int
    #: `fee_total` 중 **매수 때 낸 몫**. DB 열이 아니라 원화 환산에만 쓴다.
    #: 나머지(`fee_total - fee_buy`)가 매도 수수료다
    fee_buy: float = 0.0

    @property
    def fees_taxes_krw(self) -> float:
        """원화로 환산한 비용 합계 (docs/infra.md 25.96).

        **매수 수수료는 매수일 환율, 매도 수수료·세금은 매도일 환율**이다.
        전부 매도일 환율로 환산하던 때가 있었는데, 그러면 바로 옆에 적히는
        `realized_pnl_krw` 와 어긋난다 — 그쪽은 `proceeds * sell_fx - cost * buy_fx` 라
        매수 비용을 **매수일 환율**로 이미 제대로 세고 있다.

        환율이 1,300 → 1,400 으로 오른 구간이면 매수 수수료가 **7.7% 부풀어** 보인다.
        금액 자체는 작지만, **한 화면의 두 숫자가 서로 맞지 않는 것**이 문제다
        (CLAUDE.md: 근거에 쓴 수치는 어디서 왔는지 확인할 수 있어야 한다).
        """
        return self.fee_buy * self.buy_fx + (self.fee_total - self.fee_buy + self.tax_total) * self.sell_fx


@dataclass
class OpenLot:
    """아직 팔지 않은 매수분."""

    trade_id: int
    trade_date: str
    quantity: float
    price: float
    fx: float
    unit_fee: float  # 주당 매수 수수료
    horizon: str | None
    estimated: bool


@dataclass
class Position:
    stock_id: int
    quantity: float
    currency: str
    avg_price: float
    avg_fx: float
    cost: float
    cost_krw: float
    first_buy_date: str
    horizon: str | None
    cost_estimated: bool = False
    # 평가 (valuate 가 채운다)
    price_date: str | None = None
    close: float | None = None
    fx_date: str | None = None
    fx_now: float | None = None
    market_value: float | None = None
    market_value_krw: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_krw: float | None = None
    unrealized_price_pnl_krw: float | None = None
    unrealized_fx_pnl_krw: float | None = None
    weight_pct: float | None = None


def fee_and_tax(trade: Trade, rates: CostRates) -> tuple[float, float, bool]:
    """(수수료, 세금, 추정 여부). 입력값이 있으면 그대로, 없으면 설정 비율로 추정, 비율도 없으면 0 과 추정 표시."""
    gross = trade.price * trade.quantity
    estimated = False
    fee = trade.fee
    if fee is None:
        pct = rates.buy_fee_pct if trade.side == "buy" else rates.sell_fee_pct
        fee = gross * (pct or 0.0) / 100
        if trade.side == "sell" and trade.tax is not None:
            # 추정 수수료도 **입력 세금을 뺀 나머지를 넘지 않는다** (docs/infra.md 25.737, 감사) — 25.616 의 거울이다.
            # 웹은 빈 수수료를 0 으로 보고 세금 ≤ 체결금액만 막아, 세금을 체결금액만큼 적은 매도의 매도대금이 음수가
            # 됐다
            fee = min(fee, max(0.0, gross - trade.tax))
        estimated = True
    tax = trade.tax
    if tax is None:
        if trade.side == "sell" and rates.has_sell_tax:
            # 추정 세금은 **수수료를 뺀 나머지를 넘지 않는다** (docs/infra.md 25.616, 감사). 웹은 빈 세금을 0 으로 보고
            # 수수료 ≤ 체결금액만 막아, 수수료를 거의 다 적은 매도에 추정 세금이 붙으면 매도대금이 음수가 됐다
            세율 = rates.sell_tax_pct
            if rates.sell_tax_schedule and trade.trade_date < rates.sell_tax_schedule[-1][0]:
                for 시행일, pct in rates.sell_tax_schedule:
                    if 시행일 <= trade.trade_date:
                        세율 = pct
            tax = min(gross * (세율 or 0.0) / 100, max(0.0, gross - fee))
            estimated = True
        else:
            tax = 0.0
    return fee, tax, estimated


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def same_day_order(t: Trade) -> tuple[str, int, int]:
    """매매 처리 순서: 날짜 → 같은 날이면 매수 먼저 → 입력 순서(id). (docs/infra.md 25.397)

    같은 날 매수를 고치려고 지우고 다시 넣으면(수정 경로가 그것뿐이다) 새 매수가 매도보다 큰 id 를
    받는다. id 순이면 매도가 먼저 와 "보유보다 많이 판" 것이 되고 **판 주식이 보유로 되살아났다.**
    웹의 보유 수량 검사(`lib/portfolio.ts` HELD_QUANTITY)도 같은 날 순서를 보지 않는다 — 기준을 맞춘다.
    같은 날 팔고 다시 사는 경우는 FIFO 라 먼저 산 묶음과 짝지어지므로 결과가 같다.
    """
    return (t.trade_date, 0 if t.side == "buy" else 1, t.id)


def match_fifo(trades: list[Trade], rates: CostRates) -> tuple[list[Lot], list[OpenLot], list[str]]:
    """한 종목의 매매를 먼저 산 것부터 판다(FIFO). 같은 날이면 **매수 먼저**, 그다음 입력 순서(id).

    보유보다 많이 판 매도는 보유분까지만 짝짓고 경고를 남긴다. 원본을 고치지 않는다.
    """
    lots: list[Lot] = []
    open_lots: list[OpenLot] = []
    warnings: list[str] = []

    for trade in sorted(trades, key=same_day_order):
        fee, tax, estimated = fee_and_tax(trade, rates)
        if trade.side == "buy":
            open_lots.append(
                OpenLot(
                    trade_id=trade.id,
                    trade_date=trade.trade_date,
                    quantity=trade.quantity,
                    price=trade.price,
                    fx=trade.fx_rate,
                    unit_fee=fee / trade.quantity,
                    horizon=trade.horizon,
                    estimated=estimated,
                )
            )
            continue

        remaining = trade.quantity
        unit_fee = fee / trade.quantity
        unit_tax = tax / trade.quantity
        while remaining > QTY_EPS and open_lots:
            head = open_lots[0]
            q = min(head.quantity, remaining)
            cost = q * (head.price + head.unit_fee)
            proceeds = q * (trade.price - unit_fee - unit_tax)
            pnl = proceeds - cost
            total_krw = proceeds * trade.fx_rate - cost * head.fx
            price_krw = pnl * head.fx
            fx_krw = proceeds * (trade.fx_rate - head.fx)
            lots.append(
                Lot(
                    buy_trade_id=head.trade_id,
                    sell_trade_id=trade.id,
                    stock_id=trade.stock_id,
                    quantity=q,
                    buy_price=head.price,
                    sell_price=trade.price,
                    currency=trade.currency,
                    buy_fx=head.fx,
                    sell_fx=trade.fx_rate,
                    cost=cost,
                    proceeds=proceeds,
                    realized_pnl=pnl,
                    realized_pnl_krw=total_krw,
                    price_pnl_krw=price_krw,
                    fx_pnl_krw=fx_krw,
                    fee_total=q * (head.unit_fee + unit_fee),
                    tax_total=q * unit_tax,
                    fee_buy=q * head.unit_fee,
                    cost_estimated=estimated or head.estimated,
                    holding_days=_days(head.trade_date, trade.trade_date),
                )
            )
            head.quantity -= q
            remaining -= q
            if head.quantity <= QTY_EPS:
                open_lots.pop(0)
        if remaining > QTY_EPS:
            warnings.append(
                f"매도 {trade.id}({trade.trade_date}): 보유보다 {remaining:g}주 많이 팔았습니다."
                " 보유분까지만 계산했습니다"
            )
    return lots, open_lots, warnings


def dominant_horizon(open_lots: list[OpenLot]) -> str | None:
    """남은 로트 가운데 **원가가 가장 큰 투자 기간** (docs/sell_flags.md 3장, docs/infra.md 25.406).

    예전에는 가장 오래된 로트의 기간(`open_lots[0]`)이었다. 장기로 10주, 뒤에 단기로 100주를 사면 포지션
    전체가 장기가 되어 손절 −7% 가 아니라 −25%, 목표 +10% 가 아니라 +50% 로 판정했고 기간초과도 뜨지 않았다.
    목표·손절은 포지션 전체 기준이므로(로트별 판정 없음) 돈이 가장 많이 걸린 기간을 따른다. 같으면 먼저 산 쪽.
    """
    합: dict[str | None, float] = {}
    for lot in open_lots:
        합[lot.horizon] = 합.get(lot.horizon, 0.0) + lot.quantity * (lot.price + lot.unit_fee)
    if not 합:
        return None
    최대 = max(합.values())
    return next(lot.horizon for lot in open_lots if 합[lot.horizon] == 최대)


def position_from(stock_id: int, currency: str, open_lots: list[OpenLot]) -> Position | None:
    quantity = sum(lot.quantity for lot in open_lots)
    if quantity <= QTY_EPS:
        return None
    cost = sum(lot.quantity * (lot.price + lot.unit_fee) for lot in open_lots)
    cost_krw = sum(lot.quantity * (lot.price + lot.unit_fee) * lot.fx for lot in open_lots)
    return Position(
        stock_id=stock_id,
        quantity=quantity,
        currency=currency,
        avg_price=cost / quantity,
        # 원가 가중 평균 환율. 평가 때 주가 손익·환차손익을 나누는 기준이다
        avg_fx=cost_krw / cost if cost else 1.0,
        cost=cost,
        cost_krw=cost_krw,
        first_buy_date=min(lot.trade_date for lot in open_lots),
        horizon=dominant_horizon(open_lots),
        cost_estimated=any(lot.estimated for lot in open_lots),
    )


def valuate(
    position: Position, close: float | None, price_date: str | None, fx_now: float | None, fx_date: str | None
) -> Position:
    """종가·환율로 평가한다. 없으면 평가 칸을 비운다(없는 숫자를 만들지 않는다)."""
    position.close, position.price_date = close, price_date
    position.fx_now, position.fx_date = fx_now, fx_date
    if close is None or fx_now is None:
        return position
    mv = position.quantity * close
    position.market_value = mv
    position.market_value_krw = mv * fx_now
    position.unrealized_pnl = mv - position.cost
    position.unrealized_pnl_krw = mv * fx_now - position.cost_krw
    position.unrealized_price_pnl_krw = (mv - position.cost) * position.avg_fx
    position.unrealized_fx_pnl_krw = mv * (fx_now - position.avg_fx)
    return position


#: 평가에 쓴 **환율**이 이보다 묵었으면 말한다 (docs/infra.md 25.136).
#: `batch/services/fx.MAX_STALE_DAYS` 와 **같은 값이어야 한다** — 그쪽은 매매를 입력할 때
#: "이보다 오래된 환율은 자동으로 채우지 않는다" 를 이미 정해 두었다. 같은 판단을
#: 평가에서만 다르게 할 이유가 없다 (25.0 「한 규칙이 두 곳에 있다」).
def _환율_묵음_일수() -> int:
    from batch.services.fx import MAX_STALE_DAYS

    return MAX_STALE_DAYS


#: 평가에 쓴 **종가**가 이보다 묵었으면 말한다.
#: `metrics.MAX_SESSION_GAP_DAYS`(11일, `exchange_calendars` 실측 — 거래일 사이 최장 간격)와
#: 같다. **정상 휴장은 절대 안 걸린다.** 걸리는 것은 거래정지나 수집 구멍뿐이다.
def _종가_묵음_일수() -> int:
    from batch.services.metrics import MAX_SESSION_GAP_DAYS

    return MAX_SESSION_GAP_DAYS


def _며칠_전(day: str | None, today: str) -> int | None:
    if not day or day == "-":
        return None
    try:
        return (date.fromisoformat(today) - date.fromisoformat(day)).days
    except ValueError:
        return None


def stale_notes(positions: list[Position], today: str) -> list[str]:
    """**평가에 쓴 값이 얼마나 묵었나** (docs/infra.md 25.136).

    평가는 늘 "가장 최근 종가 × 가장 최근 환율" 이다. 그런데 그 '최근' 이 **언제**인지는
    아무도 따지지 않았다. 수집이 멈추면 2주 전 가격과 환율로 낸 금액이 **오늘 값처럼**
    화면에 뜬다 — 사용자는 자산이 그대로인 줄 안다.

    `price_date` · `fx_date` 는 처음부터 저장하고 화면에도 찍고 있었다. **적어 놓고
    그 날짜를 보고 판단하는 곳이 없었다**(25.0 「절반만 지킨 규칙」).

    **값을 지우지 않는다.** 평가액을 비우면 화면이 통째로 빈다. 대신 말한다.
    """
    종가한도, 환율한도 = _종가_묵음_일수(), _환율_묵음_일수()
    묵은종가: list[str] = []
    묵은환율: dict[str, int] = {}
    for p in positions:
        나이 = _며칠_전(p.price_date, today)
        if 나이 is not None and 나이 > 종가한도:
            묵은종가.append(f"{p.price_date}({나이}일 전)")
        환율나이 = _며칠_전(p.fx_date, today)
        if p.currency != "KRW" and 환율나이 is not None and 환율나이 > 환율한도:
            묵은환율[p.currency] = max(묵은환율.get(p.currency, 0), 환율나이)

    out: list[str] = []
    if 묵은종가:
        보기 = ", ".join(sorted(set(묵은종가))[:3])
        out.append(
            f"종가가 {종가한도}일 넘게 묵은 종목 {len(묵은종가)}개로 평가했습니다 ({보기}) —"
            " 정상 휴장으로는 이만큼 벌어지지 않습니다. 시세 수집을 확인하세요"
        )
    for 통화, 나이 in sorted(묵은환율.items()):
        out.append(
            f"{통화} 환율이 {나이}일 전 값입니다 ({환율한도}일 넘음). 원화 평가액과 환차손익이"
            " 그만큼 어긋납니다 — 환율 수집을 확인하세요 (docs/infra.md 25.83)"
        )
    return out


def set_weights(positions: list[Position]) -> None:
    total = sum(p.market_value_krw or 0 for p in positions)
    for p in positions:
        p.weight_pct = (p.market_value_krw or 0) / total * 100 if total > 0 and p.market_value_krw is not None else None


#: 업종을 모르는 보유를 모아 두는 칸. 업종이 아니므로 상한 판정에서 뺀다
NO_SECTOR = "업종 없음"


def allocation(
    positions: list[Position],
    sector_of: dict[int, str | None],
    max_sector_pct: float | None = None,
    total_investable_krw: float | None = None,
) -> dict:
    """업종·종목·통화별 평가액 비중(%). 업종을 모르면 '업종 없음'.

    **업종 상한을 넘은 업종도 여기서 고른다** (docs/infra.md 25.217). 화면이 "30% 상한을 넘는 업종이 있습니다" 를
    30 을 글자로 박아 판정하고 있었다 — 설정(`max_weight_per_sector`)을 바꾸면 경고가 실제 상한과 어긋나고,
    '업종 없음' 묶음도 업종처럼 걸렸다. 웹은 계산하지 않는다(CLAUDE.md) — 배치가 고르고 화면은 읽는다.

    **상한의 분모는 총 투자가능금액이다** (docs/infra.md 25.238). CLAUDE.md "권장 금액 = 총 투자가능금액 × 종목
    비중" 의 비중과 같은 잣대이고, 리포트 2부(`report_picks.compose`)가 섹터 여력을 그것으로 잰다. 예전에는
    **보유 평가액 합**으로 나눠서, 투자금 절반만 들어간 사람에게 화면은 "상한을 넘었다" 하고 리포트는 같은 업종에
    더 사라고 했다. 보유가 투자가능금액보다 커졌으면(올랐거나 설정이 낡았으면) 보유 합으로 나눈다 — 낡은 설정값으로
    나누면 100% 를 넘는 비중이 나온다. 설정이 없으면(0) 보유 합이다.
    `by_sector` 는 그대로 **보유 구성**(보유 합 대비)이다.
    """
    total = sum(p.market_value_krw or 0 for p in positions)
    상한_분모 = max(total, total_investable_krw or 0.0)
    by_sector: dict[str, float] = defaultdict(float)
    by_currency: dict[str, float] = defaultdict(float)
    for p in positions:
        value = p.market_value_krw or 0
        by_sector[sector_of.get(p.stock_id) or NO_SECTOR] += value
        by_currency[p.currency] += value

    def pct(d: dict[str, float]) -> dict[str, float]:
        return {k: round(v / total * 100, 2) for k, v in sorted(d.items(), key=lambda kv: -kv[1])} if total else {}

    sector_pct = pct(by_sector)
    return {
        "total_krw": total,
        "by_sector": sector_pct,
        "max_sector_pct": max_sector_pct,
        "cap_base_krw": 상한_분모,
        "sectors_over_cap": []
        if max_sector_pct is None or not 상한_분모
        else [
            k for k, v in by_sector.items()
            if k != NO_SECTOR and round(v / 상한_분모 * 100, 2) > max_sector_pct
        ],
        "by_currency": pct(by_currency),
        "by_stock": {str(p.stock_id): round(p.weight_pct, 2) for p in positions if p.weight_pct is not None},
    }


#: ETF 투시에서 보이는 종목 수 (docs/portfolio.md 8장)
LOOKTHROUGH_TOP = 15
#: ETF 안에서 우리 종목에 잇지 못한 비중의 업종 이름
LOOKTHROUGH_UNKNOWN = "ETF 속 미확인"


def lookthrough(
    positions: list[Position],
    is_etf: set[int],
    weights: dict[int, dict[int, float]],
    info: dict[int, tuple[str, str | None]],
) -> dict | None:
    """계좌 전체 노출 — 보유 ETF 를 구성종목으로 펼친 종목·업종 비중 (docs/portfolio.md 8장, 25.1002).

    weights = ETF stock_id → {구성 stock_id: ETF 안 비중 %}. info = stock_id → (이름, 업종).
    ETF 를 하나도 들고 있지 않으면 None(펼칠 것이 없다). 구성을 모르는 ETF 는 통째로, 아는 ETF 의 나머지 비중은
    `LOOKTHROUGH_UNKNOWN` 으로 센다 — 모르는 것을 아는 업종에 나눠 넣지 않는다."""
    total = sum(p.market_value_krw or 0 for p in positions)
    etf_value = sum(p.market_value_krw or 0 for p in positions if p.stock_id in is_etf)
    if not total or not etf_value:
        return None
    direct: dict[int, float] = defaultdict(float)
    via: dict[int, float] = defaultdict(float)
    unknown = 0.0
    모름: list[str] = []
    for p in positions:
        v = p.market_value_krw or 0
        if v <= 0:
            continue
        if p.stock_id not in is_etf:
            direct[p.stock_id] += v
            continue
        w = weights.get(p.stock_id)
        if not w:
            unknown += v
            모름.append(info.get(p.stock_id, (str(p.stock_id), None))[0])
            continue
        합 = sum(w.values())
        scale = 100 / 합 if 합 > 100 else 1.0  # 합이 100 을 넘으면(파생 상계 전 기재 등) 100 으로 줄인다
        for sid, pct in w.items():
            via[sid] += v * pct * scale / 100
        unknown += v * (100 - min(합, 100.0)) / 100

    def r(x: float) -> float:
        return round(x / total * 100, 2)

    ids = set(direct) | set(via)
    rows = sorted(ids, key=lambda s: -(direct.get(s, 0) + via.get(s, 0)))
    by_sector: dict[str, float] = defaultdict(float)
    for s in ids:
        by_sector[info.get(s, ("", None))[1] or NO_SECTOR] += direct.get(s, 0) + via.get(s, 0)
    if unknown:
        by_sector[LOOKTHROUGH_UNKNOWN] += unknown
    return {
        "etf_pct": r(etf_value),
        "covered_pct": round((etf_value - unknown) / etf_value * 100, 1),
        "unknown_etfs": sorted(모름),
        "by_stock": [
            {"stock_id": s, "name": info.get(s, (str(s), None))[0], "direct_pct": r(direct.get(s, 0)),
             "via_pct": r(via.get(s, 0)), "total_pct": r(direct.get(s, 0) + via.get(s, 0))}
            for s in rows[:LOOKTHROUGH_TOP]
        ],
        "by_sector": {k: r(v) for k, v in sorted(by_sector.items(), key=lambda kv: -kv[1])},
    }  # fmt: skip


# ----------------------------------------------------------------------
# 날짜별 평가액과 시간가중 수익률
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class DividendReceipt:
    stock_id: int
    pay_date: str
    net_amount: float
    fx_rate: float


@dataclass
class ValueRow:
    date: str
    value_krw: float
    net_flow_krw: float
    dividends_krw: float
    twr_index: float


def invested_rows(rows: list[ValueRow]) -> list[ValueRow]:
    """**아무것도 들고 있지 않은 날**을 뺀 평가 계열 (docs/infra.md 25.736, 감사).

    전날 평가액도 그날 평가액도 0 이고 오간 돈·배당도 없으면 그날은 현금 상태다.
    지수는 1.0 그대로라 수익률 0 인 행이 되는데, 전부 팔고 쉬는 동안(또는 다 판 종목의 종가가
    오늘까지 이어지는 동안) 그런 행이 쌓여 변동성을 낮추고 샤프를 부풀리고
    200 거래일 표본 하한을 현금 일수로 채웠다. 전량 매도한 날(전날 평가액 > 0)은 그날 수익률을 담으므로 남긴다.
    지수는 누적이라 빼도 수익이 사라지지 않는다(주말 행을 뺀 25.332 와 같은 이치).
    """
    out: list[ValueRow] = []
    prev = 0.0
    for r in rows:
        if not (prev == 0 and r.value_krw == 0 and r.net_flow_krw == 0 and r.dividends_krw == 0):
            out.append(r)
        prev = r.value_krw
    return out


def fresher_price(series: dict[str, float], trades: list[Trade]) -> tuple[float | None, str | None]:
    """평가에 쓸 가격과 그 날짜. 마지막 종가가 **마지막 체결일보다 앞서면** 체결가를 쓴다 (docs/infra.md 25.398).

    종가는 포트폴리오 첫 매매일부터 불러오므로, 오늘 처음 산 종목도 "어제 종가" 가 가장 최근 값으로 잡혔다.
    장중에 1만원에 사고 바로 재계산하면 전일 종가 9,500원으로 평가되어 손익 −5% 가 떴다. 체결가가 더 새 값이다.
    종가가 **아예 없으면** 비운다 — 평가 칸을 비우고 경고하는 규칙(docs/portfolio.md 5절)은 그대로다.
    """
    last_day = max(series, default=None)
    if last_day is None:
        return None, None
    last_trade = max(trades, key=same_day_order, default=None)
    if last_trade is not None and last_day < last_trade.trade_date:
        return last_trade.price, last_trade.trade_date
    return series[last_day], last_day


def _carry(series: dict[str, float], dates_sorted: list[str], day: str, cache: dict) -> float | None:
    """day 이전(포함) 가장 최근 값. 휴장일·가격 끊김은 마지막 값으로 들고 간다."""
    key = id(series)
    pos = cache.get(key, 0)
    last = cache.get((key, "v"))
    while pos < len(dates_sorted) and dates_sorted[pos] <= day:
        last = series[dates_sorted[pos]]
        pos += 1
    cache[key], cache[(key, "v")] = pos, last
    return last


def _carry_date(dates_sorted: list[str], cache: dict, series: dict[str, float]) -> str:
    """바로 앞 `_carry` 가 고른 값의 날짜. 없으면 빈 문자열."""
    pos = cache.get(id(series), 0)
    return dates_sorted[pos - 1] if pos > 0 else ""


#: 지급일 앞 이 기간의 최대 보유를 "배당을 번 몫" 으로 본다 — 국내 12월 결산 배당락~지급이 약 4개월 (25.828)
DIVIDEND_EARN_WINDOW_DAYS = 183


def _몫이_빠진_날(d: DividendReceipt, trades: list[Trade]) -> str | None:
    """지급일 보유가 앞 `DIVIDEND_EARN_WINDOW_DAYS` 일 최대 보유의 절반 아래면 절반 아래로 떨어진 마지막 날.

    아니면 None.
    """
    창_시작 = (date.fromisoformat(d.pay_date) - timedelta(days=DIVIDEND_EARN_WINDOW_DAYS)).isoformat()
    보유 = 0.0
    출발 = 0.0  # 창이 열릴 때의 보유
    흐름: list[tuple[str, float]] = []  # 창 안의 (날, 그 매매 뒤 보유)
    # **지급일 당일의 매수는 그 배당을 벌지 못했다** (docs/infra.md 25.932, 감사 재현). 배당은 배당락일 전에 든 몫에
    # 나오고
    # 배당락일은 지급일보다 앞이다. 예전에는 `<=` 라 처음 산 날과 지급일이 같으면(기록 전부터 든 주식의 배당) 25.616 이
    # 막으려던 부풀림이 경고 없이 났다(1.0 → 1.0381). 당일 매도는 그대로 센다 — 배당을 번 몫이 그날 나간 것이다.
    # `배당_붙일_날` 도 같은 규칙이다
    for t in sorted((t for t in trades if t.stock_id == d.stock_id
                     and (t.trade_date < d.pay_date or (t.trade_date == d.pay_date and t.side != "buy"))),
                    key=same_day_order):  # fmt: skip
        보유 = 보유 + t.quantity if t.side == "buy" else max(0.0, 보유 - t.quantity)
        if t.trade_date < 창_시작:
            출발 = 보유
        elif 흐름 and 흐름[-1][0] == t.trade_date:
            # **그날의 마지막 보유만** — 하루 안에 사고판 왕복의 장중 정점을 최대로 세면 배당이 그 왕복한 날에 붙어
            # 묽어졌다
            # (25.831, 교차검증)
            흐름[-1] = (t.trade_date, 보유)
        else:
            흐름.append((t.trade_date, 보유))
    문턱 = max([출발] + [q for _, q in 흐름]) * 0.5
    if 보유 >= 문턱:
        return None
    떨어진_날: str | None = None
    직전 = 출발
    for 날, q in 흐름:
        if 직전 >= 문턱 > q:
            떨어진_날 = 날
        직전 = q
    return 떨어진_날


def 배당_붙일_날(d: DividendReceipt, trades: list[Trade]) -> str | None:
    """배당을 시간가중 수익률의 **어느 날에** 붙이나 (docs/infra.md 25.582, 교차검증).

    지급일에 그 종목을 들고 있으면 지급일. **다 판 뒤에 들어온 배당**(국내 12월 결산 배당은 이듬해 4월 무렵)이면
    **그 종목을 마지막으로 다 판 날**에 붙인다 — 그 배당을 번 돈이 그날 나갔기 때문이다. 지급일에 붙이면
    그날 남은 **다른 종목의 평가액**을 밑으로 나눠, 1원짜리 종목만 남은 날 50원 배당이 지수를 51배로 만들었다.
    25.578 은 보유가 통째로 0 인 날만 보고 경고했다.
    **지급일까지 한 번도 들고 있지 않았던 종목이면 None** — 지수에 넣지 않는다 (docs/infra.md 25.616, 감사). 예전에는
    지급일 그대로 붙여, 기록 전부터 가지고 있던 주식의 배당(흔한 입력)이 그날 **다른 종목의 평가액**으로 나뉘어 지수를
    부풀렸다(1,000원 보유에 500원 배당 → 1.0 → 1.5, 경고 없음). 배당 합계에는 그대로 들어간다.
    """
    보유 = 0.0
    다_판_날: str | None = None
    # 지급일 당일 매수는 빼고 센다 — `_몫이_빠진_날` 과 같은 규칙 (25.932)
    for t in sorted((t for t in trades if t.stock_id == d.stock_id
                     and (t.trade_date < d.pay_date or (t.trade_date == d.pay_date and t.side != "buy"))),
                    key=same_day_order):  # fmt: skip
        if t.side == "buy":
            보유 += t.quantity
        else:
            # **실제로 줄어든 매도만** 다 판 날이 된다 (25.621, 교차검증) — 이미 0 인데 들어온 짝 없는 매도(과다 매도
            # 기록)가
            # 다 판 날을 뒤로 밀어, 배당이 그날(보유 0 이 아닌 다른 종목만 남은 날)에 붙었다
            if 보유 > QTY_EPS:
                보유 -= min(t.quantity, 보유)
                if 보유 <= QTY_EPS:
                    다_판_날 = t.trade_date
    if 보유 > QTY_EPS:
        # **지급일에 남은 몫이 배당을 번 몫보다 훨씬 작으면** 그 몫이 빠진 날에 붙인다 (docs/infra.md 25.828, 감사).
        # 배당은 배당락일 수량만큼 나오는데 밑은 지급일 평가액이라, 배당락 뒤 999주를 팔았거나 다 팔고 1주를 다시 산
        # 경우 지급일에 붙이면 지수가 0.98 → 30.98 이 됐다(경고 없음). 배당락일을 모르므로 지급일 앞 6개월의 최대
        # 보유를 배당을 번 몫으로 보고, 지급일 보유가 그 절반 아래면 **절반 아래로 떨어진 마지막 날**에 붙인다
        return _몫이_빠진_날(d, trades) or d.pay_date
    # **산 적이 없으면 None** — 매수 기록 없이 매도만 있는 종목(매수를 지운 뒤)도 한 번도 들지 않은 것이다
    # (25.619, 교차검증: 예전에는 그 매도일에 붙여 25.616 이 막으려던 부풀림이 그대로 났다)
    산_적 = any(t.side == "buy" and t.stock_id == d.stock_id and t.trade_date < d.pay_date for t in trades)
    return 다_판_날 if 산_적 else None


#: 해외주식 양도소득 기본공제(연, 원). 소득세법의 해외주식 양도소득 기본공제 — 다른 해외 자산과 **함께** 쓰는 한도라
#: 이 앱 밖의 거래가 있으면 실제 공제는 더 작다
US_CGT_DEDUCTION_KRW = 2_500_000


def us_capital_gains_estimates(
    lots: list[Lot], sell_dates: dict[int, str], rate_pct: float | None
) -> list[dict[str, Any]]:
    """해외 종목 양도세 **연간 추정** (docs/portfolio.md 4장, docs/infra.md 25.617).

    해외주식 양도세는 거래마다가 아니라 **한 해 실현손익을 합산**(손실과 상계)해 기본공제 250만원을 뺀 뒤 매긴다.
    그래서 실현손익(`realized_pnl_krw`)에 섞지 않고 매도 연도별 추정 줄로 따로 낸다.
    세율은 설정(`taxes.us_capital_gains_pct`) — 없으면 과세표준까지만 내고 세액은 비운다.
    원화 환산은 이 앱의 체결 환율(매수·매도 입력 환율)을 쓴다 — 세법의 환산 기준(결제일 기준환율 등)과 다를 수 있다
    [확인필요]. 신고용이 아니라 **대략의 크기**를 보이는 참고값이다.
    """
    by_year: dict[str, float] = defaultdict(float)
    for lot in lots:
        if lot.currency == "KRW":
            continue
        day = sell_dates.get(lot.sell_trade_id)
        if day is None:
            continue
        by_year[day[:4]] += lot.realized_pnl_krw
    out: list[dict[str, Any]] = []
    for year in sorted(by_year):
        gains = by_year[year]
        taxable = max(0.0, gains - US_CGT_DEDUCTION_KRW)
        out.append({
            "year": int(year), "gains_krw": gains, "taxable_krw": taxable, "rate_pct": rate_pct,
            "deduction_krw": US_CGT_DEDUCTION_KRW,  # 화면이 공제액을 따로 적지 않게 (25.619)
            "tax_krw": None if rate_pct is None else taxable * rate_pct / 100,
        })  # fmt: skip
    return out


def value_series(
    trades: list[Trade],
    dividends: list[DividendReceipt],
    closes: dict[int, dict[str, float]],
    fx: dict[str, dict[str, float]],
    end: str,
    rates_by_currency: dict[str, CostRates],
) -> tuple[list[ValueRow], list[str]]:
    """첫 매매일부터 end 까지 날짜별 평가액과 시간가중 수익률 지수.

    하루 수익률 r = (그날 평가액 + 배당 + 나간 돈) / (전날 평가액 + 들어온 돈) − 1.
    들어온 돈 = max(0, 왕복 원가 − 전날 몫 대금, 순투입), 나간 돈 = 들어온 돈 − 순투입.
    순투입 = 매수 원가(수수료 포함) − 매도 실수령. 같은 날 판 돈으로 산 몫은 안에서 옮겨 간 것이라
    오가지 않고, **그날 산 몫을 그날 판** 왕복은 그 원가만큼 새 돈이 먼저 들어와야 한다
    (docs/infra.md 25.396·25.416·25.780·25.782).
    처음 식 (평가액 + 배당 − 순투입) / 전날 평가액 은 그날 산 종목의 "체결가 → 종가" 손익을
    전날 평가액만으로 나눠, 적게 들고 있다가 크게 산 날 지수가 −30%·음수까지 갔다.
    fx 는 {통화: {날짜: 1단위당 원}}. 원화는 비워 두면 1 로 본다.
    """
    warnings: list[str] = []
    if not trades:
        return [], warnings
    start = min(t.trade_date for t in trades)
    trade_days = sorted({t.trade_date for t in trades} | {d.pay_date for d in dividends})
    price_days = {d for series in closes.values() for d in series if start <= d <= end}
    days = sorted(price_days | {d for d in trade_days if d <= end})

    by_day: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_day[t.trade_date].append(t)
    div_by_day: dict[str, float] = defaultdict(float)
    보유한_적_없는_배당: list[str] = []
    for d in dividends:
        붙일_날 = 배당_붙일_날(d, trades)
        if 붙일_날 is None:
            보유한_적_없는_배당.append(d.pay_date)
            continue
        div_by_day[붙일_날] += d.net_amount * d.fx_rate

    close_dates = {sid: sorted(s) for sid, s in closes.items()}
    fx_dates = {cur: sorted(s) for cur, s in fx.items()}
    cache: dict = {}
    holdings: dict[int, float] = defaultdict(float)
    currency_of: dict[int, str] = {}
    rows: list[ValueRow] = []
    prev_value = 0.0
    index = 1.0
    missing_price: set[int] = set()
    #: 종목 → 가격·환율이 없던 날들. **마지막 날 하루만** 없으면 그날 산 종목의 종가가 아직 안 들어온 것뿐이라
    #: 경고하지 않는다 (25.923)
    빈날: dict[int, set[str]] = defaultdict(set)
    #: 담을 곳이 없는 날(보유 0·그날 매수 없음)의 배당 — 지수가 나눌 밑이 없어 빠진다 (docs/infra.md 25.578)
    못_담은_배당: list[str] = []
    #: 종목별 마지막 체결가·환율. 그날까지 종가·환율이 없으면 이것으로 평가한다 (docs/infra.md 25.328).
    #: 예전에는 빼 버려서, 첫 매수일에 종가가 아직 없으면(장중에 입력하고 바로 재계산) 그날 평가액이 0 →
    #: 지수가 0 이 되고, 그 뒤로는 `prev_value == 0` 이라 **영영 0** — 화면에 "시간가중 수익률 −100%" 가 떴다
    체결가: dict[int, tuple[float, float]] = {}
    체결일: dict[int, str] = {}

    for day in days:
        flow = 0.0
        # **판 몫을 둘로 가른다** (docs/infra.md 25.780·25.782). 매도는 FIFO 에 같은 날은 매수 먼저라(`same_day_order`)
        # 전날부터 들고 있던 몫(P)부터 줄이고, 넘친 만큼이 **그날 산 몫을 그날 판** 것(S, 그 매수 원가 C)이다.
        # 아침에 밖에서 들어온 돈 = max(0, C − P, B − P − S) — 그날 왕복한 몫을 사려면 C 가 먼저 필요하고(전날 몫
        # 대금으로
        # 댈 수 있다), 하루 전체로는 순투입 B − P − S 만큼 모자란다. 저녁에 나간 돈 = 들어옴 − 순투입.
        # 25.780 이전: 전부 상계해 1,000원 보유 + 당일 1천만원 매수·1,100만원 매도가 지수 1001배.
        # 25.780: S 를 늘 저녁에 나간 돈으로 봐, 그 돈으로 같은 날 다른 종목을 사면 새 돈을 두 번 셌다(교차검증)
        매수원가 = 0.0
        전날몫_대금 = 0.0
        당일몫_대금 = 0.0
        # 그날 산 묶음을 건드리지 않은 매도 줄의 전날 몫 대금 — **이것만** 그날 왕복 매수의 자금이 될 수 있다 (25.787,
        # 교차검증).
        # 한 줄이 전날 몫과 그날 묶음에 걸쳐 팔렸으면 그 줄은 매수 뒤에 체결된 것이라 그 대금으로 매수를 댈 수 없다
        자유_전날몫_대금 = 0.0
        왕복_원가 = 0.0
        전날_남은: dict[int, float] = {}
        # 종목 → [[남은 수량, 묶음 원가(원), 세었나]] FIFO. **한 주라도 팔린 묶음은 원가 전체를 왕복 원가로 센다**
        # (25.784,
        # 교차검증) — 한 줄로 입력된 매수는 매도보다 먼저 통째로 산 것이다. 판 몫만 세면 그 매수가 "먼저 산 몫" 과 "판
        # 돈으로
        # 나중에 산 몫" 으로 쪼개져, 첫날 100주를 사서 50주를 +10% 에 팔면 지수가 1.1 이 아니라 1.2 였다
        오늘_산_묶음: dict[int, list[list[float]]] = defaultdict(list)
        for t in sorted(by_day.get(day, []), key=same_day_order):
            fee, tax, _ = fee_and_tax(t, rates_by_currency.get(t.currency, CostRates()))
            currency_of[t.stock_id] = t.currency
            체결가[t.stock_id] = (t.price, t.fx_rate)
            체결일[t.stock_id] = day
            if t.stock_id not in 전날_남은:
                전날_남은[t.stock_id] = holdings[t.stock_id]
            if t.side == "buy":
                holdings[t.stock_id] += t.quantity
                원가 = (t.price * t.quantity + fee) * t.fx_rate
                flow += 원가
                매수원가 += 원가
                if t.quantity > 0:
                    오늘_산_묶음[t.stock_id].append([t.quantity, 원가, 0.0])
            else:
                sold = min(t.quantity, holdings[t.stock_id])
                holdings[t.stock_id] -= sold
                대금 = (t.price * sold - (fee + tax) * (sold / t.quantity)) * t.fx_rate
                flow -= 대금
                전날몫 = min(sold, max(0.0, 전날_남은[t.stock_id]))
                전날_남은[t.stock_id] -= 전날몫
                if sold > 0:
                    전날몫_대금 += 대금 * (전날몫 / sold)
                    당일몫_대금 += 대금 * ((sold - 전날몫) / sold)
                남길 = sold - 전날몫
                for 묶음 in 오늘_산_묶음[t.stock_id]:
                    if 남길 <= QTY_EPS:
                        break
                    if 묶음[0] <= QTY_EPS:
                        continue
                    뗌 = min(남길, 묶음[0])
                    if not 묶음[2]:
                        왕복_원가 += 묶음[1]
                        묶음[2] = 1.0
                    묶음[0] -= 뗌
                    남길 -= 뗌
                if sold > 0 and sold - 전날몫 <= QTY_EPS:
                    # 이 줄은 전날 몫만 팔았다 — 그날 묶음을 건드리지 않았다
                    자유_전날몫_대금 += 대금

        value = 0.0
        for sid, qty in holdings.items():
            if qty <= QTY_EPS:
                continue
            close = _carry(closes.get(sid, {}), close_dates.get(sid, []), day, cache)
            종가일 = _carry_date(close_dates.get(sid, []), cache, closes.get(sid, {}))
            if close is not None and 종가일 < 체결일[sid]:
                close = 체결가[sid][0]  # 종가가 체결보다 묵었다 — 체결가가 더 새 값이다 (25.398)
            currency = currency_of.get(sid, "KRW")
            rate = 1.0 if currency == "KRW" else _carry(fx.get(currency, {}), fx_dates.get(currency, []), day, cache)
            if close is None or rate is None:
                빈날[sid].add(str(day))
                if sid not in 체결가:
                    missing_price.add(sid)
                    continue
                # 아직 종가(또는 환율)가 없다 — 마지막 체결가로 평가한다. 빼면 지수가 0 에 갇힌다
                missing_price.add(sid)
                가, 율 = 체결가[sid]
                close = close if close is not None else 가
                rate = rate if rate is not None else 율
            value += qty * close * rate

        dividends_krw = div_by_day.get(day, 0.0)
        # **안에서 옮겨 간 돈은 오간 것이 아니다** (docs/infra.md 25.416, 25.396 을 교차검증이 반박). 그날 판 돈으로
        # 산 몫은
        # 포트폴리오 안에서 옮겨 간 것이다. 25.396 은 매수 원가 전부를 아침에 넣고 매도 대금 전부를 저녁에 빼서, 전량
        # 교체한 날 실제 +10% 가 +4.76% 로 깎였다. 왕복한 몫은 위 주석(25.780·25.782)
        들어옴 = max(0.0, 왕복_원가 - 자유_전날몫_대금, 매수원가 - 전날몫_대금 - 당일몫_대금)  # 아침에 들어온 새 돈
        나감 = 들어옴 - flow  # 저녁에 나간 돈 (flow = 매수원가 − 전날몫_대금 − 당일몫_대금, 부동소수 오차만 남는다)
        나감 = max(0.0, 나감)
        base = prev_value + 들어옴
        if base > 0:
            # 분자가 음수가 될 수 없어 지수가 음수로 가지 않는다. 처음 들어간 날은 prev_value 0 → 넣은 돈 대비 종가
            index *= (value + dividends_krw + 나감) / base
        elif dividends_krw > 0:
            # 전량 매도 뒤 들어온 배당(국내 12월 결산 배당은 이듬해 4월 무렵 들어온다)·첫 매수 전 날짜의 배당.
            # 예전에는 말없이 버려 수익률이 낮게 나왔다
            못_담은_배당.append(day)
        rows.append(ValueRow(day, value, flow, dividends_krw, index))
        prev_value = value

    # **마지막 날 하루만 비면 경고하지 않는다** (docs/infra.md 25.923). 장중·장 마감 전에 매매를 넣으면 그날 종가가
    # 다음 일일 배치에야
    # 들어와 "가격·환율이 없는 날이 있는 종목 N개" 가 매번 떴고, 그 경고 하나로 포트폴리오·일일 배치가 partial 이
    # 됐다(10-02 하루 7번).
    # 그날은 체결가로 평가하는 것이 맞고 다음 날 종가가 오면 저절로 풀린다. 그 밖의 날이 비면 그대로 경고한다
    마지막날 = str(rows[-1].date) if rows else None
    경고할 = {sid for sid in missing_price if 빈날.get(sid, set()) - ({마지막날} if 마지막날 else set())}
    if 경고할:
        warnings.append(
            f"가격·환율이 없는 날이 있는 종목 {len(경고할)}개는 그날 마지막 체결가(환율)로 평가했습니다"
        )
    if 보유한_적_없는_배당:
        warnings.append(
            f"지급일까지 매수 기록이 없는 종목의 배당 {len(보유한_적_없는_배당)}건({min(보유한_적_없는_배당)} 등)은"
            " 시간가중 수익률에 넣지 않았습니다 — 기록 전부터 가진 주식이면 그 매수를 입력하세요."
            " 배당 합계에는 들어 있습니다"
        )
    if 못_담은_배당:
        warnings.append(
            f"보유 종목이 하나도 없던 날 받은 배당 {len(못_담은_배당)}일치({못_담은_배당[0]} 등)는 시간가중 수익률에"
            " 넣지 못했습니다. 배당 합계에는 들어 있습니다"
        )
    return rows, warnings


# ----------------------------------------------------------------------
# 실적 발표 D-day (법정 제출 기한으로 추정)
# ----------------------------------------------------------------------

# 분기·반기 보고서와 사업보고서의 법정 제출 기한(결산일 뒤 일수). 한국 증권신고서 규정은 45·90일,
# 미국 SEC 는 회사 규모에 따라 10-Q 40~45일·10-K 60~90일. 가장 늦은 쪽을 쓴다(늦어도 이날까지).
QUARTER_DEADLINE_DAYS = 45
ANNUAL_DEADLINE_DAYS = 90


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    # 말일 결산을 유지한다(3·6·9·12월 말)
    next_first = date(year + (month // 12), month % 12 + 1, 1)
    last_day = (next_first - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day) if d.day < 28 else last_day)


def upcoming_deadlines(fiscal_year_end: str, today: date, count: int = 4) -> list[tuple[str, str, int]]:
    """다음 count 개의 법정 공시 기한. [(종류, 기한, D-day)]. next_filing_deadline 을 이어 부른다.

    실적 발표 **예정일** 이 아니라 "늦어도 이날까지" 다 (docs/portfolio.md 5장). 캘린더에 넣을 때
    추정임을 반드시 함께 적는다.
    """
    out: list[tuple[str, str, int]] = []
    cursor = today
    for _ in range(count):
        kind, deadline, d_day = next_filing_deadline(fiscal_year_end, cursor)
        out.append((kind, deadline, (date.fromisoformat(deadline) - today).days))
        cursor = date.fromisoformat(deadline) + timedelta(days=1)
    return out


def next_filing_deadline(fiscal_year_end: str, today: date) -> tuple[str, str, int]:
    """다음 실적 공시의 법정 기한. (종류, 기한 날짜, D-day).

    fiscal_year_end 는 최근 결산일(예: 2025-12-31). 거기서 3개월씩 분기말을 만든다.
    기한이 오늘 이후인 가장 가까운 분기말을 고른다. 4번째 분기말(= 다음 결산일)은 연간 기한이다.
    """
    end = date.fromisoformat(fiscal_year_end)
    # step 0 = 방금 끝난 회계연도의 연간 보고서(아직 안 냈을 수 있다)
    for step in range(0, 9):
        period_end = _add_months(end, 3 * step) if step else end
        annual = step % 4 == 0
        deadline = period_end + timedelta(days=ANNUAL_DEADLINE_DAYS if annual else QUARTER_DEADLINE_DAYS)
        if deadline >= today:
            kind = "연간" if annual else "분기"
            return kind, deadline.isoformat(), (deadline - today).days
    raise ValueError("결산일이 너무 오래됐습니다")
