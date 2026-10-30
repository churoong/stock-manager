"""깔때기 ⑧-3 첫 단 — 장부형 리플레이(`CoreStrategy`)의 시장 통화 장부 (25.2058, docs/funnel.md 4장 7).

같은 합성 자료를 국내(원화, KOSPI)와 미국(달러, SP500 — 지수 값은 같다)으로 달아 B·G 를 끝까지 돌리면 매수·매도 결정이
같다. 예전엔 `buy_orders` 에 환율 없이(`fx_rate=None`) 넘겨 운영이 달러 종목을 "환율 없음" 으로 걸러 미국에서 아무것도
사지 않았다. 남는 차이는 이름 붙인 것만: 호가 단위(원화 10원 대 달러 0.01 — 회차 지정가·주당 금액이 아주 조금 갈린다)와
거래소 달력(관문 묵음 바닥 — 신호 행 기준일이 cutoff 라 닿지 않는다). 운영 미국 `run_step` 과의 일치는 다음 단(⑧-3b).
"""

from __future__ import annotations

from functools import cache

import pytest

from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import core_strategy as cs
from tests.test_core_strategy_1532 import _자료, 날들, 모의


def _달기(나라: str) -> tuple:
    universe, prices, turn, snaps, idx = _자료()
    if 나라 == "KR":
        # 국면 지수를 하나로(KOSDAQ 종목도 KOSPI) — 미국은 SP500 하나라 같은 국면을 보게
        u = [dict(r, market="KOSPI") for r in universe]
        return u, prices, turn, snaps, {"KOSPI": idx["KOSPI"]}
    u = [dict(r, market="NASDAQ", currency="USD") for r in universe]
    s = {k: [dict(x, values=dict(x["values"], currency="USD")) for x in v] for k, v in snaps.items()}
    return u, prices, turn, s, {"SP500": idx["KOSPI"]}


@cache
def _끝까지(variant: str, 나라: str) -> tuple[cs.CoreStrategy, bt.BacktestResult]:
    universe, prices, turn, snaps, idx = _달기(나라)
    s = cs.make_core_strategy(variant, cb.CoreMarket(universe, snaps, idx, country=나라), sessions=날들,
                              costs=bt.Costs.zero(), buy_fee_pct=0.0)  # fmt: skip
    return s, bt.simulate(prices, 모의, 모의, s, bt.Costs.zero(), turnover=turn, has_delisted=True)


def _결정(s: cs.CoreStrategy) -> list[tuple]:
    return [(lg.day, tuple(sorted(lg.buys)), tuple(sorted((k, tuple(sorted(v))) for k, v in lg.sells.items())))
            for lg in s.log]  # fmt: skip


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_미국_달러_장부는_국내와_같은_결정을_낸다(variant: str) -> None:
    kr, kr_r = _끝까지(variant, "KR")
    us, us_r = _끝까지(variant, "US")
    assert sum(len(lg.buys) for lg in us.log) >= 5, "미국이 거의 사지 않는다 — 환율 거름이 다시 걸렸나"
    assert _결정(us) == _결정(kr)
    # 곡선은 호가 단위 차이(회차 지정가 반올림 → 주당 금액)만큼만 — 실측 최대 7.7e-9(B)·7.0e-9(G), 그 백 배쯤
    assert max(abs(a[1] - b[1]) for a, b in zip(kr_r.curve, us_r.curve, strict=True)) < 1e-6
