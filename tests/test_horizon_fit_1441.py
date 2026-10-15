"""중기 신호의 126일 지수 대비 — 기록만 (docs/infra.md 25.1441, docs/signals.md 10.4, factors.md 12.31 P4)."""

from __future__ import annotations

import pytest

from batch.services import outcomes as oc
from tests.test_outcomes import SIG, series

중기 = {**SIG, "horizon": "mid"}


def test_126일_창이_다_차야_잰다() -> None:
    # 진입 100(기준일 다음 날), 60번째 날 110, 126번째 날 120 — 지수 60번째 +2%, 126번째 +5%
    값 = [99.0, 100.0] + [100.0] * 59 + [110.0] * 66 + [120.0] * 3
    지수 = [1000.0] * 61 + [1020.0] * 66 + [1050.0] * 3
    o = oc.evaluate(중기, series("2026-01-02", 값), series("2026-01-02", 지수))
    assert o.ret_fit == pytest.approx(0.2) and o.bench_fit == pytest.approx(0.05)
    짧은 = oc.evaluate(중기, series("2026-01-02", 값[:100]), series("2026-01-02", 지수[:100]))
    assert 짧은.ret_fit is None


def _o(sid: int, day: str, ex60: float, ex126: float, horizon: str = "mid") -> oc.Outcome:
    o = oc.Outcome(sid, day, horizon, ret_fit=ex126, bench_fit=0.0)
    o.rets[60], o.bench[60] = ex60, 0.0
    return o


def test_종목_첫_신호_한_건과_월_묶음_t() -> None:
    결과 = [_o(1, "2026-01-05", 0.01, 0.03), _o(1, "2026-01-06", 0.0, 0.5),  # 연속 신호 — 첫 것만
            _o(2, "2026-02-03", 0.02, 0.01), _o(3, "2026-03-03", 0.0, 0.02),
            _o(4, "2026-03-04", 0.0, 0.04, horizon="short")]  # 단기는 뺀다  # fmt: skip
    r = oc.horizon_fit(결과)
    assert (r["n"], r["months"]) == (3, 3)
    assert r["mean_d"] == pytest.approx((0.02 - 0.01 + 0.02) / 3, abs=1e-4)
    assert r["t_month"] is not None and r["halves"][1] is not None
    assert oc.horizon_fit([]) == {"n": 0}
