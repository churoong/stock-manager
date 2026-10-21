"""25.1621 교차검증 [권고] — 핵심 H 백테스트의 로트 원가 수수료 (docs/infra.md 25.1625).

지키는 것: 로트 원가(`unit_factor`)의 수수료는 운영 가상모의투자(`jobs/paper._수수료`)와 같은 **매수 쪽** 값.
안 주면 예전처럼 편도 평균(설정이 비면 둘이 같다)
"""

from __future__ import annotations

import pytest

from batch.jobs import paper as jp
from batch.services import backtest as bt
from batch.services import core_strategy as cs
from tests.test_core_strategy_1532 import 가짜시장


def test_로트_원가는_매수_쪽_수수료() -> None:
    설정 = {"kr_buy_pct": 0.015, "kr_sell_pct": 0.25}
    c = bt.Costs.for_country("KR", 설정, {})
    assert c.commission_pct == pytest.approx(0.1325)  # 엔진의 회전 비용은 편도 평균 그대로
    매수 = jp._수수료("KR", 설정)[0]
    s = cs.make_core_strategy("core_h", 가짜시장([], {}), sessions=[], costs=c, buy_fee_pct=매수)
    assert s.unit_factor == pytest.approx(1 + 0.015 / 100)
    assert cs.make_core_strategy("core_h", 가짜시장([], {}), sessions=[], costs=c).unit_factor == pytest.approx(
        1 + 0.1325 / 100)  # fmt: skip
