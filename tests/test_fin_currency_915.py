"""재무 통화가 종목 통화와 다르면 시총·가격과 나누지 않는다 (docs/infra.md 25.915, 감사).

두산밥캣(국내)은 연결재무를 USD 로 공시한다(운영 9행, 2026-10-03). 순이익 2.8억 달러가 "2.8억원" 으로 원화 시총에 나뉘어 E/P·B/P·S/P·PBR 이 약 1/1,400 이었다.
"""

from __future__ import annotations

import pytest

from batch.jobs import scores as job
from tests.test_scores_job import universe_row


def _재무(통화: str) -> dict:
    return {1: {2025: {"net_income": 100, "total_equity": 500, "revenue": 800, "operating_income": 50,
                       "total_assets": 1000, "total_liabilities": 500, "currency": 통화}}}  # fmt: skip


def test_통화가_다르면_밸류_비율은_비고_통화와_무관한_비율은_산다() -> None:
    행 = {**universe_row(1), "currency": "KRW"}
    m = job.build_inputs([행], _재무("USD"), {})[0].metrics
    assert m["ep"] is None and m["bp"] is None and m["sp"] is None
    assert m["roe"] == pytest.approx(0.2)  # 100 / 500 — 통화가 약분된다
    같음 = job.build_inputs([행], _재무("KRW"), {})[0].metrics
    assert 같음["ep"] is not None


def test_자본을_읽는_질의가_종목_통화로_거른다() -> None:
    import inspect

    from batch.jobs import signals, valuation_bands

    for src in (inspect.getsource(signals.load_band), inspect.getsource(signals._latest_equity),
                inspect.getsource(valuation_bands.load_equities)):  # fmt: skip
        assert "currency = (SELECT st.currency FROM stocks st WHERE st.id =" in src


def test_표시통화가_바뀐_해는_성장률을_내지_않는다() -> None:
    """두산밥캣 연결: 2022 KRW → 2023 USD (운영 확인 2026-10-03). 3년 CAGR 이 달러 ÷ 원이 됐다 (25.919, 11회차)."""
    행 = {**universe_row(1), "currency": "KRW"}
    재무 = {1: {
        2022: {"revenue": 8_000_000_000_000, "operating_income": 900_000_000_000, "total_assets": 1, "currency": "KRW"},
        2023: {"revenue": 9_000_000_000, "operating_income": 1_000_000_000, "total_assets": 1, "currency": "USD"},
        2024: {"revenue": 8_500_000_000, "operating_income": 900_000_000, "total_assets": 1, "currency": "USD"},
        2025: {"revenue": 8_000_000_000, "operating_income": 800_000_000, "total_assets": 1, "currency": "USD"},
    }}  # fmt: skip
    m = job.build_inputs([행], 재무, {})[0].metrics
    assert m["revenue_growth"] == pytest.approx(8_000 / 8_500 - 1)  # 같은 통화 두 해는 그대로
    assert m["revenue_cagr_3y"] is None  # 2022(KRW) 와는 견주지 않는다
    재무[1][2024]["currency"] = "KRW"
    assert job.build_inputs([행], 재무, {})[0].metrics["revenue_growth"] is None


def test_통화를_모르는_행은_막지_않는다() -> None:
    from batch.services import scoring as sc

    assert sc.same_currency({"currency": "KRW"}, {"x": 1}, None)
    assert not sc.same_currency({"currency": "KRW"}, {"currency": "USD"})


def test_백테스트도_같은_규칙을_쓴다() -> None:
    import inspect

    from batch.jobs import backtest, signals

    src = inspect.getsource(backtest.build_pit_inputs)
    assert "sc.same_currency(cur_v, prev_v)" in src and "sc.same_currency(cur_v, old_v)" in src and "밸류_v" in src
    assert "fin.currency AS ledger_currency" in inspect.getsource(backtest.load_snapshots)
    assert "sc.same_currency(merged, r.get(\"values\"))" in inspect.getsource(backtest.year_values)
    assert "scoring_svc.same_currency(current, previous)" in inspect.getsource(signals.load_growth)


def test_앞_공시로_빈칸을_메울_때_통화가_다르면_건너뛴다() -> None:
    from batch.jobs import backtest

    뒤 = {"as_of_date": "2024-04-01", "fiscal_year": 2023, "consolidated": True, "values": {"currency": "USD", "revenue": 1.0}}
    앞 = {"as_of_date": "2024-03-01", "fiscal_year": 2023, "consolidated": True,
          "values": {"currency": "KRW", "revenue": 9.0, "operating_cash_flow": 1400.0}}  # fmt: skip
    got = backtest.year_values([앞, 뒤], "2024-05-01", 뒤)
    assert got.get("operating_cash_flow") is None


def test_분기_계열은_최근_분기와_통화가_다른_분기를_비운다() -> None:
    """두산밥캣 분기도 2023 부터 USD — 그 전후 전년동기 변화가 달러 − 원이었다 (25.920, 11회차 1)."""
    from batch.services import quarterly_earnings as qe

    def 행(y: int, code: str, ni: float, ccy: str) -> dict:
        return {"as_of_date": f"{y + (1 if code == '11011' else 0)}-0{3 if code == '11011' else 8}-01", "fiscal_year": y,
                "report_code": code, "values": {"net_income": ni, "currency": ccy}, "consolidated": True}  # fmt: skip

    rows = []
    for y, ccy in ((2021, "KRW"), (2022, "KRW"), (2023, "USD"), (2024, "USD")):
        k = 1_000_000_000 if ccy == "KRW" else 1_000_000
        rows += [행(y, "11013", 1 * k, ccy), 행(y, "11012", 2 * k, ccy), 행(y, "11014", 3 * k, ccy), 행(y, "11011", 10 * k, ccy)]
    계열 = qe.quarter_series(rows, "2025-03-15")
    assert 계열 is not None and len(계열) == 12
    assert 계열[:4] == [None] * 4  # 2022 분기(KRW)는 비웠다
    assert 계열[-1] == pytest.approx(4_000_000)  # 2024 4분기 = 연간 − 세 분기(USD 끼리)
    섞임 = [dict(r, values=dict(r["values"])) for r in rows]
    섞임[-1]["values"]["currency"] = "KRW"  # 2024 연간만 KRW — 4분기 뺄셈이 섞인다
    assert qe.quarter_series(섞임, "2025-03-15")[-1] is None
