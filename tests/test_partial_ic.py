"""증분 IC — 통제 지표를 걷어 낸 스피어만 편상관 (docs/backtest.md 2.5, docs/infra.md 25.742)."""

from __future__ import annotations

import numpy as np
import pytest

from batch.jobs import backtest as job
from batch.services import factor_ic as fic


def _residual_partial(x: list[float], z: list[float], y: list[float]) -> float:
    """다른 길로 잰 값 — 순위를 통제 순위에 회귀한 잔차끼리의 피어슨 상관."""
    rx, rz, ry = (np.array(fic._ranks(v)) for v in (x, z, y))
    Z = np.column_stack([np.ones(len(rz)), rz])
    ex = rx - Z @ np.linalg.lstsq(Z, rx, rcond=None)[0]
    ey = ry - Z @ np.linalg.lstsq(Z, ry, rcond=None)[0]
    return float(np.corrcoef(ex, ey)[0, 1])


def test_잔차끼리의_상관과_같다() -> None:
    n = 40
    z = [float((i * 7) % n) for i in range(n)]
    x = [z[i] + (i % 5) * 3.0 for i in range(n)]
    y = [((i * 13) % n) + 0.5 * z[i] for i in range(n)]
    ic, 쓴수 = fic.partial_period_ic_n(dict(enumerate(x)), dict(enumerate(z)), dict(enumerate(y)))
    assert 쓴수 == n
    assert ic == pytest.approx(_residual_partial(x, z, y), abs=1e-9)


def test_수익이_통제로만_설명되면_증분은_거의_없다() -> None:
    n = 40
    z = [float(i) for i in range(n)]
    x = [float(i + (3 if i % 2 else -3)) for i in range(n)]  # 통제와 거의 같은 순위
    y = [float(i) * 2 for i in range(n)]  # 수익은 통제 그대로
    raw = fic.period_ic(dict(enumerate(x)), dict(enumerate(y)))
    ic, _ = fic.partial_period_ic_n(dict(enumerate(x)), dict(enumerate(z)), dict(enumerate(y)))
    assert raw is not None and raw > 0.9
    assert ic is None  # 수익 순위 = 통제 순위 → 분모 0, 남는 것이 없다


def test_셋_다_있는_종목만_표본_하한도_같다() -> None:
    n = 40
    x = {i: float(i) for i in range(n)}
    z = {i: float((i * 3) % n) for i in range(n)}
    y = {i: float((i * 11) % n) for i in range(n)}
    z[0] = None
    ic, 쓴수 = fic.partial_period_ic_n(x, z, y)
    assert 쓴수 == n - 1 and ic is not None
    ic, 쓴수 = fic.partial_period_ic_n({i: x[i] for i in range(20)}, z, y)
    assert ic is None and 쓴수 < fic.MIN_STOCKS


def test_거래대금_급증은_단기_반전을_통제한_증분도_잰다() -> None:
    import inspect

    assert "flow_surge_x_rev" in job.IC_NAMES
    assert "fic.partial_period_ic_n(급증점수, 반전점수, 수익)" in inspect.getsource(job.factor_ics)
