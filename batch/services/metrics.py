"""성과 지표 계산.

계산식은 docs/metrics.md 에 적혀 있다. 여기 있는 것과 문서가 어긋나면
문서가 맞다. 문서에 없는 지표는 만들지 않는다.

**순수 함수다.** 데이터베이스를 모르고, 같은 입력에 항상 같은 값을 낸다.
그래서 손으로 계산 가능한 고정 데이터로 검증할 수 있다.

**값을 만들어 내지 않는다.** 표본이 모자라거나 분모가 0 이면 None 이다.
0 으로 두면 "움직이지 않은 종목" 처럼 보이고, 그게 맞는 값처럼 읽힌다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

# 연환산 계수. 한국과 미국 모두 연간 거래일이 대략 이 수준이다.
TRADING_DAYS_PER_YEAR = 252

# 기간별 최소 거래일. 못 미치면 그 지표는 None 이다.
MIN_POINTS = {"1Y": 200, "3Y": 600, "5Y": 1000}

#: 이보다 **더 벌어진** 두 행 사이의 수익률은 **하루치가 아니다** (docs/metrics.md 0장 「일간 수익률」).
#:
#: 왜 11 인가 — **실측**이다. `exchange_calendars` 로 2016~2026 의 거래일 사이 간격을
#: 재면 XKRX 최장 **11일**(2017-09-29 → 10-10, 추석+임시공휴일+개천절), XNYS 최장 4일이다
#: (`tests/test_stale_threshold.py` 가 같은 값을 다시 잰다).
#: 그러므로 **정상 휴장은 절대 안 걸린다.** 걸리는 것은 거래정지나 수집 구멍뿐이다.
#:
#: **알고 두는 한계.** 사흘짜리 짧은 정지는 긴 연휴와 구별되지 않아 못 잡는다.
#: 구별하려면 그 시장의 거래일 목록이 필요한데, 계산 함수는 DB 도 달력도 모른다.
#: 큰 왜곡부터 막고, 못 잡는 것을 적어 둔다 `[확인필요: 거래일 목록을 넘기는 편이 나은지]`.
MAX_SESSION_GAP_DAYS = 11

#: 창의 **시작**을 채웠다고 볼 여유(달력일) — 창 시작일 뒤 이만큼 안에 첫 시세가 있어야 한다 (25.703).
#: 창 길이(1Y 365·3Y 1095일)의 몇 %에 불과한 값이고, 상장 2.3년 종목의 "3년" 값은 막는다 [확인필요: 근거는 감사 제안값]
COVER_START_SLACK_DAYS = 45

# 계산식이 바뀌면 올린다. 과거 값과 섞이지 않게 한다.
#   1: 처음 (2026-09-16)
#   2: 구멍을 사이에 둔 한 쌍을 일간 수익률로 세지 않는다 (2026-09-22, docs/infra.md 25.102)
#   3: 수정주가 사이의 빈 행을 원 종가로 메우지 않고 뺀다 (2026-09-29, docs/infra.md 25.701).
#      창의 시작·끝을 채우지 못하면 값을 내지 않는다 (같은 날 25.703 — 판 3 이 운영에서 돌기 전이라 합쳤다 [확인필요])
#   4: 가운데 빈 행을 빼지 않고 원 종가·옮긴 값 중 직전에 가까운 쪽으로, 창 끝은 시장별 "대부분이 들어온 날"
#      (2026-09-30, docs/infra.md 25.709·25.710·25.715).
#      판 3 은 D1 따라잡기(날마다 00:05 UTC)가 한 번 돌렸을 수 있어 합치지 않았다
CALC_VERSION = 4

#: 리스크 판정이 볼 창. **앞의 것을 먼저 보고, 없으면 뒤로 내려간다**
#: (docs/factors.md 3.5). `jobs/scores` 와 `jobs/signals` 가 **둘 다** 이것을 쓴다 —
#: 예전에는 양쪽이 `("3Y", "1Y")` 를 따로 적고 고르는 규칙도 따로 갖고 있었다
#: (docs/infra.md 25.98).
RISK_WINDOWS = ("3Y", "1Y")


def has_values(row: dict) -> bool:
    """이 성과지표 행에 **쓸 값이 들었나.**

    `jobs/metrics.py` 는 표본이 모자라도 행을 쓴다 — 개수만 담고 값은 전부 NULL 이다
    (`compute()`: "표본 부족. 개수만 남기고 값은 채우지 않는다"). 그 판정과 **같은 글자**를
    여기 둔다. 갈라지면 "표본 부족" 이라고 세어 놓고 점수에서는 그 행을 쓰게 된다.
    """
    return row.get("cagr") is not None or row.get("mdd") is not None


def pick_window(rows: list[dict]) -> dict[int, dict]:
    """종목별로 **쓸 성과지표 행 하나**를 고른다. `{stock_id: row}`.

    두 단계다.

    1. 같은 (종목, 창)에 여럿이면 **가장 최근 계산일**, 같은 날이면 **가장 높은 계산 판**
    2. 창은 **값이 있는 쪽 먼저**, 그 다음이 `RISK_WINDOWS` 순서

    2번의 앞쪽이 2026-09-21 에 더해졌다(docs/infra.md 25.97). 그전에는 창 이름만 봐서
    **빈 3Y 행이 값이 든 1Y 행을 이겼고**, 상장 1~3년 종목은 리스크 지표 일곱을 잃어
    종합 점수가 아예 안 나왔다 — 문서가 약속한 폴백이 **필요한 바로 그 종목에서만**
    안 돈 것이다.

    1번의 뒤쪽(계산 판)이 2026-09-22 에 더해졌다(docs/infra.md 25.102).
    `performance_metrics` 의 유니크 키에 `calc_version` 이 들어 있어, 판을 올리면
    **같은 (종목, 기준일, 창)에 두 행**이 남는다. 그때 어느 쪽이 뽑힐지 정해져 있지
    않았다 — 옛 계산식으로 낸 변동성이 새 점수에 들어갈 수 있었다(25.91 과 같은 모양).

    부르는 쪽이 `as_of` 를 질의에 걸어 넘긴다. **여기서는 시점을 보지 않는다** —
    이 함수가 받는 것은 이미 걸러진 행들이다.

    `calc_version` 이 행에 없으면 0 으로 친다. 없는 쪽이 지므로 **있는 쪽이 이긴다** —
    질의가 그 열을 안 고르는 옛 호출부가 있어도 순서가 뒤집히지는 않는다.
    """
    최근: dict[tuple[int, str], dict] = {}
    for row in rows:
        _창을_확인한다(str(row["window"]))
        키 = (int(row["stock_id"]), str(row["window"]))
        있던것 = 최근.get(키)
        if 있던것 is None or _새로움(row) > _새로움(있던것):
            최근[키] = row

    나온것: dict[int, dict] = {}
    for stock_id, row in ((k[0], v) for k, v in 최근.items()):  # 창은 행 안에도 있다
        있던것 = 나온것.get(stock_id)
        if 있던것 is None or _우선순위(row) < _우선순위(있던것):
            나온것[stock_id] = row
    return 나온것


def _새로움(row: dict) -> tuple[str, int]:
    """클수록 새것. (계산일, 계산 판)."""
    판 = row.get("calc_version")
    return (str(row["as_of_date"]), int(판) if 판 is not None else 0)


def _창을_확인한다(window: str) -> None:
    """리스크 판정이 보기로 한 창인가 (docs/infra.md 25.176).

    **예전에는 이 확인이 비교하는 순간에만 일어났다.** `_우선순위()` 안의
    `RISK_WINDOWS.index()` 가 그 일을 했는데, 그 함수는 **같은 종목에 행이 둘 이상일
    때만** 불린다. 그래서 같은 잘못이 한 번은 `ValueError` 로 터지고 한 번은 조용히
    지나갔다 — 5Y 행 하나만 온 종목은 **규칙에 없는 창으로 리스크를 판정받았다.**
    `performance_metrics` 에는 5Y 행이 실제로 있다(docs/metrics.md 표).

    터지는 쪽으로 맞췄다. 여기 오는 행의 창은 **부르는 쪽 질의가 정한다.** 규칙에 없는
    창이 왔다면 질의와 규칙이 어긋난 것이고, 그것은 데이터가 아니라 코드의 잘못이다.
    조용히 고르면 어느 창으로 판정했는지 아무도 모른 채 점수가 나간다.
    """
    if window not in RISK_WINDOWS:
        raise ValueError(
            f"리스크 판정이 보지 않는 창입니다: {window!r}."
            f" 볼 창은 {RISK_WINDOWS} 다 (docs/factors.md 3.5)."
            " 부르는 쪽 질의의 `window IN (...)` 을 `metrics.RISK_WINDOWS` 로 만들어라"
        )


def _우선순위(row: dict) -> tuple[int, int]:
    """작을수록 먼저. (값이 있나, 창 순서)."""
    return (0 if has_values(row) else 1, RISK_WINDOWS.index(str(row["window"])))


@dataclass(frozen=True)
class PricePoint:
    """계산에 쓰는 한 점. 날짜와 가격만 있으면 된다."""

    date: date
    close: float
    #: 그날 거래대금(원 종가 × 거래량) — 매물대(docs/analysis.md 45장)만 쓴다. 분할이 있어도 바뀌지 않는 값이다
    value: float | None = None


@dataclass
class DrawdownResult:
    mdd: float | None  # 음수. 35% 하락은 -0.35
    peak_date: date | None
    trough_date: date | None
    recovery_days: int | None  # 아직 회복 못 했으면 None


@dataclass
class Metrics:
    """한 기간에 대한 성과 지표 한 벌."""

    window: str
    data_points: int
    cagr: float | None = None
    mdd: float | None = None
    mdd_peak_date: date | None = None
    mdd_trough_date: date | None = None
    mdd_recovery_days: int | None = None
    volatility_ann: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    beta: float | None = None
    benchmark: str | None = None
    risk_free_rate_used: float | None = None
    calc_version: int = CALC_VERSION


# ----------------------------------------------------------------------
# 기초
# ----------------------------------------------------------------------
def daily_returns(points: list[PricePoint]) -> list[float]:
    """일간 수익률.

    가격이 0 이하인 구간은 건너뛴다. 거래정지 종목에서 0 이 들어오는 경우가
    있는데, 그대로 나누면 무한대가 되거나 부호가 뒤집힌다.

    **두 행이 `MAX_SESSION_GAP_DAYS` 보다 벌어져 있으면 그 수익률은 버린다**
    (2026-09-22, docs/metrics.md 0장). 2주 쉬었다 재개한 종목의 첫날 움직임은
    누적된 것이지 **하루치가 아니다.** 그것을 일간 수익률로 세면

      · 변동성·샤프·소르티노가 그 한 점에 끌려간다
      · 정지 이력이 있는 종목이 일제히 "위험한 종목" 으로 보인다

    버리는 쪽을 골랐다 — **모르는 것을 지어내지 않는다.** 며칠치인지 알더라도
    루트로 나눠 하루치로 환산하는 것은 그 기간에 매일 같은 크기로 움직였다는
    가정이라, 없는 정보를 만드는 일이다.
    """
    out: list[float] = []
    for prev, curr in zip(points, points[1:], strict=False):
        if prev.close <= 0 or curr.close <= 0:
            continue
        if (curr.date - prev.date).days > MAX_SESSION_GAP_DAYS:
            continue
        out.append(curr.close / prev.close - 1)
    return out


def stdev(values: list[float]) -> float | None:
    """표본표준편차. 모집단이 아니라 표본이다."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def annualize_volatility(daily_sigma: float) -> float:
    return daily_sigma * math.sqrt(TRADING_DAYS_PER_YEAR)


def years_between(start: date, end: date) -> float:
    """달력 기준 연수. 거래일이 아니다.

    거래일로 나누면 휴장이 많은 해에 값이 부풀려진다.
    """
    return (end - start).days / 365.25


# ----------------------------------------------------------------------
# 지표
# ----------------------------------------------------------------------
def cagr(points: list[PricePoint]) -> float | None:
    """연평균 성장률. (기말/기초)^(1/연수) - 1"""
    if len(points) < 2:
        return None

    start, end = points[0], points[-1]
    if start.close <= 0 or end.close <= 0:
        return None

    years = years_between(start.date, end.date)
    if years <= 0:
        return None

    return (end.close / start.close) ** (1 / years) - 1


def max_drawdown(points: list[PricePoint]) -> DrawdownResult:
    """최대 낙폭과 그 경위.

    회복 기간이 None 인 것과 0 인 것은 다르다.
    None 은 아직 회복하지 못했다는 뜻이고, 그쪽이 더 나쁜 신호다.
    """
    if len(points) < 2:
        return DrawdownResult(None, None, None, None)

    peak_value = points[0].close
    peak_index = 0
    worst = 0.0
    worst_peak_index: int | None = None
    worst_trough_index: int | None = None

    for i, point in enumerate(points):
        if point.close > peak_value:
            peak_value = point.close
            peak_index = i
            continue
        if peak_value <= 0:
            continue

        drawdown = point.close / peak_value - 1
        if drawdown < worst:
            worst = drawdown
            worst_peak_index = peak_index
            worst_trough_index = i

    if worst_trough_index is None:
        # 한 번도 고점 아래로 내려가지 않았다. **회복 기간은 0 이다 — None 이 아니다** (docs/infra.md 25.310).
        # None 은 "아직 회복 못 함" 이라 점수 계산(`missing_is_worst`)이 집단의 최악값으로 바꿔 넣는다 —
        # 한 번도 안 빠진 종목이 리스크 팩터에서 가장 나쁜 회복 점수를 받았다
        return DrawdownResult(0.0, None, None, 0)

    peak_price = points[worst_peak_index].close
    recovery = None
    for i in range(worst_trough_index + 1, len(points)):
        if points[i].close >= peak_price:
            recovery = i - worst_trough_index
            break

    return DrawdownResult(
        mdd=worst,
        peak_date=points[worst_peak_index].date,
        trough_date=points[worst_trough_index].date,
        recovery_days=recovery,
    )


def volatility(points: list[PricePoint]) -> float | None:
    """연환산 변동성."""
    returns = daily_returns(points)
    sigma = stdev(returns)
    return annualize_volatility(sigma) if sigma is not None else None


def downside_deviation(returns: list[float]) -> float | None:
    """하방편차. 기준점은 0 이다.

    제곱합을 나누는 수는 **n - 1** 이다(n = 전체 관측치 수). 하락한 날의 개수가 아니다.
    하락한 날로 나누면 하락이 드문 종목의 하방편차가 과대평가된다.
    """
    n = len(returns)
    if n < 2:
        return None
    squared = sum(min(r, 0.0) ** 2 for r in returns)
    return math.sqrt(squared / (n - 1))


def sharpe(points: list[PricePoint], risk_free_annual: float | None) -> float | None:
    """(CAGR - 무위험수익률) / 연환산변동성.

    무위험수익률이 없으면 None 이다. 0 으로 두면 무위험수익률이 0인 세상의
    값이 나오는데, 그게 맞는 값처럼 보인다.
    """
    if risk_free_annual is None:
        return None

    growth = cagr(points)
    sigma = volatility(points)
    if growth is None or sigma is None or sigma == 0:
        return None

    return (growth - risk_free_annual) / sigma


def sortino(points: list[PricePoint], risk_free_annual: float | None) -> float | None:
    """(CAGR - 무위험수익률) / 연환산 하방편차."""
    if risk_free_annual is None:
        return None

    growth = cagr(points)
    if growth is None:
        return None

    deviation = downside_deviation(daily_returns(points))
    if deviation is None or deviation == 0:
        return None

    return (growth - risk_free_annual) / annualize_volatility(deviation)


def beta(
    stock: list[PricePoint], market: list[PricePoint]
) -> tuple[float | None, int]:
    """시장 대비 베타와 계산에 쓴 겹치는 날 수.

    **같은 날짜끼리 맞춰서 계산한다.** 종목과 시장의 거래일이 다르면
    겹치는 날만 쓴다. 이걸 안 맞추면 값이 통째로 틀린다.
    """
    aligned_stock, aligned_market = align(stock, market)
    if len(aligned_stock) < 3:
        return None, len(aligned_stock)

    stock_returns, market_returns = paired_returns(aligned_stock, aligned_market)
    n = len(stock_returns)
    if n < 2:
        return None, n

    stock_mean = sum(stock_returns) / n
    market_mean = sum(market_returns) / n

    covariance = sum(
        (s - stock_mean) * (m - market_mean)
        for s, m in zip(stock_returns, market_returns, strict=True)
    ) / (n - 1)
    market_variance = sum((m - market_mean) ** 2 for m in market_returns) / (n - 1)

    if market_variance == 0:
        return None, n

    return covariance / market_variance, n


def paired_returns(
    stock: list[PricePoint], market: list[PricePoint]
) -> tuple[list[float], list[float]]:
    """날짜를 맞춘 두 계열의 일간 수익률을 **짝으로** 낸다 (docs/infra.md 25.202).

    `daily_returns` 를 양쪽에 따로 부르면 안 된다. 그 함수는 종가가 0 이하인 날을 건너뛰는데
    그런 날은 **한쪽에만** 있다(거래정지 종목의 0). 종목 쪽 목록만 둘 짧아지고, 뒤에서 자르면
    **그날 뒤의 짝이 전부 어긋난다** — 베타 2 인 종목이 0 하루로 0.25 가 됐다.
    그래서 한 쌍씩 보고, **어느 쪽이든** 못 쓰면 그 쌍을 **둘 다** 버린다.
    11일 넘게 벌어진 쌍도 버린다(`daily_returns` 와 같은 규칙). 날짜가 같으므로 양쪽이 같이 걸린다.
    """
    s_ret: list[float] = []
    m_ret: list[float] = []
    for i in range(1, min(len(stock), len(market))):
        sp, sc, mp, mc = stock[i - 1], stock[i], market[i - 1], market[i]
        if min(sp.close, sc.close, mp.close, mc.close) <= 0:
            continue
        if (sc.date - sp.date).days > MAX_SESSION_GAP_DAYS:
            continue
        s_ret.append(sc.close / sp.close - 1)
        m_ret.append(mc.close / mp.close - 1)
    return s_ret, m_ret


def align(
    left: list[PricePoint], right: list[PricePoint]
) -> tuple[list[PricePoint], list[PricePoint]]:
    """두 계열에서 날짜가 겹치는 점만 남긴다. 순서는 날짜순이다."""
    right_by_date = {p.date: p for p in right}
    pairs = [(p, right_by_date[p.date]) for p in left if p.date in right_by_date]
    pairs.sort(key=lambda pair: pair[0].date)
    return [p for p, _ in pairs], [q for _, q in pairs]


# ----------------------------------------------------------------------
# 한 벌 계산
# ----------------------------------------------------------------------
def compute(
    window: str,
    points: list[PricePoint],
    *,
    market: list[PricePoint] | None = None,
    benchmark: str | None = None,
    risk_free_annual: float | None = None,
    span: tuple[date, date] | None = None,
) -> Metrics:
    """한 기간의 지표를 모두 계산한다.

    표본이 하한에 못 미치면 값들을 None 으로 둔다. 100일치로 계산한 값을
    "1년 변동성" 이라고 부르면 안 된다.
    """
    # **0 이하 종가는 값이 없는 날이다** (docs/infra.md 25.203). 거래정지 종목에서 0 이 들어오는데
    # (docs/metrics.md 0장) 수익률만 그것을 건너뛰고 MDD·CAGR 은 가격 경로를 그대로 봤다 —
    # 꾸준히 오른 종목이 0 하루로 **MDD −100%** 가 됐다. 여기서 한 번 빼면 모든 지표가 같은 계열을 본다.
    # 뺀 자리는 휴장처럼 이어진다. 11일을 넘게 벌어지면 수익률 쪽 간격 규칙이 그 쌍을 버린다
    points = sorted((p for p in points if p.close > 0), key=lambda p: p.date)
    count = len(points)
    result = Metrics(window=window, data_points=count)

    minimum = MIN_POINTS.get(window)
    if minimum is not None and count < minimum:
        # 표본 부족. 개수만 남기고 값은 채우지 않는다
        return result
    # **창의 시작과 끝을 채웠는가** (docs/infra.md 25.703, 감사 재현). 행 수만 봐서 상장 2.3년(610행) 종목에도 "3년"
    # 값이 났고,
    # 앞쪽 650행만 있고 마지막 6개월이 빈 종목도 비어 있는 동안의 하락을 모른 채 "기준일 현재" 값으로 저장됐다.
    # `span=(창 시작일, 기준일)` 을 받으면 첫 시세가 시작일 + 45일 안, 마지막 시세가 기준일 − 11일 안이어야 값을 낸다.
    # 못 맞추면 개수만 남기고 값은 None — `pick_window` 가 더 짧은 창으로 내려간다
    if span is not None and points:
        시작, 끝 = span
        if (points[0].date - 시작).days > COVER_START_SLACK_DAYS or (끝 - points[-1].date).days > MAX_SESSION_GAP_DAYS:
            return result

    result.cagr = cagr(points)
    result.volatility_ann = volatility(points)
    result.sharpe = sharpe(points, risk_free_annual)
    result.sortino = sortino(points, risk_free_annual)
    result.risk_free_rate_used = risk_free_annual

    drawdown = max_drawdown(points)
    result.mdd = drawdown.mdd
    result.mdd_peak_date = drawdown.peak_date
    result.mdd_trough_date = drawdown.trough_date
    result.mdd_recovery_days = drawdown.recovery_days

    if market:
        값, _쌍 = beta(points, market)
        # **겹치는 날도 표본 하한을 넘어야 한다** (docs/metrics.md 6장, docs/infra.md 25.227). 예전에는 종목 쪽
        # 행 수만 하한을 봐서, 지수가 짧은 동안(따라잡기 중 등) 70일 겹침으로 낸 값이 "1년 베타" 로 저장됐다.
        # 하한은 **겹친 날 수**로 잰다 — 다른 지표가 `count`(행 수)로 재는 것과 같은 잣대 (docs/infra.md 25.303).
        # `beta()` 가 돌려주는 수익률 쌍 수(겹친 날 − 1)로 재면 딱 200일 계열의 1년 베타만 빠졌다
        겹친날 = len(align(points, market)[0])
        result.beta = 값 if minimum is None or 겹친날 >= minimum else None
        # **베타를 낸 때만 지수 이름을 적는다** (docs/metrics.md 8장 "베타 계산에 쓴 지수", docs/infra.md 25.684, 감사).
        # 예전에는 겹침이 모자라 베타가 None 이어도 채워, 비어 있는 베타가 "KOSPI 로 쟀다" 고 읽혔다
        result.benchmark = benchmark if result.beta is not None else None

    return result
