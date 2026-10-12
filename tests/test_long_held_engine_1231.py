"""장기 신호 전략이 엔진이 실제로 들지 않은 종목을 1년 붙들지 않는다 (docs/infra.md 25.1231, 백테스트 감사 재현).

보유 상한 1. 2/1 A 에 신호가 켜졌지만 A 는 2/1~2/5 거래정지라 엔진이 사지 못한다. 3월부터는 B 에만 신호.
예전: A 를 "든 것" 으로 세어 3/1 에 신호 없이 A 를 사고 끝까지 들었다(B 는 못 삼).
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from batch.jobs import backtest as job
from batch.services import backtest as bt


def _날들() -> list[str]:
    out, d = [], date(2024, 1, 2)
    while d <= date(2024, 6, 28):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _돌림(monkeypatch: pytest.MonkeyPatch, prices: dict, signal: set) -> bt.BacktestResult:
    지금: dict = {}
    monkeypatch.setattr(job, "build_pit_inputs", lambda *a: [object()])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=s, factor=f, score=90.0) for s in (1, 2) for f in ("value", "quality")])  # fmt: skip
    monkeypatch.setattr(job, "long_entry", lambda q, v, closes, eq, sh, th: (지금["t"], 지금["sid"]) in signal)
    원래 = bt.PriceView.level_closes

    def 엿봄(self, sid):  # noqa: ANN001, ANN202
        지금["sid"] = sid
        return 원래(self, sid)

    monkeypatch.setattr(bt.PriceView, "level_closes", 엿봄)
    monkeypatch.setattr(job, "pit_equities", lambda *a: [])
    판단 = job.make_long_strategy(60, [{"stock_id": 1, "listed_shares": 10}, {"stock_id": 2, "listed_shares": 10}],
                                 {}, max_holdings=1)  # fmt: skip

    def decide(t, view):  # noqa: ANN001, ANN202
        지금["t"] = t
        return 판단(t, view)

    dates = bt.market_dates(prices, _날들()[0])
    return bt.simulate(prices, dates, bt.month_starts(dates), decide, bt.Costs.zero())


def test_정지로_못_산_종목은_자리를_차지하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    days = _날들()
    정지 = {x for x in days if "2024-02-01" <= x <= "2024-02-05"}
    prices = {
        1: {x: 100.0 * (0.995**i) for i, x in enumerate(days) if x not in 정지},
        2: {x: 100.0 * (1.003**i) for i, x in enumerate(days)},
        3: {x: 100.0 for x in days},
    }
    r = _돌림(monkeypatch, prices, {("2024-02-01", 1), ("2024-03-01", 2), ("2024-04-01", 2), ("2024-05-01", 2)})
    by = {rb.date: rb.weights for rb in r.rebalances}
    assert by["2024-03-01"] == {2: 1.0}
    assert all(1 not in w or w[1] == 0 for w in by.values())


def test_상장폐지로_판_종목은_자리를_비운다(monkeypatch: pytest.MonkeyPatch) -> None:
    days = _날들()
    prices = {
        1: {x: 100.0 for x in days if x <= "2024-02-20"},  # 2/20 뒤 거래 없음 — 엔진이 마지막 종가로 판다
        2: {x: 100.0 * (1.003**i) for i, x in enumerate(days)},
        3: {x: 100.0 for x in days},
    }
    r = _돌림(monkeypatch, prices, {("2024-02-01", 1), ("2024-04-01", 2), ("2024-05-01", 2)})
    by = {rb.date: rb.weights for rb in r.rebalances}
    assert by["2024-02-01"] == {1: 1.0}
    assert by["2024-04-01"] == {2: 1.0}  # 예전: A 가 1년 자리를 막아 B 를 못 샀다
