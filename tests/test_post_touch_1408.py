"""목표·손절에 먼저 닿은 뒤 60일째까지의 지수 대비 — 기록만 (docs/infra.md 25.1408, docs/factors.md 12.29 P2)."""

from __future__ import annotations

import pytest

from batch.services import outcomes as oc
from tests.test_outcomes import SIG, series


def test_목표에_먼저_닿은_뒤의_표류() -> None:
    # 진입 100, 10번째 날 110(목표), 60번째 날 121 — 지수는 그 사이 +5%
    값 = [99.0, 100.0] + [100.0] * 9 + [110.0] + [110.0] * 48 + [121.0] + [121.0] * 5
    closes = series("2026-01-02", 값)
    지수값 = [1000.0] * 12 + [1000.0] * 48 + [1050.0] * 10
    o = oc.evaluate(SIG, closes, series("2026-01-02", 지수값))
    assert o.hit_target is True
    무리, 날, v = o.post_touch
    assert 무리 == "target" and 날 == closes[11][0]
    assert v == pytest.approx((121 / 110 - 1) - 0.05)


def test_40번째_날_뒤의_터치와_모자란_창은_세지_않는다() -> None:
    늦은 = [99.0, 100.0] + [100.0] * 45 + [110.0] * 30  # 45번째 날에 닿음
    assert oc.evaluate(SIG, series("2026-01-02", 늦은), series("2026-01-02", [1000.0] * 80)).post_touch is None
    짧은 = [99.0, 100.0, 110.0] + [110.0] * 20  # 창 60일이 안 참
    assert oc.evaluate(SIG, series("2026-01-02", 짧은), series("2026-01-02", [1000.0] * 30)).post_touch is None


def test_모음은_종목·터치일_한_건이고_장기는_뺀다() -> None:
    a = oc.Outcome(1, "2026-01-02", "short", post_touch=("target", "2026-01-12", 0.05))
    b = oc.Outcome(1, "2026-01-03", "short", post_touch=("target", "2026-01-12", 0.07))  # 연속 신호, 같은 터치일
    c = oc.Outcome(2, "2026-01-02", "short", post_touch=("stop", "2026-01-08", -0.02))
    d = oc.Outcome(3, "2026-01-02", "long", post_touch=("target", "2026-01-09", 0.30))
    out = oc.summarize_post_touch([a, b, c, d])
    assert out == {"short:stop": {"n": 1, "stocks": 1, "mean": -0.02},
                   "short:target": {"n": 1, "stocks": 1, "mean": 0.05}}  # fmt: skip
