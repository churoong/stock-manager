"""시장 국면 근거표 행 (docs/infra.md 25.688, 배분 감사 재현)."""

from __future__ import annotations

from batch.services import signals as sg
from batch.services import trend


def test_모르는_국면은_왜_모르는지_적는다() -> None:
    r = trend.regime_from_closes("KOSPI", [("2026-07-19", 2600.0)], "2026-09-29")
    _, data = trend.factor_for(r, {"enabled": True})
    행 = sg.regime_criteria(data)[0]
    assert "72일 오래됨" in str(행)


def test_근거표_행도_같아_보이면_자리를_늘린다() -> None:
    data = {
        "regime": {"index_code": "KOSPI", "state": "bear", "close": 2640.2, "sma": 2640.3, "date": "2026-09-26"},
        "regime_factor": 0.5,
        "bear_factor": 0.5,
    }
    행 = str(sg.regime_criteria(data)[0])
    assert "2,640.2 < 200일선 2,640.3" in 행


def test_배수_1_00_이면_축소라_부르지_않는다() -> None:
    _, _, data = sg.size_weight(10, 0.10, -0.05)  # 기준 아래 — 줄이지 않는다
    inp = sg.SignalInput.__new__(sg.SignalInput)
    inp.metrics_date = "2026-09-26"  # type: ignore[attr-defined]
    이름 = [r["label"] for r in sg.sizing_criteria(inp, data)]
    assert "변동성 (축소 없음)" in 이름 and "낙폭 (축소 없음)" in 이름
    _, _, data = sg.size_weight(10, 0.60, -0.60)
    이름 = [r["label"] for r in sg.sizing_criteria(inp, data)]
    assert "변동성 축소" in 이름 and "낙폭 축소" in 이름
