"""장부 주문(`LedgerOrders`) — 든 것은 그대로, 판 것·산 것만 (docs/backtest.md 10장, docs/infra.md 25.1526)."""

from __future__ import annotations

import pytest

from batch.services import backtest as bt

날 = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
무비용 = bt.Costs(commission_pct=0.0, tax_sell_pct=0.0, slippage_pct=0.0)
값 = {
    1: {"2026-01-02": 100.0, "2026-01-05": 120.0, "2026-01-06": 90.0, "2026-01-07": 110.0},
    2: {"2026-01-02": 50.0, "2026-01-05": 50.0, "2026-01-06": 60.0, "2026-01-07": 55.0},
}


def _첫날만_사고(buys: dict[int, float], 이후: bt.LedgerOrders | None = None):
    def 판단(t: str, view: bt.PriceView) -> bt.LedgerOrders:
        if not view.weights:
            return bt.LedgerOrders(buys=buys)
        return 이후 or bt.LedgerOrders()

    return 판단


def test_hold_drifts_without_turnover() -> None:
    """빈 주문이면 날마다 판단해도 회전 0 — 곡선이 손으로 잰 사고-들기와 같다"""
    r = bt.simulate(값, 날, 날, _첫날만_사고({1: 0.5, 2: 0.5}), 무비용, has_delisted=True)
    assert [x.turnover for x in r.rebalances[1:]] == [0.0, 0.0]
    for d, v in r.curve:
        assert v == pytest.approx(0.5 * 값[1][d] / 100 + 0.5 * 값[2][d] / 50)


def test_weights_dict_rebalances_daily_by_contrast() -> None:
    """비교: 지금 비중을 dict 로 돌려주면 날마다 다시 맞춘다(회전 > 0) — 장부 주문이 필요한 까닭"""
    판단 = lambda t, v: dict(v.weights) if v.weights else {1: 0.5, 2: 0.5}  # noqa: E731
    r = bt.simulate(값, 날, 날, 판단, 무비용, has_delisted=True)
    assert any(x.turnover > 0 for x in r.rebalances[1:])


def test_sell_leaves_cash() -> None:
    """01-06 에 1 을 팔면 그 돈은 현금 — 01-07 의 1 가격 변화가 곡선에 안 닿는다"""

    def 판단(t: str, view: bt.PriceView) -> bt.LedgerOrders:
        if not view.weights:
            return bt.LedgerOrders(buys={1: 0.5, 2: 0.5})
        return bt.LedgerOrders(sells=frozenset({1})) if t == "2026-01-06" else bt.LedgerOrders()

    r = bt.simulate(값, 날, 날, 판단, 무비용, has_delisted=True)
    현금 = 0.5 * 90 / 100
    assert dict(r.curve)["2026-01-07"] == pytest.approx(현금 + 0.5 * 55 / 50)
    assert set(r.rebalances[2].weights) == {2}


def test_unbuyable_buy_stays_cash() -> None:
    """오늘 거래 없는 종목 매수는 빼고 현금으로 — 다른 종목에 나누지 않는다"""
    prices = {**값, 3: {"2026-01-05": 10.0, "2026-01-06": 10.0}}
    r = bt.simulate(prices, 날, 날, _첫날만_사고({1: 0.3, 3: 0.3}), 무비용, has_delisted=True)
    assert r.rebalances[0].weights == {1: pytest.approx(0.3)} and r.rebalances[0].dropped == [3]


def test_buys_scaled_to_room_and_negative_rejected() -> None:
    target, dropped = bt.ledger_target(
        bt.LedgerOrders(buys={2: 0.4, 3: 0.4}), {1: 0.6}, tradable=lambda s: True, halted=lambda s: False
    )
    assert target == {1: 0.6, 2: pytest.approx(0.2), 3: pytest.approx(0.2)} and dropped == []
    with pytest.raises(ValueError):
        bt.ledger_target(bt.LedgerOrders(buys={2: -0.1}), {}, tradable=lambda s: True, halted=lambda s: False)


def test_halted_cannot_be_sold_and_delisted_is_sold() -> None:
    """정지(오늘 없고 뒤에 있음)는 sells 에 있어도 묶임 · 상장폐지(뒤에 없음)는 sells 없이도 판다"""
    target, _ = bt.ledger_target(
        bt.LedgerOrders(sells=frozenset({1})),
        {1: 0.3, 2: 0.3, 3: 0.3},
        tradable=lambda s: s == 3,
        halted=lambda s: s == 1,
    )
    assert target == {1: 0.3, 3: 0.3}  # 2 는 오늘 거래 없고 정지도 아님 → 상장폐지로 팜
