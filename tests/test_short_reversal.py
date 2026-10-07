"""단기 반전 IC (docs/factors.md 12.2, docs/infra.md 25.475)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.services import short_reversal as sr
from batch.services.scoring import MOMENTUM_SKIP_DAYS


def test_21거래일_수익률의_반대_부호() -> None:
    closes = [100.0] * 10 + [100.0] + [100.0] * (MOMENTUM_SKIP_DAYS - 1) + [110.0]
    assert sr.reversal_1m(closes) == pytest.approx(-0.10)


def test_거래일이_모자라거나_기준점이_0_이면_없다() -> None:
    assert sr.reversal_1m([100.0] * MOMENTUM_SKIP_DAYS) is None
    assert sr.reversal_1m([0.0] + [1.0] * MOMENTUM_SKIP_DAYS) is None


def test_백테스트_IC_가_reversal_1m_을_잰다(monkeypatch: pytest.MonkeyPatch) -> None:
    """최근 한 달 덜 오른 종목이 다음 달 더 오르면 IC 가 1 이다."""
    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = [f"2026-01-{d:02d}" for d in range(1, 31)] + ["2026-02-02", "2026-03-02"]
    prices: dict[int, dict[str, float]] = {}
    for sid in ids:
        by = {d: 100.0 for d in dates[:-2]}
        by[dates[-3]] = 100.0 - sid  # 기준일(t-1): 번호가 클수록 최근 한 달 더 내렸다
        by["2026-02-02"] = 100.0
        by["2026-03-02"] = 100.0 + sid  # 그리고 다음 달 더 오른다
        prices[sid] = by
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
    got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-02-02", "2026-03-02"], {})
    assert got["reversal_1m"].months == 1 and got["reversal_1m"].mean == pytest.approx(1.0)
