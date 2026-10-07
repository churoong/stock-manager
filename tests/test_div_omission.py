"""배당 중단 IC (docs/factors.md 12.2, docs/infra.md 25.479)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.services import div_omission as dvo


def test_전년_있고_올해_0_이면_중단() -> None:
    이력 = [("2025-03-10", 2024, 100.0), ("2026-03-10", 2025, 0.0)]
    assert dvo.omitted(이력, "2026-03-11") is True
    assert dvo.omitted(이력, "2026-03-09") is None  # 2025 를 아직 모른다 → 최근 해 2024, 2023 모름
    assert dvo.omitted([("2025-03-10", 2024, 0.0), ("2026-03-10", 2025, 0.0)], "2026-04-01") is False  # 계속 무배당


def test_같은_해는_뒤_접수가_이긴다() -> None:
    이력 = [("2025-03-10", 2024, 100.0), ("2026-03-10", 2025, 0.0), ("2026-04-01", 2025, 50.0)]  # 정정으로 배당
    assert dvo.omitted(이력, "2026-03-20") is True
    assert dvo.omitted(이력, "2026-04-02") is False


def test_IC_와_걸린_비율(monkeypatch: pytest.MonkeyPatch) -> None:
    """중단한 종목이 다음 달 덜 오르면 IC 가 양이고, 걸린 비율을 기록에 남긴다."""
    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-03-30", "2026-03-31", "2026-04-29", "2026-04-30"]
    중단한 = set(range(1, 11))
    prices = {sid: {"2026-03-30": 100.0, "2026-03-31": 100.0,
                    "2026-04-30": 90.0 if sid in 중단한 else 110.0 + sid} for sid in ids}  # fmt: skip
    배당 = {sid: [("2025-03-10", 2024, 100.0), ("2026-03-10", 2025, 0.0 if sid in 중단한 else 100.0)] for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
    got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-03-31", "2026-04-30"], {},
                         dividends_by_stock=배당)  # fmt: skip
    s = got["div_omission"]
    assert s.months == 1 and s.mean is not None and s.mean > 0.5
    assert s.hit_rate == pytest.approx(0.25)
    assert job.ic_log(got)["div_omission"]["hit_rate"] == 0.25


def test_미국_종목은_재지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """미국은 주당배당만 있는 해가 0 으로 읽혀 거짓 중단이 난다 (docs/infra.md 25.481, 교차검증)."""
    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-03-30", "2026-03-31", "2026-04-29", "2026-04-30"]
    prices = {sid: {"2026-03-30": 100.0, "2026-03-31": 100.0, "2026-04-30": 100.0 + sid} for sid in ids}
    배당 = {sid: [("2025-03-10", 2024, 100.0), ("2026-03-10", 2025, 0.0 if sid < 10 else 100.0)] for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="NASDAQ", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
    got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-03-31", "2026-04-30"], {},
                         dividends_by_stock=배당)  # fmt: skip
    assert got["div_omission"].months == 0 and got["div_omission"].hit_rate is None
