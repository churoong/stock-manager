"""매수 신호와 사이징.

규칙의 단일 정의처는 `docs/signals.md` 다. 여기 있는 함수는 그 문서를 코드로
옮긴 것이고, 문서에 없는 규칙은 만들지 않는다.

이 모듈은 **DB 를 모른다.** 숫자를 받아 숫자와 문장을 돌려준다. 같은 입력이면
항상 같은 결과다. 네트워크 없이 손으로 검증할 수 있어야 한다.

지켜야 하는 경계 셋 (CLAUDE.md 절대 규칙)
  규칙 기반이다. LLM 이 그때그때 판단하지 않는다
  근거 문장은 받은 수치만 인용한다. 없는 값은 문장에서 빠진다
  자동 매매는 없다. 여기서 나오는 것은 표시할 정보뿐이다
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from batch.services import insider, scoring

# 이 파일 아래에 `criteria()` 함수가 있다. 모듈을 `criteria` 로 들여오면 함수가 그 이름을
# 가려 호출 때 AttributeError 가 난다. 그래서 쓰는 이름만 직접 들여온다
from batch.services.criteria import (  # noqa: F401 — 옛 이름(`signals.displayable`)으로 부르는 곳이 있다
    CRITERION_REQUIRED_KEYS,
    displayable,
)
from batch.services.criteria import (
    displayable as _displayable,
)
from batch.services.criteria import (
    usable as _usable,
)

CALC_VERSION = 1

HORIZONS = ("short", "mid", "long")
HORIZON_LABEL = {"short": "단기", "mid": "중기", "long": "장기"}

# ----------------------------------------------------------------------
# 상수. 근거는 docs/signals.md 에 있다.
# ----------------------------------------------------------------------

# 단기 신호
MA_SHORT_DAYS = 20
MA_LONG_DAYS = 60
MAX_EXTENSION = 0.10  # 20일선에서 10% 넘게 뜨면 과열로 본다
TURNOVER_MULTIPLE = 1.2
MIN_MOMENTUM_SCORE = 60.0

# 중기 신호
MIN_GROWTH_SCORE = 70.0
MIN_QUALITY_SCORE_MID = 50.0

# 장기 신호 — 밸류에이션 밴드의 숫자는 **전부 여기가 정의처다** (docs/infra.md 25.108).
#
# 넷이 코드 곳곳에 글자로 박혀 있었다. `BAND_ENTRY_PERCENTILE` 은 상수로 있으면서
# **판정에는 안 쓰였고**(규칙이 `band.p30` 을 직접 봤다) 근거표 글 한 줄에만 쓰였다 —
# 바꿔도 동작은 그대로고 화면 글만 바뀐다. 사용자에게 **틀린 문턱을 보여 주는** 자리다.
BAND_DAYS = 750  # 3년 ≈ 750 거래일. jobs/signals·jobs/backtest·services/valuation_band 가 가져다 쓴다
BAND_MIN_SAMPLE = 250  # 1년도 안 되는 계열로 '3년 밴드' 를 말할 수 없다
BAND_YEARS = BAND_DAYS // 250  # 사람에게 보여 줄 기간. 창을 바꾸면 글도 따라 바뀐다
BAND_ENTRY_PERCENTILE = 30  # 이 분위 **이하**면 밴드 하단으로 본다 (docs/signals.md 1.3)
BAND_STOP_PERCENTILE = 20  # 장기 손절선이 되는 분위 (docs/signals.md 2장)
# 2026-09-17 60 → 55. 5년 백테스트(docs/backtest.md 7.1)에서 60 은 보유가 적어 낙폭이 벤치마크보다
# 12%p 나빴고, 55 는 낙폭 -19.2% 로 미리 정한 기준을 통과한 가장 높은 문턱이었다 (사용자 결정)
MIN_QUALITY_SCORE_LONG = 55.0
MIN_VALUE_SCORE_LONG = 55.0

# 권장 매수 구간의 폭 (docs/signals.md 2장, docs/infra.md 25.109).
#
# **근거가 어디에도 없다** (2026-09-22 확인). 처음 짤 때 정한 값이고 백테스트로 재 본 적이
# 없다. 장기 구간은 밴드 분위에서 나오지만 단기·중기는 **이 숫자 셋이 정한다** — 그리고
# 권장 금액과 3회 분할 가격이 이 구간에서 나오므로 **돈에 닿는 자리**다.
# 이름이 없으면 바꿔도 되는지 알 수 없고 문서와 갈라져도 아무도 모른다(CLAUDE.md 기록 규칙).
# `[확인필요: 백테스트로 폭을 정한다. 지금 값은 근거 없는 초깃값이다]`
SHORT_ZONE_HIGH = 1.02  # 단기 상단 = min(현재가, MA20 × 1.02)
SHORT_ZONE_LOW = 0.98  # 단기 하단 = MA20 × 0.98
MID_ZONE_LOW = 0.95  # 중기 하단 = 현재가 × 0.95 (상단은 현재가)

# 사이징. 기준값은 실측으로 조정한다 [확인필요]
TARGET_VOLATILITY = 0.25
TARGET_MDD = 0.35
REDUCTION_FLOOR = 0.3

# 3회 분할 비중. 첫 진입을 가장 크게 둔다.
# 아래로 갈수록 키우면 하락할 때만 계획이 완성된다.
TRANCHE_RATIOS = (0.40, 0.30, 0.30)

# 기간별 목표수익률과 손절선. 설정에서 바뀌면 그 값이 우선한다.
DEFAULT_TARGETS = {
    "short": {"target_pct": 10.0, "stop_pct": -7.0},
    "mid": {"target_pct": 25.0, "stop_pct": -15.0},
    "long": {"target_pct": 50.0, "stop_pct": -25.0},
}

SECTOR_CAP_UNAVAILABLE = "섹터 상한 미적용 (업종 데이터 없음)"


# ----------------------------------------------------------------------
# 기초 계산
# ----------------------------------------------------------------------


def moving_average(values: list[float], days: int) -> float | None:
    """최근 N 개의 평균. 개수가 모자라면 None.

    100일치로 낸 값을 60일 이동평균이라고 부르지 않는다.
    """
    if days <= 0:
        raise ValueError("days 는 1 이상이어야 합니다")
    usable = [v for v in values[-days:] if v is not None]
    if len(usable) < days:
        return None
    return sum(usable) / days


def percentile(values: list[float], q: float) -> float | None:
    """q 분위값(0~100). 선형 보간을 쓴다."""
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q 는 0 과 100 사이여야 합니다")

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = q / 100 * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def percentile_rank(values: list[float], target: float) -> float | None:
    """target 이 분포에서 몇 % 지점인가. 밴드의 어디쯤인지 말할 때 쓴다."""
    if not values:
        return None
    below = sum(1 for v in values if v < target)
    return below / len(values) * 100


# ----------------------------------------------------------------------
# 밸류에이션 밴드
# ----------------------------------------------------------------------


def known_equity_at(
    equities: list[tuple[str, float]] | list[tuple[str, float, int]], as_of: str
) -> float | None:
    """그 날짜에 알 수 있던 자본총계.

    equities 는 (발표일, 자본총계[, 사업연도]) 목록이다(발표일 오름차순). 발표일이 기준일보다 뒤인 재무는
    그때 알 수 없었다. 2026-03-10 에 접수된 보고서를 2026-03-09 의 계산에
    쓰면 미래를 보는 것이고, 백테스트 성적이 실제보다 좋게 나온다.

    **사업연도를 주면 알 수 있던 것 중 가장 늦은 연도**를 고르고, 같은 연도는 늦게 접수된 것(정정)을 쓴다
    (docs/infra.md 25.549, 감사 재현). 접수일만 보면 2026-05 에 정정된 FY2023 이 FY2025(2026-03 접수)를 덮어
    현재 PBR·밴드·백테스트가 몇 해 묵은 자본을 "최신" 으로 썼다.
    """
    row = known_equity_row(equities, as_of)
    return row[1] if row else None


def known_equity_row(
    equities: list[tuple[str, float]] | list[tuple[str, float, int]], as_of: str
) -> tuple[str, float] | tuple[str, float, int] | None:
    """`known_equity_at` 이 고른 행 그대로 (발표일까지 보여 줄 때)."""
    known = [e for e in equities if e[0] <= as_of]
    if not known:
        return None
    if all(len(e) > 2 for e in known):
        return max(known, key=lambda e: (e[2], e[0]))  # type: ignore[misc]
    return known[-1]


def pbr_series(
    prices: list[tuple[str, float]],
    equities: list[tuple[str, float]] | list[tuple[str, float, int]],
    listed_shares: int | None,
) -> list[float]:
    """일별 PBR 계열. 그 시점에 알 수 있던 자본총계만 쓴다.

    prices 와 equities 는 날짜 오름차순이어야 한다.

    **알려진 한계**: 상장주식수는 현재 값 하나뿐이라 과거 시점에도 그 값을
    쓴다. 증자·감자가 있었으면 과거 PBR 이 실제와 어긋난다 [확인필요].
    """
    if not listed_shares or listed_shares <= 0:
        return []

    series: list[float] = []
    for as_of, close in prices:
        equity = known_equity_at(equities, as_of)
        if equity is None or equity <= 0 or close is None or close <= 0:
            continue
        bps = equity / listed_shares
        if bps <= 0:
            continue
        series.append(close / bps)
    return series


@dataclass
class Band:
    """밸류에이션 밴드. 그 종목 자신의 과거와 비교한 값이다."""

    p20: float
    p30: float
    p50: float
    p80: float
    sample: int

    def at(self, percentile: int) -> float:
        """그 분위 값.

        **표에 있는 분위만 준다.** 없는 분위를 물으면 막는다 — 지어내면 문턱을 바꾼 줄
        알았는데 조용히 예전 값으로 도는 일이 생긴다. `valuation_bands` 표의 열도 이 넷이다.
        """
        try:
            return getattr(self, f"p{percentile}")
        except AttributeError:
            raise ValueError(
                f"밴드에 {percentile}% 분위가 없습니다. 20·30·50·80 중에서 고르거나 Band 를 늘리세요"
            ) from None


def build_band(series: list[float], min_sample: int = BAND_MIN_SAMPLE) -> Band | None:
    """밴드를 만든다. 표본이 모자라면 만들지 않는다.

    1년도 안 되는 계열로 '3년 밴드' 를 말할 수 없다 (`BAND_MIN_SAMPLE`).
    """
    if len(series) < min_sample:
        return None
    return Band(
        p20=percentile(series, 20),
        p30=percentile(series, 30),
        p50=percentile(series, 50),
        p80=percentile(series, 80),
        sample=len(series),
    )


# ----------------------------------------------------------------------
# 입력과 출력
# ----------------------------------------------------------------------


@dataclass
class SignalInput:
    """한 종목의 신호 판정 입력. 전부 DB 에서 읽은 값이다."""

    stock_id: int
    ticker: str
    name: str
    market: str
    currency: str = "KRW"
    sector: str | None = None

    # 날짜 오름차순. 마지막이 기준일이다.
    closes: list[float] = field(default_factory=list)
    turnovers: list[float] = field(default_factory=list)

    factor_scores: dict[str, float | None] = field(default_factory=dict)

    revenue_growth: float | None = None
    #: 사업보고서가 묵어 성장률을 비운 까닭 (docs/infra.md 25.660·25.663). 있으면 판정표가 "값 없음" 대신 이것을 말한다
    annual_stale: str | None = None
    operating_income_growth: float | None = None
    fiscal_year: int | None = None
    #: 재무가 연결인가. 연결이 없는 회사는 별도를 쓰고 근거 문장에 밝힌다 (docs/infra.md 25.314)
    consolidated: bool = True

    pbr_now: float | None = None
    band: Band | None = None
    #: `pbr_now` 을 낸 종가(분할만 반영, 미국은 야후 Close).
    #: 장기 구간을 가격으로 되돌릴 때 **같은 종가**를 쓴다 (25.562)
    band_close: float | None = None

    volatility_ann: float | None = None
    mdd: float | None = None
    # 과거 성과 요약 — 1부가 싣는다 (docs/design.md 3.9, docs/infra.md 25.803). 판정에는 쓰지 않는다
    cagr: float | None = None
    sharpe: float | None = None
    metrics_window: str | None = None

    # 내부자 매매 요약 (services/insider.InsiderSummary). 근거표 참고 행에만 쓴다 (docs/signals.md 8장)
    insider: Any = None
    listed_shares: int | None = None

    # 근거표의 "기준일" 열. 값이 언제 기준인지 사용자가 확인할 수 있어야 한다.
    price_date: str | None = None  # 마지막 종가의 거래일
    score_date: str | None = None  # 팩터 점수 계산일
    metrics_date: str | None = None  # 성과 지표 계산일

    @property
    def close(self) -> float | None:
        return self.closes[-1] if self.closes else None


@dataclass
class Tranche:
    step: int
    ratio: float
    price: float
    amount: float | None = None


@dataclass
class Signal:
    stock_id: int
    ticker: str
    name: str
    horizon: str
    signal_type: str
    buy_zone_low: float
    buy_zone_high: float
    tranches: list[Tranche]
    target_price: float
    stop_price: float
    currency: str
    rationale_text: str
    rationale_data: dict = field(default_factory=dict)

    weight_pct: float | None = None
    suggested_amount: float | None = None
    size_reduction: float = 1.0
    sector_cap_applied: bool = False
    sector_cap_note: str | None = None


# ----------------------------------------------------------------------
# 기간별 판정
# ----------------------------------------------------------------------


def _score(inp: SignalInput, name: str) -> float | None:
    value = inp.factor_scores.get(name)
    return None if value is None else float(value)


def short_term(inp: SignalInput) -> tuple[bool, dict]:
    """단기 기술적 신호. 조건을 전부 만족해야 한다.

    돌려주는 dict 에는 판정에 쓴 실제 수치가 들어 있다. 근거 문장이 그것만
    인용하고, 저장해 두면 나중에 검증할 수 있다.
    """
    ma20 = moving_average(inp.closes, MA_SHORT_DAYS)
    ma60 = moving_average(inp.closes, MA_LONG_DAYS)
    close = inp.close
    momentum = _score(inp, "momentum")

    data: dict = {"ma20": ma20, "ma60": ma60, "close": close, "momentum": momentum}

    # 표본이 모자라면 판단하지 않는다.
    if ma20 is None or ma60 is None or close is None:
        return False, data

    turnover_20 = moving_average(inp.turnovers, MA_SHORT_DAYS)
    turnover_60 = moving_average(inp.turnovers, MA_LONG_DAYS)
    data["turnover_20"] = turnover_20
    data["turnover_60"] = turnover_60

    extension = close / ma20 - 1
    data["extension"] = extension

    if turnover_20 is not None and turnover_60 not in (None, 0):
        data["turnover_ratio"] = turnover_20 / turnover_60

    passed = (
        ma20 > ma60
        and close > ma20
        and extension <= MAX_EXTENSION
        and data.get("turnover_ratio") is not None
        and data["turnover_ratio"] > TURNOVER_MULTIPLE
        and momentum is not None
        and momentum >= MIN_MOMENTUM_SCORE
    )
    return passed, data


def mid_term(inp: SignalInput) -> tuple[bool, dict]:
    """중기 실적 모멘텀. 이익이 늘지 않는 매출 증가는 신호가 아니다."""
    growth = _score(inp, "growth")
    quality = _score(inp, "quality")
    data = {
        "growth": growth,
        "quality": quality,
        "revenue_growth": inp.revenue_growth,
        "operating_income_growth": inp.operating_income_growth,
        "fiscal_year": inp.fiscal_year,
    }

    passed = (
        growth is not None
        and growth >= MIN_GROWTH_SCORE
        and quality is not None
        and quality >= MIN_QUALITY_SCORE_MID
        and inp.revenue_growth is not None
        and inp.revenue_growth > 0
        and inp.operating_income_growth is not None
        and inp.operating_income_growth > 0
    )
    return passed, data


def long_term(inp: SignalInput) -> tuple[bool, dict]:
    """장기 밸류에이션 밴드 + 퀄리티.

    업종 평균이 아니라 그 종목 자신의 과거와 비교한다.
    """
    quality = _score(inp, "quality")
    value = _score(inp, "value")
    data: dict = {"quality": quality, "value": value, "pbr": inp.pbr_now}

    if inp.band is None or inp.pbr_now is None:
        return False, data

    data["band_p20"] = inp.band.p20
    data["band_p30"] = inp.band.p30
    data["band_p50"] = inp.band.p50
    data["band_sample"] = inp.band.sample

    passed = (
        inp.pbr_now <= inp.band.at(BAND_ENTRY_PERCENTILE)
        and quality is not None
        and quality >= MIN_QUALITY_SCORE_LONG
        and value is not None
        and value >= MIN_VALUE_SCORE_LONG
    )
    return passed, data


RULES = {"short": short_term, "mid": mid_term, "long": long_term}

SIGNAL_TYPE = {
    "short": "기술적 추세",
    "mid": "실적 모멘텀",
    "long": "밸류에이션 밴드",
}


# ----------------------------------------------------------------------
# 매수 구간과 분할
# ----------------------------------------------------------------------


def buy_zone(
    horizon: str, inp: SignalInput, data: dict
) -> tuple[float, float] | None:
    """권장 매수 구간 (하단, 상단). 뒤집히면 None 을 돌려준다."""
    close = inp.close
    if close is None or close <= 0:
        return None

    if horizon == "short":
        ma20 = data.get("ma20")
        if ma20 is None:
            return None
        high = min(close, ma20 * SHORT_ZONE_HIGH)
        low = ma20 * SHORT_ZONE_LOW
    elif horizon == "mid":
        high = close
        low = close * MID_ZONE_LOW
    else:
        # 진입·손절 분위를 **상수에서** 고른다. `band_p30`·`band_p20` 을 글자로 집으면
        # 문턱을 바꿔도 여기만 안 따라온다 (docs/infra.md 25.108)
        pbr = inp.pbr_now
        if inp.band is None or not pbr or pbr <= 0:
            return None
        band_entry = inp.band.at(BAND_ENTRY_PERCENTILE)
        band_stop = inp.band.at(BAND_STOP_PERCENTILE)
        # PBR 을 가격으로 되돌린다. 현재가 / 현재 PBR 이 주당순자산이다.
        # **PBR 을 낸 종가와 같은 종가로** 되돌린다 (docs/infra.md 25.562, 감사 재현). 25.521 이 현재 PBR 을 분할만
        # 반영한 종가로 바꿨는데 여기는 수정종가(미국은 배당까지)를 그대로 써, 두 종가가 다른 날 구간·목표·손절·분할이
        # 모두 그 차이(예 3%)만큼 낮게 나왔고 장중 "구간 진입" 알림(원 시세와 비교)이 늦게 울렸다
        기준가 = inp.band_close if inp.band_close and inp.band_close > 0 else close
        bps = 기준가 / pbr
        high = min(기준가, band_entry * bps)
        low = band_stop * bps

    if low <= 0 or high <= 0 or high < low:
        # 뒤집힌 구간을 보여주지 않는다. 신호를 버린다.
        return None
    return low, high


#: 사업보고서가 이보다 오래되면 중기 성장률을 쓰지 않는다 (25.660). 정의처는 `services/scoring.STALE_ANNUAL_DAYS`
#: (25.804)
STALE_ANNUAL_DAYS = scoring.STALE_ANNUAL_DAYS
annual_stale_reason = scoring.annual_stale_reason

#: 국내 호가 단위 (2023-01-25 코스피·코스닥 통일). (이 가격 미만, 단위) [확인필요: 거래소 공지와 연 1회 대조]
KR_TICKS: tuple[tuple[float, float], ...] = (
    (2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500), (float("inf"), 1_000),
)


def tick_size(price: float, currency: str) -> float | None:
    """그 가격의 호가 단위 (docs/signals.md 2장, docs/infra.md 25.657). 모르는 통화는 None(반올림하지 않음).

    예전에는 분할 2차(구간 중앙)·목표·손절이 73,260원 같은 값으로 저장·표시됐다 — 5만~20만원 호가는 100원이라
    그대로는 주문할 수 없다. 미국은 1달러 이상 1센트, 미만 0.0001달러(Reg NMS 612).
    """
    if currency == "KRW":
        return next(단위 for 한계, 단위 in KR_TICKS if price < 한계)
    if currency == "USD":
        return 0.01 if price >= 1 else 0.0001
    return None


def to_tick(price: float, currency: str, mode: str = "nearest") -> float:
    """호가 단위로 맞춘다. `up`·`down`·`nearest`. 단위를 모르면 그대로."""
    단위 = tick_size(price, currency)
    if not 단위:
        return price
    몫 = price / 단위
    # nearest 는 반값을 올린다 — 파이썬 `round` 는 짝수 쪽이라 73,250 이 73,200 이 됐다 (25.661, 교차검증)
    if mode == "up":
        칸 = math.ceil(몫 - 1e-9)
    elif mode == "down":
        칸 = math.floor(몫 + 1e-9)
    else:
        칸 = math.floor(몫 + 0.5 + 1e-9)
    return round(칸 * 단위, 4)


def tranche_plan(low: float, high: float, currency: str | None = None) -> list[Tranche]:
    """3회 분할. 비중의 합은 정확히 1.0 이다. 통화를 주면 가운데 가격도 호가 단위로 (25.657)."""
    middle = (low + high) / 2
    if currency:
        middle = min(high, max(low, to_tick(middle, currency)))
    prices = (high, middle, low)
    return [
        Tranche(step=i + 1, ratio=ratio, price=price)
        for i, (ratio, price) in enumerate(zip(TRANCHE_RATIOS, prices, strict=True))
    ]


def target_and_stop(
    horizon: str, low: float, high: float, targets: dict | None = None
) -> tuple[float, float]:
    """목표가와 손절가. 기준은 **매수 구간의 중앙**이다.

    현재가로 잡으면 실제로 살 값과 어긋난다.
    """
    config = (targets or DEFAULT_TARGETS).get(horizon, DEFAULT_TARGETS[horizon])
    base = (low + high) / 2
    return (
        base * (1 + config["target_pct"] / 100),
        base * (1 + config["stop_pct"] / 100),
    )


# ----------------------------------------------------------------------
# 사이징
# ----------------------------------------------------------------------


def size_reduction(
    volatility_ann: float | None, mdd: float | None
) -> tuple[float, dict]:
    """비중 축소 배수와 그 근거.

    **모르는 것을 위험하다고 단정하지 않는다.** 지표가 없으면 축소하지 않고
    그 사실을 남긴다.
    """
    reasons: dict = {}
    factors: list[float] = []

    # NaN·inf 는 모름이다 (25.687, 감사 재현) — `nan != 0` 이 참이라 MDD NaN 이 배수로 들어가 `round(nan)` 에서
    # 신호 배치가 멈췄고, 근거표에 "MDD nan%" 가 찍혀 웹의 JSON.parse 가 읽지 못하는 NaN 이 저장됐다
    if volatility_ann is not None and math.isfinite(volatility_ann) and volatility_ann > 0:
        ratio = min(max(TARGET_VOLATILITY / volatility_ann, REDUCTION_FLOOR), 1.0)
        reasons["volatility_ann"] = volatility_ann
        reasons["volatility_factor"] = ratio
        factors.append(ratio)

    if mdd is not None and math.isfinite(mdd) and mdd != 0:
        ratio = min(max(TARGET_MDD / abs(mdd), REDUCTION_FLOOR), 1.0)
        reasons["mdd"] = mdd
        reasons["mdd_factor"] = ratio
        factors.append(ratio)

    if not factors:
        reasons["note"] = "변동성·MDD 를 몰라 비중을 줄이지 않았습니다"
        return 1.0, reasons

    return min(factors), reasons


def reduction_note(reduction: float, 종목_배수: float, reasons: dict, regime_factor: float) -> str | None:
    """비중을 줄인 까닭 한 줄 — **실제로 적용된 요인만** 적는다 (docs/infra.md 25.624).

    배수는 min(변동성, 낙폭) × 국면이다. 예전에는 웹이 `(1 − 배수) × 100` 을 계산하고(웹은 계산하지 않는다)
    1 미만인 요인을 모두 까닭으로 적어, min 에서 진 요인(적용 안 됨)까지 "때문에" 라고 했다. 줄이지 않았으면 None.
    """
    # 반올림해 1% 가 안 되면 줄였다고 적지 않는다 (25.687) — 배수 0.995~0.999 가 "비중을 0% 줄였습니다" 로 나갔다
    if reduction >= 0.999 or round((1 - reduction) * 100) < 1:
        return None
    까닭: list[str] = []
    if 종목_배수 < 0.999:
        # min 을 만든 쪽만 — 둘이 같으면 둘 다
        if abs(reasons.get("volatility_factor", 2.0) - 종목_배수) < 1e-9:
            까닭.append("변동성")
        if abs(reasons.get("mdd_factor", 2.0) - 종목_배수) < 1e-9:
            까닭.append("낙폭")
    if regime_factor < 0.999:
        까닭.append("시장 약세(지수 200일선 아래)")
    앞 = f"{'·'.join(까닭)} 때문에" if 까닭 else "위험 기준에 따라"
    return f"{앞} 비중을 {round((1 - reduction) * 100)}% 줄였습니다 (배수 ×{reduction:.2f})"


def size_weight(
    max_weight_per_stock: float,
    volatility_ann: float | None,
    mdd: float | None,
    regime_factor: float = 1.0,
    regime_data: dict | None = None,
) -> tuple[float, float, dict]:
    """(비중, 축소 배수, 근거). 비중은 절대 상한을 넘지 않는다.

    regime_factor 는 시장 추세 필터의 배수(docs/signals.md 3.5). 종목 위험 축소와 **곱한다**.
    regime_data 는 그 근거(지수·200일선·배수)이고 근거표가 그대로 그린다.
    """
    if max_weight_per_stock < 0:
        raise ValueError("비중 상한은 0 이상이어야 합니다")
    if not 0.0 <= regime_factor <= 1.0:
        raise ValueError("국면 배수는 0~1 이어야 합니다")

    reduction, reasons = size_reduction(volatility_ann, mdd)
    종목_배수 = reduction
    reduction *= regime_factor
    if regime_data:
        reasons.update(regime_data)
    reasons["reduction_note"] = reduction_note(reduction, 종목_배수, reasons, regime_factor)
    weight = max_weight_per_stock * reduction
    # 축소 배수가 1.0 을 넘을 수 없으므로 상한을 넘을 수 없다. 그래도 막아 둔다.
    return min(weight, max_weight_per_stock), reduction, reasons


# ----------------------------------------------------------------------
# 근거표 — 사용자가 확인할 수 있어야 한다
# ----------------------------------------------------------------------
#
# CLAUDE.md 절대 규칙 (2026-09-16 사용자 확정): 모든 추천은 왜 추천했는지가
# 분명해야 하고, 근거의 모든 수치는 어느 행에서 왔는지·언제 기준인지·출처가
# 무엇인지를 화면에서 펼쳐 볼 수 있어야 한다.
#
# 근거표는 **여기서** 만든다. 웹이 아니라 배치가 만드는 이유는 두 가지다.
#   문턱값의 정의처가 이 파일 하나여야 한다. 웹에 복사하면 둘이 어긋난다
#   웹은 계산하지 않는다는 규칙이 있다. 표를 그리기만 해야 한다
#
# 표의 한 행은 "이 기준을 이 값이 이렇게 통과했다" 다. 통과하지 못한 기준은
# 신호가 나지 않으므로 표에 없다. 다만 사이징(비중 축소)은 통과·실패가 아니라
# 얼마나 줄였는지이므로 passed 대신 그 사실을 적는다.

SOURCE_PRICES = "prices (시세)"
SOURCE_SCORES = "scores (팩터 점수)"
SOURCE_FINANCIALS = "financials (연간 재무)"
SOURCE_METRICS = "performance_metrics (성과 지표)"
SOURCE_DERIVED = "계산 (docs/signals.md)"


def performance_data(inp: SignalInput) -> dict:
    """1부의 "과거 성과 요약" — **신호를 낼 때 읽은 성과 행 그대로**.

    docs/design.md 3.9, docs/infra.md 25.803, 추천 감사 #3. 텔레그램 1부는 창·기준일과 함께 CAGR·MDD·샤프를
    싣는데 웹 1부(추천 화면)에는 없었다 — 같은 1부가 두 곳에서 달랐다. 웹이 창을 고르거나 다시 읽지 않게
    (웹은 계산하지 않는다) 배치가 쓴 행을 근거 데이터에 싣는다. 값이 하나도 없으면 싣지 않는다.
    """
    if inp.cagr is None and inp.mdd is None and inp.sharpe is None:
        return {}
    return {"performance": {
        "window": inp.metrics_window, "as_of": inp.metrics_date, "cagr": inp.cagr, "mdd": inp.mdd,
        "sharpe": inp.sharpe, "source": SOURCE_METRICS,
    }}  # fmt: skip


def verifiable(signal: Signal) -> bool:
    """**확인할 수 있는 추천인가.**

    CLAUDE.md 절대 규칙: "근거표를 만들 수 없는 추천은 표시하지 않는다"
    (2026-09-16 사용자 확정).

    화면은 근거표가 비면 표만 안 그리고 **추천 카드는 그대로 그린다.** 그러면 사용자가
    확인할 수 없는 추천이 확인할 수 있는 추천과 똑같은 모습으로 섞인다. 화면에서 막는
    길도 있지만, 근거가 없는 추천은 **애초에 내보내지 않는 쪽**이 맞다 — 화면이 넷이고
    (종목·ETF·위성·적립) 하나라도 빠뜨리면 규칙이 새기 때문이다.

    **판정 자체는 `services/criteria.py` 에 있다.** 넷이 같은 판정을 써야 하는데
    여기 두면 나머지 셋이 종목 신호 모듈을 import 하게 된다(docs/infra.md 25.174).
    """
    rows = signal.rationale_data.get("criteria") if signal.rationale_data else None
    # **판정 행이 있어야 한다** (docs/infra.md 25.620, 감사). 예전엔 화면에 가는 행이 아무거나 한 줄이면 통과했는데,
    # 모든 신호에 참고 행(목표·손절·권장 금액·매수 구간·비중)이 붙어 판정 근거가 0줄이어도 늘 통과했다.
    # 참고 행은 `passed=None` 이다 — "통과" 인 판정 행이 하나는 있어야 왜 추천했는지 확인할 수 있다
    return _usable(rows) and any(_displayable(r) and r.get("passed") is True for r in rows or [])


def _criterion(
    label: str,
    display: str,
    threshold: str,
    source: str,
    as_of: str | None,
    passed: bool | None = True,
) -> dict:
    return {
        "label": label,
        "display": display,
        "threshold": threshold,
        "source": source,
        "as_of": as_of,
        "passed": passed,
    }


def _문턱_자릿수(value: float, threshold: float, base: int, 점수: bool = False) -> int:
    """값을 몇 자리로 적을까 — 반올림한 값이 문턱과 같아 보이면 자릿수를 늘린다 (docs/infra.md 25.630, 감사).

    모멘텀 59.6 을 "60점 / ≥ 60점 / 탈락" 으로, 이격도 10.04% 를 "10.0% / ≤ 10% / 탈락" 으로 적어 판정과 표시가 어긋나
    보였다. 값이 문턱과 다른데 반올림으로 같아지면 다를 때까지(최대 base+3) 늘린다.

    **실제로 찍힐 글자끼리** 견준다 (25.801, 교차검증). 25.799 는 비교만 .5 올림으로 바꿔, 파이썬 서식
    (짝수 쪽)으로 찍는 배수·이격·증가율에서 1.25 가 "문턱 1.2 와 다르다" 로 판정돼 "1.2배 / > 1.2배 / 통과"
    가 됐다. 점수의 정수 표시만 .5 올림(`_점수`)이다
    """

    def 글(x: float, d: int) -> str:
        return str(math.floor(x + 0.5)) if 점수 and d == 0 else f"{x:.{d}f}"

    digits = base
    while digits < base + 3 and value != threshold and 글(value, digits) == 글(threshold, digits):
        digits += 1
    return digits


def _증가율(value: float | None) -> str | None:
    """"> 0%" 문턱과 견주는 증가율 — 0.03% 가 "0.0%" 로 보여 통과가 모순돼 보이지 않게 (25.796, 감사)."""
    return None if value is None else _fmt_pct(value, _문턱_자릿수(value * 100, 0.0, 1))


def _이격(value: float | None) -> str | None:
    """20일선 이격 — "≤ 10%" 문턱과 같아 보이지 않게 (25.630·25.796)."""
    return None if value is None else _fmt_pct(value, _문턱_자릿수(value * 100, MAX_EXTENSION * 100, 1))


def _점수(value: float, threshold: float | None = None) -> str:
    """점수 표시 — 문턱과 같아 보이면 자릿수를 늘리고(25.630), 정수로 적을 때는 **.5 를 올린다**.

    docs/infra.md 25.796, 감사.

    파이썬 서식은 .5 를 짝수 쪽으로 보내(72.5 → "72") 같은 카드의 팩터 줄(웹 `toFixed(0)`, 올림 → "73")과 1점 달랐다.
    점수는 소수 1자리로 저장되므로 x.5 가 흔하다.
    """
    digits = 0 if threshold is None else _문턱_자릿수(value, threshold, 0, 점수=True)
    if digits == 0:
        return f"{math.floor(value + 0.5)}점"
    return f"{value:.{digits}f}점"


def _check(
    label: str, value: float | None, ok: bool, display: str | None, threshold: str, source: str, as_of: str | None,
) -> dict:
    """판정표 한 행. 값이 없으면 탈락이고 그 사실을 적는다 — "모름" 은 통과가 아니다."""
    if value is None:
        return _criterion(label, "값 없음", threshold, source, as_of, passed=False)
    return _criterion(label, display or "", threshold, source, as_of, passed=ok)


def judgement(horizon: str, inp: SignalInput, data: dict) -> list[dict]:
    """기준마다 통과·탈락을 적은 판정표 (docs/signals.md 9장). 신호가 안 난 종목에도 만든다.

    RULES[horizon] 과 같은 조건을 같은 문턱으로 본다. 전부 통과 ⇔ 신호. 테스트가 이 동치를 고정한다.
    criteria() 는 신호가 난 뒤의 근거표라 값이 없는 행을 만들지 않지만, 여기서는 값이 없는 것도
    탈락 사유이므로 "값 없음" 행을 남긴다.
    """
    rows: list[dict] = []
    unit = _unit(inp)

    def score_row(label: str, value: float | None, minimum: float) -> dict:
        display = None if value is None else _점수(value, minimum)
        return _check(label, value, value is not None and value >= minimum, display,
                      f"≥ {minimum:.0f}점", SOURCE_SCORES, inp.score_date)

    if horizon == "short":
        ma20, ma60, close = data.get("ma20"), data.get("ma60"), data.get("close")
        if ma20 is None or ma60 is None or close is None:
            return [_criterion(
                "표본", f"거래일 {len(inp.closes)}일 (60일 필요)", "MA20·MA60 을 낼 수 있어야",
                SOURCE_PRICES, inp.price_date, passed=False,
            )]
        ext = data.get("extension")
        ratio = data.get("turnover_ratio")
        px = (SOURCE_PRICES, inp.price_date)
        rows.append(_check("정배열", ma20, ma20 > ma60,
                           f"20일선 {_가격(ma20, inp)}{unit} vs 60일선 {_가격(ma60, inp)}{unit}", "MA20 > MA60", *px))
        rows.append(_check("추세 위", close, close > ma20,
                           f"종가 {_가격(close, inp)}{unit} vs 20일선 {_가격(ma20, inp)}{unit}", "close > MA20", *px))
        rows.append(_check("과열 아님", ext, ext is not None and ext <= MAX_EXTENSION,
                           None if ext is None else
                           f"20일선에서 {_fmt_pct(ext, _문턱_자릿수(ext * 100, MAX_EXTENSION * 100, 1))}",
                           f"≤ {MAX_EXTENSION * 100:.0f}%", *px))
        rows.append(_check("수급 유입", ratio, ratio is not None and ratio > TURNOVER_MULTIPLE,
                           None if ratio is None else
                           f"20일 거래대금이 60일 평균의 {ratio:.{_문턱_자릿수(ratio, TURNOVER_MULTIPLE, 2)}f}배",
                           f"> {TURNOVER_MULTIPLE}배", *px))
        rows.append(score_row("모멘텀 점수", data.get("momentum"), MIN_MOMENTUM_SCORE))
    elif horizon == "mid":
        fy = f"{inp.fiscal_year} {_annual_report(inp)}" if inp.fiscal_year else None
        rg, og = inp.revenue_growth, inp.operating_income_growth
        rows.append(score_row("성장 점수", data.get("growth"), MIN_GROWTH_SCORE))
        rows.append(score_row("퀄리티 점수", data.get("quality"), MIN_QUALITY_SCORE_MID))
        if inp.annual_stale:
            # 해당 연도 값이 있는데 "값 없음" 으로만 보여 까닭을 알 수 없었다 (25.663, 교차검증)
            문턱 = f"접수 {STALE_ANNUAL_DAYS}일 이내·작년 회계연도"
            rows.append(_criterion("사업보고서 신선도", inp.annual_stale, 문턱, SOURCE_FINANCIALS, fy, passed=False))
        rows.append(_check("매출 증가", rg, rg is not None and rg > 0, f"전년 대비 {_증가율(rg)}",
                           "> 0%", SOURCE_FINANCIALS, fy))
        rows.append(_check("영업이익 증가", og, og is not None and og > 0, f"전년 대비 {_증가율(og)}",
                           "> 0%", SOURCE_FINANCIALS, fy))
    else:
        if inp.band is None or inp.pbr_now is None:
            rows.append(_criterion(
                "밸류에이션 밴드",
                f"밴드({BAND_YEARS}년 PBR 표본 {BAND_MIN_SAMPLE}일) 또는 현재 PBR 없음",
                "밴드와 PBR 이 있어야", SOURCE_DERIVED, inp.price_date, passed=False,
            ))
        else:
            문턱 = inp.band.at(BAND_ENTRY_PERCENTILE)
            rows.append(_check(
                "밸류에이션 밴드 하단", inp.pbr_now, inp.pbr_now <= 문턱,
                f"PBR {inp.pbr_now:.2f}배 vs {BAND_YEARS}년 {BAND_ENTRY_PERCENTILE}% 분위 {문턱:.2f}배",
                f"PBR ≤ 밴드 {BAND_ENTRY_PERCENTILE}% 분위", SOURCE_DERIVED, inp.price_date,
            ))
            # **손절 분위 아래면 매수 구간이 뒤집혀 신호를 버린다** (`buy_zone`). 판정표에 그 행이 없어 "전부 통과" 인데
            # 신호가 없었다 — 가장 싼 종목이 왜 빠졌는지 설명하지 못했다 (docs/infra.md 25.522, 감사)
            바닥 = inp.band.at(BAND_STOP_PERCENTILE)
            # 통과 여부는 **구간 계산 그 자체**로 본다 (25.531, 교차검증) — `pbr ≥ 바닥` 으로 따로 비교하면
            # 경계(같은 값)에서 부동소수점 때문에 표는 통과인데 구간은 뒤집혀 신호가 없었다
            구간_됨 = buy_zone("long", inp, data) is not None
            rows.append(_check(
                "손절 분위 위", inp.pbr_now, 구간_됨,
                f"PBR {inp.pbr_now:.2f}배 vs {BAND_YEARS}년 {BAND_STOP_PERCENTILE}% 분위 {바닥:.2f}배"
                + ("" if 구간_됨 else " — 이미 손절선 아래라 매수 구간을 만들 수 없다"),
                f"PBR ≥ 밴드 {BAND_STOP_PERCENTILE}% 분위", SOURCE_DERIVED, inp.price_date,
            ))
        rows.append(score_row("퀄리티 점수", data.get("quality"), MIN_QUALITY_SCORE_LONG))
        rows.append(score_row("밸류 점수", data.get("value"), MIN_VALUE_SCORE_LONG))
    return rows


def price_levels(inp: SignalInput) -> dict[str, list[dict]]:
    """판정표 기준 가운데 **가격으로 풀 수 있는 것** (docs/analysis.md 12.2, 25.1038). 기간 → [{label, price, need}].

    "다른 것이 그대로일 때 종가가 이 값을 넘으면(need=above)·밑돌면(need=below) 그 기준이 충족된다."
    오늘 계열 그대로 푼다 — 내일은 이동평균 창이 한 칸 밀려 조금 달라진다. 점수·재무 기준은 가격으로 풀 수 없어 없다.
    새 문턱이 아니다 — 판정표의 문턱 그대로다.
    """
    out: dict[str, list[dict]] = {h: [] for h in HORIZONS}
    ma20 = moving_average(inp.closes, MA_SHORT_DAYS)
    if ma20:
        out["short"].append({"label": "단기: 추세 위 (20일선)", "price": ma20, "need": "above"})
        out["short"].append({"label": f"단기: 과열 아님 (20일선 +{MAX_EXTENSION * 100:.0f}%)",
                             "price": ma20 * (1 + MAX_EXTENSION), "need": "below"})  # fmt: skip
    if inp.band is not None and inp.pbr_now and inp.pbr_now > 0:
        기준가 = inp.band_close if inp.band_close and inp.band_close > 0 else inp.close
        if 기준가:
            out["long"].append({"label": f"장기: 밸류에이션 밴드 하단 (PBR {BAND_ENTRY_PERCENTILE}% 분위)",
                                "price": inp.band.at(BAND_ENTRY_PERCENTILE) * 기준가 / inp.pbr_now, "need": "below"})
    return out


def judgements(inp: SignalInput) -> dict[str, tuple[bool, list[dict]]]:
    """기간마다 (신호 여부, 판정표). 신호 여부는 RULES 가 낸 것 그대로다."""
    out: dict[str, tuple[bool, list[dict]]] = {}
    for horizon in HORIZONS:
        passed, data = RULES[horizon](inp)
        표 = judgement(horizon, inp, data)
        # 판정표에 떨어진 행이 있으면 신호 여부도 떨어진다 — 규칙은 통과해도 구간이 뒤집히면 신호가 없다 (25.522)
        out[horizon] = (passed and all(r.get("passed") is not False for r in 표), 표)
    return out


def criteria(horizon: str, inp: SignalInput, data: dict) -> list[dict]:
    """근거표. 신호가 난 뒤에만 부르므로 모든 조건이 통과한 상태다.

    값이 없는 기준은 행을 만들지 않는다. 표에 "-" 를 넣어 채우면 확인이 아니라
    장식이 된다.
    """
    rows: list[dict] = []
    unit = _unit(inp)

    if horizon == "short":
        ma20, ma60, close = data.get("ma20"), data.get("ma60"), data.get("close")
        if ma20 is not None and ma60 is not None:
            rows.append(_criterion(
                "정배열", f"20일선 {_가격(ma20, inp)}{unit} > 60일선 {_가격(ma60, inp)}{unit}",
                "MA20 > MA60", SOURCE_PRICES, inp.price_date,
            ))
        if close is not None and ma20 is not None:
            rows.append(_criterion(
                "추세 위", f"종가 {_가격(close, inp)}{unit} > 20일선 {_가격(ma20, inp)}{unit}",
                "close > MA20", SOURCE_PRICES, inp.price_date,
            ))
            rows.append(_criterion(
                "과열 아님", f"20일선에서 {_이격(data.get('extension'))} 위",
                f"≤ {MAX_EXTENSION * 100:.0f}%", SOURCE_PRICES, inp.price_date,
            ))
        ratio = data.get("turnover_ratio")
        if ratio is not None:
            rows.append(_criterion(
                # 문턱과 같아 보이면 자릿수를 늘린다 — 판정표(judgement)에만 있던 규칙을 근거표에도 (25.796, 감사)
                "수급 유입", f"20일 거래대금이 60일 평균의 {ratio:.{_문턱_자릿수(ratio, TURNOVER_MULTIPLE, 2)}f}배",
                f"> {TURNOVER_MULTIPLE}배", SOURCE_PRICES, inp.price_date,
            ))
        if data.get("momentum") is not None:
            rows.append(_criterion(
                "모멘텀 점수", _점수(data['momentum'], MIN_MOMENTUM_SCORE),
                f"≥ {MIN_MOMENTUM_SCORE:.0f}점", SOURCE_SCORES, inp.score_date,
            ))

    elif horizon == "mid":
        fy = f"{inp.fiscal_year} {_annual_report(inp)}" if inp.fiscal_year else None
        if data.get("growth") is not None:
            rows.append(_criterion(
                "성장 점수", _점수(data['growth'], MIN_GROWTH_SCORE),
                f"≥ {MIN_GROWTH_SCORE:.0f}점", SOURCE_SCORES, inp.score_date,
            ))
        if data.get("quality") is not None:
            rows.append(_criterion(
                "퀄리티 점수", _점수(data['quality'], MIN_QUALITY_SCORE_MID),
                f"≥ {MIN_QUALITY_SCORE_MID:.0f}점", SOURCE_SCORES, inp.score_date,
            ))
        if inp.revenue_growth is not None:
            rows.append(_criterion(
                "매출 증가", f"전년 대비 {_증가율(inp.revenue_growth)}",
                "> 0%", SOURCE_FINANCIALS, fy,
            ))
        if inp.operating_income_growth is not None:
            rows.append(_criterion(
                "영업이익 증가", f"전년 대비 {_증가율(inp.operating_income_growth)}",
                "> 0%", SOURCE_FINANCIALS, fy,
            ))

    else:
        if inp.pbr_now is not None and inp.band is not None:
            rows.append(_criterion(
                "밸류에이션 밴드 하단",
                f"PBR {inp.pbr_now:.2f}배 ({BAND_YEARS}년 {BAND_ENTRY_PERCENTILE}% 분위 "
                f"{inp.band.at(BAND_ENTRY_PERCENTILE):.2f}배, 표본 {data.get('band_sample', 0)}일)",
                f"≤ 밴드 {BAND_ENTRY_PERCENTILE}% 분위", SOURCE_DERIVED, inp.price_date,
            ))
        if data.get("quality") is not None:
            rows.append(_criterion(
                "퀄리티 점수", _점수(data['quality'], MIN_QUALITY_SCORE_LONG),
                f"≥ {MIN_QUALITY_SCORE_LONG:.0f}점", SOURCE_SCORES, inp.score_date,
            ))
        if data.get("value") is not None:
            rows.append(_criterion(
                "밸류 점수", _점수(data['value'], MIN_VALUE_SCORE_LONG),
                f"≥ {MIN_VALUE_SCORE_LONG:.0f}점", SOURCE_SCORES, inp.score_date,
            ))

    return rows


def sizing_criteria(inp: SignalInput, size_data: dict) -> list[dict]:
    """비중 축소의 근거. "왜 금액이 작은가" 에 답한다.

    통과·실패가 아니라 얼마나 줄였는지이므로 passed 는 뜻이 없다. 줄이지
    않았으면 그 사실을 한 행으로 남긴다. 모르는 것을 위험하다고 단정하지
    않았다는 것도 확인할 수 있어야 한다.
    """
    rows: list[dict] = []
    # **어느 창의 값인지** 적는다 (docs/infra.md 25.939, 감사). 성과 지표는 날마다 1Y·3Y·5Y 세 행이고 신호는 3Y(없으면
    # 1Y)를 쓴다 —
    # 종목 상세는 세 창을 나란히 보여 주는데 근거는 날짜와 출처만 말해, 어느 칸에서 온 숫자인지 가를 수 없었다
    창 = f"({inp.metrics_window})" if inp.metrics_window else ""
    if "volatility_factor" in size_data:
        # 배수가 반올림해 ×1.00 이면 "축소" 라 부르지 않는다 (25.688, 감사) — "변동성 축소 … ×1.00" 은
        # 줄인 것처럼 읽혔다
        rows.append(_criterion(
            "변동성 축소" if size_data["volatility_factor"] < 0.995 else "변동성 (축소 없음)",
            f"연환산 변동성{창} {_fmt_pct(size_data['volatility_ann'])} → "
            f"비중 ×{size_data['volatility_factor']:.2f}",
            f"기준 {TARGET_VOLATILITY * 100:.0f}%, 하한 ×{REDUCTION_FLOOR}",
            SOURCE_METRICS, inp.metrics_date, passed=None,
        ))
    if "mdd_factor" in size_data:
        rows.append(_criterion(
            "낙폭 축소" if size_data["mdd_factor"] < 0.995 else "낙폭 (축소 없음)",
            f"MDD{창} {_fmt_pct(size_data['mdd'])} → 비중 ×{size_data['mdd_factor']:.2f}",
            f"기준 {TARGET_MDD * 100:.0f}%, 하한 ×{REDUCTION_FLOOR}",
            SOURCE_METRICS, inp.metrics_date, passed=None,
        ))
    if not rows:
        rows.append(_criterion(
            "비중 축소 없음", "변동성·MDD 를 몰라 줄이지 않았습니다",
            "지표가 있을 때만 축소", SOURCE_METRICS, None, passed=None,
        ))
    rows.extend(regime_criteria(size_data))
    return rows


def regime_criteria(size_data: dict) -> list[dict]:
    """시장 추세 필터의 근거 행 (docs/signals.md 3.5). 필터가 꺼져 있으면 행이 없다."""
    if "regime_factor" not in size_data:
        return []
    regime = size_data.get("regime") or {}
    source = f"index_prices ({regime.get('source') or 'yfinance'})"
    if not regime or regime.get("state") == "unknown":
        return [_criterion(
            "시장 국면 미판정",
            size_data.get("regime_note") or "국면을 몰라 줄이지 않았습니다",
            f"지수 {SMA_LABEL} 미만이면 배수 적용. 모르면 ×1.00",
            source, regime.get("date"), passed=None,
        )]
    label = "약세" if regime["state"] == "bear" else "강세"
    sign = "<" if regime["state"] == "bear" else "≥"
    # 머리 줄(`Regime.describe`)과 같은 자리 수 (25.688, 감사) — `:,.0f` 고정이라
    # "2,640 < 200일선 2,640" 이 모순으로 나갔다
    from batch.services.trend import 가르는_자리

    자리 = 가르는_자리(float(regime["close"]), float(regime["sma"]))
    return [_criterion(
        "시장 국면",
        f"{regime['index_code']} {regime['close']:,.{자리}f} {sign} {SMA_LABEL} {regime['sma']:,.{자리}f}"
        f" ({label}) → 비중 ×{size_data['regime_factor']:.2f}",
        f"약세면 ×{size_data.get('bear_factor', size_data['regime_factor']):.2f} (설정), 강세면 ×1.00",
        source, regime.get("date"), passed=None,
    )]


SMA_LABEL = "200일선"


def fx_criteria(fx: dict | None) -> list[dict]:
    """권장 금액을 어느 환율로 달러로 바꿨는지. 원화 종목이면 행이 없다."""
    if not fx:
        return []
    return [
        _criterion(
            "환율 (참고)",
            f"{fx['rate']:,.2f}원/달러",
            "권장 금액 = 원화 설정값 ÷ 환율 (docs/signals.md 3.4)",
            f"fx_rates ({fx['source']} KRW=X)",
            fx["date"],
            passed=None,
        )
    ]


SOURCE_SETTINGS = "settings (설정)"


def plan_criteria(
    inp: SignalInput,
    horizon: str,
    low: float,
    high: float,
    target_price: float,
    stop_price: float,
    targets: dict | None,
    amount: float | None,
    total_investable: float,
    weight: float,
    min_order_amount: float = 0.0,
    fx_missing: bool = False,
    data: dict | None = None,
) -> list[dict]:
    """목표가·손절가·권장 금액·**매수 구간과 분할 가격**이 **어디서 왔는지** (docs/infra.md 25.263·25.562).

    금액이 최소 주문 금액 미만이면 카드는 "권장 금액 없음" 인데 근거표가 걸러지기 전 금액을 적었다 —
    한 카드의 두 숫자가 어긋나고 왜 없는지를 따라갈 수 없었다. 이제 걸렀다는 행을 적는다 (docs/infra.md 25.291).

    카드는 세 숫자를 보여 주는데 근거표에는 행이 없었다 — 목표·손절 %(설정 `horizon_targets` 또는 기본값)도,
    총 투자가능금액도, 매수 구간 중앙이라는 기준도 적혀 있지 않았다.
    CLAUDE.md: 근거에 쓴 모든 수치는 펼쳐 볼 수 있어야 한다.
    """
    config = (targets or DEFAULT_TARGETS).get(horizon, DEFAULT_TARGETS[horizon])
    # **그 기간에 설정값을 실제로 썼을 때만** 출처가 설정이다 (docs/infra.md 25.522, 감사). 예전에는 설정 전체가
    # 있는지만 봐서, 중기가 범위 밖이라 버려져 기본값(+25%/−15%)으로 계산했는데도 출처가 "설정" 이었다
    출처 = (
        SOURCE_SETTINGS + " horizon_targets" if targets and horizon in targets
        else "기본값 (CLAUDE.md 매매 규칙 — 설정이 없거나 범위 밖)"
    )
    base = (low + high) / 2
    # **통화를 붙인다** (docs/infra.md 25.385). 예전에는 "82,500.00" 처럼 단위 없이 소수 둘째 자리까지 적었다 —
    # 국내 가격에 소수가 붙고, 미국인지 국내인지 근거표만 봐서는 알 수 없었다
    가격 = (lambda v: f"{v:,.0f}원") if inp.currency == "KRW" else (lambda v: f"{v:,.2f} {inp.currency}")
    # **매수 구간·분할 가격의 식과 재료** (docs/infra.md 25.562, 감사 재현). 카드의 돈에 닿는 숫자인데 근거표에 종가도
    # "현재가 × 0.95" 도, 장기의 밴드 20% 분위도 없었다 — 중기 근거표에는 가격 행이 한 줄도 없었다
    종가 = inp.close or 0.0
    if horizon == "short":
        ma20 = (data or {}).get("ma20")
        식 = (
            f"하단 20일선 {가격(ma20)} × {SHORT_ZONE_LOW:g}"
            f" · 상단 min(종가 {가격(종가)}, 20일선 × {SHORT_ZONE_HIGH:g})"
            if ma20 else f"20일선 기준 (종가 {가격(종가)})"
        )
        재료 = "prices (종가·20일 이동평균)"
    elif horizon == "mid":
        식 = f"하단 종가 {가격(종가)} × {MID_ZONE_LOW:g} · 상단 종가"
        재료 = "prices (종가)"
    else:
        기준가 = inp.band_close if inp.band_close and inp.band_close > 0 else 종가
        if inp.band is not None and inp.pbr_now:
            식 = (f"주당순자산 = 원 종가(분할만 반영) {가격(기준가)} ÷ PBR {inp.pbr_now:.2f}배"
                 f" → 하단 × {BAND_STOP_PERCENTILE}% 분위"
                 f" {inp.band.at(BAND_STOP_PERCENTILE):.2f}배 · 상단 min(종가, × {BAND_ENTRY_PERCENTILE}% 분위"
                 f" {inp.band.at(BAND_ENTRY_PERCENTILE):.2f}배)")
        else:
            식 = "밸류에이션 밴드 기준"
        # **실제 재료를 적는다** (docs/infra.md 25.620, 감사). 신호의 밴드는 매일 직접 만들고 `valuation_bands` 표
        # (주 1회, 종목 상세가 읽는다)는 읽지 않는다 — 그 표를 가리키면 사용자가 날짜가 다른 분위와 맞춰 보게 된다
        재료 = ("prices (분할만 반영한 종가)·financials (자본총계)·stocks.listed_shares"
              " — 밴드는 신호가 기준일에 직접 계산")
    구간행 = _criterion(
        "매수 구간·분할 (참고)",
        # 분할 가운데는 **카드와 같은 값**(호가로 맞춘 것) — 반올림 전 중앙을 적어 73,250원처럼 주문할 수 없는 값이
        # 근거표에만 남았다 (25.661, 교차검증). 목표·손절의 "구간 중앙 기준" 은 식이라 반올림 전 값 그대로 둔다
        f"구간 {가격(low)} ~ {가격(high)} · 분할 {가격(high)} / {가격(tranche_plan(low, high, inp.currency)[1].price)}"
        f" / {가격(low)}",
        식 + " — 분할은 상단·중앙·하단",
        재료,
        inp.price_date,
        passed=None,
    )
    rows = [
        _criterion(
            "목표·손절 (참고)",
            f"목표 {가격(target_price)} ({config['target_pct']:+g}%)"
            f" · 손절 {가격(stop_price)} ({config['stop_pct']:+g}%)",
            f"매수 구간 중앙 {가격(base)} 기준",
            출처,
            inp.price_date,
            passed=None,
        )
    ]
    # 미국은 설정의 원화 총액을 환율로 바꾼 값이다 — 통화 없이 "35,000" 만 적으면 설정 화면(원화 5천만)에서 같은
    # 숫자를 찾을 수 없었다 (25.562). 금액 행의 **모든 갈래**가 같은 문구를 쓴다 (25.565, 교차검증)
    # 금액은 통화의 최소 단위까지 적는다 — 달러를 정수로 적어 카드($1,234.56)·2부($35,273.90)와 어긋났다 (25.567)
    def 금(v: float) -> str:
        return f"{v:,.0f}" if inp.currency == "KRW" else f"{v:,.2f}"

    총액문구 = 금(total_investable)
    if inp.currency != "KRW":
        총액문구 += f" {inp.currency} (설정 원화 총액 ÷ USDKRW)"
    if amount is None and fx_missing:
        # 미국: 원화 총액은 있는데 달러로 바꿀 환율이 없다. "설정에서 넣으라" 고 보내지 않는다
        # (docs/infra.md 25.292·25.295)
        rows.append(_criterion(
            "권장 금액 (참고)", "환율이 없어 원화 총액을 달러로 바꾸지 못해 내지 않았습니다",
            "쓸 수 있는 USDKRW(7일 이내)가 생기면 채워진다", "fx_rates",
            inp.price_date, passed=None,
        ))  # fmt: skip
    elif amount is None:
        rows.append(_criterion(
            "권장 금액 (참고)", "총 투자가능금액이 없어 내지 않았습니다",
            "설정에서 총 투자가능금액을 넣으면 채워진다", SOURCE_SETTINGS + " total_investable_amount",
            inp.price_date, passed=None,
        ))  # fmt: skip
    elif amount <= 0:
        # 약세 국면 배수 0 과 최소 주문 0 이 겹치면 금액이 0 이다 — 카드는 금액을 비웠는데(25.409) 여기만 "0 KRW"
        # 로 남아 한 카드의 두 숫자가 어긋났다 (docs/infra.md 25.522, 감사)
        rows.append(_criterion(
            "권장 금액 (참고)", "비중이 0 이라 신규 매수 금액을 내지 않았습니다",
            f"총 투자가능금액 {총액문구} × 비중 {weight:.2f}% (시장 국면 배수 0 등)",
            SOURCE_SETTINGS + " total_investable_amount", inp.price_date, passed=None,
        ))  # fmt: skip
    elif amount < min_order_amount:
        rows.append(_criterion(
            "권장 금액 (참고)",
            f"최소 주문 금액 미만이라 내지 않았습니다 ({금(amount)} < {금(min_order_amount)} {inp.currency})",
            f"총 투자가능금액 {총액문구} × 비중 {weight:.2f}% — 최소 주문 금액(설정)보다 작다",
            SOURCE_SETTINGS + " total_investable_amount·min_order_amount", inp.price_date, passed=None,
        ))  # fmt: skip
    else:
        rows.append(_criterion(
            "권장 금액 (참고)", f"{금(amount)} {inp.currency}",
            f"총 투자가능금액 {총액문구} × 비중 {weight:.2f}%",
            SOURCE_SETTINGS + " total_investable_amount", inp.price_date, passed=None,
        ))  # fmt: skip
    rows.append(구간행)
    # **손절선이 3차 매수가(구간 하단) 위면 말한다** (docs/infra.md 25.563, 감사 재현). 손절은 구간 중앙 기준이라,
    # 손절을 −2% 처럼 좁게 두면 중기 손절 0.9555c 가 3차 매수가 0.95c 보다 높다 —
    # 계획대로 사면 3차는 이미 손절선 아래다.
    # 설정 검사(`settings_range`)는 "손절 < 0" 만 본다. 단기·중기 기본값(−7%·−15%)에서는 생기지 않지만
    # **장기는 기본 −25% 에서도 생긴다** — 밴드 30% 분위가 20% 분위의 1.67배 이상이면 구간 하단이 중앙의 75% 밑이다
    # (docs/infra.md 25.1089, 감사 재현: p20 0.5·p30 1.0·PBR 0.9 → 구간 5,560~10,000, 손절 5,840)
    if stop_price >= low:
        rows.append(_criterion(
            "손절선 위치 (주의)",
            f"손절 {가격(stop_price)} ≥ 3차 매수가 {가격(low)}",
            "손절선은 매수 구간 하단보다 낮아야 한다 — 설정의 손절 %가 구간 폭보다 좁다",
            출처, inp.price_date, passed=None,
        ))  # fmt: skip
    # **목표가가 1차 매수가(구간 상단) 이하면 말한다** (docs/infra.md 25.1089, 감사 재현). 목표도 구간 중앙 기준이라
    # 목표 %가 구간 폭의 절반보다 좁으면(중기 +2.5%·구간 5% → 목표 9,990 < 1차 10,000) 카드는 "목표 +2.5%" 인데
    # 1차로 산 값보다 낮다. 장기는 기본 +50% 에서도 밴드가 넓으면(30% 분위 ≥ 20% 분위의 3배) 생긴다.
    # 계산은 사양(docs/signals.md 2장)대로 두고 근거표에만 적는다 — 손절선 위치와 같은 방식
    if target_price <= high:
        rows.append(_criterion(
            "목표가 위치 (주의)",
            f"목표 {가격(target_price)} ≤ 1차 매수가 {가격(high)}",
            "목표가는 매수 구간 상단보다 높아야 한다 — 목표 %가 구간 폭보다 좁다",
            출처, inp.price_date, passed=None,
        ))  # fmt: skip
    return rows


# ----------------------------------------------------------------------
# 근거 문장
# ----------------------------------------------------------------------


def _fmt_pct(value: float | None, digits: int = 1) -> str | None:
    return None if value is None else f"{value * 100:.{digits}f}%"


def _fmt_num(value: float | None, digits: int = 0) -> str | None:
    return None if value is None else f"{value:,.{digits}f}"


def _가격(value: float | None, inp: SignalInput) -> str | None:
    """가격 서식 — 원화는 정수, 그 밖은 소수 둘째 자리 (docs/infra.md 25.620, 감사).

    근거표의 정배열·추세 위 행이 달러도 정수로 적어, 근거 문장은 "20일선 9.75달러" 인데 같은 카드의 표는
    "20일선 10달러 > 60일선 10달러" 였다 — 25.600 은 문장만 고쳤다. 문장과 표가 이 한 함수를 쓴다.
    """
    return _fmt_num(value, 0 if inp.currency == "KRW" else 2)


def rationale(horizon: str, inp: SignalInput, data: dict) -> str:
    """근거 문장. 템플릿 + 실제 수치다.

    **수치가 없으면 그 문장을 넣지 않는다.** "양호함" 같은 말로 채우지 않는다.
    """
    parts: list[str] = []

    if horizon == "short":
        ma20, close = data.get("ma20"), data.get("close")
        if ma20 and close:
            parts.append(
                # 원화가 아니면 소수 둘째 자리까지 — 9.60 을 "10달러" 로 적어 종가 $9.80 과 모순됐다 (25.600, 감사)
                f"20일선 {_가격(ma20, inp)}"
                f"{_unit(inp)} 위에서 "
                f"{_이격(close / ma20 - 1)} 거리"
            )
        if data.get("ma20") and data.get("ma60"):
            parts.append("정배열(20일선 > 60일선)")
        ratio = data.get("turnover_ratio")
        if ratio:
            parts.append(f"20일 거래대금이 60일 평균의 {ratio:.{_문턱_자릿수(ratio, TURNOVER_MULTIPLE, 1)}f}배")
        if data.get("momentum") is not None:
            parts.append(f"모멘텀 {_점수(data['momentum'])}")

    elif horizon == "mid":
        if inp.revenue_growth is not None:
            parts.append(f"매출 {_증가율(inp.revenue_growth)}")
        if inp.operating_income_growth is not None:
            parts.append(f"영업이익 {_증가율(inp.operating_income_growth)}")
        if inp.fiscal_year:
            parts.append(f"{inp.fiscal_year} {_annual_report(inp)} 기준")
        for key, label in (("growth", "성장"), ("quality", "퀄리티")):
            if data.get(key) is not None:
                parts.append(f"{label} {_점수(data[key])}")

    else:
        if inp.pbr_now is not None and data.get("band_sample"):
            # **아는 만큼만 말한다** (docs/infra.md 25.302). 신호는 분위 네 점(20/30/50/80)만 갖고 있어
            # "몇 % 분위" 를 셀 수 없다 — 예전 `band_rank` 는 0·25·50·75·100 만 나와 20% 아래를 "0% 분위" 라 적었다.
            # 정확한 지점은 종목 상세의 밸류에이션 밴드(3년 계열 전체로 센다, services/valuation_band)에 있다
            p20, p30 = data.get("band_p20"), data.get("band_p30")
            if p20 is not None and inp.pbr_now < p20:
                suffix = " 20% 분위 아래"
            elif p20 is not None and p30 is not None and inp.pbr_now <= p30:
                suffix = " 20~30% 분위 사이"
            else:
                suffix = " 하단"
            parts.append(f"PBR {inp.pbr_now:.2f}배로 밴드{suffix}")
        if data.get("band_p20") is not None:
            parts.append(f"밴드 하단 {data['band_p20']:.2f}배")
        for key, label in (("quality", "퀄리티"), ("value", "밸류")):
            if data.get(key) is not None:
                parts.append(f"{label} {_점수(data[key])}")

    return ". ".join(parts) if parts else ""


def _annual_report(inp: SignalInput) -> str:
    """연간 재무의 이름. 국내는 DART 사업보고서, 미국은 SEC 10-K 다. 통화로 나라를 가른다."""
    이름 = "사업보고서" if inp.currency == "KRW" else "10-K"
    # 연결이 없어 별도를 썼으면 밝힌다 — 연결과 별도는 같은 회사라도 숫자가 다르다 (docs/infra.md 25.314)
    return 이름 if inp.consolidated else f"{이름}(별도)"


def _unit(inp: SignalInput) -> str:
    return "원" if inp.currency == "KRW" else "달러"


# ----------------------------------------------------------------------
# 한 종목의 신호 만들기
# ----------------------------------------------------------------------


def evaluate(
    inp: SignalInput,
    max_weight_per_stock: float = 10.0,
    total_investable: float = 0.0,
    min_order_amount: float = 100_000.0,
    targets: dict | None = None,
    fx: dict | None = None,
    regime_factor: float = 1.0,
    regime_data: dict | None = None,
) -> list[Signal]:
    """한 종목의 기간별 신호. 조건을 만족하는 기간만 돌려준다.

    total_investable·min_order_amount 는 이미 종목 통화로 환산된 값이다(docs/signals.md 3.4).
    fx 는 환산에 쓴 환율. 근거표에 참고 행으로 남긴다.
    regime_factor·regime_data 는 시장 추세 필터(3.5). 비중에만 곱하고 판정은 바꾸지 않는다.
    """
    out: list[Signal] = []

    for horizon in HORIZONS:
        passed, data = RULES[horizon](inp)
        if not passed:
            continue

        zone = buy_zone(horizon, inp, data)
        if zone is None:
            continue
        # 호가 단위로 — 하단은 올리고 상단은 내려 구간 안에 둔다. 한 호가보다 좁으면 한 값으로 (25.657)
        low, high = to_tick(zone[0], inp.currency, "up"), to_tick(zone[1], inp.currency, "down")
        if high < low:
            low = high = to_tick(zone[1], inp.currency)

        weight, reduction, size_data = size_weight(
            max_weight_per_stock, inp.volatility_ann, inp.mdd, regime_factor, regime_data
        )
        amount = total_investable * weight / 100 if total_investable > 0 else None
        tranches = tranche_plan(low, high, inp.currency)
        # **권장 금액을 비울 때는 회차 금액도 비운다** (docs/infra.md 25.380). 아래에서 최소 주문 미만이면
        # `suggested_amount` 만 비우고 회차는 채워 둬서, 카드가 "권장 금액 없음" 인데
        # 분할 칩은 "1차 40% · 12,000원" 이었다.
        # 추세 필터 배수가 0 이면 칩에 "0원" 이 나왔다. 25.291 은 근거표만 고쳤다
        if amount is not None and amount >= min_order_amount and amount > 0:
            for tranche in tranches:
                tranche.amount = amount * tranche.ratio

        target_price, stop_price = target_and_stop(horizon, low, high, targets)
        target_price, stop_price = to_tick(target_price, inp.currency), to_tick(stop_price, inp.currency)

        out.append(
            Signal(
                stock_id=inp.stock_id,
                ticker=inp.ticker,
                name=inp.name,
                horizon=horizon,
                signal_type=SIGNAL_TYPE[horizon],
                buy_zone_low=low,
                buy_zone_high=high,
                tranches=tranches,
                target_price=target_price,
                stop_price=stop_price,
                currency=inp.currency,
                rationale_text=rationale(horizon, inp, data),
                rationale_data={
                    **data,
                    **size_data,
                    # 화면이 그대로 그리는 근거표. 문턱값의 정의처는 이 파일이다.
                    "criteria": criteria(horizon, inp, data)
                    + sizing_criteria(inp, size_data)
                    + fx_criteria(fx)
                    + plan_criteria(
                        inp, horizon, low, high, target_price, stop_price, targets, amount, total_investable, weight,
                        min_order_amount,
                        # 원화가 아닌데 환율이 없으면 총액 0 의 까닭은 환율이다(jobs/signals.load_settings)
                        fx_missing=inp.currency != "KRW" and fx is None,
                        data=data,
                    )
                    + insider.criteria_rows(inp.insider, inp.listed_shares),
                    **({"fx": fx} if fx else {}),
                    **performance_data(inp),
                    # **목표·손절이 어느 가격 단위인지** (docs/infra.md 25.215). 그날 종가다. 나중에 분할·배당으로
                    # 가격 계열이 다시 조정되어도 성적표가 "지금 단위의 그날 종가 ÷ 이 값" 으로 목표·손절을 옮긴다
                    # 장기 구간은 `band_close`(분할만 반영) 단위로 냈다(25.562) — 기준 종가도 **그 종가**여야 한다.
                    # 수정종가를 두면 장중 감시·성적표가 "그날 가격 ÷ ref_close" 비율을 한 번 더 곱해
                    # 반대로 3% 틀어졌다 (25.565)
                    "ref_close": (
                        inp.band_close if horizon == "long" and inp.band_close and inp.band_close > 0 else inp.close
                    ),
                },
                weight_pct=weight,
                suggested_amount=(
                    amount
                    # **0원은 금액이 아니다** (docs/infra.md 25.409). 최소 주문 0 과 약세장 배수 0 이 겹치면
                    # `0 >= 0` 이라 0 이 저장되어, 2부가 "0원 (0.0%)" 을 배분으로 싣고 제외 사유
                    # ("약세장 비중 0으로 0원", 25.340)를 적지 못했다
                    if amount is None or (amount > 0 and amount >= min_order_amount)
                    else None
                ),
                size_reduction=reduction,
                # 업종 데이터가 없으면 섹터 상한을 적용할 수 없다.
                # 조용히 넘어가지 않고 사유를 남긴다.
                sector_cap_applied=bool(inp.sector),
                sector_cap_note=None if inp.sector else SECTOR_CAP_UNAVAILABLE,
            )
        )
    return out
