"""`PriceView.weights` — 장부형 전략이 읽는 지금 비중은 cutoff 종가로 잰다 (docs/backtest.md 10장 단계 0, docs/infra.md 25.1525)."""

from __future__ import annotations

import pytest

from batch.services import backtest as bt

날 = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]


def _돌려(prices: dict[int, dict[str, float]], 판단날: list[str] | None = None) -> list[dict[int, float] | None]:
    """판단날마다 — 첫 판단은 1·2 반반, 그 뒤는 지금 비중을 그대로 들고 간다(본 비중을 적어 둔다)"""
    본: list[dict[int, float] | None] = []

    def 판단(t: str, view: bt.PriceView) -> dict[int, float]:
        본.append(None if view.weights is None else dict(view.weights))
        return dict(view.weights) if view.weights else {1: 0.5, 2: 0.5}

    bt.simulate(prices, 날, 판단날 or 날, 판단, bt.Costs(commission_pct=0.0, tax_sell_pct=0.0, slippage_pct=0.0), has_delisted=True)
    return 본


def test_weights_by_hand() -> None:
    """첫날 반반 → 다음 날 1 이 두 배면 2/3·1/3 (손으로)"""
    prices = {
        1: {"2026-01-02": 100.0, "2026-01-05": 200.0, "2026-01-06": 200.0, "2026-01-07": 200.0},
        2: {"2026-01-02": 100.0, "2026-01-05": 100.0, "2026-01-06": 100.0, "2026-01-07": 100.0},
    }
    본 = _돌려(prices, ["2026-01-02", "2026-01-06"])
    assert 본[0] == {}  # 첫 판단: cutoff 없음 → 든 것 없음
    assert 본[1] == {1: pytest.approx(2 / 3), 2: pytest.approx(1 / 3)}  # 01-06 판단은 01-05 종가로 잰다
    본 = _돌려(prices)  # 날마다 반반으로 되돌리면 01-05 에 다시 맞춰 01-06 판단은 반반
    assert 본[1] == 본[2] == {1: pytest.approx(0.5), 2: pytest.approx(0.5)}


def test_weights_do_not_see_decision_day_price() -> None:
    """t 종가를 바꿔도 t 의 판단이 보는 비중은 그대로 — t 종가로 재면 깨진다(look-ahead)"""
    기본 = {
        1: {"2026-01-02": 100.0, "2026-01-05": 100.0, "2026-01-06": 100.0, "2026-01-07": 100.0},
        2: {"2026-01-02": 100.0, "2026-01-05": 100.0, "2026-01-06": 100.0, "2026-01-07": 100.0},
    }
    바뀜 = {sid: dict(v) for sid, v in 기본.items()}
    바뀜[1]["2026-01-06"] = 300.0  # 01-06 판단일의 종가
    판단날 = ["2026-01-02", "2026-01-06"]
    assert _돌려(기본, 판단날)[1] == _돌려(바뀜, 판단날)[1] == {1: pytest.approx(0.5), 2: pytest.approx(0.5)}


def test_weights_read_only_and_outside_engine_unknown() -> None:
    v = bt.PriceView({1: {"2026-01-02": 1.0}}, "2026-01-02", weights={1: 0.4})
    with pytest.raises(TypeError):
        v.weights[1] = 1.0  # type: ignore[index]
    assert dict(v.weights) == {1: 0.4} and v.weights.get(2) is None
    assert bt.PriceView({}, "2026-01-02").weights is None


def test_weights_leave_curve_unchanged() -> None:
    """비중을 읽기만 하면 곡선은 같다 — 판단 함수가 weights 를 안 봐도 같은 결과"""
    prices = {
        1: {"2026-01-02": 100.0, "2026-01-05": 110.0, "2026-01-06": 90.0, "2026-01-07": 95.0},
        2: {"2026-01-02": 50.0, "2026-01-05": 55.0, "2026-01-06": 60.0, "2026-01-07": 58.0},
    }
    비용 = bt.Costs(commission_pct=0.1, tax_sell_pct=0.2, slippage_pct=0.1)
    a = bt.simulate(prices, 날, 날, lambda t, v: {1: 0.5, 2: 0.5}, 비용, has_delisted=True)
    b = bt.simulate(prices, 날, 날, lambda t, v: ({1: 0.5, 2: 0.5} if v.weights is not None else {}), 비용, has_delisted=True)
    assert a.curve == b.curve
