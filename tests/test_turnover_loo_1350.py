"""단기 "수급 유입" — 가장 큰 하루를 빼고도 1.2배인지 **기록만** (docs/infra.md 25.1350, 22회차 P2)."""

from __future__ import annotations

import pytest

from batch.services import signals as sg
from tests.test_signals import short_input


def test_하루_이벤트만으로_넘은_비는_빼면_아래() -> None:
    # 59일 100, 마지막 20일 중 19일 100 · 하루 1,000 — 20일 평균 145, 60일 평균 115 → 1.26배(넘음)
    거래 = [100.0] * 59 + [100.0] * 19 + [1000.0]
    assert sg.moving_average(거래, 20) / sg.moving_average(거래, 60) > sg.TURNOVER_MULTIPLE
    assert sg.turnover_ratio_loo(거래) == pytest.approx(1.0)  # 그 하루를 빼면 평소 그대로


def test_고르게_늘었으면_빼도_넘는다() -> None:
    assert sg.turnover_ratio_loo([100.0] * 60 + [200.0] * 20) > sg.TURNOVER_MULTIPLE
    assert sg.turnover_ratio_loo([100.0] * 30) is None  # 60일이 모자라다


def test_판정과_근거표는_그대로() -> None:
    거래 = [100.0] * 59 + [100.0] * 19 + [1000.0]
    passed, data = sg.short_term(short_input(turnovers=거래))
    assert data["turnover_ratio_loo"] == pytest.approx(1.0)
    assert passed == sg.short_term(short_input(turnovers=거래))[0]  # 기록만 — 조건에 안 들어간다
    assert "turnover_ratio_loo" not in str(sg.judgement("short", short_input(turnovers=거래), data))
