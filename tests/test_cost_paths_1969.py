"""25.1969 — 엔진 비용 길 빈도(`bt.cost_paths`). 장부형 리밸런스마다 비용을 현금에서 냈나(cash) 자본 전체에 비례로
냈나(fallback — 현금 < 비용, 운영과 다른 길)를 적고, 핵심 H 실행 결과 행(`metrics_json.cost_path`)에 센 값을 싣는다.

손 계산(편도 0.1%): 둘째 날 비중 0.5 매수 → 현금 0.5 ≥ 비용 0.0005 → cash, 든 비중 0.5 ÷ 0.9995. 셋째 날 0.5 더 사
목표 합이 1 로 묶임 → 현금 0 < 비용 → fallback(비용 = 회전율 (1 − 0.5/0.9995)/2 × 0.002). 매수 없는 날도 장부형
리밸런스라 cash(비용 0)
"""

from __future__ import annotations

import json

import pytest

from batch.services import backtest as bt
from tests.test_engine_cash_cost_1957 import _돌림, 날


def test_비용_길을_센다() -> None:
    r = _돌림({1: bt.LedgerOrders(buys={1: 0.5}), 2: bt.LedgerOrders(buys={1: 0.5})}, {1: [100.0] * 4})
    길 = {x.date: x.cost_path for x in r.rebalances}
    assert 길[날[1]] == "cash" and 길[날[2]] == "fallback"
    cp = bt.cost_paths(r)
    assert cp is not None and cp["fallback"] == 1 and cp["cash"] == len(r.rebalances) - 1
    assert cp["fallback_cost"] == pytest.approx((1 - 0.5 / 0.9995) / 2 * 0.002) and cp["fallback_first"] == 날[2]
    json.dumps(cp)  # 결과 행에 그대로 싣는다


def test_대체_길이_없으면_0() -> None:
    r = _돌림({1: bt.LedgerOrders(buys={1: 0.5})}, {1: [100.0] * 4})
    cp = bt.cost_paths(r)
    assert cp is not None and cp["fallback"] == 0 and cp["fallback_cost"] == 0 and cp["fallback_first"] is None


def test_비중형은_길이_없다() -> None:
    prices = {1: {d: 100.0 for d in 날}}
    r = bt.simulate(prices, 날, [날[1]], lambda d, _v: {1: 0.5}, bt.Costs(), has_delisted=True)
    assert r.rebalances[0].cost_path is None and bt.cost_paths(r) is None
