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


def test_다_투자한_채_매매_없는_날은_대체_길이_아니다() -> None:
    """25.1972 (25.1969 교차검증 [권고]) — 일곱 종목을 1/7 씩 전액 사고 30일 거래 없이 두면 표류 비중을 다시 나눈 Σ목표가
    부동소수로 1.0000000000000002 가 되는 날이 있다(아래 씨앗 5 에서 둘). 비용 0 인 날은 두 길의 값이 같으므로 대체 길로
    세지 않는다 — 대체 길은 사는 날(현금 0 < 비용) 하나뿐"""
    import random

    from tests import test_core_strategy_1532 as t32

    날30 = t32._영업일("2024-01-02", 30)
    rnd = random.Random(5)
    assert rnd.randint(3, 9) == 7  # 찾은 씨앗의 첫 뽑기(종목 수) — 뒤 가격 계열이 같게
    prices: dict[int, dict[str, float]] = {}
    for sid in range(1, 8):
        p = rnd.uniform(10, 200)
        prices[sid] = {}
        for d in 날30:
            prices[sid][d] = round(p, 2)
            p *= 1 + rnd.uniform(-0.03, 0.03)
    비용 = bt.Costs(commission_pct=0.1, tax_sell_pct=0.0, slippage_pct=0.0)
    산날 = bt.LedgerOrders(buys={sid: 1 / 7 for sid in prices})
    r = bt.simulate(prices, 날30, 날30, lambda d, _v: 산날 if d == 날30[1] else bt.LedgerOrders(), 비용,
                    has_delisted=True)  # fmt: skip
    넘침 = [x for x in r.rebalances if x.cost == 0 and sum(x.weights.values()) > 1]
    assert 넘침  # 재현 조건이 살아 있다
    assert all(x.cost_path == "cash" for x in 넘침)
    cp = bt.cost_paths(r)
    assert cp is not None and cp["fallback"] == 1 and cp["fallback_first"] == 날30[1]
