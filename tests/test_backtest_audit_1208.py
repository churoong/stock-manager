"""백테스트 감사 셋 (docs/infra.md 25.1208~25.1210)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import backtest as job
from batch.services import backtest as bt
from batch.services import pit_universe as pit
from batch.services import universe as uni


def test_시점_유니버스_창도_드문_날을_뺀다() -> None:
    """한 종목만 행이 있는 휴장일(1/1)이 20일 창에 0원 날로 들어가 하한 근처 30종목이 모두 빠졌다 (25.1208)."""
    쉼 = {"2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30", "2024-12-25"}
    days, d = [], date(2024, 10, 1)
    while d <= date(2025, 2, 28):
        if d.weekday() < 5 and d.isoformat() not in 쉼:
            days.append(d.isoformat())
        d += timedelta(days=1)
    n = 30
    prices = {sid: dict.fromkeys(days, 10000.0) for sid in range(n)}
    turn = {sid: dict.fromkeys(days, 520e6) for sid in range(n)}  # 하한 500M 위 4%
    prices[0]["2025-01-01"], turn[0]["2025-01-01"] = 10000.0, 1e6
    universe = [{"stock_id": s, "listed_date": "2000-01-01", "listed_shares": 10**8, "market": "KOSPI"} for s in range(n)]
    base = uni.UniverseFilters.korea()
    stocks = job.pit_stocks(universe)
    series = job.pit_series(prices, turn)
    filters = pit.Filters(min_market_cap=base.min_market_cap, min_avg_turnover_20d=base.min_avg_turnover_20d,
                          min_listed_days=base.min_listed_days, history_starts=job.history_starts(stocks, series))  # fmt: skip
    md = job.pit_market_dates(series)
    assert "2025-01-01" not in md
    hit, _ = pit.members_at(stocks, series, "2025-01-31", filters, md)
    assert len(hit) == n
    # 예전 합집합 창이면 0 이었다
    assert len(pit.members_at(stocks, series, "2025-01-31", filters, pit.market_dates_of(series))[0]) == 0


def test_가격_보기는_거래대금도_기준일까지만_담는다() -> None:
    """`PriceView` 가 거래대금은 통째로 들고 있어 판단 함수가 미래 거래대금을 읽을 수 있었다 (25.1210)."""
    v = bt.PriceView({1: {"2026-01-01": 1.0, "2026-01-02": 1.0, "2026-01-03": 1.0}}, "2026-01-02",
                     {1: {"2026-01-01": 5.0, "2026-01-02": 6.0, "2026-01-03": 7.0}})  # fmt: skip
    assert "2026-01-03" not in v._turnover[1]


def test_빈_창은_공유_캐시에_넣지_않는다() -> None:
    """첫 리밸런스가 가격 이력 첫날(월말)이면 빈 창 결과가 캐시에 남아 다음 달을 현금으로 보냈다 (25.1209)."""
    days, d = [], date(2024, 1, 31)
    while d <= date(2024, 4, 30):
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    n = 30
    prices = {s: {x: 100.0 * (1 + 0.001 * s) ** i for i, x in enumerate(days)} for s in range(n)}
    universe = [{"stock_id": s, "market": "KOSPI", "sector": "A", "listed_shares": 10**7, "currency": "KRW"} for s in range(n)]
    snaps = {s: [{"as_of_date": "2023-03-15", "fiscal_year": 2022, "consolidated": True,
                  "values": {"net_income": 1e8 * (s + 1), "total_equity": 1e9, "revenue": 5e9, "total_assets": 2e9,
                             "operating_income": 1.2e8 * (s + 1), "total_liabilities": 1e9, "currency": "KRW"}}]
             for s in range(n)}  # fmt: skip
    weights = {"value": 20.0, "quality": 20.0, "growth": 20.0, "momentum": 20.0, "risk": 20.0}
    reb = bt.month_starts(days)
    결과 = []
    for cache in (None, {}):
        decide = job.make_strategy("value", universe, snaps, 20, weights, [], None, {}, {}, cache)
        r = bt.simulate(prices, days, reb, decide, bt.Costs.zero())
        결과.append(([len(x.weights) for x in r.rebalances], round(r.curve[-1][1], 6)))
    assert 결과[0] == 결과[1] and 결과[1][0][1] == 20
