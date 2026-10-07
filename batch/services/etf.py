"""ETF 장기 적립 판정. 순수 계산만 한다. 네트워크와 DB 를 모른다.

규칙의 단일 정의처는 docs/etf.md 8장(미국)·9장(국내)이다. 여기 있는 숫자를
바꾸면 그 문서도 같은 커밋에서 고친다.

**수익률로 고르지 않는다.** 20년 적립에서 지난 수익률은 다음 수익률을 말해
주지 않는다. 보는 것은 상품 자체의 질이다 — 보수·규모·이력·지수의 넓이.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from batch.services import criteria
from batch.services import metrics as mt
from batch.sources.yahoo_fund import FundProfile

# 2: 넓은 지수에 Large Growth(미국)·NASDAQ 100·코스닥 150(국내)을 넣었다 (2026-09-17 사용자 결정)
# 3: 국내 액티브를 제외하지 않는다, 미국 Large Value 를 넣었다 (2026-09-17 사용자 결정)
#: 4 — 보수를 화면 단위로 반올림해 순위를 매긴다 (docs/infra.md 25.758·25.760). 5 — 표시 함수 값으로 (25.762)
CALC_VERSION = 5

SOURCE_PROFILE = "yahoo_quotesummary"
SOURCE_KRX = "krx_openapi"
SOURCE_SIGNALS = "signals"

# ----------------------------------------------------------------------
# 미국 필수 조건 (etf.md 8.2)
# ----------------------------------------------------------------------

MIN_YEARS = 3  # 상장 직후 청산되는 ETF 가 많다. 살아남았다는 최소 증거
MIN_ASSETS_USD = 1_000_000_000  # 종목 유니버스의 미국 시총 하한과 같은 자릿수
# 초기값 [확인필요: 실측 뒤 조정]. 호가 차이를 무시할 만큼 거래되는지를 자릿수로 가른다
MIN_TURNOVER_USD = 5_000_000

# 야후 분류가 이것으로 시작하면 레버리지·인버스다.
# TQQQ "Trading--Leveraged Equity", SQQQ "Trading--Inverse Equity" 를 실제로 확인했다.
TRADING_CATEGORY_PREFIX = "Trading--"

# 이름에 이 단어가 있으면 레버리지·인버스로 본다. 분류가 비어 있을 때의 안전망이다.
# 대소문자를 가리지 않고 단어 단위로 본다. "Bearing" 같은 이름을 잘못 잡지 않게 한다.
#
# **알고 있는 오판**: "Ultra-Short Income" 같은 초단기 채권 ETF 도 ultra 에 걸린다.
# 그런 상품은 분류가 허용 목록 밖이라 어차피 빠지지만, 제외 사유가 "레버리지" 로
# 표시된다. 분류 판정이 먼저라 실제로는 분류가 있는 대부분이 분류 사유로 빠진다.
LEVERAGE_NAME_PATTERN = re.compile(
    r"(?<![a-z0-9])(-?[1-4]x|ultra|ultrapro|ultrashort|inverse|bear|leveraged)(?![a-z0-9])"
)

# 넓은 지수 허용 목록. 분류 → 묶음. 2026-09-17 실제 응답에서 확인한 이름이다.
# 첫 정식 실행(35167448877)에서 Mid-Cap Blend 21개를 확인했다.
BROAD_CATEGORIES: dict[str, str] = {
    "Large Blend": "미국 주식",
    "Large Growth": "미국 주식",  # 2026-09-17 사용자 결정. 국내의 NASDAQ 100 과 기준을 맞췄다
    "Large Value": "미국 주식",  # 2026-09-17 사용자 결정. 성장과 가치를 같게 본다
    "Mid-Cap Blend": "미국 주식",
    "Small Blend": "미국 주식",
    "Foreign Large Blend": "해외 주식",
    "Diversified Emerging Mkts": "해외 주식",
    "Global Large-Stock Blend": "세계 주식",
    "Intermediate Core Bond": "채권",
}

# ----------------------------------------------------------------------
# 국내 필수 조건 (etf.md 9장)
# ----------------------------------------------------------------------

MIN_ASSETS_KRW = 100_000_000_000  # 1,000억원. 2.1 의 국내 규모 하한
# 초기값 [확인필요: 실측 뒤 조정]. 미국 500만달러를 그대로 옮기면 약 70억원이라 국내에선
# 대표 ETF 만 남는다. 호가 차이를 무시할 수 있는 자릿수로 10억원을 둔다.
MIN_TURNOVER_KRW = 1_000_000_000
# 20 거래일 중 이만큼은 데이터가 있어야 평균을 믿는다
MIN_KR_DAYS = 15

#: 상품 유형 판정 행의 기준 칸. 제외 표기(`KR_NAME_EXCLUSIONS`)가 무엇을 빼는지 다 적는다 — 버퍼·옵션(25.544)이
#: 빠져 있어 근거표에서 뺀 이유가 기준과 맞지 않았다 (25.551)
KR_TYPE_CRITERION = "레버리지·인버스·합성·커버드콜·버퍼·옵션 전략 아님"

# 이름 표기로 거른다. 한국거래소 응답에는 상품 유형 열이 없다.
# **기초지수로는 레버리지를 못 거른다.** KODEX 레버리지의 기초지수는 KODEX 200 과 같은
# "코스피 200" 이다(data-sources 13.1-1).
KR_NAME_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    ("레버리지", "레버리지·인버스 상품 (20년 적립과 정반대)"),
    ("인버스", "레버리지·인버스 상품 (20년 적립과 정반대)"),
    ("2X", "레버리지·인버스 상품 (20년 적립과 정반대)"),
    ("곱버스", "레버리지·인버스 상품 (20년 적립과 정반대)"),
    ("합성", "합성(스왑) 복제 — 거래상대방 위험 (2.1)"),
    ("커버드콜", "커버드콜 — 상승을 팔아 분배금을 만든다. 적립과 목적이 다르다"),
    ("콜매도", "커버드콜 — 상승을 팔아 분배금을 만든다. 적립과 목적이 다르다"),
    ("프리미엄", "커버드콜 — 상승을 팔아 분배금을 만든다. 적립과 목적이 다르다"),
    # 버퍼·옵션 전략은 지수를 그대로 따르지 않는다 — 상승을 잘라 하락을 막는 구조라 넓은 지수 적립과 다르다
    # (25.544, 감사 재현: "KODEX 미국S&P500버퍼3월액티브" 가 기초지수 S&P 500 으로 핵심 추천에 올랐다).
    # 미국 위성은 buffer·defined outcome 을 이미 거른다
    ("버퍼", "버퍼·옵션 전략 — 지수를 그대로 따르지 않는다"),
    ("옵션", "버퍼·옵션 전략 — 지수를 그대로 따르지 않는다"),
)
# 배수 표기 "2X"·"1.5x"·"-1X" (docs/infra.md 25.633). 앞이 영문·숫자·점이 아닐 때만 — "S&P500" 같은
# 숫자 끝에 붙은 x 가 없어 오판은 없다고 봤다 (2026-09-29 코드 기준, 실제 국내 ETF 목록과 대조는 못 했다 [확인필요])
KR_MULTIPLE_PATTERN = re.compile(r"(?<![A-Za-z0-9.])-?\d+(?:\.\d+)?X(?![A-Za-z])", re.IGNORECASE)
# 액티브는 제외하지 않는다 (2026-09-17 사용자 결정, 계산 버전 3).
# 첫 실행에서 국내 종합채권 ETF 가 전부 이름에 "액티브" 를 달고 있어 채권이 0개가 됐다.
# 사용자는 주식·채권 모두 액티브를 포함하고 미국도 같게 하라고 했다. 미국은 원래 이름으로
# 액티브를 거르지 않았으므로(야후 분류만 본다) 바뀌는 것이 없다.
# 대신 화면이 액티브임을 알 수 있게 근거표에 표시한다(ACTIVE_MARKER).
ACTIVE_MARKER = "액티브"

# 넓은 지수 허용 목록. 한국거래소 IDX_IND_NM 그대로. 2026-09-16 응답에서 확인한 이름만.
KR_BROAD_INDEXES: dict[str, str] = {
    "코스피 200": "국내 주식",
    "코스피 200 TR": "국내 주식",
    "코스피": "국내 주식",
    "코스피 100": "국내 주식",
    "KRX 300": "국내 주식",
    "MSCI Korea TR Index": "국내 주식",
    "코스닥 150": "국내 주식",  # 2026-09-17 사용자 결정
    "S&P 500": "해외 주식",
    "NASDAQ 100": "해외 주식",  # 2026-09-17 사용자 결정
    "MSCI World": "해외 주식",
    "KAP 한국종합채권지수": "채권",
    "KAP K-종합채권지수(AA-이상, 총수익)": "채권",
    "KAP 종합채권지수(AA-이상, 총수익)": "채권",
    "KAP 종합채권 AA- 이상 지수(총수익)": "채권",
    "KIS 종합채권 지수(A-이상)(총수익지수)": "채권",
    "KIS 종합채권 AA-이상 총수익 지수(Total return Index)": "채권",
}

BUCKET_ORDER = ("국내 주식", "미국 주식", "해외 주식", "세계 주식", "채권")

# ----------------------------------------------------------------------
# 점수 (etf.md 8.3 · 9.3)
# ----------------------------------------------------------------------

# 2.2 의 기본 가중치.
BASE_WEIGHTS = {"expense": 40, "assets": 20, "tracking": 20, "premium": 10, "distribution": 10}
AVAILABLE_US = ("expense", "assets")
# 국내는 보수가 없다(자동 경로 없음, 사용자가 "보수 없이 먼저" 로 결정). 괴리율은 있다.
AVAILABLE_KR = ("assets", "premium")
AVAILABLE_BY_COUNTRY = {"US": AVAILABLE_US, "KR": AVAILABLE_KR}

# 기준마다 높을수록 좋은가
HIGHER_IS_BETTER = {"expense": False, "assets": True, "premium": False}

CRITERION_LABEL = {"expense": "총보수", "assets": "순자산", "premium": "괴리율"}


def renormalized_weights(available: tuple[str, ...] = AVAILABLE_US) -> dict[str, float]:
    """빠진 기준의 가중치를 나머지에 비례해 나눈다. 합이 1 이다."""
    total = sum(BASE_WEIGHTS[k] for k in available)
    return {k: BASE_WEIGHTS[k] / total for k in available}


def name_looks_leveraged(name: str) -> bool:
    return LEVERAGE_NAME_PATTERN.search(name.lower()) is not None


def years_between(start_iso: str, as_of: date) -> float | None:
    """ISO 날짜 문자열에서 연수. 읽을 수 없으면 None.

    **나누는 식은 `services/metrics.years_between` 이 정의처다** (docs/infra.md 25.109).
    같은 `/ 365.25` 가 세 곳에 따로 적혀 있었다. 여기가 하는 일은 **글자를 날짜로 바꾸는 것**
    뿐이다 — 달력 연수냐 거래일이냐는 판단이 두 벌이 되면 안 된다.
    """
    try:
        start = date.fromisoformat(start_iso)
    except (TypeError, ValueError):
        return None
    return mt.years_between(start, as_of)


# ----------------------------------------------------------------------
# 표시
# ----------------------------------------------------------------------


def fmt_pct(ratio: float | None, digits: int = 2) -> str:
    return "-" if ratio is None else f"{ratio * 100:.{digits}f}%"


def fmt_usd(value: float | None) -> str:
    """자릿수를 읽기 쉽게. $1.76T, $97.3B, $5.2M."""
    if value is None:
        return "-"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(value) >= size:
            return f"${value / size:.2f}{unit}".replace(".00", "")
    return f"${value:,.0f}"


def fmt_krw(value: float | None) -> str:
    """2.47조원, 1,000억원, 10억원."""
    if value is None:
        return "-"
    if abs(value) >= 1e12:
        return f"{value / 1e12:.2f}조원".replace(".00", "")
    if abs(value) >= 1e8:
        return f"{value / 1e8:,.0f}억원"
    return f"{value:,.0f}원"


def fmt_money(value: float | None, country: str) -> str:
    return fmt_krw(value) if country == "KR" else fmt_usd(value)


def _criterion(label: str, display: str, threshold: str, source: str, as_of: str | None, passed: bool) -> dict:
    """종목 추천(signals.criteria)과 같은 모양. 화면이 같은 표로 그린다."""
    return {
        "label": label,
        "display": display,
        "threshold": threshold,
        "source": source,
        "as_of": as_of,
        "passed": passed,
    }


# ----------------------------------------------------------------------
# 판정 결과
# ----------------------------------------------------------------------


@dataclass
class Evaluation:
    symbol: str
    name: str
    profile: FundProfile | None
    passed: bool
    bucket: str | None
    excluded_reason: str | None
    criteria: list[dict] = field(default_factory=list)

    country: str = "US"
    category: str | None = None  # 미국은 야후 분류, 국내는 기초지수 이름. 같은 것끼리만 비교한다
    metrics: dict[str, float] = field(default_factory=dict)  # 점수에 쓰는 값. 키는 AVAILABLE_*

    # 통과한 것만 채운다
    score: float | None = None
    rank_in_category: int | None = None
    category_size: int | None = None
    component_scores: dict[str, float] = field(default_factory=dict)


# ----------------------------------------------------------------------
# 미국 판정
# ----------------------------------------------------------------------


def evaluate(symbol: str, name: str, profile: FundProfile | None, as_of: date, fetched: str) -> Evaluation:
    """필수 조건을 차례로 본다. 처음 걸린 조건이 제외 사유가 된다.

    근거표에는 **값을 확인한 조건만** 행을 만든다. 값이 없어 판정하지 못한
    조건은 행 대신 제외 사유로 남긴다. "-" 로 채운 행은 확인이 아니라 장식이다.
    """
    rows: list[dict] = []
    category = profile.category if profile else None

    def excluded(reason: str, bucket: str | None = None) -> Evaluation:
        return Evaluation(symbol, name, profile, False, bucket, reason, rows, country="US", category=category)

    if profile is None:
        return excluded("프로필을 받지 못했습니다")

    leveraged = bool(category and category.startswith(TRADING_CATEGORY_PREFIX)) or name_looks_leveraged(name)
    if category:
        rows.append(_criterion(
            "레버리지·인버스 아님", f"분류 {category}",
            f"분류가 {TRADING_CATEGORY_PREFIX} 로 시작하지 않음", SOURCE_PROFILE, fetched, not leveraged,
        ))
    if leveraged:
        return excluded("레버리지·인버스 상품 (20년 적립과 정반대)")

    missing = [
        label
        for label, value in (
            ("분류", category),
            ("총보수", profile.expense_ratio),
            ("순자산", profile.total_assets),
            ("설정일", profile.inception_date),
        )
        if value is None
    ]
    if missing:
        return excluded(f"확인할 수 없는 값: {', '.join(missing)}")

    assert category is not None  # 위에서 걸렀다. 타입 검사용
    bucket = BROAD_CATEGORIES.get(category)
    rows.append(_criterion(
        "넓은 지수", f"분류 {category}", "허용 목록 (docs/etf.md 8.2)", SOURCE_PROFILE, fetched, bucket is not None,
    ))
    if bucket is None:
        return excluded(f"좁거나 기운 지수 (분류 {category})")

    years = years_between(str(profile.inception_date), as_of)
    rows.append(_criterion(
        "운용 이력",
        f"설정 {profile.inception_date} ({years:.1f}년)" if years is not None else f"설정 {profile.inception_date}",
        f"≥ {MIN_YEARS}년", SOURCE_PROFILE, fetched, years is not None and years >= MIN_YEARS,
    ))
    # 설정일을 읽지 못하면 "미만" 이 아니라 "확인할 수 없다" 다 — 모르는 값을 미달로 적지 않는다 (25.759, ETF 감사)
    if years is None:
        return excluded(f"확인할 수 없는 값: 설정일 ({profile.inception_date})", bucket)
    if years < MIN_YEARS:
        return excluded(f"운용 {MIN_YEARS}년 미만", bucket)

    assets = float(profile.total_assets or 0)
    rows.append(_criterion(
        "규모", f"순자산 {fmt_usd(assets)}", f"≥ {fmt_usd(MIN_ASSETS_USD)}",
        SOURCE_PROFILE, fetched, assets >= MIN_ASSETS_USD,
    ))
    if assets < MIN_ASSETS_USD:
        return excluded(f"순자산 {fmt_usd(MIN_ASSETS_USD)} 미만", bucket)

    turnover = profile.turnover_est
    if turnover is None:
        return excluded("확인할 수 없는 값: 거래량", bucket)
    rows.append(_criterion(
        "유동성", f"하루 거래대금 약 {fmt_usd(turnover)} (평균 거래량 × 전일 종가, 추정)",
        f"≥ {fmt_usd(MIN_TURNOVER_USD)}", SOURCE_PROFILE, fetched, turnover >= MIN_TURNOVER_USD,
    ))
    if turnover < MIN_TURNOVER_USD:
        return excluded(f"거래대금 {fmt_usd(MIN_TURNOVER_USD)} 미만", bucket)

    return Evaluation(
        symbol, name, profile, True, bucket, None, rows,
        country="US", category=category,
        metrics={"expense": float(profile.expense_ratio or 0), "assets": assets},
    )


# ----------------------------------------------------------------------
# 국내 판정 (etf.md 9장)
# ----------------------------------------------------------------------


@dataclass
class KrEtfInput:
    """국내 ETF 한 개의 판정 입력. 전부 한국거래소 ETF 일별매매정보에서 온다."""

    symbol: str  # 단축코드
    name: str
    index_name: str
    net_assets: float | None  # 기준일 순자산총액
    avg_turnover: float | None  # 최근 거래일 평균 거래대금 (실제 값)
    premium_abs_avg: float | None  # 괴리율 절댓값 평균
    days_observed: int  # 평균을 낸 거래일 수
    listed_3y_ago: bool  # 3년 전 같은 무렵의 응답에 있었는가
    #: 괴리율 평균을 낸 날 수 — NAV·종가가 있는 날만이라 `days_observed`(거래대금 있는 날)와 다를 수 있다 (25.714).
    #: 없으면 같다고 본다
    premium_days: int | None = None


def evaluate_kr(inp: KrEtfInput, fetched: str) -> Evaluation:
    """국내 필수 조건. 순서와 원칙은 미국 evaluate 와 같다."""
    rows: list[dict] = []
    bucket = KR_BROAD_INDEXES.get(inp.index_name)

    def excluded(reason: str) -> Evaluation:
        return Evaluation(
            inp.symbol, inp.name, None, False, bucket, reason, rows, country="KR", category=inp.index_name or None
        )

    # 대소문자를 가리지 않는다 (docs/infra.md 25.633, 감사). 예전에는 `marker in name` 이라 "2x"·"3X"·"1.5X"
    # 같은 표기와 영문 "Leveraged"·"Inverse" 를 놓쳤다. 미국 이름 안전망(`name_looks_leveraged`)도 함께 본다
    for marker, reason in KR_NAME_EXCLUSIONS:
        if marker.casefold() in inp.name.casefold():
            rows.append(_criterion("상품 유형", f"이름에 '{marker}'", KR_TYPE_CRITERION,
                                   SOURCE_KRX, fetched, False))
            return excluded(reason)
    if name_looks_leveraged(inp.name) or KR_MULTIPLE_PATTERN.search(inp.name):
        rows.append(_criterion("상품 유형", "이름에 배수·레버리지·인버스 표기", KR_TYPE_CRITERION,
                               SOURCE_KRX, fetched, False))
        return excluded(KR_NAME_EXCLUSIONS[0][1])
    kind = "액티브 (지수 추종이 느슨함)" if ACTIVE_MARKER in inp.name else "이름에 제외 표기 없음"
    rows.append(_criterion("상품 유형", kind, KR_TYPE_CRITERION, SOURCE_KRX, fetched, True))

    if not inp.index_name:
        return excluded("확인할 수 없는 값: 기초지수")
    rows.append(_criterion(
        "넓은 지수", f"기초지수 {inp.index_name}", "허용 목록 (docs/etf.md 9.2)",
        SOURCE_KRX, fetched, bucket is not None,
    ))
    if bucket is None:
        return excluded(f"좁거나 기운 지수 (기초지수 {inp.index_name})")

    rows.append(_criterion(
        "운용 이력", "3년 전 응답에 있음" if inp.listed_3y_ago else "3년 전 응답에 없음",
        f"≥ {MIN_YEARS}년 (3년 전 같은 무렵 상장 여부)", SOURCE_KRX, fetched, inp.listed_3y_ago,
    ))
    if not inp.listed_3y_ago:
        return excluded(f"운용 {MIN_YEARS}년 미만")

    if inp.net_assets is None:
        return excluded("확인할 수 없는 값: 순자산")
    rows.append(_criterion(
        "규모", f"순자산 {fmt_krw(inp.net_assets)}", f"≥ {fmt_krw(MIN_ASSETS_KRW)}",
        SOURCE_KRX, fetched, inp.net_assets >= MIN_ASSETS_KRW,
    ))
    if inp.net_assets < MIN_ASSETS_KRW:
        return excluded(f"순자산 {fmt_krw(MIN_ASSETS_KRW)} 미만")

    if inp.avg_turnover is None or inp.days_observed < MIN_KR_DAYS:
        return excluded(f"확인할 수 없는 값: 거래대금 (관측 {inp.days_observed}일)")
    rows.append(_criterion(
        "유동성", f"{inp.days_observed}일 평균 거래대금 {fmt_krw(inp.avg_turnover)}",
        f"≥ {fmt_krw(MIN_TURNOVER_KRW)}", SOURCE_KRX, fetched, inp.avg_turnover >= MIN_TURNOVER_KRW,
    ))
    if inp.avg_turnover < MIN_TURNOVER_KRW:
        return excluded(f"거래대금 {fmt_krw(MIN_TURNOVER_KRW)} 미만")

    if inp.premium_abs_avg is None:
        return excluded("확인할 수 없는 값: 괴리율 (NAV 없음)")
    rows.append(_criterion(
        # 실제로 평균 낸 날 수를 적는다 (25.714, 감사) — 20일 중 NAV 가 있는 10일 평균을 "20일 평균" 이라 적었다
        "괴리율",
        f"{inp.premium_days if inp.premium_days is not None else inp.days_observed}일 평균 |종가−NAV|/NAV"
        f" {fmt_pct(inp.premium_abs_avg, 3)}",
        "작을수록 (점수 기준)", SOURCE_KRX, fetched, True,
    ))

    return Evaluation(
        inp.symbol, inp.name, None, True, bucket, None, rows,
        country="KR", category=inp.index_name,
        metrics={"assets": float(inp.net_assets), "premium": float(inp.premium_abs_avg)},
    )


# ----------------------------------------------------------------------
# 점수
# ----------------------------------------------------------------------


def percentile_scores(values: list[float], higher_is_better: bool) -> list[float]:
    """백분위 0~100. 가장 좋은 것이 100, 가장 나쁜 것이 0, 동률은 평균 순위.

    n 이 1 이면 100 이다. 비교 대상이 없으면 그 분류에서 가장 좋은 것이다.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [100.0]

    # 나쁜 것부터 좋은 것 순으로 정렬해 순위(0 = 가장 나쁨)를 매긴다
    order = sorted(range(n), key=lambda i: values[i] if higher_is_better else -values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return [r / (n - 1) * 100 for r in ranks]


def _weight_label(weight: float) -> str:
    for text, value in (("2/3", 2 / 3), ("1/3", 1 / 3)):
        if abs(weight - value) < 1e-9:
            return text
    return f"{weight * 100:.0f}%"


def score_within_categories(evaluations: list[Evaluation]) -> list[str]:
    """통과한 것끼리 **같은 나라·같은 분류 안에서** 점수와 순위를 매긴다. 제자리에서 채운다.

    나라마다 쓸 수 있는 기준이 다르다(AVAILABLE_BY_COUNTRY). 빠진 기준의 가중치는
    나머지에 비례해 나눈다.

    **먼저 근거표를 못 만든 것을 뺀다** (CLAUDE.md 절대 규칙, docs/infra.md 25.174).
    핵심 ETF(미국·국내)와 위성 ETF 가 모두 이 함수를 지나므로 여기가 그 자리다.
    순위를 매기기 전에 빼야 집단 크기와 백분위가 실제 후보만 세고 만들어진다.
    돌려주는 것은 뺀 것의 심볼 — **비어 있어야 정상이다.**
    """
    뺀것 = criteria.drop_unverifiable(evaluations, lambda ev: ev.symbol)

    groups: dict[tuple[str, str], list[Evaluation]] = {}
    for ev in evaluations:
        if ev.passed and ev.category:
            groups.setdefault((ev.country, ev.category), []).append(ev)

    for group in groups.values():
        # **보수는 화면 표시 단위(0.01% = 소수 넷째 자리)로 반올림해 견준다** (25.758·25.760 — 여섯째 자리로는 화면에
        # 같은
        # "0.03%" 인 0.0003·0.00031 이 갈렸다, 교차검증). 야후는 VOO 보수를
        # 0.00029999999 로 줘 다른 0.03% ETF(0.0003)와 동률이 깨졌다 — 화면엔 셋 다 0.03% 인데 가장 무거운 기준의
        # 노이즈가 1위를 정했다
        for ev in group:
            if isinstance(ev.metrics.get("expense"), float):
                # 표시 함수(`fmt_pct`)가 찍는 값 그대로 견준다 — round(x, 4) 는 0.065% 같은 반 bp 에서 표시와 갈렸다
                # (25.762)
                ev.metrics["expense"] = float(fmt_pct(ev.metrics["expense"])[:-1]) / 100

    for (country, _category), group in groups.items():
        available = AVAILABLE_BY_COUNTRY[country]
        weights = renormalized_weights(available)
        components = {
            key: percentile_scores([e.metrics[key] for e in group], HIGHER_IS_BETTER[key]) for key in available
        }
        for index, ev in enumerate(group):
            ev.component_scores = {key: round(components[key][index], 1) for key in available}
            ev.score = round(sum(weights[key] * components[key][index] for key in available), 1)
            ev.category_size = len(group)

        # 점수 높은 순. 같으면 첫 기준(미국 보수·국내 순자산)이 좋은 순, 그다음 티커.
        first = available[0]
        sign = -1 if HIGHER_IS_BETTER[first] else 1
        ranked = sorted(group, key=lambda e: (-(e.score or 0), sign * e.metrics[first], e.symbol))
        for rank, ev in enumerate(ranked, start=1):
            ev.rank_in_category = rank
            fetched = ev.criteria[0]["as_of"] if ev.criteria else None
            for key in available:
                if key != first:
                    continue
                value = ev.metrics[key]
                better = HIGHER_IS_BETTER[key]
                position = 1 + sum(
                    1 for other in group if (other.metrics[key] > value if better else other.metrics[key] < value)
                )
                shown = fmt_pct(value) if key == "expense" else fmt_money(value, country)
                direction = "큼" if better else "낮음"
                ev.criteria.append(_criterion(
                    CRITERION_LABEL[key],
                    f"{shown} (같은 분류 {len(group)}개 중 {position}번째로 {direction})",
                    f"{'클수록' if better else '낮을수록'} (가중 {_weight_label(weights[key])})",
                    SOURCE_PROFILE if country == "US" else SOURCE_KRX, fetched, True,
                ))

    return 뺀것


# ----------------------------------------------------------------------
# 추천 종목 겹침 (etf.md 8.4) — 보조 정보. 점수에 넣지 않는다
# ----------------------------------------------------------------------


def overlap(profile: FundProfile | None, recommended: dict[str, str], signals_as_of: str | None) -> dict:
    """상위 10개 보유종목 중 추천 종목의 비중 합. 하한값이다.

    recommended: {티커: 추천 기간 라벨}. 비어 있으면 "모른다" 로 남긴다.
    0% 로 적지 않는다. 0% 는 "겹치지 않는다" 는 주장이다.
    """
    base = {"basis": "상위 10개 보유종목", "source": SOURCE_SIGNALS, "as_of": signals_as_of}
    if not recommended:
        return {**base, "available": False, "note": "미국 추천 종목이 아직 없어 계산하지 않았습니다"}
    if profile is None or not profile.holdings:
        return {**base, "available": False, "note": "보유종목 정보가 없습니다"}

    matched = [
        {"symbol": h.symbol, "name": h.name, "pct": round(h.pct, 6), "horizon": recommended[_norm(h.symbol)]}
        for h in profile.holdings
        if _norm(h.symbol) in recommended
    ]
    return {
        **base,
        "available": True,
        "holdings_seen": len(profile.holdings),
        "matched": matched,
        "total_pct": round(sum(m["pct"] for m in matched), 6),
    }


def overlap_kr() -> dict:
    """국내는 구성종목 경로가 없다(data-sources 13.2). 모른다고 남긴다."""
    return {
        "basis": "구성종목",
        "source": SOURCE_KRX,
        "as_of": None,
        "available": False,
        "note": "국내 ETF 는 구성종목 자료를 받을 경로가 없어 계산하지 않았습니다",
    }


def _norm(symbol: str) -> str:
    """BRK.B 와 BRK-B 를 같게 본다. 보유종목과 종목 마스터의 표기가 다를 수 있다."""
    return symbol.upper().replace(".", "-")


def normalize_recommended(tickers: dict[str, str]) -> dict[str, str]:
    return {_norm(k): v for k, v in tickers.items()}


# ----------------------------------------------------------------------
# 근거 문장
# ----------------------------------------------------------------------


def rationale_text(ev: Evaluation) -> str:
    """템플릿 + 실제 수치. 없는 숫자를 만들지 않는다."""
    if not ev.passed:
        return f"제외: {ev.excluded_reason}"

    label = "분류" if ev.country == "US" else "기초지수"
    parts = []
    if ev.category_size == 1:
        # 혼자면 "1개 중 1위" 가 된다. 비교한 것이 없으니 순위를 말하지 않는다
        parts.append(f"같은 {label}({ev.category})에서 조건을 통과한 유일한 ETF")
    elif ev.rank_in_category == 1:
        parts.append(f"같은 {label}({ev.category}) {ev.category_size}개 중 점수 1위")
    elif ev.category_size:
        parts.append(f"같은 {label}({ev.category}) {ev.category_size}개 중 {ev.rank_in_category}위")

    if ev.country == "US" and ev.profile is not None:
        parts.append(f"총보수 {fmt_pct(ev.profile.expense_ratio)}")
        parts.append(f"순자산 {fmt_usd(ev.profile.total_assets)}")
        if ev.profile.inception_date:
            parts.append(f"설정 {ev.profile.inception_date[:4]}년")
    else:
        parts.append(f"순자산 {fmt_krw(ev.metrics.get('assets'))}")
        parts.append(f"평균 괴리율 {fmt_pct(ev.metrics.get('premium'), 3)}")
        # 보수는 자료가 없어 판정에 넣지 않았다. 빠졌다는 사실을 문장에서 숨기지 않는다
        parts.append("총보수는 자료가 없어 반영하지 않음")
    return ". ".join(parts) + "."
