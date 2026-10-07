"""발생액 IC 에서 금융업을 뺀다 (docs/factors.md 12.2, docs/infra.md 25.452)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from batch.services import sectors


@pytest.mark.parametrize(
    ("code", "want"),
    [("SIC 60", True), ("SIC 63", True), ("SIC 67", True), ("SIC 69", True), ("SIC 59", False), ("SIC 70", False),
     ("KSIC 64", True), ("KSIC 65", True), ("KSIC 66", True), ("KSIC 68", True), ("KSIC 70", False), ("KSIC 26", False),
     (None, None), ("", None), ("SIC", None), ("NAICS 52", None)],
)  # fmt: skip
def test_금융업_판별(code: str | None, want: bool | None) -> None:
    assert sectors.is_financial(code) is want


def test_저장되는_코드_모양과_맞다() -> None:
    """판별 함수가 읽는 모양이 수집이 적는 모양과 같아야 한다 — 다르면 아무것도 빠지지 않는다."""
    assert sectors.is_financial(sectors.us_sector("6021")[1])
    assert sectors.is_financial(sectors.kr_sector("64191")[1])
    assert not sectors.is_financial(sectors.us_sector("3571")[1])


def test_발생액_IC_는_금융업을_빼고_비율도_금융업을_뺀_후보_대비(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import backtest as job

    ids = list(range(1, 51))
    금융 = set(range(41, 51))  # 10종목은 은행
    dates = ["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
    prices = {sid: {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-03-02": 100.0 + sid} for sid in ids}
    universe = [{"stock_id": sid, "sector_code": "SIC 60" if sid in 금융 else "SIC 35"} for sid in ids]
    # 비금융은 발생액이 작을수록 더 오른다(IC +1). 금융업은 반대로 둬서 섞이면 IC 가 1 이 아니게 된다
    발생 = {sid: (float(sid) if sid in 금융 else -float(sid)) for sid in ids}
    snaps = {sid: [{"sid": sid}] for sid in ids}

    def values(rows: list[dict[str, Any]], _cutoff: str, _fy: object) -> dict[str, float]:
        return {"net_income": 발생[rows[0]["sid"]], "operating_cash_flow": 0.0, "total_assets": 1.0}

    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="NASDAQ", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
    monkeypatch.setattr(job, "pit_financials", lambda rows, cutoff: (2025, 2024, None))
    monkeypatch.setattr(job, "year_values", values)
    got = job.factor_ics(universe, snaps, prices, dates, ["2026-02-02", "2026-03-02"], {})
    s = got["accrual"]
    assert s.months == 1 and s.mean == pytest.approx(1.0)  # 금융업이 빠져 완벽한 순위
    assert s.coverage == pytest.approx(1.0)  # 40 / (50 − 10)
    assert got["momentum"].coverage == pytest.approx(1.0)  # 다른 IC 의 분모는 그대로 50


def test_업종_코드를_모르는_종목은_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """모르는 것으로 자르지 않는다 — 코드가 비었으면 발생액 IC 에 든다."""
    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
    prices = {sid: {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-03-02": 100.0 + sid} for sid in ids}
    universe = [{"stock_id": sid, "sector_code": None} for sid in ids]
    snaps = {sid: [{"sid": sid}] for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="NASDAQ", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
    monkeypatch.setattr(job, "pit_financials", lambda rows, cutoff: (2025, 2024, None))
    monkeypatch.setattr(job, "year_values", lambda rows, c, fy: {
        "net_income": -float(rows[0]["sid"]), "operating_cash_flow": 0.0, "total_assets": 1.0})
    got = job.factor_ics(universe, snaps, prices, dates, ["2026-02-02", "2026-03-02"], {})
    assert got["accrual"].coverage == pytest.approx(1.0) and got["accrual"].mean == pytest.approx(1.0)


def test_유니버스가_업종_코드를_읽는다() -> None:
    import inspect

    from batch.jobs import backtest as job

    assert "s.sector_code" in inspect.getsource(job.load_universe)


class Test주식수_짝으로만_메우기:
    """당기·전기 가중평균 주식수는 한 공시에서 짝으로 온다 (docs/infra.md 25.453, 교차검증)."""

    def test_정정_공시의_분할_후_당기와_원래_공시의_분할_전_전기를_섞지_않는다(self) -> None:
        from batch.sources import sec_facts

        원래 = {"shares_basic": 100.0, "shares_basic_prev": 100.0, "net_income": 5.0}
        정정 = {"shares_basic": 150.0, "shares_basic_prev": None, "net_income": None}
        got = sec_facts.fill_blanks(정정, 원래)
        assert (got["shares_basic"], got["shares_basic_prev"]) == (100.0, 100.0)  # 원래 짝 통째로
        assert got["net_income"] == 5.0  # 다른 열쇠는 전처럼 칸마다

    def test_새_공시의_짝이_다_차면_그대로(self) -> None:
        from batch.sources import sec_facts

        got = sec_facts.fill_blanks({"shares_basic": 150.0, "shares_basic_prev": 140.0},
                                    {"shares_basic": 100.0, "shares_basic_prev": 90.0})  # fmt: skip
        assert (got["shares_basic"], got["shares_basic_prev"]) == (150.0, 140.0)

    def test_둘_다_짝이_깨졌으면_새_공시_값(self) -> None:
        from batch.sources import sec_facts

        got = sec_facts.fill_blanks({"shares_basic": 150.0, "shares_basic_prev": None},
                                    {"shares_basic": None, "shares_basic_prev": 90.0})  # fmt: skip
        assert (got["shares_basic"], got["shares_basic_prev"]) == (150.0, None)

    def test_백테스트_year_values_도_짝으로(self) -> None:
        from batch.jobs import backtest as job
        from batch.services import share_issuance as si

        원래 = {"fiscal_year": 2025, "as_of_date": "2026-02-01",
                "values": {"shares_basic": 100.0, "shares_basic_prev": 100.0}}
        정정 = {"fiscal_year": 2025, "as_of_date": "2026-03-01",
                "values": {"shares_basic": 150.0, "shares_basic_prev": None}}  # fmt: skip
        v = job.year_values([원래, 정정], "2026-03-02", 정정)
        assert si.net_issuance(v["shares_basic"], v["shares_basic_prev"], split_adjusted=True) == 0.0

    def test_연도_합치기도_짝으로(self) -> None:
        from batch.sources import sec_facts

        def rep(filed: str, values: dict[str, float | None]) -> sec_facts.AnnualReport:
            return sec_facts.AnnualReport(accn=filed, form="10-K", filed=filed, period_end="2025-12-31",
                                          fiscal_year=2025, values=values, concepts={})  # fmt: skip

        out = sec_facts.latest_by_year([
            rep("2026-02-01", {"shares_basic": 100.0, "shares_basic_prev": 100.0}),
            rep("2026-03-01", {"shares_basic": 150.0, "shares_basic_prev": None}),
        ])
        assert out[2025].values == {"shares_basic": 100.0, "shares_basic_prev": 100.0}
