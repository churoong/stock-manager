"""스트레스 테스트. 바스켓의 과거 최악 구간 재현.

계산의 단일 정의처는 `docs/stress.md` 다. 여기 없는 시나리오는 만들지 않는다.

이 모듈은 DB 를 모른다. 비중과 가격을 받아 창별 낙폭을 돌려준다.

목적은 수익 예측이 아니다. "이만큼은 떨어질 수 있다" 를 미리 보여 그 낙폭을
견딜 수 있는지 스스로 묻게 하는 것이다. 그래서 확률 분포(VaR)도 가상
시나리오도 만들지 않는다. 실제로 일어난 구간만 보여준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 2: 모든 종목에 가격이 있는 첫 날부터 잰다 — 짧은 이력 종목의 비중이 현금으로 바뀌어 낙폭이 작게 나왔다 (25.823)
# 3: 각 종목 기준가를 시작일 이전 마지막 종가로 — 하루 결측 종목을 빼지 않는다 (25.827·25.831)
CALC_VERSION = 3

# 창 길이(거래일). 근거는 docs/stress.md 1장.
WINDOWS = (20, 60, 120, 250)

# 창 길이의 몇 배가 있어야 그 창을 계산하는가. 표본이 창 하나뿐이면
# "최악" 이 아니라 "유일" 이다.
MIN_SAMPLE_MULTIPLE = 2

WARN_SURVIVORSHIP = (
    "생존편향: 지금 추천된 바스켓은 살아남은 종목뿐이다. 여기 나온 낙폭은 실제보다 작다"
)
WARN_NOT_FORECAST = "과거의 최악은 앞으로의 최악이 아니다"


@dataclass
class Window:
    length: int
    start: str
    end: str
    return_pct: float  # 창 수익률. -0.35 는 35% 하락
    max_drawdown: float  # 창 안의 최대 낙폭(음수)
    recovery_days: int | None  # 창 끝의 저점에서 이전 고점 회복까지. 미회복 None


@dataclass
class StressResult:
    curve: list[tuple[str, float]]  # 바스켓 가치. 시작 1.0
    weights_used: dict[int, float]
    weighting: str  # "suggested" | "equal"
    cash_weight: float  # 비중 합이 1 에 못 미치는 만큼
    excluded: list[int]  # 시작일 가격이 없어 뺀 종목
    windows: list[Window]
    skipped_windows: list[int]  # 표본 부족으로 계산하지 않은 창 길이
    warnings: list[str] = field(default_factory=list)


def basket_curve(
    weights: dict[int, float],
    prices: dict[int, dict[str, float]],
    dates: list[str],
    min_points: int = 0,
) -> tuple[list[tuple[str, float]], dict[int, float], float, list[int]]:
    """바스켓 가치 곡선.

    Returns:
        (곡선, 실제로 쓴 비중, 현금 비중, 뺀 종목)

    비중의 합이 1 에 못 미치면 나머지는 현금(수익 0)이다. 100% 로 다시 나누면
    낙폭이 과장된다. 시작일에 가격이 없는 종목은 뺀다.
    """
    if not dates:
        raise ValueError("거래일이 없습니다")
    if any(w < 0 for w in weights.values()):
        raise ValueError("비중은 음수일 수 없습니다")

    dates = sorted(dates)
    # **모든 종목에 가격이 있는 첫 날부터** 잰다 (docs/infra.md 25.823, 감사). 합집합의 첫 날로 잡으면 상장 5년
    # 미만·백필이
    # 짧은 종목이 시작일에 빠져 그 비중이 현금(수익 0)이 되고, 낙폭이 실제 주식 비중만큼 작게 나왔다. 그렇게 줄인 표본이
    # `min_points` 에 못 미치면 예전처럼 첫 날부터 재고 빠진 비중은 부르는 쪽이 경고한다
    첫날 = [min(d for d, v in prices[sid].items() if v > 0) for sid, w in weights.items()
            if w > 0 and sid in prices and any(v > 0 for v in prices[sid].values())]
    if 첫날 and min_points > 0:
        줄인 = [d for d in dates if d >= max(첫날)]
        if len(줄인) >= min_points:
            dates = 줄인
    start = dates[0]

    used: dict[int, float] = {}
    excluded: list[int] = []
    기준가: dict[int, float] = {}
    for sid, w in weights.items():
        if w <= 0:
            continue
        # 시작일 **이전까지의 마지막** 양의 종가를 기준으로 — 이력이 긴 종목이 공통 시작일 하루만 결측·정지여도 통째로
        # 빠져
        # 그 비중이 수익 0 이 됐다 (25.827, 교차검증). 도중 결측을 잇는 `carry` 와 같은 규칙이다
        앞 = [d for d, v in prices.get(sid, {}).items() if d <= start and v > 0]
        if 앞:
            used[sid] = w
            기준가[sid] = prices[sid][max(앞)]
        else:
            excluded.append(sid)

    total = sum(used.values())
    if total > 1 + 1e-9:
        raise ValueError(f"비중의 합이 1 을 넘습니다: {total:.4f}")
    cash = max(0.0, 1.0 - total)

    def carry(sid: int, d: str) -> float:
        # 0 이하 종가는 값이 없는 날로 보고 앞 값을 잇는다 (docs/infra.md 25.203).
        # 거래정지의 0 을 그대로 쓰면 바스켓이 그날 가짜로 폭락하고 최악 창이 거기 잡힌다
        by_date = prices[sid]
        if d in by_date and by_date[d] > 0:
            return by_date[d]
        earlier = [k for k in by_date if k < d and by_date[k] > 0]
        return by_date[max(earlier)]

    curve: list[tuple[str, float]] = []
    for d in dates:
        value = cash
        for sid, w in used.items():
            value += w * carry(sid, d) / 기준가[sid]
        curve.append((d, value))
    return curve, used, cash, sorted(excluded)


def worst_window(curve: list[tuple[str, float]], length: int) -> Window | None:
    """길이가 length 인 창 중 수익률이 가장 나쁜 창.

    표본이 길이의 MIN_SAMPLE_MULTIPLE 배에 못 미치면 None. 창 하나뿐이면
    "최악" 이 아니라 "유일" 이다.
    """
    if length <= 0:
        raise ValueError("창 길이는 1 이상이어야 합니다")
    if len(curve) < length * MIN_SAMPLE_MULTIPLE:
        return None

    worst: Window | None = None
    for i in range(len(curve) - length):
        start_d, start_v = curve[i]
        end_d, end_v = curve[i + length]
        if start_v <= 0:
            continue
        ret = end_v / start_v - 1
        if worst is None or ret < worst.return_pct:
            segment = curve[i : i + length + 1]
            mdd, trough_index = _max_drawdown(segment)
            worst = Window(
                length=length,
                start=start_d,
                end=end_d,
                return_pct=ret,
                max_drawdown=mdd,
                recovery_days=_recovery_days(curve, i + trough_index),
            )
    return worst


def _max_drawdown(segment: list[tuple[str, float]]) -> tuple[float, int]:
    """구간 안의 최대 낙폭과 저점 위치. docs/metrics.md 의 정의와 같다."""
    peak = segment[0][1]
    mdd = 0.0
    trough = 0
    for i, (_d, v) in enumerate(segment):
        if v > peak:
            peak = v
        dd = v / peak - 1 if peak > 0 else 0.0
        if dd < mdd:
            mdd = dd
            trough = i
    return mdd, trough


def _recovery_days(curve: list[tuple[str, float]], trough_index: int) -> int | None:
    """저점 이후 이전 고점을 되찾기까지의 거래일. 못 찾으면 None."""
    peak = max(v for _d, v in curve[: trough_index + 1])
    for j in range(trough_index + 1, len(curve)):
        if curve[j][1] >= peak:
            return j - trough_index
    return None


def run(
    weights: dict[int, float],
    prices: dict[int, dict[str, float]],
    dates: list[str],
    *,
    weighting: str = "suggested",
    windows: tuple[int, ...] = WINDOWS,
) -> StressResult:
    """바스켓 하나의 스트레스 결과."""
    curve, used, cash, excluded = basket_curve(weights, prices, dates, min_points=windows[0] * MIN_SAMPLE_MULTIPLE)
    # 상한에 걸려 남은 진짜 현금과, 가격이 없어 뺀 비중을 가른다 — 둘 다 수익 0 으로 계산되지만 뜻이 다르다 (25.823)
    진짜_현금 = max(0.0, 1.0 - sum(w for w in weights.values() if w > 0))
    뺀_비중 = max(0.0, cash - 진짜_현금)

    found: list[Window] = []
    skipped: list[int] = []
    for length in windows:
        w = worst_window(curve, length)
        if w is None:
            skipped.append(length)
        else:
            found.append(w)

    return StressResult(
        curve=curve,
        weights_used=used,
        weighting=weighting,
        cash_weight=진짜_현금,
        excluded=excluded,
        windows=found,
        skipped_windows=skipped,
        # 경고는 항상 붙는다. 수익률보다 먼저 보여준다
        warnings=[WARN_SURVIVORSHIP, WARN_NOT_FORECAST]
        + ([f"시작일까지 가격이 없어 뺀 {len(excluded)}종목의 비중 {뺀_비중:.0%} 는 수익 0 으로 계산했습니다"
            " — 낙폭이 그만큼 작게 보입니다"] if 뺀_비중 > 1e-9 else []),
    )
