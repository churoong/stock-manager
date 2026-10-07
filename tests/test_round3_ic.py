"""발굴 3회차 — 희석 사건 공시·거래대금 급증 IC (docs/factors.md 12.2, docs/infra.md 25.738)."""

from __future__ import annotations

import math

import pytest

from batch.jobs import backtest as job
from batch.services import dilution as dil
from batch.services import flow_surge as fs


class Test희석:
    @pytest.mark.parametrize(
        "title",
        ["주요사항보고서(유상증자결정)", "주요사항보고서(전환사채권발행결정)", "주요사항보고서(신주인수권부사채권발행결정)",
         "주요사항보고서(유무상증자결정)"],
    )  # fmt: skip
    def test_사건으로_센다(self, title: str) -> None:
        assert dil.is_dilution(title)

    @pytest.mark.parametrize(
        "title",
        ["[기재정정]주요사항보고서(유상증자결정)", "주요사항보고서(무상증자결정)", "주요사항보고서(교환사채권발행결정)",
         "주요사항보고서(자회사의 주요경영사항)(유상증자결정)", "유상증자결정(종속회사의주요경영사항)"],
    )  # fmt: skip
    def test_사건이_아니다(self, title: str) -> None:
        assert not dil.is_dilution(title)

    def test_IC_이름에_있다(self) -> None:
        assert {"dilution", "flow_surge"} <= set(job.IC_NAMES)


class Test거래대금급증:
    def test_최근_20일이_60일_평균의_두_배면_ln2(self) -> None:
        values = [100.0] * 40 + [200.0] * 20  # 60일 평균 = (4000 + 4000) / 60
        assert fs.flow_surge(values) == pytest.approx(math.log(200 / (8000 / 60)))

    def test_60일이_모자라면_None(self) -> None:
        assert fs.flow_surge([100.0] * 59) is None

    def test_비는_날이_있으면_None(self) -> None:
        assert fs.flow_surge([100.0] * 59 + [None]) is None

    def test_평균이_0이면_None(self) -> None:
        assert fs.flow_surge([0.0] * 60) is None


def test_IC_루프가_두_점수를_잇는다() -> None:
    import inspect

    src = inspect.getsource(job.factor_ics)
    assert '"dilution":\n                점수 = 희석점수' in src
    assert '"flow_surge":\n                점수 = 급증점수' in src
    assert "else -float(f)) if sid in 국내 else None" in src  # 국내만, 방향 −


class Test매출총이익:
    """3회차 E — 매출총이익/총자산, 미국 (docs/factors.md 12.2, docs/infra.md 25.740)."""

    @staticmethod
    def _aapl_with(concept: str, ratio: float) -> dict:
        import copy
        import json
        from pathlib import Path

        payload = copy.deepcopy(
            json.loads(Path("tests/fixtures/sec_companyfacts.json").read_text(encoding="utf-8"))["aapl"]
        )
        gaap = payload["facts"]["us-gaap"]
        rev = gaap["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
        gaap[concept] = {"units": {"USD": [{**f, "val": int(f["val"] * ratio)} for f in rev]}}
        return payload

    def test_GrossProfit_태그를_그대로_쓴다(self) -> None:
        from batch.sources import sec_facts as sf

        r = sf.latest_by_year(sf.parse_annual_reports(self._aapl_with("GrossProfit", 0.4)))[2025]
        assert r.values["gross_profit"] == int(r.values["revenue"] * 0.4)
        assert r.concepts["gross_profit"] == "GrossProfit"

    def test_태그가_없으면_같은_공시의_매출_빼기_원가(self) -> None:
        from batch.sources import sec_facts as sf

        r = sf.latest_by_year(sf.parse_annual_reports(self._aapl_with("CostOfRevenue", 0.6)))[2025]
        assert r.values["gross_profit"] == r.values["revenue"] - r.values["cost_of_revenue"]
        assert "CostOfRevenue" in r.concepts["gross_profit"]

    def test_둘_다_없으면_None(self) -> None:
        import json
        from pathlib import Path

        from batch.sources import sec_facts as sf

        payload = json.loads(Path("tests/fixtures/sec_companyfacts.json").read_text(encoding="utf-8"))["jpm"]
        assert sf.latest_by_year(sf.parse_annual_reports(payload))[2025].values["gross_profit"] is None

    def test_비율(self) -> None:
        from batch.services import gross_profitability as gp

        assert gp.gross_profitability(30.0, 100.0) == pytest.approx(0.3)
        assert gp.gross_profitability(-5.0, 100.0) == pytest.approx(-0.05)
        assert gp.gross_profitability(30.0, 0.0) is None
        assert gp.gross_profitability(None, 100.0) is None

    def test_스냅샷_payload_채우기에_들어간다(self) -> None:
        from batch.jobs import us_financials as usf

        assert "gross_profit" in usf.ADDED_PAYLOAD_KEYS

    def test_IC_는_미국만_금융업_빼고(self) -> None:
        import inspect

        src = inspect.getsource(job.factor_ics)
        assert "None if sid in 국내 or sid in 금융" in src
        assert '"gross_profitability":\n                점수 = 총이익점수' in src


class Test교차검증_25_743:
    def test_제품원가_태그로는_매출총이익을_만들지_않는다(self) -> None:
        from batch.sources import sec_facts as sf

        r = sf.latest_by_year(
            sf.parse_annual_reports(Test매출총이익._aapl_with("CostOfGoodsAndServicesSold", 0.6))
        )[2025]
        assert r.values["gross_profit"] is None
        r = sf.latest_by_year(sf.parse_annual_reports(Test매출총이익._aapl_with("CostOfRevenue", 0.6)))[2025]
        assert r.values["gross_profit"] == r.values["revenue"] - r.values["cost_of_revenue"]

    def test_백테스트는_결산일이_다른_앞_공시로_메우지_않는다(self) -> None:
        snaps = [
            {"as_of_date": "2025-03-01", "fiscal_year": 2025,
             "values": {"period_end": "2025-01-31", "gross_profit": 999, "total_assets": 100}},
            {"as_of_date": "2026-02-20", "fiscal_year": 2025,
             "values": {"period_end": "2025-12-31", "gross_profit": None, "total_assets": 2000}},
        ]  # fmt: skip
        got = job.year_values(snaps, "2026-03-01", snaps[1])
        assert got["gross_profit"] is None and got["total_assets"] == 2000

    def test_결산일을_모르면_예전처럼_메운다(self) -> None:
        snaps = [
            {"as_of_date": "2025-03-01", "fiscal_year": 2025, "values": {"operating_cash_flow": 5}},
            {"as_of_date": "2025-05-01", "fiscal_year": 2025, "values": {"operating_cash_flow": None}},
        ]
        assert job.year_values(snaps, "2026-03-01", snaps[1])["operating_cash_flow"] == 5

    def test_자사주도_종속회사와_띄어쓴_제외어를_뺀다(self) -> None:
        from batch.services import buyback as bb

        assert bb.is_buyback("주요사항보고서(자기주식취득결정)")
        assert not bb.is_buyback("자기주식취득결정(종속회사의주요경영사항)")
        assert not bb.is_buyback("자기주식취득결정(종속 회사의 주요경영사항)")

    def test_매출총이익_분모도_금융업을_뺀다(self) -> None:
        import inspect

        assert 'name in ("accrual", "gross_profitability")' in inspect.getsource(job.factor_ics)
