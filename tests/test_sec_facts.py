"""SEC companyfacts → 연간 재무 파싱 테스트. 픽스처는 2026-09-17 실제 응답의 일부(AAPL·JPM)다."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from batch.jobs import us_financials as job
from batch.sources import sec_facts as sf

FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "sec_companyfacts.json").read_text(encoding="utf-8")
)


def by_year(payload: dict) -> dict[int, sf.AnnualReport]:
    return {r.fiscal_year: r for r in sf.parse_annual_reports(payload)}


class Test애플:
    reports = by_year(FIXTURE["aapl"])

    def test_10K_두_건만_10Q_는_뺀다(self) -> None:
        assert sorted(self.reports) == [2024, 2025]

    def test_당기값만_고른다_비교기간이_아니라(self) -> None:
        r = self.reports[2025]
        assert (r.accn, r.filed, r.period_end) == ("0000320193-25-000079", "2025-10-31", "2025-09-27")
        assert r.values["revenue"] == 416_161_000_000
        assert r.concepts["revenue"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
        assert r.values["operating_income"] == 133_050_000_000
        assert r.values["net_income"] == 112_010_000_000
        assert r.values["pretax_income"] == 132_729_000_000
        assert self.reports[2024].values["revenue"] == 391_035_000_000

    def test_재무상태는_결산일_시점값(self) -> None:
        v = self.reports[2025].values
        assert v["total_assets"] == 359_241_000_000
        assert v["total_liabilities"] == 285_508_000_000
        assert v["total_equity"] == 73_733_000_000
        assert v["noncurrent_assets"] == 359_241_000_000 - 147_957_000_000
        assert v["retained_earnings"] == -14_264_000_000
        assert v["capital_stock"] is None  # AAPL 은 APIC 포함 태그만 낸다 → 넣지 않는다


class Test은행:
    def test_영업이익은_없고_매출은_Revenues(self) -> None:
        r = by_year(FIXTURE["jpm"])[2025]
        assert r.values["operating_income"] is None
        assert r.values["revenue"] == 182_447_000_000
        assert r.values["capital_stock"] == 4_105_000_000

    def test_매출_태그가_없으면_순이자이익_더하기_비이자이익(self) -> None:
        payload = copy.deepcopy(FIXTURE["jpm"])
        # JPM 은 RevenuesNetOfInterestExpense 도 같은 값으로 낸다. 둘 다 지워야 대체 경로를 탄다
        for concept in ("Revenues", "RevenuesNetOfInterestExpense"):
            payload["facts"]["us-gaap"].pop(concept, None)
        r = by_year(payload)[2025]
        assert r.values["revenue"] == 95_443_000_000 + 87_004_000_000
        assert "InterestIncomeExpenseNet" in r.concepts["revenue"]

    def test_부채_태그가_없으면_부채와자본총계에서_자본을_뺀다(self) -> None:
        payload = copy.deepcopy(FIXTURE["jpm"])
        del payload["facts"]["us-gaap"]["Liabilities"]
        r = by_year(payload)[2025]
        assert r.values["total_liabilities"] == 4_062_462_000_000


def test_연도_표기() -> None:
    assert sf.fiscal_year_of("2025-09-27") == 2025
    assert sf.fiscal_year_of("2026-01-31") == 2026  # WMT FY2026
    assert sf.fiscal_year_of("2026-01-03") == 2025  # 52/53주, 1월 첫 주 결산


def _report(accn: str, filed: str, **values: int | None) -> sf.AnnualReport:
    return sf.AnnualReport(
        accn=accn, form="10-K", filed=filed, period_end="2025-12-31", fiscal_year=2025, values=dict(values)
    )


def test_정정공시는_늦은_값이_이기고_빈칸은_앞_공시로() -> None:
    first = _report("a", "2026-02-10", revenue=100, net_income=10)
    amended = _report("b", "2026-05-01", revenue=120, net_income=None)
    merged = sf.latest_by_year([first, amended])[2025]
    assert merged.accn == "b"
    assert merged.values == {"revenue": 120, "net_income": 10}


def test_결산일이_다르면_앞_공시로_빈칸을_메우지_않는다() -> None:
    # 결산월 변경(1월 말 → 12월 말): 두 10-K 가 같은 회계연도 번호를 받아도 기간이 다르다 (25.730)
    first = sf.AnnualReport(
        accn="a",
        form="10-K",
        filed="2025-03-20",
        period_end="2025-01-31",
        fiscal_year=2025,
        values={"revenue": 100, "net_income": 10},
    )
    changed = sf.AnnualReport(
        accn="b",
        form="10-K",
        filed="2026-02-20",
        period_end="2025-12-31",
        fiscal_year=2025,
        values={"revenue": 90, "net_income": None},
    )
    merged = sf.latest_by_year([first, changed])[2025]
    assert merged.accn == "b"
    assert merged.values == {"revenue": 90, "net_income": None}


def test_기간이_연간이_아닌_fact_만_있으면_건너뛴다() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-10-01",
                                "end": "2025-12-31",
                                "val": 5,
                                "accn": "x",
                                "form": "10-K",
                                "filed": "2026-02-01",
                            }
                        ]
                    }
                }
            }
        }
    }
    assert sf.parse_annual_reports(payload) == []


class Test저장_행:
    def test_연도별_financials_와_공시별_스냅샷(self) -> None:
        reports = sf.parse_annual_reports(FIXTURE["aapl"])
        fin, snap = job.build_rows(7, reports, min_year=2020, as_of_for=lambda filed: "2025-11-03", now="t")
        assert len(fin) == 2 and len(snap) == 2
        row = dict(zip([c.strip() for c in job.FIN_COLS.split(",")], fin[-1], strict=True))
        assert row["stock_id"] == 7 and row["fiscal_year"] == 2025
        assert row["report_code"] == "11011" and row["consolidated"] == 1
        assert row["report_date"] == "2025-10-31" and row["receipt_no"] == "0000320193-25-000079"
        assert row["currency"] == "USD" and row["accounting_standard"] == "US-GAAP"
        assert row["revenue"] == 416_161_000_000
        snap_row = dict(zip([c.strip() for c in job.SNAP_COLS.split(",")], snap[-1], strict=True))
        assert snap_row["as_of_date"] == "2025-11-03"
        assert json.loads(snap_row["payload"])["period_end"] == "2025-09-27"

    def test_오래된_연도는_넣지_않는다(self) -> None:
        reports = sf.parse_annual_reports(FIXTURE["aapl"])
        fin, snap = job.build_rows(7, reports, min_year=2025, as_of_for=lambda filed: filed, now="t")
        assert [r[1] for r in fin] == [2025]
        assert len(snap) == 1


class Test배당:
    def test_애플_배당_지급액과_주당배당금(self) -> None:
        r = by_year(FIXTURE["aapl"])[2025]
        assert r.values["dividends_paid"] == 15_421_000_000
        assert r.values["dps_common"] == 1.02
        assert r.concepts["dividends_paid"] == "PaymentsOfDividends"

    def test_배당_행은_태그가_있는_연도만_달러로(self) -> None:
        reports = sf.parse_annual_reports(FIXTURE["aapl"])
        rows = job.build_dividend_rows(7, reports, min_year=2020, as_of_for=lambda filed: "2025-11-03", now="t")
        from batch.jobs import dividends

        cols = [c.strip() for c in dividends._COLS.split(",")]
        last = dict(zip(cols, rows[-1], strict=True))
        assert last["fiscal_year"] == last["report_year"] == 2025
        assert last["cash_dividend_total"] == 15_421_000_000
        assert last["dps_common"] == 1.02
        assert last["payout_ratio"] == round(15_421_000_000 / 112_010_000_000 * 100, 1)
        assert last["as_of_date"] == "2025-11-03"

    def test_배당_태그가_없으면_행이_없다(self) -> None:
        payload = copy.deepcopy(FIXTURE["jpm"])
        for concept in (
            "PaymentsOfDividends",
            "CommonStockDividendsPerShareDeclared",
            "CommonStockDividendsPerShareCashPaid",
        ):
            payload["facts"]["us-gaap"].pop(concept, None)
        reports = sf.parse_annual_reports(payload)
        assert job.build_dividend_rows(1, reports, min_year=2020, as_of_for=lambda f: f, now="t") == []


def test_영업현금흐름을_싣는다() -> None:
    """발굴 루프 1회차 C(발생액)의 입력 — 이미 받는 응답에서 꺼낸다 (docs/infra.md 25.440)."""

    def fact(val: int) -> dict:
        return {
            "start": "2024-10-01",
            "end": "2025-09-30",
            "val": val,
            "accn": "a",
            "form": "10-K",
            "filed": "2025-11-01",
        }

    payload = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [fact(100)]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [fact(140)]}},
    }}}  # fmt: skip
    r = sf.parse_annual_reports(payload)[0]
    assert r.values["operating_cash_flow"] == 140
    assert r.concepts["operating_cash_flow"] == "NetCashProvidedByUsedInOperatingActivities"
    fin, snap = job.build_rows(7, [r], min_year=2020, as_of_for=lambda filed: "2025-11-03", now="t")
    assert json.loads(snap[0][6])["operating_cash_flow"] == 140  # payload 에 실린다
    assert len(fin[0]) == len(job.FIN_COLS.split(","))  # financials 열은 그대로


def test_옛_스냅샷_payload_에_영업현금흐름을_더한다() -> None:
    """스냅샷은 덮지 않아 25.440 전 공시에는 CFO 가 없다. 같은 공시의 값이라 **그 열쇠만** 더한다 (docs/infra.md 25.444)."""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (7, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    for accn, payload in (("a", {"net_income": 100}), ("b", {"net_income": 5, "operating_cash_flow": 9})):
        mem.conn.execute(
            "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code, consolidated,"
            " payload, source, fetched_at) VALUES (7, '2025-11-03', ?, 2025, '11011', 1, ?, 'sec_edgar', 't')",
            [accn, json.dumps(payload)],
        )
    reports = [
        sf.AnnualReport(accn="a", form="10-K", filed="2025-11-01", period_end="2025-09-30", fiscal_year=2025,
                        values={"operating_cash_flow": 140}),
        sf.AnnualReport(accn="b", form="10-K", filed="2025-11-01", period_end="2025-09-30", fiscal_year=2025,
                        values={"operating_cash_flow": 999}),
    ]  # fmt: skip
    mem.batch(job.payload_fill_statements(7, reports))
    got = {r[0]: json.loads(r[1]) for r in mem.conn.execute("SELECT receipt_no, payload FROM financial_snapshots")}
    assert got["a"] == {"net_income": 100, "operating_cash_flow": 140}  # 다른 열쇠는 그대로
    assert got["b"]["operating_cash_flow"] == 9  # 이미 있으면 바꾸지 않는다(덮지 않는다)


def test_실행이_새_스냅샷_뒤에_채운다() -> None:
    import inspect

    원본 = inspect.getsource(job.run)
    assert "payload_fill_statements(stock_id" in 원본
    assert 원본.index('_bulk(client, "financial_snapshots"') < 원본.index("client.batch(fill_buf[")


def test_null_로_적힌_값은_채우기가_건드리지_않는다() -> None:
    """열쇠가 아예 없는 행만 채운다 — `json_extract IS NULL` 은 null 로 적힌 행도 잡았다 (docs/infra.md 25.448)."""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (7, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    mem.conn.execute(
        "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code, consolidated,"
        " payload, source, fetched_at) VALUES (7, '2025-11-03', 'a', 2025, '11011', 1, ?, 'sec_edgar', 't')",
        [json.dumps({"operating_cash_flow": None})],
    )
    r = sf.AnnualReport(accn="a", form="10-K", filed="2025-11-01", period_end="2025-09-30", fiscal_year=2025,
                        values={"operating_cash_flow": 140})  # fmt: skip
    mem.batch(job.payload_fill_statements(7, [r]))
    got = json.loads(mem.conn.execute("SELECT payload FROM financial_snapshots").fetchone()[0])
    assert got["operating_cash_flow"] is None
