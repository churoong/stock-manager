"""장기 적립 종목 판정 (국내 v1, 미국 v1). 순수 계산만 한다. 네트워크와 DB 를 모른다.

규칙의 단일 정의처는 docs/accumulation.md 다. 문턱을 바꾸면 그 문서도 같은 커밋에서 고친다.

가격·수익률·밸류 점수를 쓰지 않는다. 5년 재무의 연속성·최저값·최대값과 배당 연속성만 본다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from batch.services import criteria
from batch.services import metrics as mt
from batch.services.etf import percentile_scores

CALC_VERSION = 1

YEARS = 5
MIN_LISTED_YEARS = 10  # G2
MAX_DEBT_RATIO = 1.0  # G7 5년 최대 부채비율 100%
MAX_CAP_RANK = 400  # G9 유니버스 안 시총 순위

SOURCE_FIN = "financials (DART 사업보고서, 연결)"
SOURCE_DIV = "stock_dividends (DART 배당에 관한 사항)"
SOURCE_UNI = "universe_members"

#: 근거표를 못 만들어 뺀 것의 깔때기 칸 이름. 관문(G1~G9)이 아니라 별도의 칸이다.
#: 웹의 `GATE_LABELS` 에 같은 이름이 있어야 화면에 글로 뜬다(web/lib/accumulation.ts)
NO_CRITERIA_GATE = "근거"


@dataclass(frozen=True)
class Rules:
    """나라별로 다른 부분 (docs/accumulation.md 7장). G2·G4·G5·G9 가 다르고 나머지 문턱은 같다."""

    country: str
    label: str
    source_fin: str
    source_div: str
    max_cap_rank: int
    # 미국은 상장일을 모른다(가격 관측 첫날뿐). 연간 재무가 10개 연도 연속으로 있는지로 대신한다
    history_from_filings: bool
    # 미국은 금융업을 따로 거르지 않는다. 은행·보험은 G7 부채비율 100% 에서 빠진다
    financial_proxy: bool
    # 미국 대형사 21% 가 영업이익 줄을 내지 않는다(JNJ·CVX). 그해만 세전이익으로 대신한다 (사용자 결정 2026-09-17)
    pretax_proxy: bool
    currency: str


KR = Rules("KR", "국내", SOURCE_FIN, SOURCE_DIV, MAX_CAP_RANK, False, True, False, "KRW")
# 500위: S&P 500 에서 발상만 빌렸다. 미국 유니버스 안 순위다 (사용자 결정 2026-09-17)
US = Rules(
    "US", "미국", "financials (SEC 10-K, 연결)", "stock_dividends (SEC 10-K 현금흐름표 배당 지급액)",
    500, True, False, True, "USD",
)
RULES = {"KR": KR, "US": US}

# 순위 입력 네 개 (docs/accumulation.md 3장). 같은 가중이다 [확인필요]
RANK_INPUTS = (
    ("margin_std", False, "영업이익률 5년 표준편차"),
    ("min_roe", True, "5년 최저 ROE"),
    ("max_debt", False, "5년 최대 부채비율"),
    ("retained_up", True, "이익잉여금 증가 구간 수"),
)


@dataclass
class FinYear:
    fiscal_year: int
    report_date: str | None
    revenue: float | None
    operating_income: float | None
    net_income: float | None
    total_equity: float | None
    total_liabilities: float | None
    retained_earnings: float | None
    pretax_income: float | None = None

    def profit(self, pretax_proxy: bool) -> tuple[float | None, bool]:
        """G5·이익률에 쓰는 이익과 대리 여부. 영업이익이 있으면 늘 그것이다."""
        if self.operating_income is not None or not pretax_proxy:
            return self.operating_income, False
        return self.pretax_income, self.pretax_income is not None


@dataclass
class DivYear:
    fiscal_year: int
    as_of_date: str | None
    cash_dividend_total: float | None
    dps_common: float | None
    payout_ratio: float | None


@dataclass
class StockInput:
    stock_id: int
    ticker: str
    name: str
    market: str
    listed_date: str | None
    market_cap: float | None
    cap_rank: int | None  # 유니버스 안 시총 순위 (1 = 가장 큼)
    years: dict[int, FinYear] = field(default_factory=dict)
    dividends: dict[int, DivYear] = field(default_factory=dict)
    # 미국 G2: 연간 재무가 있는 회계연도 전체 (5년 창 밖 포함)
    filing_years: set[int] = field(default_factory=set)
    # 편입·시총 순위를 읽은 유니버스 스냅샷 날짜 — G1·G9 근거 행의 기준일이다 (docs/infra.md 25.261)
    snapshot_date: str | None = None


@dataclass
class Aggregate:
    complete: bool  # 5개 연도 연결 재무가 모두 있는가
    revenue_null: int = 0
    op_positive: int = 0
    op_proxy_years: int = 0
    equity_positive: int = 0
    net_income_null: int = 0
    min_roe: float | None = None
    avg_roe: float | None = None
    max_debt: float | None = None
    margin_std: float | None = None
    retained_up: int = 0
    retained_pairs: int = 0


@dataclass
class Judgement:
    stock_id: int
    ticker: str
    name: str
    market: str
    passed: bool
    first_failed_gate: str | None
    excluded_reason: str | None
    criteria: list[dict] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    score: float | None = None
    rank: int | None = None
    group_size: int | None = None
    component_scores: dict[str, float] = field(default_factory=dict)


def dedupe_share_classes(
    inputs: list[StockInput], company_of: dict[int, str], turnover: dict[int, float | None]
) -> tuple[list[StockInput], dict[int, str]]:
    """한 회사의 주식 종류가 여럿이면(LEN·LEN.B, GOOGL·GOOG) 거래대금이 가장 큰 하나만 남긴다.

    같은 10-K 를 공유하므로 재무 판정이 똑같고, 둘 다 남기면 순위 두 자리를 한 회사가 차지한다.
    company_of 는 stock_id → 회사 키(최신 10-K 접수번호). 키가 없는 종목은 그대로 둔다.
    돌려주는 dict 는 뺀 stock_id → 남긴 종목의 티커.
    """
    groups: dict[str, list[StockInput]] = {}
    for inp in inputs:
        key = company_of.get(inp.stock_id)
        if key:
            groups.setdefault(key, []).append(inp)
    dropped: dict[int, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = max(members, key=lambda i: (turnover.get(i.stock_id) or 0, -len(i.ticker), i.ticker))
        for other in members:
            if other is not keep:
                dropped[other.stock_id] = keep.ticker
    return [i for i in inputs if i.stock_id not in dropped], dropped


def display_name(name: str) -> str:
    """나스닥 목록 이름의 꼬리(" - Common Stock", " Common Stock" 등)를 뗀다. 화면·로그·텔레그램 리포트용.

    2026-10-05 미국 리포트에서 "○○○ Holding Ltd - Ordinary Shares 4/15" 처럼 꼬리가 한 줄을 다
    먹었다 — " - Ordinary Shares"·" Class A Ordinary Shares" 를 더했다 (docs/infra.md 25.957). 국내 이름은 그대로다.
    """
    for tail in (" - Common Stock", " Class A Common Stock", " Common Stock", " Common Shares", " - Class A",
                 " - Ordinary Shares", " Class A Ordinary Shares", " Ordinary Shares"):  # fmt: skip
        if name.endswith(tail):
            return name[: -len(tail)].rstrip(" -")
    return name


def window(latest_fy: int) -> list[int]:
    return list(range(latest_fy - YEARS + 1, latest_fy + 1))


def years_for(inp: StockInput, reference_fy: int, rules: Rules = KR) -> list[int]:
    """그 종목의 5년 창.

    국내는 대부분 12월 결산이라 기준 창 하나를 같이 쓴다(비12월 결산도 기준 창 — 한 해 묵은 자료로
    판정, 25.632). 미국은 결산월이 제각각이라(MSFT 6월, NVDA 1월) 회사마다
    자기 최신 회계연도로 끝나는 창을 쓴다. 다만 최신 연도가 기준 연도(가장 많은 회사의 최신 연도)보다
    오래됐으면 공시가 끊긴 회사로 보고 기준 창을 줘 G3 에서 떨어지게 한다 (docs/accumulation.md 7장).
    """
    if rules.country != "US" or not inp.filing_years:
        return window(reference_fy)
    own = max(inp.filing_years)
    return window(own) if own >= reference_fy else window(reference_fy)


def _criterion(label: str, display: str, threshold: str, source: str, as_of: str | None, passed: bool | None) -> dict:
    return {
        "label": label, "display": display, "threshold": threshold,
        "source": source, "as_of": as_of, "passed": passed,
    }


def pct(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f}%"


def eok(value: float | None, currency: str = "KRW") -> str:
    if value is None:
        return "-"
    if currency == "USD":
        return f"${value / 1e9:,.1f}B"
    if abs(value) >= 1e12:
        return f"{value / 1e12:.2f}조원"
    return f"{value / 1e8:,.0f}억원"


def aggregate(inp: StockInput, years: list[int], rules: Rules = KR) -> Aggregate:
    rows = [inp.years.get(y) for y in years]
    if any(r is None for r in rows):
        return Aggregate(complete=False)
    fin = [r for r in rows if r is not None]

    agg = Aggregate(complete=True)
    agg.revenue_null = sum(1 for r in fin if r.revenue is None)
    profits = [r.profit(rules.pretax_proxy) for r in fin]
    agg.op_positive = sum(1 for value, _ in profits if (value or 0) > 0)
    agg.op_proxy_years = sum(1 for _, proxy in profits if proxy)
    agg.equity_positive = sum(1 for r in fin if (r.total_equity or 0) > 0)
    agg.net_income_null = sum(1 for r in fin if r.net_income is None)

    roes = [r.net_income / r.total_equity for r in fin if r.net_income is not None and (r.total_equity or 0) > 0]
    if len(roes) == YEARS:
        agg.min_roe = min(roes)
        agg.avg_roe = sum(roes) / YEARS
    debts = [
        r.total_liabilities / r.total_equity
        for r in fin
        if r.total_liabilities is not None and (r.total_equity or 0) > 0
    ]
    if len(debts) == YEARS:
        agg.max_debt = max(debts)
    margins = [
        value / r.revenue
        for r, (value, _) in zip(fin, profits, strict=True)
        if value is not None and (r.revenue or 0) > 0
    ]
    if len(margins) == YEARS:
        agg.margin_std = statistics.stdev(margins)
    for prev, cur in zip(fin, fin[1:], strict=False):
        if prev.retained_earnings is not None and cur.retained_earnings is not None:
            agg.retained_pairs += 1
            if cur.retained_earnings > prev.retained_earnings:
                agg.retained_up += 1
    return agg


def measure_thresholds(
    inputs: list[StockInput], years: list[int], rules: Rules = KR, reference_fy: int | None = None
) -> dict:
    """G8 중앙값. 연결 5개년이 있고 5년 ROE 를 낼 수 있는 집단에서 잰다(docs/accumulation.md G8). 나라마다 따로.

    reference_fy 를 주면 종목마다 years_for 로 창을 고른다(미국).
    """
    aggs = (
        aggregate(i, years_for(i, reference_fy, rules) if reference_fy else years, rules) for i in inputs
    )
    values = [a.avg_roe for a in aggs if a.complete and a.avg_roe is not None]
    median = statistics.median(values) if values else None
    return {"roe_median": median, "roe_n": len(values), "years": years}


def evaluate(inp: StockInput, years: list[int], thresholds: dict, as_of: date, rules: Rules = KR) -> Judgement:
    rows: list[dict] = []
    latest = inp.years.get(years[-1])
    fin_as_of = latest.report_date if latest else None
    today = as_of.isoformat()

    def fail(gate: str, reason: str) -> Judgement:
        return Judgement(inp.stock_id, inp.ticker, inp.name, inp.market, False, gate, reason, rows)

    # G1·G9 의 수치(편입·시총 순위)는 **유니버스 스냅샷**에서 왔다. 기준일은 판정을 돌린 날이 아니라 그 스냅샷 날짜다
    # (docs/infra.md 25.261). 예전에는 실행일(UTC)을 적어, 유니버스 갱신이 몇 주 멈춰도
    # 근거표가 "오늘 기준" 으로 보였다.
    스냅샷 = inp.snapshot_date or today

    # G1 은 입력 자체가 편입 종목이다. 행으로 남긴다.
    rows.append(
        _criterion("G1 유니버스 편입", f"{rules.label} 최신 스냅샷 편입", "included = 1", SOURCE_UNI, 스냅샷, True)
    )
    source_fin, source_div = rules.source_fin, rules.source_div

    # G2 상장 경과 (미국은 연간 재무 이력으로 대신)
    listed_ok = False
    display = "상장일 없음"
    if rules.history_from_filings:
        need = set(range(years[-1] - MIN_LISTED_YEARS + 1, years[-1] + 1))
        have = len(need & inp.filing_years)
        listed_ok = have == MIN_LISTED_YEARS
        rows.append(_criterion(
            "G2 공시 이력", f"FY{min(need)}~{years[-1]} 연간 재무 {have}/{MIN_LISTED_YEARS}개 연도",
            f"{MIN_LISTED_YEARS}/{MIN_LISTED_YEARS} (상장일 대신, docs/accumulation.md 7장)",
            source_fin, fin_as_of, listed_ok,
        ))
        if not listed_ok:
            return fail("G2", f"연간 재무 {MIN_LISTED_YEARS}개 연도가 연속으로 있지 않음")
    elif inp.listed_date:
        try:
            listed = date.fromisoformat(inp.listed_date[:10])
            age = mt.years_between(listed, as_of)  # 식의 정의처는 services/metrics (25.109)
            listed_ok = age >= MIN_LISTED_YEARS
            display = f"상장 {inp.listed_date[:10]} ({age:.1f}년)"
        except ValueError:
            pass
    if not rules.history_from_filings:
        rows.append(
            _criterion("G2 상장 경과", display, f"≥ {MIN_LISTED_YEARS}년", "stocks.listed_date", today, listed_ok)
        )
        if not listed_ok:
            return fail("G2", f"상장 {MIN_LISTED_YEARS}년 미만 또는 상장일 없음")

    agg = aggregate(inp, years, rules)
    span = f"FY{years[0]}~{years[-1]}"
    shown = "모두 있음" if agg.complete else "빠진 연도 있음"
    rows.append(_criterion(
        "G3 재무 이력", f"연결 연간 재무 {span} {shown}", f"{YEARS}개 연도", source_fin, fin_as_of, agg.complete,
    ))
    if not agg.complete:
        return fail("G3", f"연결 재무 {YEARS}개 연도가 모두 있지 않음")

    if rules.financial_proxy:
        rows.append(_criterion(
            "G4 금융업 제외(대리)", f"매출 없는 연도 {agg.revenue_null}개", "0개 (docs/accumulation.md G4)",
            source_fin, fin_as_of, agg.revenue_null == 0,
        ))
        if agg.revenue_null:
            return fail("G4", "매출이 없는 연도가 있음 (금융업 대리 판정)")
    else:
        rows.append(_criterion(
            "G4 금융업 제외", "따로 거르지 않음", "G7 부채비율이 대신 거른다 (docs/accumulation.md 7장)",
            source_fin, fin_as_of, None,
        ))

    rows.append(_criterion(
        "G5 영업흑자",
        f"{agg.op_positive}/{YEARS}년 흑자"
        + (f" (영업이익 없는 {agg.op_proxy_years}년은 세전이익으로 대신)" if agg.op_proxy_years else ""),
        f"{YEARS}/{YEARS}년",
        source_fin, fin_as_of, agg.op_positive == YEARS,
    ))
    if agg.op_positive < YEARS:
        return fail("G5", f"{YEARS}년 중 영업적자 연도가 있음")

    g6 = agg.equity_positive == YEARS and agg.net_income_null == 0 and agg.min_roe is not None and agg.min_roe > 0
    rows.append(_criterion("G6 적자·자본잠식 없음", f"5년 최저 ROE {pct(agg.min_roe)}", "자본 > 0, 최저 ROE > 0",
                           source_fin, fin_as_of, g6))
    if not g6:
        return fail("G6", "순손실 또는 자본잠식 연도가 있음")

    g7 = agg.max_debt is not None and agg.max_debt <= MAX_DEBT_RATIO
    rows.append(_criterion("G7 부채비율", f"5년 최대 {pct(agg.max_debt, 0)}", f"≤ {MAX_DEBT_RATIO * 100:.0f}%",
                           source_fin, fin_as_of, g7))
    if not g7:
        return fail("G7", f"5년 최대 부채비율 {pct(agg.max_debt, 0)} > {MAX_DEBT_RATIO * 100:.0f}%")

    median = thresholds.get("roe_median")
    g8 = median is not None and agg.avg_roe is not None and agg.avg_roe >= median
    threshold = f"≥ 중앙값 {pct(median, 2)} (n={thresholds.get('roe_n')}, {today})"
    rows.append(_criterion("G8 수익성", f"5년 평균 ROE {pct(agg.avg_roe, 2)}", threshold, source_fin, fin_as_of, g8))
    if not g8:
        return fail("G8", "5년 평균 ROE 가 집단 중앙값 미만")

    g9 = inp.cap_rank is not None and inp.cap_rank <= rules.max_cap_rank
    rows.append(_criterion("G9 규모", f"유니버스 시총 {inp.cap_rank}위 ({eok(inp.market_cap, rules.currency)})",
                           f"≤ {rules.max_cap_rank}위 (유니버스 안 순위)", SOURCE_UNI, 스냅샷, g9))
    if not g9:
        return fail("G9", f"유니버스 시총 {rules.max_cap_rank}위 밖")

    divs = [inp.dividends.get(y) for y in years]
    paid = sum(1 for d in divs if d is not None and (d.cash_dividend_total or 0) > 0)
    div_as_of = max((d.as_of_date for d in divs if d and d.as_of_date), default=None)
    # 국내는 배당 행이 **아예 없는** 해를 무배당과 가른다 (docs/infra.md 25.634, 감사). DART 는 무배당도 "-" 로
    # 행을 주므로(dart_dividends) 행이 없으면 수집이 안 됐거나 배당 항목 자체가 없다(013) — 한도·연속 실패로 멈춘 날
    # 그 종목이
    # "현금배당이 없는 해가 있음" 으로 떨어졌다. 판정은 그대로 떨어뜨린다(확인할 수 없는 것은 통과시키지 않는다).
    # 미국은 태그가 없으면 행을 안 넣는 것이 무배당이라(us_financials) 가르지 않는다
    빈해 = sum(1 for d in divs if d is None) if rules.country == "KR" else 0
    shown_g10 = f"{span} 중 {paid}년 현금배당" + (f", {빈해}년 배당 자료 없음" if 빈해 else "")
    rows.append(_criterion("G10 배당 연속성", shown_g10, f"{YEARS}/{YEARS}년 (총액 기준)",
                           source_div, div_as_of, paid == YEARS))
    if paid < YEARS:
        if 빈해 and paid + 빈해 == YEARS:
            return fail("G10", f"배당 자료가 없는 해가 있음 ({빈해}년 — DART 에 배당 항목이 없거나 수집이 안 됐다)")
        return fail("G10", f"최근 {YEARS}년 중 현금배당이 없는 해가 있음")

    # 참고 행 — 판정에 쓰지 않는다
    totals = [d.cash_dividend_total for d in divs if d is not None]
    cuts = sum(1 for a, b in zip(totals, totals[1:], strict=False) if a and b and b < a)
    last = divs[-1]
    if last and last.dps_common is not None:
        per_share = f"${last.dps_common:,.2f}" if rules.currency == "USD" else f"{last.dps_common:,.0f}원"
        ratio = "-" if last.payout_ratio is None else f"{last.payout_ratio}%"
        shown = f"FY{years[-1]} 주당 {per_share} · 배당성향 {ratio} · 총액 감소 {cuts}회"
    else:
        shown = f"총액 감소 {cuts}회"
    rows.append(_criterion("배당 (참고)", shown, "참고", source_div, div_as_of, None))

    # **모르는 값을 0 으로 적지 않는다** (2026-09-23, docs/infra.md 25.181).
    #
    # 예전에는 `agg.margin_std if … else 0.0` 이었다. `margin_std` 는 **낮을수록 좋은**
    # 값이라 0.0 은 **가장 안정적**이라는 뜻이다 — 구하지 못한 종목이 그 축에서
    # 백분위 100 을 받고 순위 위로 올라갔다. 미국은 G4(매출 유무)를 안 보므로
    # 매출 줄이 없는 해가 있는 회사가 G1~G10 을 통과한 채로 여기 온다.
    #
    # `min_roe`·`max_debt` 는 G6·G7 이 `is not None` 을 이미 보장하지만, 같은 `or 0.0`
    # 덫을 남겨 두지 않는다 — `max_debt` 도 낮을수록 좋은 값이다.
    metrics: dict[str, float | None] = {
        "margin_std": agg.margin_std,
        "min_roe": agg.min_roe,
        "max_debt": agg.max_debt,
        # 네 구간을 다 알 때만 센다 (docs/infra.md 25.632, 감사). 예전에는 늘 `float(retained_up)` 라
        # 이익잉여금 줄이 없는 회사가 **0 구간 — 가장 나쁨** 으로 분포에 들어가 남의 백분위까지 움직였고,
        # 두 구간만 아는 회사는 2/2 가 3/4 보다 낮게 줄 섰다. 다른 축(`len(...) == YEARS`)과 같이 맞춘다
        "retained_up": float(agg.retained_up) if agg.retained_pairs == YEARS - 1 else None,
    }
    for key, _, label in RANK_INPUTS:
        value = metrics[key]
        if value is None:
            일부 = key == "retained_up" and agg.retained_pairs
            shown = f"모름 ({agg.retained_up}/{agg.retained_pairs} 구간만 앎)" if 일부 else "모름"
            문턱 = "순위 입력 (참고) — 구하지 못해 순위에서 뺐습니다"
        else:
            shown = f"{value:.0f}/{agg.retained_pairs}" if key == "retained_up" else pct(value, 2)
            문턱 = "순위 입력 (참고)"
        rows.append(_criterion(label, shown, 문턱, source_fin, fin_as_of, None))

    return Judgement(inp.stock_id, inp.ticker, inp.name, inp.market, True, None, None, rows, metrics)


def rank(judgements: list[Judgement]) -> list[str]:
    """통과 종목 한 집단 안에서 네 입력의 백분위를 같은 가중으로 평균한다.

    **먼저 근거표를 못 만든 것을 뺀다** (CLAUDE.md 절대 규칙, docs/infra.md 25.174).
    순위를 매기기 전이라야 집단 크기와 백분위가 실제 후보만 세고 만들어진다.
    돌려주는 것은 뺀 종목의 티커 — **비어 있어야 정상이다.**
    """
    뺀것 = criteria.drop_unverifiable(judgements, lambda j: j.ticker)
    for j in judgements:
        if j.excluded_reason == criteria.NO_CRITERIA_REASON:
            # 깔때기 표(web/lib/accumulation.GATE_LABELS)가 "어디서 떨어졌나" 로 센다.
            # 관문에서 떨어진 것이 아니므로 제 이름을 준다
            j.first_failed_gate = NO_CRITERIA_GATE

    passed = [j for j in judgements if j.passed]
    if not passed:
        return 뺀것
    # **모르는 축은 빼고 아는 축만으로 줄 세운다** (docs/infra.md 25.181).
    #
    # 모르는 값을 0 으로 채워 분포에 넣으면 두 가지가 한꺼번에 틀어진다 —
    # 그 종목이 "가장 좋음" 을 받고, **남들의 백분위도 그 가짜 값을 세고 만들어진다.**
    # 그래서 축마다 값을 아는 종목만 모아 백분위를 내고, 점수는 **아는 축의 평균**이다.
    # 어느 축이 빠졌는지는 근거표의 그 줄이 "모름" 으로 말한다.
    components: dict[str, dict[int, float]] = {}
    for key, higher, _ in RANK_INPUTS:
        아는것 = [(i, j.metrics.get(key)) for i, j in enumerate(passed) if j.metrics.get(key) is not None]
        점수들 = percentile_scores([float(v) for _, v in 아는것], higher)  # type: ignore[arg-type]
        components[key] = {i: 점수 for (i, _), 점수 in zip(아는것, 점수들, strict=True)}

    for index, j in enumerate(passed):
        제것 = {key: components[key][index] for key, _, _ in RANK_INPUTS if index in components[key]}
        j.component_scores = {key: round(값, 1) for key, 값 in 제것.items()}
        # 아는 축이 하나도 없으면 줄 세울 수가 없다. G6·G7 이 둘을 보장하므로
        # 실제로는 오지 않지만, 0 으로 나누는 것보다 **점수 없음**이 옳다
        j.score = round(sum(제것.values()) / len(제것), 1) if 제것 else None
        j.group_size = len(passed)
    # 점수를 못 낸 것은 뒤로 (`-(x.score or 0)` 이면 0 점과 섞인다)
    for position, j in enumerate(
        sorted(passed, key=lambda x: (x.score is None, -(x.score or 0), x.ticker)), start=1
    ):
        j.rank = position
    return 뺀것


def rationale_text(j: Judgement) -> str:
    if not j.passed:
        return f"신규 적립 중단 검토 대상 또는 제외: {j.excluded_reason} ({j.first_failed_gate})"
    return (
        f"적립 후보 {j.group_size}개 중 안정성 {j.rank}위. 5년 연속 영업흑자·배당, "
        f"최저 ROE {pct(j.metrics.get('min_roe'))}, 최대 부채비율 {pct(j.metrics.get('max_debt'), 0)}."
    )
