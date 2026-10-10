"""백테스트 적재의 순수한 부분 테스트.

가장 중요한 것은 pit_financials 다. 발표일이 기준일 이후인 스냅샷을 하나라도
쓰면 미래를 보는 것이고, 백테스트 성적이 실제보다 좋게 나온다.
"""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import backtest as job
from batch.jobs import stress as stress_job
from batch.services import backtest as bt

SNAPS = [
    {"as_of_date": "2024-03-12", "fiscal_year": 2023, "values": {"revenue": 100, "operating_income": 10}},
    {"as_of_date": "2025-03-11", "fiscal_year": 2024, "values": {"revenue": 120, "operating_income": 12}},
    {"as_of_date": "2026-03-10", "fiscal_year": 2025, "values": {"revenue": 150, "operating_income": 15}},
    # 2024 사업보고서 정정. 나중에 접수됐다
    {"as_of_date": "2025-06-01", "fiscal_year": 2024, "values": {"revenue": 125, "operating_income": 12}},
]


class Test시점재무:
    def test_발표_하루_전에는_모른다(self) -> None:
        """2026-03-10 접수 보고서를 2026-03-09 계산에 쓰면 미래를 보는 것이다."""
        cur, prev, _ = job.pit_financials(SNAPS, "2026-03-09")
        assert cur["fiscal_year"] == 2024
        assert prev["fiscal_year"] == 2023

    def test_발표_당일부터_안다(self) -> None:
        cur, _, _ = job.pit_financials(SNAPS, "2026-03-10")
        assert cur["fiscal_year"] == 2025

    def test_정정본은_접수된_뒤에만_쓴다(self) -> None:
        # 2025-05-31: 정정 전 값 120
        cur, _, _ = job.pit_financials(SNAPS, "2025-05-31")
        assert cur["values"]["revenue"] == 120
        # 2025-06-01: 정정본 125
        cur, _, _ = job.pit_financials(SNAPS, "2025-06-01")
        assert cur["values"]["revenue"] == 125

    def test_아무것도_모르면_전부_없다(self) -> None:
        assert job.pit_financials(SNAPS, "2024-01-01") == (None, None, None)

    def test_3년_전_재무(self) -> None:
        snaps = SNAPS + [
            {"as_of_date": "2023-03-10", "fiscal_year": 2022, "values": {"revenue": 80}},
        ]
        _cur, _prev, old = job.pit_financials(snaps, "2026-03-10")
        assert old["fiscal_year"] == 2022

    def test_이익_안정성도_시점_기준이다(self) -> None:
        profitable, observed = job.stability_from(SNAPS, "2026-03-09", 2024)
        assert observed == 2  # 2023, 2024 만 알 수 있다
        assert profitable == 2


class Test판단함수:
    def test_점수로_고르는_전략은_빈_창에서_아무것도_사지_않는다(self) -> None:
        universe = [{"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 100}]
        decide = job.make_strategy("composite", universe, {}, 5, {"value": 20.0, "quality": 20.0, "growth": 20.0, "momentum": 20.0, "risk": 20.0}, [])
        assert decide("2026-01-02", bt.PriceView({}, "2026-01-01")) == {}

    def test_벤치마크는_살아있는_종목_동일가중(self) -> None:
        universe = [
            {"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 1},
            {"stock_id": 2, "market": "KOSPI", "sector": None, "listed_shares": 1},
            {"stock_id": 3, "market": "KOSPI", "sector": None, "listed_shares": 1},
        ]
        prices = {1: {"2026-01-05": 1.0}, 2: {"2026-01-05": 1.0}}  # 3 은 가격이 없다
        decide = job.make_strategy("benchmark", universe, {}, 5, {}, [])
        w = decide("2026-01-06", bt.PriceView(prices, "2026-01-05"))
        assert w == {1: 0.5, 2: 0.5}

    def test_리스크는_가격으로_그_자리에서_낸다(self) -> None:
        closes = [(f"2026-01-{i:02d}", 100.0 + i) for i in range(1, 29)]
        risk = job.risk_from_prices(closes)
        # 표본이 3Y 하한(600)에 못 미치므로 전부 None. 값을 지어내지 않는다
        assert risk["mdd_abs"] is None
        assert risk["volatility_ann"] is None


class Test적재:
    def test_backtest_runs_열_개수(self) -> None:
        # 2026-09-22 에 `scoring_calc_version` 을 더해 17 → 18 (docs/infra.md 25.101)
        assert db.column_count(job._RUN_COLS) == 18

    def test_stress_runs_열_개수(self) -> None:
        assert db.column_count(stress_job._COLS) == 13


# ----------------------------------------------------------------------
# 장기 신호 문턱 비교 (docs/backtest.md 7장)
# ----------------------------------------------------------------------


def _falling_closes(days: int = 300) -> list[tuple[str, float]]:
    from datetime import date, timedelta

    start = date(2024, 1, 1)
    return [((start + timedelta(days=i)).isoformat(), 200.0 - i * 0.5) for i in range(days)]


class Test장기진입:
    equities = [("2023-03-15", 1000.0)]  # BPS 100

    def test_문턱과_밴드_하단을_둘_다_넘어야(self) -> None:
        closes = _falling_closes()
        assert job.long_entry(62, 70, closes, self.equities, 10, 60)
        assert not job.long_entry(58, 70, closes, self.equities, 10, 60)
        assert job.long_entry(58, 70, closes, self.equities, 10, 55)

    def test_가격이_밴드_위면_안_들어간다(self) -> None:
        rising = [(d, 400.0 - c) for d, c in _falling_closes()]
        assert not job.long_entry(80, 80, rising, self.equities, 10, 50)

    def test_밴드_표본이_모자라면_안_들어간다(self) -> None:
        assert not job.long_entry(80, 80, _falling_closes(200), self.equities, 10, 50)

    def test_발표일이_기준일_뒤인_자본은_쓰지_않는다(self) -> None:
        snaps = [
            {"as_of_date": "2024-03-15", "fiscal_year": 2023, "values": {"total_equity": 5}},
            {"as_of_date": "2025-03-15", "fiscal_year": 2024, "values": {"total_equity": 7}},
        ]
        assert job.pit_equities(snaps, "2024-12-31") == [("2024-03-15", 5.0, 2023)]


class Test장기보유:
    def test_들어가면_1년_들고_있다가_판다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        signal_on = {"2025-01-02"}
        monkeypatch.setattr(job, "build_pit_inputs", lambda *a: [object()])
        monkeypatch.setattr(
            job.sc, "score_factors",
            lambda inputs: [SimpleNamespace(stock_id=1, factor="value", score=90.0),
                            SimpleNamespace(stock_id=1, factor="quality", score=90.0)],
        )  # fmt: skip
        monkeypatch.setattr(job, "long_entry", lambda *a: current["t"] in signal_on)
        current = {"t": ""}
        decide = job.make_long_strategy(60, [{"stock_id": 1, "listed_shares": 10}], {}, max_holdings=20)

        def at(t: str) -> dict[int, float]:
            current["t"] = t
            return decide(t, bt.PriceView({}, t))

        assert at("2024-12-02") == {}
        assert at("2025-01-02") == {1: 1.0}
        assert at("2025-06-02") == {1: 1.0}  # 신호가 꺼져도 1년은 든다
        assert at("2026-01-02") == {}  # 365일 지나면 판다

    def test_노출_집계(self) -> None:
        result = bt.BacktestResult(
            curve=[("2025-01-02", 1.0)],
            rebalances=[
                bt.Rebalance("2025-01-02", {1: 0.5, 2: 0.5}, 0, 0),
                bt.Rebalance("2025-02-03", {}, 0, 0),
            ],
            costs=bt.Costs(),
        )
        assert job.exposure(result) == (1.0, 0.5)

    def test_노출을_결과에_싣고_기준_아래면_경고한다(self) -> None:
        """평균 보유·투자한 달이 로그에만 있었다 (docs/infra.md 25.786, 백테스트 감사 #1)."""
        result = bt.BacktestResult(
            curve=[("2025-01-02", 1.0)],
            rebalances=[
                bt.Rebalance("2025-01-02", {}, 0, 0),
                bt.Rebalance("2025-02-03", {1: 0.5, 2: 0.5}, 0, 0),
            ],
            costs=bt.Costs(),
        )
        필드 = job.exposure_fields(result)
        assert 필드 == {"avg_holdings": 1.0, "invested_share": 0.5, "first_invested": "2025-02-03"}
        말 = job.exposure_warning("composite", 필드)
        assert 말 and 말.startswith("composite: ") and "평균 보유 1.00종목" in 말 and "첫 투자 2025-02-03" in 말
        assert job.exposure_warning("benchmark", 필드) is None
        assert job.exposure_warning("composite", {**필드, "avg_holdings": 20.0, "invested_share": 0.9}) is None
        # 기준 바로 아래는 내려 적는다 — "3.0종목 … 3종목 아래" 로 모순되지 않게 (25.789)
        assert "평균 보유 2.96종목" in (job.exposure_warning("value", {**필드, "avg_holdings": 2.96, "invested_share": 0.9}) or "")
        # 부동소수 오차로 한 단위 더 내려가지 않는다 (25.790): 0.57 → 57%, 1.15 → 1.15
        말2 = job.exposure_warning("value", {"invested_share": 0.57, "avg_holdings": 1.15, "first_invested": None}) or ""
        assert "투자한 달 57%·평균 보유 1.15종목" in 말2

    def test_비중_0_은_투자로_세지_않는다(self) -> None:
        """추세 필터 배수 0 이면 엔진이 비중 0 을 남긴다 — 전액 현금이다 (25.789, 교차검증)."""
        result = bt.BacktestResult(
            curve=[("2025-01-02", 1.0)],
            rebalances=[bt.Rebalance("2025-01-02", {1: 0.0, 2: 0.0}, 0, 0), bt.Rebalance("2025-02-03", {1: 0.5}, 0, 0)],
            costs=bt.Costs(),
        )
        assert job.exposure_fields(result) == {"avg_holdings": 0.5, "invested_share": 0.5, "first_invested": "2025-02-03"}


class Test시점유니버스:
    """리밸런스마다 그때의 유니버스로 후보를 자르는지 (docs/backtest.md 1.3).

    이것이 빠지면 백테스트가 "지금 살아남아 커진 종목" 만 과거에 고른다.
    handoff 가 '가장 큰 편향' 으로 적어 둔 것이다.
    """

    UNIVERSE = [
        {"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 1, "listed_date": "2020-01-01"},
        {"stock_id": 2, "market": "KOSPI", "sector": None, "listed_shares": 1, "listed_date": "2026-01-02"},
    ]

    def _prices(self) -> dict[int, dict[str, float]]:
        old = {f"2025-{m:02d}-01": 10.0 for m in range(1, 13)}
        old.update({f"2026-{m:02d}-01": 10.0 for m in range(1, 7)})
        new = {f"2026-{m:02d}-01": 10.0 for m in range(1, 7)}
        return {1: old, 2: new}

    def _eligible(self):
        from batch.services import pit_universe as pu

        stocks = job.pit_stocks(self.UNIVERSE)
        series = job.pit_series(self._prices(), {})
        filters = pu.Filters(min_market_cap=0, min_avg_turnover_20d=0)
        return lambda cutoff: pu.members_at(stocks, series, cutoff, filters)[0]

    def test_상장_1년_미만_종목은_그때_후보에서_빠진다(self) -> None:
        eligible = self._eligible()
        view = bt.PriceView(self._prices(), "2026-06-01")
        decide = job.make_strategy("benchmark", self.UNIVERSE, {}, 5, {}, [], eligible)
        # 2번은 2026-01-02 상장이라 2026-06-01 시점에는 아직 1년이 안 됐다
        assert decide("2026-06-02", view) == {1: 1.0}

    def test_1년이_지나면_들어온다(self) -> None:
        prices = self._prices()
        for month in range(7, 13):
            prices[1][f"2026-{month:02d}-01"] = 10.0
            prices[2][f"2026-{month:02d}-01"] = 10.0
        prices[1]["2027-02-01"] = 10.0
        prices[2]["2027-02-01"] = 10.0
        from batch.services import pit_universe as pu

        stocks = job.pit_stocks(self.UNIVERSE)
        series = job.pit_series(prices, {})
        filters = pu.Filters(min_market_cap=0, min_avg_turnover_20d=0)
        eligible = lambda cutoff: pu.members_at(stocks, series, cutoff, filters)[0]  # noqa: E731

        decide = job.make_strategy("benchmark", self.UNIVERSE, {}, 5, {}, [], eligible)
        weights = decide("2027-02-02", bt.PriceView(prices, "2027-02-01"))
        assert weights == {1: 0.5, 2: 0.5}

    def test_주지_않으면_옛_방식_그대로다(self) -> None:
        # eligible_at 이 없으면 오늘 유니버스를 전 구간에 쓴다 (되돌릴 수 있게 남겨 둔다)
        view = bt.PriceView(self._prices(), "2026-06-01")
        decide = job.make_strategy("benchmark", self.UNIVERSE, {}, 5, {}, [])
        assert decide("2026-06-02", view) == {1: 0.5, 2: 0.5}

    def test_시점_시계열은_거래대금을_함께_담는다(self) -> None:
        series = job.pit_series({1: {"2026-01-02": 10.0}}, {1: {"2026-01-02": 500.0}})
        assert series[1].dates == ["2026-01-02"]
        assert series[1].turnover == {"2026-01-02": 500.0}
        # 거래대금이 없는 종목도 시계열은 만들어진다 (그 조건으로 자르지 않는다)
        assert job.pit_series({2: {"2026-01-02": 1.0}}, {})[2].turnover == {}


class Test가격읽기:
    """가격과 거래대금을 한 번에 읽는다 (같은 행에서 둘 다 나온다)."""

    def _client(self):
        import sqlite3
        from typing import Any

        from batch.core.turso import ResultSet

        class Counter:
            def __init__(self) -> None:
                self.conn = sqlite3.connect(":memory:")
                self.queries: list[str] = []

            def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
                self.queries.append(sql)
                cur = self.conn.execute(sql, args or [])
                cols = [d[0] for d in cur.description or []]
                return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

            def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
                return [self.execute(sql, args) for sql, args in statements]

            def close(self) -> None:
                pass

        client = Counter()
        db.apply_migrations(client)  # type: ignore[arg-type]
        client.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, volume, currency, source, fetched_at)"
            " VALUES (1, '2026-01-02', 100, 90, 50, 'KRW', 't', 't')"
        )
        # 거래량이 없는 날. 거래대금에서만 빠지고 가격에는 남는다
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2026-01-05', 110, 'KRW', 't', 't')"
        )
        client.queries.clear()
        return client

    def test_한_번에_읽고_수익률은_수정주가_거래대금은_원_종가다(self) -> None:
        client = self._client()
        prices, turnover = job.load_prices_and_turnover(client, [1], "2020-01-01")  # type: ignore[arg-type]
        assert len(client.queries) == 1  # 따로 읽으면 같은 5년치를 두 번 가져온다
        assert prices[1]["2026-01-02"] == 90.0  # adj_close
        assert turnover[1]["2026-01-02"] == 5000.0  # 원 종가 100 × 거래량 50
        # 거래량이 없는 날: 가격은 있고 거래대금은 없다 (그 조건으로 종목을 자르지 않는다)
        assert prices[1]["2026-01-05"] == 110.0
        assert "2026-01-05" not in turnover.get(1, {})


    def test_수정주가_가운데_빈_행은_메우고_0_종가는_읽지_않는다(self) -> None:
        """1:10 분할 전 구간에 원 종가 한 행이 끼어 가짜 이익 +39% 가 남았고, 0 종가가 그 종목 몫을 0 으로 만들었다
        (docs/infra.md 25.1101, 백테스트 감사)."""
        client = self._client()
        c = client.conn
        c.execute("DELETE FROM prices")
        for day, close, adj_close in (("2026-01-02", 1000, 100), ("2026-01-05", 1010, None), ("2026-01-06", 1020, 102),
                                      ("2026-01-07", 0, None), ("2026-01-08", 103, 103)):  # fmt: skip
            c.execute(
                "INSERT INTO prices (stock_id, date, close, adj_close, volume, currency, source, fetched_at)"
                " VALUES (1, ?, ?, ?, 10, 'KRW', 't', 't')",
                [day, close, adj_close],
            )
        prices, _ = job.load_prices_and_turnover(client, [1], "2020-01-01")  # type: ignore[arg-type]
        assert prices[1]["2026-01-05"] == pytest.approx(101.0)  # 원 종가 1010 이 아니라 직전 계수 0.1 로 옮김
        assert "2026-01-07" not in prices[1]


class Test거래대금창:
    """PriceView 가 거래대금도 cutoff 로 잘라 closes() 와 같은 순서로 준다."""

    PRICES = {1: {"2026-01-02": 10.0, "2026-01-05": 11.0, "2026-01-06": 12.0}}
    TURNOVER = {1: {"2026-01-02": 500.0, "2026-01-06": 700.0}}  # 01-05 는 없다

    def test_같은_날짜_순서이고_없는_날은_None(self) -> None:
        view = bt.PriceView(self.PRICES, "2026-01-06", self.TURNOVER)
        assert [d for d, _c in view.closes(1)] == ["2026-01-02", "2026-01-05", "2026-01-06"]
        assert view.values(1) == [500.0, None, 700.0]

    def test_cutoff_뒤의_거래대금은_보이지_않는다(self) -> None:
        view = bt.PriceView(self.PRICES, "2026-01-05", self.TURNOVER)
        assert view.values(1) == [500.0, None]

    def test_거래대금을_안_주면_전부_None(self) -> None:
        view = bt.PriceView(self.PRICES, "2026-01-06")
        assert view.values(1) == [None, None, None]
        assert view.values(99) == []  # 가격이 없는 종목


class Test시점계열지표:
    """build_pit_inputs 가 적재(jobs/scores.build_inputs)와 같은 함수로 계열 지표를 낸다."""

    def _view(self, days: int = 274, with_turnover: bool = True, noise: float = 0.0) -> bt.PriceView:
        import datetime as dt

        start = dt.date(2025, 1, 1)
        dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(days)]
        prices = {1: {d: 100.0 * (1.01**i) + (noise if i % 2 else 0.0) for i, d in enumerate(dates)}}
        turnover = {1: {d: 1_000_000_000.0 for d in dates}} if with_turnover else None
        return bt.PriceView(prices, dates[-1], turnover)

    UNIVERSE = [{"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 100}]

    def test_계열_지표가_입력에_들어간다(self) -> None:
        inputs = job.build_pit_inputs(self.UNIVERSE, {}, self._view())
        m = inputs[0].metrics
        assert m["high_52w_proximity"] == pytest.approx(1.0)
        assert m["momentum_consistency"] == pytest.approx(1.0)
        assert m["momentum_vol_adjusted"] is None  # 매일 똑같이 올라 변동성 0
        assert m["amihud_illiquidity"] == pytest.approx(0.01, rel=1e-6)

    def test_흔들리면_변동성_조정_모멘텀이_생긴다(self) -> None:
        inputs = job.build_pit_inputs(self.UNIVERSE, {}, self._view(noise=0.5))
        assert inputs[0].metrics["momentum_vol_adjusted"] > 0

    def test_거래대금이_없으면_아미후드만_빈다(self) -> None:
        inputs = job.build_pit_inputs(self.UNIVERSE, {}, self._view(with_turnover=False))
        m = inputs[0].metrics
        assert m["high_52w_proximity"] == pytest.approx(1.0)
        assert m["amihud_illiquidity"] is None

    def test_창이_짧으면_계열_지표는_비고_값을_지어내지_않는다(self) -> None:
        inputs = job.build_pit_inputs(self.UNIVERSE, {}, self._view(days=30))
        m = inputs[0].metrics
        assert m["high_52w_proximity"] is None
        assert m["momentum_consistency"] is None
        assert m["amihud_illiquidity"] is None


class Test시점재무제표지표:
    """build_pit_inputs 가 스냅샷 두 해로 자산 성장률·유동비율·피오트로스키를 낸다."""

    UNIVERSE = [{"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 100}]
    CUR = {
        "net_income": 100, "total_assets": 1000, "noncurrent_liabilities": 100, "total_liabilities": 400,
        "current_assets": 300, "current_liabilities": 150, "operating_income": 120, "revenue": 800,
        "total_equity": 500,
    }
    PREV = {
        "net_income": 50, "total_assets": 900, "noncurrent_liabilities": 150, "total_liabilities": 400,
        "current_assets": 250, "current_liabilities": 150, "operating_income": 60, "revenue": 600,
        "total_equity": 400,
    }
    SNAPS = {1: [
        {"as_of_date": "2025-03-11", "fiscal_year": 2024, "values": PREV},
        {"as_of_date": "2026-03-10", "fiscal_year": 2025, "values": CUR},
    ]}

    def _view(self, cutoff: str) -> bt.PriceView:
        return bt.PriceView({1: {"2025-03-12": 10.0, "2026-03-11": 11.0}}, cutoff)

    def test_두_해를_알면_세_지표가_있다(self) -> None:
        m = job.build_pit_inputs(self.UNIVERSE, self.SNAPS, self._view("2026-03-11"))[0].metrics
        assert m["asset_growth"] == pytest.approx(1000 / 900 - 1)
        assert m["current_ratio"] == pytest.approx(2.0)
        assert m["piotroski_lite"] == 6.0

    def test_당해_보고서_접수_전에는_전년_한_해뿐이라_유동비율만_있다(self) -> None:
        # 2026-03-09: 2025 보고서(03-10 접수)는 아직 없다. 2024 만 알고 그 전 해는 없다
        m = job.build_pit_inputs(self.UNIVERSE, self.SNAPS, self._view("2026-03-09"))[0].metrics
        assert m["current_ratio"] == pytest.approx(250 / 150)
        assert m["asset_growth"] is None
        assert m["piotroski_lite"] is None


class Test추세오버레이:
    """with_trend_filter: 그 종목 시장 지수가 t-1 에 200일선 아래면 비중 × 배수 (docs/backtest.md 2.4)."""

    def _index(self, last: float, days: int = 200) -> list[tuple[str, float]]:
        import datetime as dt

        end = dt.date(2026, 3, 1)
        return [((end - dt.timedelta(days=days - 1 - i)).isoformat(), last if i == days - 1 else 100.0) for i in range(days)]

    def _decide(self, _t: str, _view: bt.PriceView) -> dict[int, float]:
        return {1: 0.5, 2: 0.5}

    def test_약세_지수의_종목만_준다(self) -> None:
        series = {"KOSPI": self._index(90.0), "KOSDAQ": self._index(110.0)}
        warnings: list[str] = []
        wrapped = job.with_trend_filter(self._decide, series, {1: "KOSPI", 2: "KOSDAQ"}, 0.5, warnings)
        view = bt.PriceView({1: {"2026-03-01": 1.0}}, "2026-03-01")
        assert wrapped("2026-03-02", view) == {1: 0.25, 2: 0.5}
        assert warnings == []

    def test_지수가_모자라면_그대로_두고_경고한다(self) -> None:
        warnings: list[str] = []
        wrapped = job.with_trend_filter(self._decide, {}, {1: "KOSPI", 2: "KOSPI"}, 0.5, warnings)
        view = bt.PriceView({1: {"2026-03-01": 1.0}}, "2026-03-01")
        assert wrapped("2026-03-02", view) == {1: 0.5, 2: 0.5}
        assert warnings == [job.WARN_TREND_NO_INDEX]

    def test_지수도_cutoff_뒤는_보지_않는다(self) -> None:
        # 3/1 까지는 100 으로 강세, 3/2 에 폭락. cutoff 3/1 판정은 강세여야 한다
        series = {"KOSPI": self._index(100.0) + [("2026-03-02", 50.0)]}
        wrapped = job.with_trend_filter(self._decide, series, {1: "KOSPI", 2: "KOSPI"}, 0.5, [])
        view = bt.PriceView({1: {"2026-03-01": 1.0}}, "2026-03-01")
        assert wrapped("2026-03-02", view) == {1: 0.5, 2: 0.5}


class Test마지막_방어선:
    """세 함수가 돌려주기 직전에 다시 본다 (2026-09-20 추가, docs/infra.md 25.35).

    `as_of_date > cutoff` 거르개가 **세 벌** 있다. 한 벌이 깨지면 백테스트는 멈추지 않고
    **더 좋은 성적**을 낸다 — 가장 나쁜 고장이다. 틀린 성적표는 틀린 줄을 모른다.
    그래서 돌려줄 행을 한 번 더 본다.
    """

    def test_거르개가_깨져도_조용히_넘어가지_않는다(self) -> None:
        # 거르개를 우회한 상황을 흉내 낸다. 미래 행이 손에 들어왔다면 멈춰야 한다
        with pytest.raises(ValueError, match="미래 데이터"):
            job._guard("2026-03-09", "2026-03-10")

    def test_같은_날은_미래가_아니다(self) -> None:
        # 접수일 당일에는 알 수 있다. 여기서 막으면 그날 데이터를 통째로 잃는다
        job._guard("2026-03-10", "2026-03-10", "2025-03-11", None)

    def test_정상_경로에서는_걸리지_않는다(self) -> None:
        # 방어선이 평소에 울리면 백테스트가 아예 못 돈다
        assert job.pit_financials(SNAPS, "2026-03-10")[0]["fiscal_year"] == 2025
        assert job.stability_from(SNAPS, "2026-03-09", 2024)[1] > 0
        assert job.pit_equities(
            [{"as_of_date": "2024-03-15", "fiscal_year": 2023, "values": {"total_equity": 5.0}}],
            "2024-12-31",
        ) == [("2024-03-15", 5.0, 2023)]


class Test결과가_어떤_계산식으로_났는지:
    """**판이 다른 실행을 나란히 놓고 비교할 수 없다** (docs/infra.md 25.101).

    `backtest_runs.calc_version` 은 **엔진 판**이다(리밸런스·비용·곡선).
    그런데 백테스트 결과를 실제로 가르는 것은 **점수 계산식**이다 —
    어떤 종목을 고르느냐가 곧 수익률이기 때문이다. 그 판은 어디에도 안 남고 있었다.

    2026-09-21~22 이틀 동안 스코어링 판의 뜻이 세 번 바뀌었다(25.92·25.99·25.100).
    그 사이 결과들은 서로 비교할 수 없는데 표에서는 똑같아 보였다.
    """

    def test_두_판을_모두_싣는다(self) -> None:
        assert "calc_version, scoring_calc_version" in job._RUN_COLS

    def test_점수_판을_실제로_넣는다(self) -> None:
        """열만 더하고 값을 안 넣으면 **늘 NULL 이다** (25.65·25.92 와 같은 모양)."""
        import inspect

        원본 = inspect.getsource(job.store_run)

        assert "sc.CALC_VERSION" in 원본, "점수 판을 안 넘긴다 — 열은 생기고 값은 안 들어간다"
        assert "bt.CALC_VERSION" in 원본, "엔진 판도 그대로 남아야 한다"

    def test_둘이_다른_값이다(self) -> None:
        """같은 수였다면 이 변경이 아무것도 안 바꾼다. 실제로 다른지 본다."""
        from batch.services import backtest as bt_svc
        from batch.services import scoring as sc_svc

        assert bt_svc.CALC_VERSION != sc_svc.CALC_VERSION

    def test_마이그레이션이_열을_만든다(self) -> None:
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        올린것 = [
            p.name
            for p in sorted((뿌리 / "migrations").glob("*.sql"))
            if "scoring_calc_version" in p.read_text(encoding="utf-8")
        ]

        assert 올린것, "열을 만드는 마이그레이션이 없다 — 운영에서 INSERT 가 터진다"

    def test_옛_행은_NULL_이다(self) -> None:
        """**모르는 것을 지어내지 않는다.** 그때 무슨 판이었는지 실제로 모른다."""
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        본문 = (뿌리 / "migrations" / "0041_backtest_scoring_version.sql").read_text(encoding="utf-8")

        assert "NOT NULL" not in 본문, "기본값을 박으면 옛 실행이 그 판으로 돌린 것처럼 보인다"

    def test_화면이_보여_준다(self) -> None:
        """**저장만 하고 안 읽으면 없는 것과 같다** (docs/infra.md 25.93·25.94)."""
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        질의 = (뿌리 / "web" / "lib" / "backtest.ts").read_text(encoding="utf-8")
        화면 = (뿌리 / "web" / "components" / "BacktestView.tsx").read_text(encoding="utf-8")

        assert "scoring_calc_version" in 질의, "웹 질의가 그 열을 안 고른다"
        assert "scoring_calc_version" in 화면, "화면이 그 값을 안 그린다"
        assert "기록 없음" in 화면, "NULL 을 0 이나 빈칸으로 찍으면 '판 0' 으로 읽힌다"


def test_시점_유니버스_경고가_후보는_오늘_유니버스뿐임을_밝힌다() -> None:
    """시점 필터는 오늘 유니버스에서 빼기만 한다. 줄어든 종목이 빠진 채라는 것을 결과가 말해야 한다 (docs/infra.md 25.283)."""
    assert "오늘 유니버스에 든 종목뿐" in job.WARN_PIT_UNIVERSE
    assert "낙관" in job.WARN_PIT_UNIVERSE


class Test무위험수익률:
    """설정 화면은 "샤프지수 계산에 씁니다" 라는데 백테스트가 읽지 않아 샤프가 늘 비었다 (docs/infra.md 25.299)."""

    def _client(self, value: str | None):
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        if value is not None:
            mem.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('risk_free_manual', ?, 't')", [value])
        return mem

    def test_설정이_있으면_그_값을_쓴다(self) -> None:
        경고: list[str] = []
        assert job.risk_free_with_warning(self._client('{"kr_pct": 3.5}'), "KR", 경고) == pytest.approx(0.035)  # type: ignore[arg-type]
        assert 경고 == []

    def test_없으면_경고를_남긴다(self) -> None:
        경고: list[str] = []
        assert job.risk_free_with_warning(self._client(None), "KR", 경고) is None  # type: ignore[arg-type]
        assert 경고 == [job.WARN_NO_RISK_FREE]

    def test_요약에_넘긴다(self) -> None:
        import inspect

        원본 = inspect.getsource(job.run)
        assert "risk_free_annual=무위험" in 원본 and "risk_free_with_warning(" in 원본


def test_중간_해가_비면_3년_전_재무를_쓰지_않는다() -> None:
    """3년 CAGR 은 4개 연도가 다 있어야 한다 (docs/factors.md 3.3, docs/infra.md 25.308)."""
    from batch.services import scoring as sc

    snaps = [
        {"as_of_date": "2023-03-10", "fiscal_year": 2022, "values": {"revenue": 100}},
        {"as_of_date": "2026-03-10", "fiscal_year": 2025, "values": {"revenue": 200}},
    ]
    assert job.pit_financials(snaps, "2026-03-11")[2] is None
    assert sc.has_cagr_years({2022: 1, 2023: 1, 2024: 1, 2025: 1}, 2025)
    assert not sc.has_cagr_years({2022: 1, 2024: 1, 2025: 1}, 2025)


def test_점수_잡도_같은_규칙을_쓴다() -> None:
    import inspect

    from batch.jobs import scores

    assert "has_cagr_years(" in inspect.getsource(scores)


class Test생존편향_경고는_후보를_본다:
    """표 전체에 폐지 종목이 있어도 후보에 없으면 편향은 그대로다 (docs/infra.md 25.402)."""

    def _client(self):
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        for sid, status in ((1, "active"), (2, "delisted")):
            mem.conn.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', ?, 't', 't')",
                [sid, f"00000{sid}", status],
            )
        return mem

    def test_폐지_종목이_후보에_없으면_경고를_끄지_않는다(self) -> None:
        assert job.has_delisted(self._client(), "KR", [1]) is False  # type: ignore[arg-type]

    def test_후보에_있으면_편향이_없다고_본다(self) -> None:
        assert job.has_delisted(self._client(), "KR", [1, 2]) is True  # type: ignore[arg-type]

    def test_실행이_후보를_넘긴다(self) -> None:
        import inspect

        assert "has_delisted(client, country, ids)" in inspect.getsource(job.run)


class Test이력_시작에_붙은_상장일은_모르는_것:
    """미국 listed_date 는 관측 첫 거래일이다. 이력 첫 1년을 전 종목 "상장 1년 미만" 으로 자르면
    백테스트 첫 12개월이 통째로 현금이었다 (docs/infra.md 25.403)."""

    def _series(self, first: str) -> object:
        from batch.services import pit_universe as pit

        return pit.Series(dates=[first, "2024-02-20"], closes={first: 100.0, "2024-02-20": 100.0})

    def test_이력_시작의_관측값은_자르지_않는다(self) -> None:
        from batch.services import pit_universe as pit

        st = pit.Stock(1, listed_date="2023-03-01", listed_date_observed=True)
        f = pit.Filters(0, 0, history_starts=("2023-03-01",))
        assert pit.judge(st, self._series("2023-03-01"), "2024-02-20", f).reason != pit.REASON_TOO_NEW

    def test_이력_중간에_처음_보인_종목은_자른다(self) -> None:
        from batch.services import pit_universe as pit

        st = pit.Stock(2, listed_date="2023-09-01", listed_date_observed=True)
        f = pit.Filters(0, 0, history_starts=("2023-03-01",))
        assert pit.judge(st, self._series("2023-09-01"), "2024-02-20", f).reason == pit.REASON_TOO_NEW

    def test_실제_상장일이면_이력_시작이어도_자른다(self) -> None:
        from batch.services import pit_universe as pit

        st = pit.Stock(3, listed_date="2023-03-01")  # 국내: 거래소가 준 실제 상장일
        f = pit.Filters(0, 0, history_starts=("2023-03-01",))
        assert pit.judge(st, self._series("2023-03-01"), "2024-02-20", f).reason == pit.REASON_TOO_NEW

    def test_실행이_미국을_관측값으로_넘기고_이력_시작을_채운다(self) -> None:
        import inspect

        from batch.services import pit_universe as pit

        원본 = inspect.getsource(job.run)
        assert 'pit_stocks(universe, observed=country == "US")' in 원본
        assert "history_starts=history_starts(pit_stock_list, series_by_stock)" in 원본
        s = {1: pit.Series(dates=["2023-03-02"]), 2: pit.Series(dates=["2023-03-01"]), 3: pit.Series()}
        종목 = [pit.Stock(1), pit.Stock(2), pit.Stock(3), pit.Stock(4, listed_date="1976-06-11")]  # 실제 상장일은 섞지 않는다
        assert job.history_starts(종목, s) == ("2023-03-01",)

    def test_백테스트_창이_백필보다_짧아도_관측_상장일로_가장자리를_잡는다(self) -> None:
        """백필 시작 2023-02-15, 3년 백테스트는 2023-08-24 부터 읽는다. 25.418 은 불러온 첫날로 가장자리를 잡아
        관측 상장일(2023-02-15)이 그보다 앞서 다시 잘렸다 (docs/infra.md 25.422, 교차검증)."""
        from batch.services import pit_universe as pit

        종목 = [pit.Stock(i, listed_date="2023-02-15", listed_date_observed=True) for i in range(12)]
        창 = {i: pit.Series(dates=["2023-08-24", "2023-09-28"], closes={"2023-08-24": 1.0, "2023-09-28": 1.0})
             for i in range(12)}  # fmt: skip
        f = pit.Filters(0, 0, history_starts=job.history_starts(종목, 창))
        assert pit.judge(종목[0], 창[0], "2023-09-28", f).reason != pit.REASON_TOO_NEW

    def test_나중에_받은_묶음도_이력_시작이다(self) -> None:
        """시범 200종목 뒤 전체를 받으면 백필 시작이 둘이다. 25.403 은 가장 이른 날만 봐서 나중 묶음을 잘랐다
        (docs/infra.md 25.418, 교차검증)."""
        from batch.services import pit_universe as pit

        첫날들 = ["2023-03-01"] * 12 + ["2023-03-15"] * 30 + ["2023-09-01"]  # 마지막은 진짜 신규 상장 하나
        시작들 = pit.history_starts_of(첫날들)
        assert 시작들 == ("2023-03-01", "2023-03-15")
        f = pit.Filters(0, 0, history_starts=시작들)
        나중묶음 = pit.Stock(1, listed_date="2023-03-15", listed_date_observed=True)
        assert pit.judge(나중묶음, self._series("2023-03-15"), "2024-02-20", f).reason != pit.REASON_TOO_NEW
        신규 = pit.Stock(2, listed_date="2023-09-01", listed_date_observed=True)
        assert pit.judge(신규, self._series("2023-09-01"), "2024-02-20", f).reason == pit.REASON_TOO_NEW

    def test_상장일이_빈_종목이_몇_개뿐이어도_창의_시작을_가장자리로(self) -> None:
        """관측 상장일 30종목 + 빈 것 3종목. 한 목록에 섞으면 빈 종목의 첫날이 무리를 못 이뤄 잘렸다
        (docs/infra.md 25.431, 교차검증)."""
        from batch.services import pit_universe as pit

        관측 = [pit.Stock(i, listed_date="2023-02-15", listed_date_observed=True) for i in range(30)]
        빈것 = [pit.Stock(100 + i) for i in range(3)]
        창 = {st.stock_id: pit.Series(dates=["2023-08-24", "2023-09-28"], closes={"2023-08-24": 1.0, "2023-09-28": 1.0})
             for st in 관측 + 빈것}  # fmt: skip
        f = pit.Filters(0, 0, history_starts=job.history_starts(관측 + 빈것, 창))
        assert pit.judge(빈것[0], 창[100], "2023-09-28", f).reason != pit.REASON_TOO_NEW

    def test_빈_무리가_신규_상장이면_가장자리가_아니다(self) -> None:
        """상장일이 빈 종목이 2024-05-01 신규 상장 하나뿐이면 25.431 은 그날을 이력 시작으로 봐 통과시켰다
        (docs/infra.md 25.436, 교차검증)."""
        from batch.services import pit_universe as pit

        관측 = [pit.Stock(i, listed_date="2023-02-15", listed_date_observed=True) for i in range(30)]
        신규 = pit.Stock(100)
        창 = {st.stock_id: pit.Series(dates=["2023-08-24", "2024-09-02"], closes={"2023-08-24": 1.0, "2024-09-02": 1.0})
             for st in 관측}  # fmt: skip
        창[100] = pit.Series(dates=["2024-05-01", "2024-09-02"], closes={"2024-05-01": 1.0, "2024-09-02": 1.0})
        시작들 = job.history_starts(관측 + [신규], 창)
        assert "2024-05-01" not in 시작들
        f = pit.Filters(0, 0, history_starts=시작들)
        assert pit.judge(신규, 창[100], "2024-09-02", f).reason == pit.REASON_TOO_NEW


def test_기본_파라미터_실행만_기본으로_적는다() -> None:
    """기법 발굴 루프의 판정은 기본 파라미터 실행만 센다 (docs/factors.md 12장 머리, infra 25.781)."""
    from batch.jobs import backtest as job
    from batch.services import backtest as bt_

    기본 = dict(years=job.DEFAULT_YEARS, top_n=bt_.DEFAULT_TOP_N, long_thresholds=(), only_long=False,
               pit_universe=True, trend_filter=False)  # fmt: skip
    assert job.실행_파라미터(**기본)["default"] is True
    # 장기 문턱은 IC 를 바꾸지 않는다
    assert job.실행_파라미터(**{**기본, "long_thresholds": (60.0, 55.0)})["default"] is True
    for 바꿈 in ({"years": 3}, {"top_n": 10}, {"only_long": True}, {"pit_universe": False}, {"trend_filter": True}):
        assert job.실행_파라미터(**{**기본, **바꿈})["default"] is False, 바꿈


def test_점수_계열도_수정주가_가운데_빈_행을_메운다() -> None:
    """점수(모멘텀·리스크) 계열도 `COALESCE(adj_close, close)` 라 같은 구멍에 원 종가가 들어갔다 (docs/infra.md 25.1101)."""
    from batch.jobs import scores

    rows = [
        {"stock_id": 1, "date": d, "px": a if a is not None else c, "adj_close": a, "close": c, "value": None,
         "volume": None}
        for d, c, a in (("2026-01-02", 1000.0, 100.0), ("2026-01-05", 1010.0, None), ("2026-01-06", 1020.0, 102.0))
    ]  # fmt: skip
    _, 종가, _ = scores.series_from_rows(rows, 10)[1]
    assert 종가 == pytest.approx([100.0, 101.0, 102.0])
