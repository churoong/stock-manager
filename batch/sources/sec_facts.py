"""SEC companyfacts → 연간 재무 (docs/data-sources.md 14.1·14.2).

한 회사의 XBRL fact 전체(평균 gzip 115KB, 풀면 1.5MB, JPM 7.9MB)에서 10-K 마다 **그 공시의 당기값**만 뽑는다.

왜 이렇게 고르나 (2026-09-17 AAPL·JPM 실측)
  - 10-K 하나에 3개 연도 손익이 실린다. `fy`·`fp` 는 공시의 연도라 기간을 알려 주지 않는다
  - `frame` 은 가장 늦게 제출된 값에만 붙어 시점 조회를 망친다 → 쓰지 않는다
  - 그래서 공시(accn)마다 **연간 길이(350~380일) 기간 fact 의 가장 늦은 end** 를 결산일로 보고,
    손익은 그 end 로 끝나는 연간 fact, 재무상태는 그 end 시점 fact 를 쓴다
  - dei 는 표지 날짜가 섞여 결산일 판정에 쓰지 않는다

연도 표기: 결산일의 연도. 52/53주 회계연도가 1월 첫 주에 끝나면 전년으로 본다(AAPL FY2025 = 2025-09-27 결산,
WMT FY2026 = 2026-01-31 결산으로 회사 표기와 같다).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

SOURCE = "sec_edgar"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})
ANNUAL_MIN_DAYS = 350
ANNUAL_MAX_DAYS = 380

# 앱 열 → us-gaap 개념 우선순위. 단일 정의처는 docs/data-sources.md 14.2 다.
DURATION_FIELDS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenuesNetOfInterestExpense",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "comprehensive_income": ("ComprehensiveIncomeNetOfTax",),
    # 배당 (장기 적립 G10, docs/accumulation.md 7장). 현금흐름표의 지급액. 총액으로 연속성을 본다
    "dividends_paid": ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "DividendsCommonStockCash"),
    # 영업활동 현금흐름 — 발굴 루프 1회차 C(발생액) 의 입력 (docs/factors.md 12.1, docs/infra.md 25.440).
    # 이미 받는 응답이라 호출이 늘지 않는다. financials 열은 없어 **스냅샷 payload 에만** 실린다
    # **계속영업 태그로 넘어가지 않는다** (25.448, 교차검증). 순이익(NetIncomeLoss)은 중단영업을 포함하므로
    # 계속영업 현금흐름과 짝지으면 중단영업 처분이익이 통째로 "발생액" 이 된다(−0.01 이 +0.11 로)
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    # 매출총이익·매출원가 — 발굴 루프 3회차 E(매출총이익/총자산, Novy-Marx 2013)의 입력 (docs/factors.md 12.2, 25.740).
    # 이미 받는 응답이라 호출이 늘지 않는다. financials 열이 없어 **스냅샷 payload 에만** 실린다
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
}
# 주당배당금은 단위가 USD/shares 다. 참고 표시에만 쓴다(액면분할로 해마다 비교할 수 없다)
PER_SHARE_FIELDS: dict[str, tuple[str, ...]] = {
    "dps_common": ("CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"),
}
INSTANT_FIELDS: dict[str, tuple[str, ...]] = {
    "total_assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "total_liabilities": ("Liabilities",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "total_equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    # APIC 를 포함한 태그는 넣지 않는다(14.2)
    "capital_stock": ("CommonStockValue",),
}
# 은행: 매출 태그가 없으면 순이자이익 + 비이자이익. 총이자수익을 매출로 쓰지 않는다(14.2)
BANK_REVENUE = ("InterestIncomeExpenseNet", "NoninterestIncome")
# 부채 태그가 없을 때: 부채와자본총계 − 비지배 포함 자본 (없으면 지배 자본)
LIAB_AND_EQUITY = "LiabilitiesAndStockholdersEquity"
EQUITY_WITH_NCI = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"

# 결산일을 판정할 때 보는 기간 개념. 거의 모든 회사가 하나는 낸다.
PERIOD_PROBES = (*DURATION_FIELDS["revenue"], *DURATION_FIELDS["net_income"], *BANK_REVENUE, "OperatingIncomeLoss")

_ALL_CONCEPTS = frozenset(
    {c for group in (*DURATION_FIELDS.values(), *INSTANT_FIELDS.values()) for c in group}
    | set(BANK_REVENUE)
    | {LIAB_AND_EQUITY, EQUITY_WITH_NCI}
)


@dataclass
class AnnualReport:
    accn: str
    form: str
    filed: str
    period_end: str
    fiscal_year: int
    values: dict[str, float | int | None] = field(default_factory=dict)
    # 어느 개념에서 왔는지. 근거표와 검증에 쓴다
    concepts: dict[str, str] = field(default_factory=dict)


def fiscal_year_of(period_end: str) -> int:
    end = date.fromisoformat(period_end)
    return end.year - 1 if end.month == 1 and end.day <= 7 else end.year


def _days(fact: dict[str, Any]) -> int | None:
    start, end = fact.get("start"), fact.get("end")
    if not start or not end:
        return None
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _is_annual(fact: dict[str, Any]) -> bool:
    days = _days(fact)
    return days is not None and ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


_PER_SHARE_CONCEPTS = frozenset(c for group in PER_SHARE_FIELDS.values() for c in group)


def _index(payload: dict) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """{concept: {accn: [fact, ...]}} 연간 공시 fact 만. 금액은 USD, 주당값은 USD/shares 단위만."""
    gaap = (payload.get("facts") or {}).get("us-gaap") or {}
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for concept in _ALL_CONCEPTS | _PER_SHARE_CONCEPTS:
        units = (gaap.get(concept) or {}).get("units") or {}
        unit = "USD/shares" if concept in _PER_SHARE_CONCEPTS else "USD"
        for fact in units.get(unit) or []:
            if fact.get("form") not in ANNUAL_FORMS or fact.get("val") is None or not fact.get("accn"):
                continue
            out.setdefault(concept, {}).setdefault(str(fact["accn"]), []).append(fact)
    return out


def _pick_duration(facts: list[dict[str, Any]], end: str) -> int | None:
    for fact in facts:
        if fact.get("end") == end and _is_annual(fact):
            return int(fact["val"])
    return None


def _pick_instant(facts: list[dict[str, Any]], end: str) -> int | None:
    for fact in facts:
        if fact.get("end") == end and not fact.get("start"):
            return int(fact["val"])
    return None


#: 발행주식수 개념 우선순위 (docs/data-sources.md 14.2 "주식수" 행과 같은 순서의 앞 둘)
SHARES_CONCEPTS = (("dei", "EntityCommonStockSharesOutstanding"), ("us-gaap", "CommonStockSharesOutstanding"))


def _shares_of(payload: dict, accn: str, end: str) -> tuple[int | None, str | None]:
    """그 공시(accn)의 발행주식수. dei 는 표지 날짜라 결산일과 다르다 — accn 으로만 고른다.
    us-gaap 은 결산일 시점값. 복수 클래스(dei 가 클래스마다 여러 줄)면 **합한다**(같은 표지 날짜의 것만)."""
    facts_all = payload.get("facts") or {}
    for taxonomy, concept in SHARES_CONCEPTS:
        units = ((facts_all.get(taxonomy) or {}).get(concept) or {}).get("units") or {}
        facts = [f for f in units.get("shares") or [] if str(f.get("accn")) == accn and f.get("val") is not None]
        if taxonomy == "us-gaap":
            facts = [f for f in facts if f.get("end") == end]
        if not facts:
            continue
        latest_end = max(str(f.get("end")) for f in facts)
        # 같은 날짜에 **다른 값**이 여럿이면(클래스별 줄 등) 모른다 — 합하지 않고 비운다 (25.450, 교차검증).
        # 예전의 (frame, 값) 중복 제거는 frame 이 한 기간에 한 줄에만 붙어 결과가 멋대로 바뀌었다
        vals = {int(f["val"]) for f in facts if str(f.get("end")) == latest_end}
        if len(vals) != 1:
            return None, None
        return vals.pop(), f"{taxonomy}:{concept}"
    return None, None


WEIGHTED_SHARES = "WeightedAverageNumberOfSharesOutstandingBasic"


def _weighted_shares(payload: dict, accn: str, end: str) -> tuple[int | None, int | None]:
    """그 10-K 의 (당기, 전기) 기본 가중평균 주식수. 둘 다 연간 기간 fact. 전기는 당기 결산일보다 앞선
    가장 늦은 연간 fact 다. 같은 기간에 다른 값이 여럿이면 None."""
    units = ((((payload.get("facts") or {}).get("us-gaap") or {}).get(WEIGHTED_SHARES) or {}).get("units") or {})
    facts = [f for f in units.get("shares") or [] if str(f.get("accn")) == accn and f.get("val") is not None
             and _is_annual(f)]  # fmt: skip

    def one(e: str) -> int | None:
        vals = {int(f["val"]) for f in facts if str(f.get("end")) == e}
        return vals.pop() if len(vals) == 1 else None

    prev_ends = sorted({str(f["end"]) for f in facts if str(f.get("end")) < end})
    return one(end), (one(prev_ends[-1]) if prev_ends else None)


def parse_annual_reports(payload: dict) -> list[AnnualReport]:
    """10-K 공시마다 당기 연간값. 재무 fact 가 없는 공시(Part III 만 고친 10-K/A 등)는 건너뛴다. filed 순."""
    index = _index(payload)
    filings: dict[str, tuple[str, str]] = {}
    for by_accn in index.values():
        for accn, facts in by_accn.items():
            filings.setdefault(accn, (str(facts[0].get("form")), str(facts[0].get("filed"))))

    reports: list[AnnualReport] = []
    for accn, (form, filed) in filings.items():
        ends = [
            str(f["end"]) for concept in PERIOD_PROBES for f in index.get(concept, {}).get(accn, []) if _is_annual(f)
        ]
        if not ends:
            continue
        end = max(ends)
        report = AnnualReport(accn=accn, form=form, filed=filed, period_end=end, fiscal_year=fiscal_year_of(end))

        for name, concepts in DURATION_FIELDS.items():
            report.values[name] = None
            for concept in concepts:
                value = _pick_duration(index.get(concept, {}).get(accn, []), end)
                if value is not None:
                    report.values[name], report.concepts[name] = value, concept
                    break
        for name, concepts in PER_SHARE_FIELDS.items():
            report.values[name] = None
            for concept in concepts:
                facts = [f for f in index.get(concept, {}).get(accn, []) if f.get("end") == end and _is_annual(f)]
                if facts:
                    report.values[name], report.concepts[name] = float(facts[0]["val"]), concept
                    break
        for name, concepts in INSTANT_FIELDS.items():
            report.values[name] = None
            for concept in concepts:
                value = _pick_instant(index.get(concept, {}).get(accn, []), end)
                if value is not None:
                    report.values[name], report.concepts[name] = value, concept
                    break

        # 발행주식수 — 기법 루프 1회차 B(순주식발행)의 입력 (docs/factors.md 12.1, docs/infra.md 25.446).
        # 그 10-K 표지의 dei 값(표지 날짜 시점), 없으면 결산일 시점 us-gaap 값. 단위는 shares
        report.values["shares_outstanding"], 개념 = _shares_of(payload, accn, end)
        if 개념:
            report.concepts["shares_outstanding"] = 개념
        # **같은 10-K 의 당기·전기 가중평균 주식수** — 순주식발행은 이것으로 낸다 (25.450, 교차검증).
        # 공시가 분할·병합을 전기에 소급 조정해 적으므로 3:2 분할도 발행으로 잡히지 않고, 두 해가 같은 개념이다
        report.values["shares_basic"], report.values["shares_basic_prev"] = _weighted_shares(payload, accn, end)

        values = report.values
        if values["revenue"] is None:
            parts = [_pick_duration(index.get(c, {}).get(accn, []), end) for c in BANK_REVENUE]
            if all(p is not None for p in parts):
                values["revenue"] = sum(p for p in parts if p is not None)
                report.concepts["revenue"] = " + ".join(BANK_REVENUE)
        # 매출총이익 태그가 없으면 **같은 공시의** 매출 − 매출원가 (25.740).
        # 은행식 매출(순이자+비이자)은 원가가 없어 쓰지 않는다
        if (
            values["gross_profit"] is None
            and report.concepts.get("revenue") in DURATION_FIELDS["revenue"]
            and values["revenue"] is not None
            and values["cost_of_revenue"] is not None
            # **총원가 태그(CostOfRevenue)일 때만** 뺀다 (25.743, 교차검증). CostOfGoodsAndServicesSold 는 제품·용역
            # 원가라
            # 금융자회사를 둔 제조사의 총매출(Revenues)에서 빼면 금융 매출이 원가 없이 통째로 이익이 된다 `[확인필요:
            # 실제 조합]`
            and report.concepts.get("cost_of_revenue") == "CostOfRevenue"
        ):
            values["gross_profit"] = values["revenue"] - values["cost_of_revenue"]
            report.concepts["gross_profit"] = f'{report.concepts["revenue"]} − {report.concepts["cost_of_revenue"]}'
        if values["total_liabilities"] is None:
            total = _pick_instant(index.get(LIAB_AND_EQUITY, {}).get(accn, []), end)
            equity = _pick_instant(index.get(EQUITY_WITH_NCI, {}).get(accn, []), end)
            if equity is None:
                equity = values["total_equity"]
            if total is not None and equity is not None:
                values["total_liabilities"] = total - equity
                report.concepts["total_liabilities"] = f"{LIAB_AND_EQUITY} − 자본"
        values["noncurrent_assets"] = (
            values["total_assets"] - values["current_assets"]
            if values["total_assets"] is not None and values["current_assets"] is not None
            else None
        )
        values["noncurrent_liabilities"] = (
            values["total_liabilities"] - values["current_liabilities"]
            if values["total_liabilities"] is not None and values["current_liabilities"] is not None
            else None
        )
        reports.append(report)

    return sorted(reports, key=lambda r: (r.filed, r.accn))


#: 한 공시에서 **짝으로만** 가져와야 하는 열쇠 (25.453, 교차검증). 당기·전기 가중평균 주식수는 같은 10-K 안에서만
#: 분할이 같은 기준으로 조정돼 있다. 열쇠마다 따로 메우면 10-K/A 의 분할 후 당기와 원래 10-K 의 분할 전 전기가
#: 섞여 3:2 분할이 +40% 발행으로 보인다 — 25.450 이 막으려던 바로 그 모양이다
PAIRED_KEYS: tuple[tuple[str, ...], ...] = (("shares_basic", "shares_basic_prev"),)


def fill_blanks(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """`newer` 의 빈 값을 `older` 로 메운다. `PAIRED_KEYS` 는 짝 전체를 한 공시에서 가져온다 —
    `newer` 의 짝이 다 차 있으면 그대로, 아니면 `older` 의 짝이 다 차 있을 때만 통째로 바꾼다."""
    out = {k: (v if v is not None else older.get(k)) for k, v in newer.items()}
    for k, v in older.items():
        out.setdefault(k, v)
    for pair in PAIRED_KEYS:
        if all(newer.get(k) is not None for k in pair):
            chosen = newer
        elif all(older.get(k) is not None for k in pair):
            chosen = older
        else:
            chosen = newer
        for k in pair:
            if k in out or chosen.get(k) is not None:
                out[k] = chosen.get(k)
    return out


def latest_by_year(reports: list[AnnualReport]) -> dict[int, AnnualReport]:
    """연도마다 financials 에 넣을 한 행. 늦게 낸 공시가 이기되, 늦은 공시에 빈 값은 앞 공시로 채운다.

    10-K/A 는 일부 표만 다시 내는 일이 많다. 통째로 덮으면 멀쩡한 값이 NULL 이 된다.
    """
    out: dict[int, AnnualReport] = {}
    for report in reports:  # filed 순
        kept = out.get(report.fiscal_year)
        if kept is None or kept.period_end != report.period_end:
            # 결산일이 다르면(결산월 변경) 다른 기간이다 — 빈칸을 앞 기간 값으로 메우면 두 기간이 섞인 행이 된다.
            # 늦은 공시를 통째로 쓴다 (docs/infra.md 25.730, 재무 감사 3번)
            out[report.fiscal_year] = report
            continue
        merged = AnnualReport(
            accn=report.accn,
            form=report.form,
            filed=report.filed,
            period_end=report.period_end,
            fiscal_year=report.fiscal_year,
            values={k: v for k, v in fill_blanks(report.values, kept.values).items() if k in report.values},
            concepts={**kept.concepts, **report.concepts},
        )
        out[report.fiscal_year] = merged
    return out
