"""시점 유니버스 테스트 (docs/backtest.md 1.3).

이 판정이 틀리면 백테스트가 조용히 미래를 본다. 그래서 경계마다 고정한다.
"""

from __future__ import annotations

from batch.services import pit_universe as pu

FILTERS = pu.Filters(min_market_cap=1_000, min_avg_turnover_20d=100)


def series(days: list[str], close: float = 10.0, volume: float = 50.0) -> pu.Series:
    return pu.Series(
        dates=list(days),
        closes={d: close for d in days},
        turnover={d: close * volume for d in days},
    )


def trading_days(start_year: int, count: int, start_month: int = 1) -> list[str]:
    from datetime import date, timedelta

    out: list[str] = []
    day = date(start_year, start_month, 1)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


class Test상장경과일:
    def test_상장_1년_미만은_뺀다(self) -> None:
        days = trading_days(2026, 30)
        stock = pu.Stock(1, listed_date="2026-01-01", listed_shares=1_000)
        assert pu.judge(stock, series(days), "2026-02-10", FILTERS).reason == pu.REASON_TOO_NEW

    def test_1년_지나면_들어온다(self) -> None:
        days = trading_days(2025, 300)
        stock = pu.Stock(1, listed_date="2025-01-01", listed_shares=1_000)
        assert pu.judge(stock, series(days), "2026-01-02", FILTERS).included

    def test_상장일이_없으면_관측된_첫_거래일로_센다(self) -> None:
        days = trading_days(2025, 300)
        stock = pu.Stock(1, listed_date=None, listed_shares=1_000)
        # 첫 거래일 2025-01-01 기준 1년이 지났다
        assert pu.judge(stock, series(days), "2026-01-02", FILTERS).included
        # 그 전에는 아직이다
        assert pu.judge(stock, series(days), "2025-06-30", FILTERS).reason == pu.REASON_TOO_NEW

    def test_가격이_아직_없으면_상장_전이다(self) -> None:
        days = trading_days(2026, 10, start_month=6)
        stock = pu.Stock(1, listed_date="2020-01-01", listed_shares=1_000)
        assert pu.judge(stock, series(days), "2026-01-05", FILTERS).reason == pu.REASON_NOT_LISTED


class Test시가총액:
    def test_그날_종가로_잰다(self) -> None:
        days = trading_days(2025, 300)
        stock = pu.Stock(1, listed_date="2020-01-01", listed_shares=100)
        # 종가 10 × 100주 = 1,000 → 하한과 같으므로 통과 (경계 포함)
        assert pu.judge(stock, series(days, close=10.0), "2026-01-02", FILTERS).included
        # 종가가 반이면 시총도 반이라 탈락한다. 오늘 시총으로 잘랐다면 통과했을 종목이다
        out = pu.judge(stock, series(days, close=5.0), "2026-01-02", FILTERS)
        assert out.reason == pu.REASON_SMALL_CAP
        assert out.market_cap == 500

    def test_주식수를_모르면_시총으로_자르지_않는다(self) -> None:
        days = trading_days(2025, 300)
        stock = pu.Stock(1, listed_date="2020-01-01", listed_shares=None)
        # 주가는 낮지만 거래는 충분하다. 시총만 모르는 상황을 만든다
        decision = pu.judge(stock, series(days, close=0.01, volume=1_000_000.0), "2026-01-02", FILTERS)
        assert decision.included
        assert decision.market_cap is None


class Test거래대금:
    def test_20일_평균이_하한에_못_미치면_뺀다(self) -> None:
        days = trading_days(2025, 300)
        stock = pu.Stock(1, listed_date="2020-01-01", listed_shares=1_000)
        thin = series(days, close=10.0, volume=1.0)  # 10×1 = 10 < 100
        out = pu.judge(stock, thin, "2026-01-02", FILTERS)
        assert out.reason == pu.REASON_THIN
        assert out.avg_turnover == 10

    def test_표본이_모자라면_모르는_것이라_통과시킨다(self) -> None:
        # 창에 9일치뿐이면 "거래가 없다" 가 아니라 "우리가 모른다" 다
        days = trading_days(2025, 300)
        few = pu.Series(dates=days, closes={d: 10.0 for d in days}, turnover={d: 1.0 for d in days[:9]})
        stock = pu.Stock(1, listed_date="2020-01-01", listed_shares=1_000)
        decision = pu.judge(stock, few, "2026-01-02", FILTERS)
        assert decision.included
        assert decision.avg_turnover is None

    def test_창은_cutoff_까지만_본다(self) -> None:
        """cutoff 뒤의 거래대금이 커도 판정이 바뀌면 미래를 본 것이다."""
        days = trading_days(2025, 300)
        cutoff = days[250]
        data = pu.Series(
            dates=days,
            closes={d: 10.0 for d in days},
            turnover={d: (10.0 if d <= cutoff else 10_000.0) for d in days},
        )
        stock = pu.Stock(1, listed_date="2020-01-01", listed_shares=1_000)
        assert pu.judge(stock, data, cutoff, FILTERS).reason == pu.REASON_THIN


class Test정적결격:
    def test_오늘_우선주면_과거에도_뺀다(self) -> None:
        days = trading_days(2025, 300)
        stock = pu.Stock(1, listed_date="2020-01-01", listed_shares=1_000, static_exclude="우선주")
        assert pu.judge(stock, series(days), "2026-01-02", FILTERS).reason == pu.REASON_STATIC


class Test집단:
    def test_시점마다_구성원이_달라진다(self) -> None:
        days = trading_days(2024, 520)
        big = pu.Stock(1, listed_date="2020-01-01", listed_shares=1_000)
        newcomer = pu.Stock(2, listed_date="2025-06-01", listed_shares=1_000)
        data = {1: series(days), 2: series([d for d in days if d >= "2025-06-01"])}

        early, reasons = pu.members_at([big, newcomer], data, "2025-07-01", FILTERS)
        assert early == {1}
        assert reasons[pu.REASON_TOO_NEW] == 1

        later, _ = pu.members_at([big, newcomer], data, "2026-07-01", FILTERS)
        assert later == {1, 2}

    def test_날짜_차이는_윤년을_지킨다(self) -> None:
        assert pu.days_between("2024-01-01", "2025-01-01") == 366  # 2024 는 윤년
        assert pu.days_between("2025-01-01", "2026-01-01") == 365


def test_오래_쉰_종목은_쉰_날을_0_으로_센다() -> None:
    """docs/infra.md 25.271 — 실제 유니버스(시장 20거래일, 빈 날 0)와 같은 창. 종목의 최근 20 행이면 쉰 날이 빠졌다."""
    날 = trading_days(2024, 400)
    시장 = pu.Series(dates=날, closes={d: 10.0 for d in 날}, turnover={d: 10.0 * 50 for d in 날})
    # 쉰 종목: 마지막 20 거래일 중 5일만 거래(거래대금 500), 그 앞은 평소대로
    쉰날 = set(날[-20:-5])
    쉰 = series([d for d in 날 if d not in 쉰날])
    big = pu.Stock(1, listed_date="2020-01-02", listed_shares=1_000)
    halt = pu.Stock(2, listed_date="2020-01-02", listed_shares=1_000)
    들어온, 사유 = pu.members_at([big, halt], {1: 시장, 2: 쉰}, 날[-1], FILTERS)
    # 5/20 × 500 = 125 는 하한 100 을 넘지만, 하한을 200 으로 올리면 빠져야 한다
    엄격 = pu.Filters(min_market_cap=1_000, min_avg_turnover_20d=200)
    들어온, 사유 = pu.members_at([big, halt], {1: 시장, 2: 쉰}, 날[-1], 엄격)
    assert 들어온 == {1} and 사유.get(pu.REASON_THIN) == 1


class Test거래대금_표본_25_539:
    """백테스트 감사 #2·#3 (docs/infra.md 25.539)."""

    def test_시장_창이_짧아도_10일_미만이면_모른다(self) -> None:
        """이틀치 평균(5.5억)으로 하한 10억 미만 판정을 냈다 — 10일 미만은 모르는 것으로 통과."""
        days = ["2024-09-02", "2024-09-03"]
        s = pu.Series(dates=days, closes={d: 1.0 for d in days}, turnover={days[0]: 5e8, days[1]: 6e8})
        f = pu.Filters(min_market_cap=0, min_avg_turnover_20d=1e9)
        d = pu.judge(pu.Stock(1, listed_date="2000-01-03", listed_shares=None), s, days[-1], f, market_window=days)
        assert d.included and d.avg_turnover is None

    def test_거래대금을_모르는_날은_0이_아니라_뺀다(self) -> None:
        """20일 중 12일만 15억이 있고 8일은 모름 → 9억(탈락)이 아니라 15억(통과)."""
        days = trading_days(2025, 20)
        s = pu.Series(dates=days, closes={d: 1.0 for d in days}, turnover={d: 15e8 for d in days[:12]})
        f = pu.Filters(min_market_cap=0, min_avg_turnover_20d=1e9)
        d = pu.judge(pu.Stock(1, listed_date="2000-01-03", listed_shares=None), s, days[-1], f, market_window=days)
        assert d.included and d.avg_turnover == 15e8
