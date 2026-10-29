"""25.1957 — 엔진이 장부형 전략(운영 계좌 재현)의 거래비용을 **현금에서** 낸다.

예전엔 `equity = drifted_total × (1 − cost)` 로 자본 전체에 비례해 빼 든 종목 수량까지 비용만큼 줄었다. 운영 가상모의투자는
체결 금액에 붙는 수수료·세금·슬리피지를 현금에서만 낸다(`pp.make_fill`). 그 차이가 일치 시험 허용 ⑨(보유 비중 차 최대
1.25e-4)와 ⑨(다) 결정 뒤집힘의 뿌리였다. 지금은 손으로 센다:

- 장부형 · 현금이 비용보다 많다: 든 종목 값 = 목표 비중 × 그날 자본(비용 전) 그대로, 현금 = (1 − Σ목표 − 비용) × 자본
- 장부형 · 현금이 모자란다(거의 다 투자): 예전처럼 자본 전체에
- 비중형(월 백테스트 — dict 를 내는 판단 함수): 예전 그대로
"""

from __future__ import annotations

import pytest

from batch.services import backtest as bt
from tests import test_core_strategy_1532 as t32

날 = t32._영업일("2024-01-02", 4)
비용 = bt.Costs(commission_pct=0.1, tax_sell_pct=0.0, slippage_pct=0.0)  # 편도 0.1% — 손으로 세기 쉽게


def _돌림(판단: dict[int, object], 종가: dict[int, list[float]]) -> bt.BacktestResult:
    prices = {sid: {날[k]: p for k, p in enumerate(xs)} for sid, xs in 종가.items()}
    return bt.simulate(prices, 날, 날, lambda d, _v: 판단.get(날.index(d), bt.LedgerOrders()), 비용, has_delisted=True)


def test_장부형은_비용을_현금에서_낸다() -> None:
    """둘째 날 종목 1 을 비중 0.5 로 산다 — 회전율 0.25, 비용 = 0.25 × 2 × 0.001 = 0.0005. 종목 값은 0.5 그대로(주식 수 0.5 ÷ 100),
    현금 0.5 − 0.0005, 자본 0.9995. 셋째 날 종가 110 → 자본 = 0.5 × 1.1 + 0.4995"""
    r = _돌림({1: bt.LedgerOrders(buys={1: 0.5})}, {1: [100.0, 100.0, 110.0, 110.0]})
    reb = r.rebalances[1]
    assert reb.cost == pytest.approx(0.0005)
    assert reb.weights[1] == pytest.approx(0.5 / 0.9995)
    자본 = dict(r.curve)
    assert 자본[날[1]] == pytest.approx(0.9995)
    assert 자본[날[2]] == pytest.approx(0.5 * 1.1 + 0.4995)


def test_든_종목은_비용으로_줄지_않는다() -> None:
    """종목 1 을 든 채 종목 2 를 산다 — 예전 엔진은 종목 1 의 주식 수까지 (1 − 비용) 만큼 줄였다. 지금은 종목 1 값 그대로"""
    r = _돌림({1: bt.LedgerOrders(buys={1: 0.4}), 2: bt.LedgerOrders(buys={2: 0.3})},
              {1: [100.0, 100.0, 100.0, 100.0], 2: [50.0, 50.0, 50.0, 50.0]})  # fmt: skip
    자본 = dict(r.curve)
    전 = 자본[날[1]]  # 0.9996 (첫 매수 비용 0.4 × 0.001)
    셋 = r.rebalances[2]
    # 종목 1 값 = 0.4 (1 − ε) 비중 × 전 — 비용 0.0003 은 현금에서만
    assert 셋.cost == pytest.approx(0.0003)
    assert 셋.weights[1] * 자본[날[2]] == pytest.approx(r.rebalances[1].weights[1] * 전)
    assert 자본[날[2]] == pytest.approx(전 * (1 - 0.0003))


def test_현금이_모자라면_예전처럼_자본_전체에() -> None:
    """비중 1.0 을 사면 현금 0 < 비용 — 자본 전체에 비례로(예전 식): 비용 = 회전율 0.5 × (0.001 × 2) = 0.001, 자본 0.999"""
    r = _돌림({1: bt.LedgerOrders(buys={1: 1.0})}, {1: [100.0, 100.0, 100.0, 100.0]})
    reb = r.rebalances[1]
    assert reb.cost == pytest.approx(0.001)  # 회전율 0.5 × 0.002
    assert reb.weights[1] == pytest.approx(1.0) and dict(r.curve)[날[1]] == pytest.approx(1 - 0.001)


def test_비중형은_예전_그대로() -> None:
    """dict 를 내는 판단 함수(월 백테스트) — 비중 0.5 를 사도 자본 전체에 비례(비중은 목표 그대로 0.5)"""
    prices = {1: {d: 100.0 for d in 날}}
    r = bt.simulate(prices, 날, [날[1]], lambda d, _v: {1: 0.5}, 비용, has_delisted=True)
    reb = r.rebalances[0]
    assert reb.weights[1] == pytest.approx(0.5) and dict(r.curve)[날[1]] == pytest.approx(1 - reb.cost)


def test_엔진_판은_7() -> None:
    assert bt.CALC_VERSION == 7


def test_현금이_모자란_날의_지정가_몫도_예전_식() -> None:
    """25.1961 (25.1957 교차검증 [권고]) — 대체 길의 지정가 갈래(`elif 지정가비율`)를 손으로. 둘째 날 종목 1 을 0.6(종가),
    종목 2 를 지정가 50 회차 0.4 로 산다(종가 49 — 찬다). Σ목표 1.0 이라 현금 0 < 비용 0.001(회전율 0.5 × 0.002) → 예전 식:
    종목 2 몫 = 0.4 × 49/50 = 0.392, 남음 = 1 − 1.0 + 0.6 + 0.392 = 0.992, 자본 = 0.999 × 0.992, 비중 = 값 ÷ 0.992"""
    r = _돌림({1: bt.LedgerOrders(buys={1: 0.6}, limits={2: ((0.4, 50.0),)})},
              {1: [100.0, 100.0, 100.0, 100.0], 2: [50.0, 49.0, 49.0, 49.0]})  # fmt: skip
    reb = r.rebalances[1]
    assert reb.cost == pytest.approx(0.001)
    assert dict(r.curve)[날[1]] == pytest.approx(0.999 * 0.992)
    assert reb.weights[1] == pytest.approx(0.6 / 0.992) and reb.weights[2] == pytest.approx(0.392 / 0.992)


def test_매도_대금과_거래세도_현금에서() -> None:
    """25.1961 (25.1957 교차검증 note) — 매도 쪽 손 계산. 수수료 0·거래세 0.2%. 둘째 날 종목 1·2 를 0.4 씩, 셋째 날 종목 2
    전량 매도: 세금 = 0.4 × 0.002 = 0.0008 은 현금에서만 — 종목 1 값 0.4 그대로, 자본 0.9992"""
    세금 = bt.Costs(commission_pct=0.0, tax_sell_pct=0.2, slippage_pct=0.0)
    prices = {1: {d: 100.0 for d in 날}, 2: {d: 50.0 for d in 날}}
    판단 = {1: bt.LedgerOrders(buys={1: 0.4, 2: 0.4}), 2: bt.LedgerOrders(sells=frozenset({2}))}
    r = bt.simulate(prices, 날, 날, lambda d, _v: 판단.get(날.index(d), bt.LedgerOrders()), 세금, has_delisted=True)
    셋 = r.rebalances[2]
    assert 셋.cost == pytest.approx(0.0008) and 2 not in 셋.weights
    자본 = dict(r.curve)[날[2]]
    assert 자본 == pytest.approx(0.9992) and 셋.weights[1] * 자본 == pytest.approx(0.4)


def test_현금이_조금_있지만_비용보다_적은_날() -> None:
    """25.1962 (25.1961 교차검증 [권고]) — Σ목표 0.9995(현금 0.0005 > 0) 인데 비용 0.0009995(회전율 0.49975 × 0.002)가 더 크다 →
    대체 길. 남음 = 1 − 0.9995 + 0.5995 + 0.4 × 49/50 = 0.992 — 남은 현금 0.0005 가 든다(Σ목표 = 1 인 시험은 이 항이 0 이라
    `1 − Σ목표` 를 빼도 지나갔다). 자본 = (1 − 0.0009995) × 0.992"""
    r = _돌림({1: bt.LedgerOrders(buys={1: 0.5995}, limits={2: ((0.4, 50.0),)})},
              {1: [100.0, 100.0, 100.0, 100.0], 2: [50.0, 49.0, 49.0, 49.0]})  # fmt: skip
    reb = r.rebalances[1]
    assert reb.cost == pytest.approx(0.0009995)
    assert dict(r.curve)[날[1]] == pytest.approx((1 - 0.0009995) * 0.992)
    assert reb.weights[1] == pytest.approx(0.5995 / 0.992) and reb.weights[2] == pytest.approx(0.392 / 0.992)



def test_대체_길_지정가로_더_산_든_종목은_든_몫을_할인하지_않는다() -> None:
    """25.1964 (25.1962 교차검증 [권고]) — 대체 길의 `남긴`(이미 든 몫은 지정가 할인 없이) 을 손으로. 앞 시험들은 빈 계좌라
    남긴 = 0 이었다.

    둘째 날 종목 2 를 0.2(종가 50) — 자본 E1 = 0.9998(비용 0.0002, 현금 길). 셋째 날 종목 2 종가 49: 든 값 0.196, 자본 D = 0.9958,
    든 비중 d = 0.196/D. 그날 종목 2 지정가 50 회차 0.2(판단 자본 E1 대비 → t 자본 대비 b = 0.2 × E1/D)와 종목 1 종가 0.602 를
    사면 현금 < 비용 → 대체 길. 비용 = (b + 0.602) × 0.001, 값2 = d + b × 49/50(든 몫 d 는 할인 없음), 남음 = 1 − 0.02 b,
    자본 = D × (1 − 비용) × 남음"""
    판단 = {1: bt.LedgerOrders(buys={2: 0.2}), 2: bt.LedgerOrders(buys={1: 0.602}, limits={2: ((0.2, 50.0),)})}
    r = _돌림(판단, {1: [100.0, 100.0, 100.0, 100.0], 2: [50.0, 50.0, 49.0, 49.0]})
    E1, D = 0.9998, 0.9998 - 0.2 * (1 - 49 / 50)
    d, b = 0.196 / D, 0.2 * E1 / D
    비용 = (b + 0.602) * 0.001
    남음 = 1 - 0.02 * b
    셋 = r.rebalances[2]
    assert 1 - (d + b + 0.602) < 셋.cost == pytest.approx(비용)  # 현금 < 비용 — 대체 길
    assert dict(r.curve)[날[2]] == pytest.approx(D * (1 - 비용) * 남음)
    assert 셋.weights[2] == pytest.approx((d + b * 49 / 50) / 남음) and 셋.weights[1] == pytest.approx(0.602 / 남음)
