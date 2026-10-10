"""위성 ETF 판정 — 배당 · 업종 · 테마. 순수 계산만 한다.

규칙의 단일 정의처는 docs/etf.md 10장이다. 여기 숫자·목록을 바꾸면 그 문서도 같은 커밋에서 고친다.

핵심(batch/services/etf.py)과 같은 점수·근거표 모양을 쓴다. 다른 점은 셋이다.
  1. 어느 묶음(배당·업종·테마)과 하위 묶음에 들어오는지를 먼저 정한다
  2. 조건이 몇 가지 더 붙는다(옵션형 이름, 보유 비중 합, 미국 배당·업종 10년)
  3. 순위는 하위 묶음 안에서만 낸다. Evaluation.category 에 하위 묶음 키를 넣어
     핵심의 score_within_categories 를 그대로 쓴다
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from batch.services import etf as core
from batch.sources.yahoo_fund import FundProfile

#: 2 — 25.544·25.548·25.553 에서 판정 규칙이 바뀌었는데 1 그대로였다 (docs/infra.md 25.758, ETF 감사)
#: 3 — 핵심 순위 규칙(보수를 표시 값으로 견줌, 25.760·25.762)을 그대로 쓴다
CALC_VERSION = 3

GROUP_ORDER = ("배당", "업종", "테마")

# ----------------------------------------------------------------------
# 공통 (10.1)
# ----------------------------------------------------------------------

MIN_YEARS_US_LONG = 10  # 미국 배당·업종. 섹터 SPDR 1998, Vanguard 섹터 2004 로 대안이 충분하다
MIN_YEARS_THEME = 3  # 테마는 대부분 젊어 10년이면 묶음이 빈다. 대신 경고를 강하게 단다(10.4)
MAX_POSITION_SUM = 1.05  # BOXX stock_position 198.81% 실측. 파생으로 부풀린 상품을 거른다

US_OPTION_NAME_PATTERN = core.US_OPTION_NAME_PATTERN  # 정의처는 핵심 판정 쪽 (25.1107)
US_OPTION_CATEGORIES = frozenset({"Derivative Income", "Defined Outcome"})

# 25.1107(ETF 감사): "…Weekly Target Income Index" 같은 옵션 인컴 기초지수가 위성 테마로 통과했다 — 미국 규칙에 있는
# Target Income·Buffer 를 더한다
KR_OPTION_INDEX_PATTERN = re.compile(
    r"커버드콜|Covered Call|Premium|콜매도|BuyWrite|Target Income|Buffer|버퍼", re.IGNORECASE
)

# ----------------------------------------------------------------------
# 미국 묶음 (10.2)
# ----------------------------------------------------------------------

# 섹터 전체 목록. 2026-09-17 DB 에 있는 것을 확인했다(FMAT 제외 43종 + FMAT).
US_SECTOR_WIDE: dict[str, str] = {}
for _sector, _symbols in (
    ("정보기술", ("XLK", "VGT", "FTEC", "IYW")),
    ("헬스케어", ("XLV", "VHT", "FHLC", "IYH")),
    ("금융", ("XLF", "VFH", "FNCL", "IYF")),
    ("경기소비재", ("XLY", "VCR", "FDIS", "IYC")),
    ("필수소비재", ("XLP", "VDC", "FSTA", "IYK")),
    ("에너지", ("XLE", "VDE", "FENY", "IYE")),
    ("산업재", ("XLI", "VIS", "FIDU", "IYJ")),
    ("소재", ("XLB", "VAW", "FMAT", "IYM")),
    ("유틸리티", ("XLU", "VPU", "FUTY", "IDU")),
    ("부동산", ("XLRE", "VNQ", "FREL", "IYR")),
    ("커뮤니케이션", ("XLC", "VOX", "FCOM", "IYZ")),
):
    for _symbol in _symbols:
        US_SECTOR_WIDE[_symbol] = _sector

US_DIVIDEND_NAME_PATTERN = re.compile(r"dividend|aristocrat", re.IGNORECASE)
US_DIVIDEND_CATEGORIES: dict[str, str] = {
    "Mid-Cap Value": "미국 중형 배당",
    "Foreign Large Value": "해외 배당",
    "Foreign Large Growth": "해외 배당",
    "Global Large-Stock Value": "해외 배당",
}

US_THEME_CATEGORIES = frozenset({
    "Technology", "Health", "Financial", "Industrials", "Natural Resources", "Equity Energy",
    "Real Estate", "Utilities", "Communications", "Consumer Cyclical", "Consumer Defensive",
    "Infrastructure", "Equity Precious Metals",
})

# ----------------------------------------------------------------------
# 국내 묶음 (10.2)
# ----------------------------------------------------------------------

KR_SECTOR_INDEXES = frozenset({
    "코스피 200 정보기술", "코스피 200 중공업", "코스피 200 건설", "코스피 200 금융", "코스피 200 헬스케어",
    "코스피 200 에너지/화학", "코스피 200 경기방어소비재", "코스피 200 철강/소재", "코스피 200 산업재",
    "코스피 200 커뮤니케이션서비스", "코스피 200 경기소비재", "코스피 200 생활소비재",
})

KR_DIVIDEND_INDEXES: dict[str, str] = {
    **dict.fromkeys((
        "FnGuide 배당주 지수", "WISE 대형고배당10 TR 지수", "FnGuide 고배당 Plus 지수", "FnGuide 고배당포커스 지수",
        "FnGuide 코리아 고배당 지수(PR)", "Dow Jones Korea Dividend 30 지수 (Price Return)", "코스피 배당성장 50",
        "코스피 고배당 50", "코스피 200 고배당지수", "MKF 웰스 고배당20", "MKF 배당귀족 지수(시장가격지수)",
        "FnGuide 고배당저변동50 지수", "FnGuide SLV 배당가치 지수", "FnGuide 고배당 알파 지수",
    ), "국내 배당"),
    **dict.fromkeys((
        "Dow Jones U.S. Dividend 100 Price Return Index", "Dow Jones U.S. Dividend 100 Index(TR)",
        "Dow Jones U.S Select Dividend Index(시장가격지수)", "S&P 500 Dividend Aristocrats 지수 (Price Return)",
        "S&P Dividend Monarchs Index(Price Return)", "WisdomTree U.S. Quality Dividend Growth Index (Price Return)",
        "Nasdaq US Low Volatility Dividend Achievers Index", "Solactive U.S. Dividend TOP 30 Index PR",
        "Dow Jones U.S. High Dividend 10 Index", "Akros 미국 고배당주 20 지수", "Euro STOXX Select Dividend 30",
    ), "해외 배당"),
}

# 국내 테마에서 뺄 비주식·단일종목 표기(기초지수 이름에 포함)
# 영문 채권·하이일드·원자재 표기를 더했다 (docs/infra.md 25.544, 감사 재현) — "Bloomberg US Corporate Bond",
# "ICE BofA US High Yield", "iBoxx", "S&P GSCI Silver", "Copper" 가 **테마(주식형)** 로 통과했다(etf.md 10.2).
# `Cash` 는 낱말로만 본다 — "Pacer US Cash Cows 100"(주식형)이 빠졌다. 채굴 기업·원자재 생산 기업·"Silver Economy"
# (고령화) 지수는 주식형이라 남긴다 (25.548, 교차검증)
KR_NON_EQUITY_PATTERN = re.compile(
    r"채권|국채|국고|국공채|KTB|금리|(?<![A-Za-z])CD(?![A-Za-z])|KOFR|SOFR|MMF|머니마켓|단기자금|"
    r"\bCash\b(?! Cows)|MSB|금융채|"
    # 영문 선물 표기 (25.933, 감사 재현) — 외국 기초지수는 한국거래소가 영문으로 준다. "S&P 500 Futures Index(ER)"·
    # "NASDAQ 100 Futures Index (ER)"·"S&P 500 VIX Short-Term Futures Index ER" 가 **테마** 로 통과했다. 한글 `선물`
    # 만 알았다
    r"Treasury|T-Bill|선물|Futures|\bVIX\b|달러|금현물|골드|Gold(?! Min)|원유|WTI|혼합|생애|TDF|"
    r"\bBonds?\b|\bCorporate\b|High Yield|하이일드|iBoxx|Aggregate|Credit|"
    r"Commodit(?!y Producers)|GSCI|Silver(?! Min| Economy)|Copper(?! Min)|은선물|구리(?!채굴)|원자재|"
    r"KRX 삼성전자 지수|KRX SK하이닉스 지수",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------
# 경고 (10.4) — 화면이 그대로 쓴다
# ----------------------------------------------------------------------

WARNINGS = {
    "공통": (
        "위성은 핵심(넓은 지수)을 대신하지 않습니다. 한 방향에 몰려 있습니다. "
        "위성 비중의 외부 표준은 없어 직접 정하세요"
    ),
    "업종": "한 업종에 몰려 있습니다. 업종의 흥망은 20년 사이에 바뀝니다",
    "배당": (
        "분배금이 현금으로 나옵니다. 적립식이면 직접 다시 사야 합니다. "
        "배당수익률·분배 이력은 이 앱에 없습니다"
    ),
    "테마": (
        "테마 펀드는 오래 살아남는 경우가 드뭅니다. 15년 전 테마 펀드의 60% 가 청산됐고 9% 만 살아남아 "
        "시장을 이겼습니다 (Morningstar Global Thematic Funds Landscape 2024, p.13-14). "
        "적립 대상이 아니라 소액 위성으로만 보세요"
    ),
    "국내": "총보수 자료가 없어 보수를 반영하지 않았습니다",
}


@dataclass
class Assignment:
    group: str  # 배당 업종 테마
    sub_group: str  # 비교 단위


def _info(label: str, display: str, source: str, as_of: str | None) -> dict:
    """판정에 쓰지 않는 참고 행. passed=None 이면 화면이 '참고' 로 그린다."""
    return {
        "label": label, "display": display, "threshold": "참고", "source": source, "as_of": as_of, "passed": None,
    }


# ----------------------------------------------------------------------
# 미국
# ----------------------------------------------------------------------


def assign_us(symbol: str, name: str, profile: FundProfile | None) -> Assignment | None:
    """어느 위성 묶음인가. 어디에도 안 들어오면 None (핵심이거나 범위 밖)."""
    if symbol in US_SECTOR_WIDE:
        return Assignment("업종", US_SECTOR_WIDE[symbol])
    category = profile.category if profile else None
    if not category:
        return None
    if category in US_DIVIDEND_CATEGORIES and US_DIVIDEND_NAME_PATTERN.search(name):
        return Assignment("배당", US_DIVIDEND_CATEGORIES[category])
    if category in US_THEME_CATEGORIES:
        return Assignment("테마", category)
    return None


def evaluate_us(symbol: str, name: str, profile: FundProfile, as_of: date, fetched: str) -> core.Evaluation | None:
    """미국 위성 판정. 묶음에 안 들어오면 None."""
    assignment = assign_us(symbol, name, profile)
    if assignment is None:
        return None

    # 근거표는 여기서 처음부터 다시 만든다. 핵심 판정은 넓은 지수 목록에서 멈춰 뒤 조건의 행이 없다.
    rows: list[dict] = []
    if profile.category:
        leveraged_category = profile.category.startswith(core.TRADING_CATEGORY_PREFIX)
        rows.append(core._criterion(
            "레버리지·인버스 아님", f"분류 {profile.category}",
            f"분류가 {core.TRADING_CATEGORY_PREFIX} 로 시작하지 않음", core.SOURCE_PROFILE, fetched,
            not leveraged_category and not core.name_looks_leveraged(name),
        ))

    def result(passed: bool, reason: str | None) -> core.Evaluation:
        ev = core.Evaluation(
            symbol, name, profile, passed, assignment.group, reason, rows,
            country="US", category=f"{assignment.group}:{assignment.sub_group}",
        )
        if passed:
            ev.metrics = {"expense": float(profile.expense_ratio or 0), "assets": float(profile.total_assets or 0)}
        return ev

    rows.append(core._criterion(
        "위성 묶음", f"{assignment.group} · {assignment.sub_group}", "docs/etf.md 10.2",
        core.SOURCE_PROFILE, fetched, True,
    ))

    option = US_OPTION_NAME_PATTERN.search(name) or (profile.category in US_OPTION_CATEGORIES)
    rows.append(core._criterion(
        "옵션형 아님", "이름·분류에 옵션형 표기 없음" if not option else "옵션형 표기 있음",
        "buywrite·covered call·buffer 등 아님", core.SOURCE_PROFILE, fetched, not option,
    ))
    if option:
        return result(False, "옵션형 상품 (분배금을 위해 상승을 판다)")

    if core.name_looks_leveraged(name) or (profile.category or "").startswith(core.TRADING_CATEGORY_PREFIX):
        return result(False, "레버리지·인버스 상품 (20년 적립과 정반대)")

    missing = [
        label for label, value in (
            # 분류도 필수다 (docs/etf.md 9.3 "값 확인", 25.761 ETF 감사) — 업종 목록 기호(XLK 등)는
            # 분류 없이 묶음에 들어와 분류로 거르는 레버리지 검사(TRADING_CATEGORY_PREFIX)를 건너뛰고 통과했다
            ("분류", profile.category),
            ("총보수", profile.expense_ratio), ("순자산", profile.total_assets), ("설정일", profile.inception_date),
        ) if value is None
    ]
    if missing:
        return result(False, f"확인할 수 없는 값: {', '.join(missing)}")

    min_years = MIN_YEARS_THEME if assignment.group == "테마" else MIN_YEARS_US_LONG
    years = core.years_between(str(profile.inception_date), as_of)
    rows.append(core._criterion(
        "운용 이력", f"설정 {profile.inception_date} ({years:.1f}년)" if years is not None else "알 수 없음",
        f"≥ {min_years}년", core.SOURCE_PROFILE, fetched, years is not None and years >= min_years,
    ))
    if years is None:  # 모르는 값을 미달로 적지 않는다 (25.759, ETF 감사)
        return result(False, f"확인할 수 없는 값: 설정일 ({profile.inception_date})")
    if years < min_years:
        return result(False, f"운용 {min_years}년 미만 (설정 {profile.inception_date})")

    assets = float(profile.total_assets or 0)
    rows.append(core._criterion(
        "규모", f"순자산 {core.fmt_usd(assets)}", f"≥ {core.fmt_usd(core.MIN_ASSETS_USD)}",
        core.SOURCE_PROFILE, fetched, assets >= core.MIN_ASSETS_USD,
    ))
    if assets < core.MIN_ASSETS_USD:
        return result(False, f"순자산 {core.fmt_usd(core.MIN_ASSETS_USD)} 미만")

    turnover = profile.turnover_est
    if turnover is not None:
        rows.append(core._criterion(
            "유동성", f"하루 거래대금 약 {core.fmt_usd(turnover)} (평균 거래량 × 전일 종가, 추정)",
            f"≥ {core.fmt_usd(core.MIN_TURNOVER_USD)}", core.SOURCE_PROFILE, fetched,
            turnover >= core.MIN_TURNOVER_USD,
        ))
    if turnover is None:  # 거래량이 없으면 "미만" 이 아니다 — 핵심 판정과 같은 말 (25.759, ETF 감사)
        return result(False, "확인할 수 없는 값: 거래량")
    if turnover < core.MIN_TURNOVER_USD:
        return result(False, f"거래대금 {core.fmt_usd(core.MIN_TURNOVER_USD)} 미만")

    positions = [p for p in (profile.stock_position, profile.bond_position) if p is not None]
    if positions:
        total = sum(positions)
        rows.append(core._criterion(
            "보유 비중 합", f"주식+채권 {total * 100:.1f}%", f"≤ {MAX_POSITION_SUM * 100:.0f}%",
            core.SOURCE_PROFILE, fetched, total <= MAX_POSITION_SUM,
        ))
        if total > MAX_POSITION_SUM:
            return result(False, f"보유 비중 합 {total * 100:.0f}% (파생으로 부풀린 구조)")

    if profile.holdings:
        top = sum(h.pct for h in profile.holdings)
        shown = f"상위 {len(profile.holdings)}개 합 {top * 100:.1f}%"
        rows.append(_info("상위 보유 비중", shown, core.SOURCE_PROFILE, fetched))
    else:
        rows.append(_info("상위 보유 비중", "보유종목 정보 없음", core.SOURCE_PROFILE, fetched))

    return result(True, None)


# ----------------------------------------------------------------------
# 국내
# ----------------------------------------------------------------------


def assign_kr(index_name: str) -> Assignment | None:
    if not index_name:
        return None
    if index_name in core.KR_BROAD_INDEXES:
        return None  # 핵심이다
    if index_name in KR_SECTOR_INDEXES:
        return Assignment("업종", index_name)
    if index_name in KR_DIVIDEND_INDEXES:
        return Assignment("배당", index_name)
    if KR_OPTION_INDEX_PATTERN.search(index_name) or KR_NON_EQUITY_PATTERN.search(index_name):
        return None  # 옵션형·비주식·단일종목은 위성 범위 밖
    return Assignment("테마", index_name)


def distribution_type(index_name: str) -> str:
    """기초지수 이름의 TR / PR 표기. 없으면 '표기 없음'."""
    if re.search(r"(?<![A-Za-z])TR(?![A-Za-z])|Total Return|총수익", index_name, re.IGNORECASE):
        return "TR (분배금 재투자형 지수)"
    if re.search(r"(?<![A-Za-z])PR(?![A-Za-z])|Price Return|시장가격", index_name, re.IGNORECASE):
        return "PR (분배금을 직접 다시 사야 함)"
    return "표기 없음"


def evaluate_kr(inp: core.KrEtfInput, fetched: str) -> core.Evaluation | None:
    """국내 위성 판정. 묶음에 안 들어오면 None."""
    assignment = assign_kr(inp.index_name)
    if assignment is None:
        return None

    base = core.evaluate_kr(inp, fetched)
    rows = [row for row in base.criteria if row["label"] != "넓은 지수"]

    rows.insert(1 if rows else 0, core._criterion(
        "위성 묶음", f"{assignment.group} · 기초지수 {inp.index_name}", "docs/etf.md 10.2",
        core.SOURCE_KRX, fetched, True,
    ))

    def result(passed: bool, reason: str | None) -> core.Evaluation:
        ev = core.Evaluation(
            inp.symbol, inp.name, None, passed, assignment.group, reason, rows,
            country="KR", category=f"{assignment.group}:{assignment.sub_group}",
        )
        if passed:
            ev.metrics = {"assets": float(inp.net_assets or 0), "premium": float(inp.premium_abs_avg or 0)}
        return ev

    # 핵심 판정이 넓은 지수에서 멈췄다면 뒤 조건(3년·순자산·거래대금·괴리율)을 보지 못했다.
    # 같은 입력을 넓은 지수 목록에 있는 것처럼 다시 판정해 나머지 조건을 채운다.
    if base.excluded_reason and base.excluded_reason.startswith("좁거나 기운 지수"):
        probe = core.KrEtfInput(**{**inp.__dict__, "index_name": "코스피 200"})
        rest = core.evaluate_kr(probe, fetched)
        rows.extend(row for row in rest.criteria if row["label"] not in ("상품 유형", "넓은 지수"))
        reason = rest.excluded_reason
    else:
        reason = base.excluded_reason

    rows.append(_info("분배금 처리", distribution_type(inp.index_name), core.SOURCE_KRX, fetched))
    rows.append(_info("환헤지", "(H) 환헤지형" if "(H)" in inp.name else "환헤지 표기 없음", core.SOURCE_KRX, fetched))

    if reason:
        return result(False, reason)
    return result(True, None)


# ----------------------------------------------------------------------
# 점수와 문장
# ----------------------------------------------------------------------


def score(evaluations: list[core.Evaluation]) -> list[str]:
    """하위 묶음 안에서만 순위. category 에 묶음 키를 넣어 핵심 점수 함수를 그대로 쓴다.

    근거표를 못 만든 것을 빼는 일도 핵심 함수가 함께 한다(docs/infra.md 25.174).
    돌려주는 것은 뺀 것의 심볼 — **비어 있어야 정상이다.**
    """
    return core.score_within_categories(evaluations)


def sub_group_of(ev: core.Evaluation) -> str:
    return (ev.category or ":").split(":", 1)[1]


def rationale_text(ev: core.Evaluation) -> str:
    if not ev.passed:
        return f"제외: {ev.excluded_reason}"
    sub = sub_group_of(ev)
    head = (
        f"{ev.bucket} · {sub} 에서 조건을 통과한 유일한 ETF"
        if ev.category_size == 1
        else f"{ev.bucket} · {sub} {ev.category_size}개 중 {ev.rank_in_category}위"
    )
    if ev.country == "US" and ev.profile is not None:
        tail = f"총보수 {core.fmt_pct(ev.profile.expense_ratio)}. 순자산 {core.fmt_usd(ev.profile.total_assets)}"
    else:
        tail = (
            f"순자산 {core.fmt_krw(ev.metrics.get('assets'))}. "
            f"평균 괴리율 {core.fmt_pct(ev.metrics.get('premium'), 3)}. "
            "총보수는 자료가 없어 반영하지 않음"
        )
    return f"{head}. {tail}."


def warnings_for(ev: core.Evaluation) -> list[str]:
    out = [WARNINGS["공통"], WARNINGS.get(ev.bucket or "", "")]
    if ev.country == "KR":
        out.append(WARNINGS["국내"])
    return [w for w in out if w]
