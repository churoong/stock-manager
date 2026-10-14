"""약세장 배수 0 이 장기 신호 전략의 1년 보유를 지우지 않는다 (docs/infra.md 25.1321, 백테스트 감사 #3).

보유 상한 1. 2/1 A 에 신호 → 산다. 3월 첫 리밸런스(t-1 = 2/29)에는 지수가 200일선 아래(약세)라 배수 0 → 비중 0.
4월에는 다시 강세. B 에는 3~5월 신호.
예전: 3월에 배수 0 이 A 를 비우자 장기 전략이 "엔진이 빼 버린 종목"(25.1231)으로 읽어 A 를 잊고, 4월에 B 를 샀다.
배수 0.01 이면 A 를 기억했다 — 배수 0 만 다르게 움직였다.
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


def _지수() -> list[tuple[str, float]]:
    """매일 100, 2/20~3/15 만 90 — 3/1 리밸런스(t-1 2/29)는 약세, 2/1·4/1 은 강세."""
    out, d = [], date(2023, 1, 1)
    while d <= date(2024, 6, 28):
        x = d.isoformat()
        out.append((x, 90.0 if "2024-02-20" <= x <= "2024-03-15" else 100.0))
        d += timedelta(days=1)
    return out


def _돌림(monkeypatch: pytest.MonkeyPatch, bear_factor: float) -> dict[str, dict[int, float]]:
    days = _날들()
    prices = {1: {x: 100.0 for x in days}, 2: {x: 100.0 * (1.003**i) for i, x in enumerate(days)},
              3: {x: 100.0 for x in days}}  # fmt: skip
    signal = {("2024-02-01", 1), ("2024-03-01", 2), ("2024-04-01", 2), ("2024-05-01", 2)}
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

    덧씌움 = job.with_trend_filter(decide, {"KOSPI": _지수()}, {1: "KOSPI", 2: "KOSPI"}, bear_factor, [])
    dates = bt.market_dates(prices, days[0])
    r = bt.simulate(prices, dates, bt.month_starts(dates), 덧씌움, bt.Costs.zero())
    return {rb.date: rb.weights for rb in r.rebalances}


def test_배수_0_이_지운_종목도_1년_보유를_기억한다(monkeypatch: pytest.MonkeyPatch) -> None:
    by = _돌림(monkeypatch, 0.0)
    assert by["2024-02-01"] == {1: 1.0}
    assert by["2024-03-01"].get(1, 0) == 0 and by["2024-03-01"].get(2, 0) == 0  # 약세 — 현금
    assert by["2024-04-01"] == {1: 1.0}  # 예전: {2: 1.0} — A 를 잊고 B 를 샀다


def test_배수_0과_작은_배수가_같은_종목을_든다(monkeypatch: pytest.MonkeyPatch) -> None:
    영 = _돌림(monkeypatch, 0.0)
    작음 = _돌림(monkeypatch, 0.01)
    assert set(영["2024-04-01"]) == set(작음["2024-04-01"]) == {1}
    assert set(영["2024-05-01"]) == set(작음["2024-05-01"])
