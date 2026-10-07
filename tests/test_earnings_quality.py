"""발생액 — 기법 발굴 루프 1회차 C (batch/services/earnings_quality.py, docs/infra.md 25.445)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.services import earnings_quality as eq


def test_손으로_센_값() -> None:
    # (100 − 140) / ((1000 + 600) / 2) = −40 / 800 = −0.05
    assert eq.accrual_ratio(100, 140, 1000, 600) == pytest.approx(-0.05)


def test_하나라도_없으면_없다_전년_총자산을_당기로_대신하지_않는다() -> None:
    assert eq.accrual_ratio(100, None, 1000, 600) is None
    assert eq.accrual_ratio(100, 140, 1000, None) is None
    assert eq.accrual_ratio(100, 140, 0, 0) is None


def test_백테스트_IC_는_부호를_뒤집고_시점_재무를_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """발생액이 작을수록(현금이 뒷받침할수록) 다음 달 더 오르면 IC 는 +1 이다."""
    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
    prices = {sid: {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-03-02": 100.0 + sid} for sid in ids}
    # 종목 번호가 클수록 영업현금흐름이 커서 발생액이 작다 → 점수(−발생액)가 크다
    snaps = {
        sid: [
            {"as_of_date": "2025-03-01", "fiscal_year": 2024, "values": {"total_assets": 1000}},
            {"as_of_date": "2026-01-15", "fiscal_year": 2025,
             "values": {"net_income": 100, "operating_cash_flow": 100 + sid, "total_assets": 1000}},
            # 기준일 뒤에 나온 공시는 없는 것이다
            {"as_of_date": "2026-02-15", "fiscal_year": 2026, "values": {"net_income": 999}},
        ]
        for sid in ids
    }  # fmt: skip
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="NASDAQ", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=0.0) for i in inputs])
    got = job.factor_ics([{"stock_id": s} for s in ids], snaps, prices, dates, ["2026-02-02", "2026-03-02"], {})
    assert got["accrual"].months == 1 and got["accrual"].mean == pytest.approx(1.0)
    assert got["accrual"].coverage == pytest.approx(1.0)


def test_정정_공시가_가린_영업현금흐름은_앞_공시로_메운다() -> None:
    """손익만 다시 낸 10-K/A 가 원래 10-K 의 CFO 를 가려 발생액이 None 이 됐다 (docs/infra.md 25.448, 교차검증)."""
    from batch.jobs import backtest as job

    원래 = {"as_of_date": "2025-11-03", "fiscal_year": 2025,
            "values": {"net_income": 100, "operating_cash_flow": 140, "total_assets": 1000}}
    정정 = {"as_of_date": "2025-12-10", "fiscal_year": 2025, "values": {"net_income": 90, "total_assets": 1000}}
    나중 = {"as_of_date": "2026-03-01", "fiscal_year": 2025, "values": {"operating_cash_flow": 999}}  # 기준일 뒤
    snaps = [원래, 정정, 나중]  # fmt: skip
    최근, _, _ = job.pit_financials(snaps, "2026-01-15")
    v = job.year_values(snaps, "2026-01-15", 최근)
    assert v["net_income"] == 90  # 고른 공시(정정)의 값이 먼저다
    assert v["operating_cash_flow"] == 140  # 빈 칸만 앞 공시로
    assert job.year_values(snaps, "2026-01-15", None) == {}


def test_계속영업_현금흐름_태그로_넘어가지_않는다() -> None:
    """순이익은 중단영업을 포함한다 — 계속영업 CFO 와 짝지으면 처분이익이 발생액이 된다 (25.448)."""
    from batch.sources import sec_facts as sf

    assert sf.DURATION_FIELDS["operating_cash_flow"] == ("NetCashProvidedByUsedInOperatingActivities",)
