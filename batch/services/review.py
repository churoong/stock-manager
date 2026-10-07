"""매매 복기. 순수 계산.

규칙의 단일 정의처는 `docs/review.md` 다. 이 모듈은 DB 를 모른다.
lot 과 매수 스냅샷을 받아 매수 결정별 복기 행과 집단별 통계를 돌려준다.

지키는 것
  단위는 매수 결정이다. lot 을 그대로 세면 관찰이 부푼다
  표본이 적으면 단정하지 않는다. 숫자는 내되 sample_ok 로 표시한다
  근거 문장은 받은 수치만 쓴다. 없는 값은 문장에서 빠진다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from batch.services import portfolio as pf
from batch.services import signals as sg

# 2: 일부 매도를 승률·표본에서 빼고, 분모가 작은 비율을 내지 않는다 (2026-09-29, docs/infra.md 25.694)
CALC_VERSION = 2

# 이 미만이면 단정하지 않는다. 출발값이고 근거는 없다 [확인필요: 몇 건부터 믿을지]
MIN_SAMPLE = 10

NO_SIGNAL = "없음"
HORIZON_LABEL = dict(sg.HORIZON_LABEL)


@dataclass(frozen=True)
class BuySnapshot:
    """매수 행에 얼려 둔 근거. trades 표의 스냅샷 열 그대로."""

    trade_id: int
    stock_id: int
    trade_date: str
    quantity: float
    currency: str
    horizon: str | None
    snapshot_as_of: str | None = None
    score_at_trade: float | None = None
    signal_type_at_trade: str | None = None
    sentiment_at_trade: float | None = None
    factor_scores_at_trade: str | None = None

    @property
    def has_snapshot(self) -> bool:
        return self.snapshot_as_of is not None


@dataclass
class Review:
    buy_trade_id: int
    stock_id: int
    horizon: str | None
    buy_date: str
    last_sell_date: str
    currency: str
    quantity_sold: float
    quantity_bought: float
    partial: bool
    cost: float
    proceeds: float
    return_pct: float
    holding_days: float
    realized_pnl_krw: float
    price_pnl_krw: float
    fx_pnl_krw: float
    has_snapshot: bool
    snapshot_as_of: str | None
    score_at_trade: float | None
    signal_type_at_trade: str | None
    sentiment_at_trade: float | None
    factor_scores_at_trade: str | None
    target_pct: float | None
    stop_pct: float | None
    outcome: str
    verdict_text: str = ""
    #: 원가를 **매수일 환율로** 원화 환산한 값. 집단 평균의 가중치다 — 종목 통화 원가로 가중하면
    #: 원화 100만 원과 1,000달러가 1000:1 로 섞여 미국 매수가 평균에서 사라진다 (docs/infra.md 25.289). DB 열이 아니다
    cost_krw: float = 0.0


@dataclass
class GroupStats:
    group_key: str
    group_kind: str
    label: str
    n: int
    sample_ok: bool
    win_rate: float | None
    avg_return_pct: float | None
    median_return_pct: float | None
    avg_holding_days: float | None
    target_rate: float | None
    stop_rate: float | None
    total_pnl_krw: float
    members: list[int] = field(default_factory=list)  # buy_trade_id


# ----------------------------------------------------------------------
# 판정
# ----------------------------------------------------------------------


def outcome_of(return_pct: float, target_pct: float | None, stop_pct: float | None) -> str:
    """목표·손절과 견준다. 경계는 포함이다(딱 도달해도 도달).

    **퍼센트로 바꿔 소수 여섯째 자리에서 반올림한 뒤 견준다** (docs/infra.md 25.243). `return_pct` 는
    `실수령 / 원가 − 1` 로 나오므로 부동소수 찌꺼기가 붙는다 — 원가 100·실수령 93 은 `-0.06999999999999995` 라
    손절 −7% 에 **딱 닿았는데 "손실 (손절 위)"** 로 판정됐다. 같은 보유를 매도 플래그(`sell_flags.evaluate`)는
    같은 반올림으로 "손절선에 닿았습니다" 라고 했었다. 두 판정이 같은 잣대를 쓴다.
    """
    if target_pct is None or stop_pct is None:
        return "none"
    r = round(return_pct * 100, 6)
    if r >= target_pct:
        return "target"
    if r <= stop_pct:
        return "stop"
    if r > 0:
        return "gain"
    if r < 0:
        return "loss"
    return "flat"


def targets_for(horizon: str | None, targets: dict | None) -> tuple[float | None, float | None]:
    """기간의 목표·손절(%). 설정이 없으면 signals.DEFAULT_TARGETS."""
    if not horizon:
        return None, None
    conf = (targets or {}).get(horizon) or sg.DEFAULT_TARGETS.get(horizon)
    if not conf:
        return None, None
    return _f(conf.get("target_pct")), _f(conf.get("stop_pct"))


def _f(v: object) -> float | None:
    return None if v is None else float(v)


# ----------------------------------------------------------------------
# 매수 결정별 복기
# ----------------------------------------------------------------------


def build_reviews(
    lots: list[pf.Lot],
    buys: dict[int, BuySnapshot],
    sell_dates: dict[int, str],
    targets: dict | None = None,
) -> list[Review]:
    """lot 을 매수 거래별로 합쳐 복기 행을 만든다.

    Args:
        lots: FIFO 결과
        buys: {buy_trade_id: 스냅샷}. 여기 없는 매수의 lot 은 건너뛴다
        sell_dates: {sell_trade_id: 체결일}
        targets: 설정 horizon_targets
    """
    grouped: dict[int, list[pf.Lot]] = {}
    for lot in lots:
        grouped.setdefault(lot.buy_trade_id, []).append(lot)

    reviews: list[Review] = []
    for buy_id in sorted(grouped):
        buy = buys.get(buy_id)
        if buy is None:
            continue
        group = grouped[buy_id]

        qty = sum(lot.quantity for lot in group)
        cost = sum(lot.cost for lot in group)
        proceeds = sum(lot.proceeds for lot in group)
        if qty <= 0 or cost <= 0:
            continue

        holding = sum(lot.holding_days * lot.quantity for lot in group) / qty
        return_pct = proceeds / cost - 1
        target, stop = targets_for(buy.horizon, targets)
        outcome = outcome_of(return_pct, target, stop)
        last_sell = max(sell_dates.get(lot.sell_trade_id, "") for lot in group)

        review = Review(
            buy_trade_id=buy_id,
            stock_id=buy.stock_id,
            horizon=buy.horizon,
            buy_date=buy.trade_date,
            last_sell_date=last_sell or buy.trade_date,
            currency=buy.currency,
            quantity_sold=qty,
            quantity_bought=buy.quantity,
            partial=qty + pf.QTY_EPS < buy.quantity,
            cost=cost,
            proceeds=proceeds,
            return_pct=return_pct,
            holding_days=holding,
            realized_pnl_krw=sum(lot.realized_pnl_krw for lot in group),
            price_pnl_krw=sum(lot.price_pnl_krw for lot in group),
            fx_pnl_krw=sum(lot.fx_pnl_krw for lot in group),
            has_snapshot=buy.has_snapshot,
            snapshot_as_of=buy.snapshot_as_of,
            score_at_trade=buy.score_at_trade,
            signal_type_at_trade=buy.signal_type_at_trade,
            sentiment_at_trade=buy.sentiment_at_trade,
            factor_scores_at_trade=buy.factor_scores_at_trade,
            target_pct=target,
            stop_pct=stop,
            outcome=outcome,
            cost_krw=sum(lot.cost * lot.buy_fx for lot in group),
        )
        review.verdict_text = verdict(review)
        reviews.append(review)
    return reviews


def verdict(r: Review) -> str:
    """근거 문장. 받은 수치만 쓴다. 없는 값은 빠진다."""
    head: list[str] = []
    if r.horizon:
        head.append(HORIZON_LABEL.get(r.horizon, r.horizon))
    if r.has_snapshot:
        head.append(r.signal_type_at_trade or "신호 없이 매수")
        if r.score_at_trade is not None:
            head.append(f"매수 시 점수 {r.score_at_trade:.0f}")
    else:
        head.append("저장된 근거 없음")

    result = f"{r.return_pct:+.1%} ({r.holding_days:.0f}일)"
    if r.partial:
        result += ", 일부 매도"

    tail: list[str] = []
    if r.target_pct is not None and r.stop_pct is not None:
        hit = "도달" if r.outcome == "target" else "미달"
        # **설정값 그대로 적는다** (docs/infra.md 25.367). `+.0f` 는 7.5 를 "+8%" 로 적어, 수익률 +7.6% 매수가
        # "목표 +8% 도달" 로 나갔다(판정은 7.5 로 한다). `:+g` 는 정수면 "+10", 소수면 "+7.5" 다
        tail.append(f"목표 {r.target_pct:+g}% {hit}")
        broke = "이탈" if r.outcome == "stop" else "유지"
        tail.append(f"손절 {r.stop_pct:+g}% {broke}")

    text = " · ".join(head) + f" → {result}"
    if tail:
        text += ". " + ", ".join(tail)
    return text


# ----------------------------------------------------------------------
# 통계
# ----------------------------------------------------------------------


def _stats(key: str, kind: str, label: str, rows: list[Review]) -> GroupStats:
    # **일부만 판 매수는 승률·평균·표본에 넣지 않는다** (docs/infra.md 25.694, 감사 재현). 1주만 +5% 에 팔고 99주를
    # −40% 로
    # 들고 있는 매수 10건이 "승률 100%, 표본 충분" 이었다 — 이익 난 몫만 먼저 파는 습관이 그대로 승률을 부풀린다.
    # 실현손익 합계에는 넣는다(실제로 오간 돈이다). 뺀 건수는 이름 옆에 적는다
    일부 = sum(1 for r in rows if r.partial)
    전체 = rows
    rows = [r for r in rows if not r.partial]
    if 일부:
        label = f"{label} (일부 매도 {일부}건 제외)"
    n = len(rows)
    if n == 0:
        return GroupStats(
            key, kind, label, 0, False, None, None, None, None, None, None,
            sum(r.realized_pnl_krw for r in 전체), [r.buy_trade_id for r in 전체],
        )  # fmt: skip

    # 가중치는 원화 원가다 — 나라가 섞인 집단(전체·기간별)에서 통화가 다른 원가를 그대로 더하지 않는다 (25.289)
    total_cost = sum(r.cost_krw for r in rows)
    weighted = sum(r.return_pct * r.cost_krw for r in rows) / total_cost if total_cost > 0 else None
    with_targets = [r for r in rows if r.outcome != "none"]
    # **비율의 분모가 표본 판정과 다르면 따로 본다** (25.694, 감사). 10건 중 3건만 기간(목표·손절)이 있으면 "표본
    # 충분" 인 집단의
    # 목표 도달률이 3건으로 난다 — 분모가 집단과 같거나(그러면 sample_ok 가 그대로 말한다) 하한을 넘을 때만 낸다
    비율_ok = len(with_targets) == n or len(with_targets) >= MIN_SAMPLE

    return GroupStats(
        group_key=key,
        group_kind=kind,
        label=label,
        n=n,
        sample_ok=n >= MIN_SAMPLE,
        win_rate=sum(1 for r in rows if r.return_pct > 0) / n,
        avg_return_pct=weighted,
        median_return_pct=median(r.return_pct for r in rows),
        avg_holding_days=sum(r.holding_days for r in rows) / n,
        target_rate=(
            sum(1 for r in with_targets if r.outcome == "target") / len(with_targets)
            if with_targets and 비율_ok else None
        ),
        stop_rate=(
            sum(1 for r in with_targets if r.outcome == "stop") / len(with_targets)
            if with_targets and 비율_ok else None
        ),
        total_pnl_krw=sum(r.realized_pnl_krw for r in 전체),
        members=[r.buy_trade_id for r in 전체],
    )


def build_stats(reviews: list[Review]) -> list[GroupStats]:
    """전체 → 기간별 → 신호별. 신호별은 스냅샷이 있는 행만 센다."""
    out: list[GroupStats] = [_stats("all", "all", "전체", reviews)]

    for horizon in sg.HORIZONS:
        rows = [r for r in reviews if r.horizon == horizon]
        if rows:
            out.append(_stats(f"horizon:{horizon}", "horizon", HORIZON_LABEL[horizon], rows))

    with_snapshot = [r for r in reviews if r.has_snapshot]
    by_signal: dict[str, list[Review]] = {}
    for r in with_snapshot:
        by_signal.setdefault(r.signal_type_at_trade or NO_SIGNAL, []).append(r)
    for signal in sorted(by_signal):
        out.append(_stats(f"signal:{signal}", "signal", signal, by_signal[signal]))
    return out
