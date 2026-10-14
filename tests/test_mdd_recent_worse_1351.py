"""최근 60일 낙폭이 저장 MDD 보다 깊은 신호를 **세기만** 한다 (docs/infra.md 25.1351, 22회차 P4)."""

from __future__ import annotations

import pytest

from batch.services import signals as sg


def test_최근_낙폭은_고점_대비_최대_하락() -> None:
    # 100 → 120 → 60 → 90: 고점 120 에서 60 으로 -50%
    assert sg.recent_drawdown([100.0, 120.0, 60.0, 90.0], kr=False, split_pending=False) == pytest.approx(-0.5)
    assert sg.recent_drawdown([100.0, 110.0, 120.0], kr=False, split_pending=False) == 0.0
    assert sg.recent_drawdown([100.0], kr=False, split_pending=False) is None


def test_분할_대기와_국내_가격제한폭_초과는_모름() -> None:
    assert sg.recent_drawdown([100.0, 120.0, 60.0], kr=False, split_pending=True) is None
    # 국내 하루 -50% 는 가격제한폭(±30%) 밖이다 — 수정주가 빈틈으로 본다
    assert sg.recent_drawdown([100.0, 120.0, 60.0], kr=True, split_pending=False) is None
    # 미국은 제한폭이 없어 그대로 잰다
    assert sg.recent_drawdown([100.0, 120.0, 60.0], kr=False, split_pending=False) == pytest.approx(-0.5)
    # 국내라도 하루 ±30% 안이면 잰다 (100 → 75 → 60: -40%)
    assert sg.recent_drawdown([100.0, 75.0, 60.0], kr=True, split_pending=False) == pytest.approx(-0.4)


def test_MDD_배수가_더_작아질_때만_센다() -> None:
    # 저장 MDD -40% → 배수 0.875, 최근 -50% → 0.7: 더 작다
    assert sg.mdd_recent_worse(-0.5, -0.4)
    # 둘 다 목표(35%) 안이면 배수가 1.0 으로 같다 — 세지 않는다
    assert not sg.mdd_recent_worse(-0.3, -0.2)
    # 최근이 덜 나쁘면 세지 않는다
    assert not sg.mdd_recent_worse(-0.4, -0.5)
    # 저장 MDD 가 없으면 모름 — 위험하다고 단정하지 않는다
    assert not sg.mdd_recent_worse(-0.5, None)
    assert not sg.mdd_recent_worse(None, -0.4)
    assert not sg.mdd_recent_worse(-0.5, float("nan"))


def test_비중은_그대로() -> None:
    # 기록만 — size_reduction 은 저장 MDD 만 본다
    assert sg.size_reduction(None, -0.4)[1]["mdd_factor"] == pytest.approx(0.875)
