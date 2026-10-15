"""모의 투자 계좌의 순수 계산 (docs/paper.md, docs/infra.md 25.1444).

DB 를 읽지 않는다 — 장부(입금·체결)와 그날 시세를 받아 체결 판정·보유·현금·검산·시간가중 수익·그림자 기준을 낸다.
배치(`jobs/paper.py`)가 읽고 적는다. 같은 입력이면 같은 결과다(복기의 전제, docs/paper.md 0장).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 계좌 규칙별로 **따라 파는** 매도 플래그 사유 (docs/paper.md 1장). None 은 모든 플래그
SELL_RULES: dict[str, frozenset[str] | None] = {
    "all": None,
    "core": frozenset({"손절", "목표도달", "기간초과"}),
}

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


@dataclass(frozen=True)
class Bar:
    date: str
    open: float | None
    close: float
    volume: float | None = None


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


def _원(v: float) -> float:
    """원 단위 반올림 — 장부의 원화 값은 정수 원으로 맞춘다(소수 찌꺼기로 검산이 1원 어긋나지 않게)."""
    return float(round(v))


def invested(flows: list[CashFlow]) -> float:
    """총 투자금액 = 입금 장부의 합(취소 행은 음수로 들어 있다)."""
    return float(sum(f.amount_krw for f in flows))


def reserved_krw(open_buys: list[Order], fx_now: dict[str, float | None]) -> float | None:
    """미체결 매수가 묶어 둔 현금(원) — 수수료 여유, 달러는 환율 여유까지 (docs/paper.md 2.1, 검증 [필수] 2).

    달러 주문인데 환율을 모르면 None — 모르는 예약을 0 으로 치면 같은 현금을 두 번 쓴다.
    부르는 쪽은 그날 매수를 하지 않는다
    """
    합 = 0.0
    for o in open_buys:
        if o.side != "buy" or o.limit_price is None:
            continue
        금액 = o.limit_price * o.quantity * (1 + FEE_BUFFER)
        if o.currency != "KRW":
            환율 = fx_now.get(o.currency)
            if not 환율:
                return None
            금액 *= 환율 * (1 + FX_BUFFER)
        합 += 금액
    return _원(합)


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
        return f"그 돈으로 이미 사서 현금이 모자랍니다 (계좌 {', '.join(모자람)})"
    return None


def halted(bar: Bar | None) -> bool:
    """그날 정지였나 — 행이 없거나 거래량 0·시가 ≤ 0 (docs/paper.md 2.3, 검증 [필수] 5).

    정지한 날 종가가 전일 값 그대로 실리면 지정가 매수가 거짓으로 찬다. 거래량을 모르면(None) 시가만 본다
    """
    if bar is None:
        return True
    if bar.volume is not None and bar.volume <= 0:
        return True
    return bar.open is not None and bar.open <= 0


def judge(order: Order, bar: Bar | None) -> tuple[float | None, str]:
    """체결가(슬리피지 전)와 까닭. 가격이 None 이면 안 찼다 (docs/paper.md 2.3).

    매수(지정가): 그날 **종가 ≤ 회차 가격**이면 회차 가격에 찬다 — 장중 저가로 찼을 몫은 놓친다(보수 쪽).
    매도(시장가): 그날 시가(`fill_basis='open'`), 장 열린 뒤 낸 주문은 종가. 시가가 없으면 종가.
    정지한 날은 None — 매수는 부르는 쪽이 취소, 매도는 미룬다
    """
    if halted(bar):
        return None, "정지(그날 시세 없음·거래량 0)"
    assert bar is not None
    if order.side == "buy":
        if order.limit_price is None or order.limit_price <= 0:
            return None, "지정가 없음"
        if bar.close <= order.limit_price:
            return order.limit_price, "종가 ≤ 회차 가격"
        return None, f"종가 {bar.close:g} > 회차 가격 {order.limit_price:g}"
    if order.fill_basis == "close":
        return bar.close, "장 열린 뒤 낸 주문 — 종가"
    if bar.open is not None and bar.open > 0:
        return bar.open, "시가"
    return bar.close, "시가 없음 — 종가"


def make_fill(order: Order, price: float, fill_date: str, fx_rate: float, costs: Costs) -> Fill:
    """체결 한 건의 비용과 현금 영향.

    지정가 매수는 그 값보다 비싸게 찰 수 없어 **슬리피지를 얹지 않는다**(검증 권장).
    시장가 매도는 슬리피지를 싸게 쪽으로
    """
    gross = price * order.quantity
    slip = 0.0 if order.side == "buy" else gross * costs.slippage_pct / 100
    if order.side == "buy":
        fee = gross * costs.buy_fee_pct / 100
        tax = 0.0
        cash = -(gross + fee) * fx_rate
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
    for f in sorted(fills, key=lambda x: (x.fill_date, 0 if x.side == "sell" else 1, x.order_id)):
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
    """매도 체결마다 실현손익(원) — 평균 원가 기준. 월간 리포트의 "가장 큰 이익·손실" 에 쓴다 (docs/paper.md 6장)."""
    보유: dict[int, tuple[int, float]] = {}
    out: list[dict[str, Any]] = []
    for f in sorted(fills, key=lambda x: (x.fill_date, 0 if x.side == "sell" else 1, x.order_id)):
        q, c = 보유.get(f.stock_id, (0, 0.0))
        if f.side == "buy":
            보유[f.stock_id] = (q + f.quantity, c - f.cash_krw)
            continue
        if q <= 0:
            continue
        원가 = c * f.quantity / q
        out.append({"stock_id": f.stock_id, "fill_date": f.fill_date, "order_id": f.order_id,
                    "pnl_krw": _원(f.cash_krw - 원가), "cost_krw": _원(원가)})  # fmt: skip
        보유[f.stock_id] = (q - f.quantity, c - 원가)
    return out


def value(
    flows: list[CashFlow],
    fills: list[Fill],
    closes: dict[int, tuple[float, str] | None],
    fx_now: dict[str, float | None],
) -> Valuation:
    """평가. `closes` 는 {종목: (마지막 종가, 날짜)}, `fx_now` 는 {통화: 원 환산률}.

    종가나 환율을 모르는 보유는 **원가로** 넣고 `unknown` 에 적는다 — 0 으로 넣으면 손익이 거짓으로 무너진다
    """
    현금 = cash(flows, fills)
    보유 = positions(fills)
    평가 = 0.0
    모름: list[dict[str, Any]] = []
    for sid, p in sorted(보유.items()):
        c = closes.get(sid)
        환율 = 1.0 if p.currency == "KRW" else fx_now.get(p.currency)
        if c is None or not 환율:
            평가 += p.cost_krw
            모름.append({"stock_id": sid, "why": "종가 없음" if c is None else f"{p.currency} 환율 없음"})
            continue
        평가 += c[0] * p.quantity * 환율
    입금 = invested(flows)
    총액 = 현금 + _원(평가)
    손익 = 총액 - 입금
    return Valuation(
        invested_krw=입금, cash_krw=현금, holdings_krw=_원(평가), equity_krw=총액, pnl_krw=손익,
        pnl_pct=(손익 / 입금 * 100) if 입금 > 0 else None, unknown=모름,
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


def twr_step(prev_index: float | None, prev_equity: float | None, equity: float, flow_since: float) -> float:
    """시간가중 수익 지수를 하루 잇는다 (docs/paper.md 3장).

    입금은 그날 아침에 들어와 그날 평가에 섞였다고 본다 — 오늘 수익률 = (오늘 총액 − 새 입금) ÷ 어제 총액 − 1.
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """2부 배분을 **정수 주 지정가 회차**로 바꾼다 (docs/paper.md 2.1).

    `allocations`: [{stock_id, amount(종목 통화), tranches:[{price_at_or_below, amount}], ...}].
    순서는 `build_portfolio` 가 낸 그대로(같은 함수 원칙), 현금(원 → 종목 통화) 안에서만 낸다. 1주도 못 사는 회차는 내지
    않는다. 최근에 판 종목(`sold_recently`, 판 뒤 `REBUY_COOLDOWN_SESSIONS`)은 사지 않는다.
    (주문, 빠진 것) — 빠진 것은 까닭을 단다
    """
    if fx_rate <= 0:
        return [], [{"stock_id": a.get("stock_id"), "why": "환율 없음"} for a in allocations]
    남은 = cash_available / fx_rate
    주문: list[dict[str, Any]] = []
    빠짐: list[dict[str, Any]] = []
    for a in allocations:
        sid = int(a["stock_id"])
        if sid in sold_recently:
            빠짐.append({"stock_id": sid, "why": f"판 뒤 {REBUY_COOLDOWN_SESSIONS}거래일 안"})
            continue
        for i, t in enumerate(a.get("tranches") or [], start=1):
            가격 = float(t.get("price_at_or_below") or 0)
            금액 = float(t.get("amount") or 0)
            if 가격 <= 0 or 금액 <= 0:
                continue
            # 비용을 넉넉히 잡아 현금이 음수로 가지 않게 — 수수료 여유 (수수료율보다 크다)
            수량 = int(min(금액, 남은 / (1 + FEE_BUFFER)) // 가격)
            if 수량 < 1:
                빠짐.append({"stock_id": sid, "tranche": i, "why": "현금 부족" if 금액 >= 가격 else "회차 금액 < 1주"})
                continue
            남은 -= 수량 * 가격 * (1 + FEE_BUFFER)
            주문.append({"stock_id": sid, "tranche": i, "limit_price": 가격, "quantity": 수량})
    return 주문, 빠짐
