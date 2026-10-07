"""신호 성적표 (docs/signals.md 10장). 손으로 맞춘 종가 계열로 수익률·터치·지수 비교를 고정한다."""

from __future__ import annotations

import datetime as dt

import pytest

from batch.jobs import signal_outcomes as job
from batch.services import outcomes as oc


def series(start: str, values: list[float]) -> list[tuple[str, float]]:
    d0 = dt.date.fromisoformat(start)
    return [((d0 + dt.timedelta(days=i)).isoformat(), v) for i, v in enumerate(values)]


SIG = {"stock_id": 1, "as_of_date": "2026-01-02", "horizon": "short", "target_price": 110.0, "stop_price": 93.0}


class Test신호_뒤의_분할:
    """**목표·손절은 신호 날의 단위, 계열은 지금의 수정주가다** (docs/infra.md 25.208)."""

    #: 신호 날 원래 종가 100 근처에서 목표 110·손절 93. 그 뒤 5:1 분할로 수정 계열이 1/5 가 됐다
    원래 = [99.0, 100.0] + [101.0] * 70

    def _계열(self) -> tuple[list[tuple[str, float]], dict[str, float]]:
        수정 = series("2026-01-02", [v / 5 for v in self.원래])
        return 수정, {d: v * 5 for d, v in 수정}

    def test_미끼__원래_종가를_안_주면_거짓_손절이_난다(self) -> None:
        수정, _ = self._계열()
        assert oc.evaluate(SIG, 수정).hit_stop is True

    def test_원래_종가로_단위를_맞추면_손절도_목표도_아니다(self) -> None:
        수정, 원래 = self._계열()

        o = oc.evaluate(SIG, 수정, raw_closes=원래)

        assert o.hit_stop is False and o.hit_target is False
        assert o.rets[20] == pytest.approx(0.01)  # 수익률은 비율이라 원래도 맞았다

    def test_분할이_없으면_그대로다(self) -> None:
        원래계열 = series("2026-01-02", self.원래)
        o = oc.evaluate(SIG, 원래계열, raw_closes=dict(원래계열))
        assert o.hit_stop is False and o.hit_target is False


class Test미국_분할은_기준_종가로_맞춘다:
    """**25.208 이 미국 분할을 못 잡았다** (docs/infra.md 25.215).

    미국 `close` 는 야후 Close 라 다시 받으면 분할만큼 과거가 조정된다. 그러면 원래 종가도 새 단위라
    (수정 ÷ 원래) 비율이 분할을 못 잡는다. 신호 날 적어 둔 기준 종가로 맞춘다.
    """

    def _미국_계열(self) -> list[tuple[str, float]]:
        # 신호 날(1/2) 종가 99, 그 뒤 5:1 분할 — 다시 받은 계열은 전부 1/5 이다. close·adj 모두 같다
        return series("2026-01-02", [v / 5 for v in Test신호_뒤의_분할.원래])

    def test_미끼__기준_종가가_없으면_미국_분할은_거짓_손절이다(self) -> None:
        계열 = self._미국_계열()
        assert oc.evaluate(SIG, 계열, raw_closes=dict(계열)).hit_stop is True

    def test_기준_종가로_맞추면_손절이_아니다(self) -> None:
        계열 = self._미국_계열()
        o = oc.evaluate({**SIG, "ref_close": 99.0}, 계열, raw_closes=dict(계열))
        assert o.hit_stop is False and o.hit_target is False


class Test성적:
    def test_진입은_기준일_다음_거래일_종가(self) -> None:
        """**기준일 그날은 진입일이 아니다** (docs/infra.md 25.154).

        `as_of_date` 는 배치가 다룬 **데이터의 날짜**(직전 거래일)이고, 그 신호가 실린
        리포트는 **다음 날 아침** 장 시작 전에 온다. 기준일 그날 종가는 신호를 만든 값
        자체라 살 수 없다. 1/2 에 99 가 있어도 진입은 1/3 의 100 이다.
        """
        closes = series("2026-01-02", [99.0, 100.0] + [101.0 + i for i in range(70)])
        o = oc.evaluate(SIG, closes)
        assert o.entry_date == "2026-01-03" and o.entry_close == 100.0
        assert o.rets[5] == pytest.approx(0.05)  # after[4] = 105
        assert o.rets[20] == pytest.approx(0.20)
        assert o.rets[60] == pytest.approx(0.60)
        assert o.hit_target is True and o.hit_stop is False
        assert o.max_up == pytest.approx(0.60) and o.max_down == pytest.approx(0.01)

    def test_창이_안_찼으면_None(self) -> None:
        o = oc.evaluate(SIG, series("2026-01-02", [99.0, 100.0, 101.0, 99.0]))
        assert o.rets[5] is None and o.rets[20] is None
        assert o.days_available == 2
        assert o.max_down == pytest.approx(-0.01)

    def test_기준일_그날_종가만_있으면_진입_없음(self) -> None:
        """**그날 종가 하나로는 성적을 낼 수 없다.** 살 수 있는 날이 아직 안 왔다.

        전에는 그 종가로 진입해 놓고 `days_available = 0` 이라 창은 전부 NULL 이었다 —
        진입가만 남아 화면에 "진입 100" 으로 보였다.
        """
        o = oc.evaluate(SIG, series("2026-01-02", [100.0]))

        assert o.entry_date is None and o.entry_close is None
        assert o.days_available == 0

    def test_백테스트와_같은_규칙이다(self) -> None:
        """`docs/backtest.md` 1.1: "판단에는 t-1 까지의 데이터만 쓴다".

        백테스트는 t-1 로 고르고 t 에 산다. 성적표도 같아야 한다 — 같은 규칙을 두 곳이
        다르게 구현하면 두 화면이 서로 다른 성적을 말한다 (25.0 「한 규칙이 두 곳에 있다」).
        """
        closes = series("2026-01-02", [50.0, 100.0, 110.0])
        o = oc.evaluate(SIG, closes)

        assert o.entry_close == 100.0, "신호를 만든 그날 종가(50)로 사면 성적이 부풀려진다"

    def test_기준일_뒤_가격이_없으면_진입_없음(self) -> None:
        o = oc.evaluate(SIG, series("2025-12-01", [100.0, 101.0]))
        assert o.entry_date is None and o.days_available == 0

    def test_손절_터치는_종가_기준(self) -> None:
        closes = series("2026-01-02", [100.0, 95.0, 92.0] + [100.0] * 60)
        o = oc.evaluate(SIG, closes)
        assert o.hit_stop is True and o.hit_target is False

    def test_목표가_없으면_터치도_None(self) -> None:
        o = oc.evaluate({**SIG, "target_price": None, "stop_price": None}, series("2026-01-02", [100.0] * 10))
        assert o.hit_target is None and o.hit_stop is None

    def test_지수는_같은_날짜끼리(self) -> None:
        closes = series("2026-01-02", [100.0] * 62)
        # 진입은 1/3 이므로 그날 지수가 1000 이 되게 둔다 (1/2 는 진입 전이다)
        index = series("2026-01-02", [990.0] + [1000.0 + 10 * i for i in range(61)])
        o = oc.evaluate(SIG, closes, index)
        assert o.bench[20] == pytest.approx(0.20)
        assert o.excess(20) == pytest.approx(-0.20)
        # 지수가 그날 없으면 None
        assert oc.evaluate(SIG, closes, index[:5]).bench[20] is None


class Test집계:
    def _outcomes(self, rets: list[float]) -> list[oc.Outcome]:
        out = []
        for i, r in enumerate(rets):
            o = oc.Outcome(i, "2026-01-02", "short")
            o.rets[20] = r
            o.rets[60] = r
            o.hit_target = r > 0.1
            o.hit_stop = r < -0.05
            o.bench[20] = 0.01
            out.append(o)
        return out

    def test_표본이_모자라면_평균을_내지_않는다(self) -> None:
        stats = {(s.horizon, s.window): s for s in oc.summarize(self._outcomes([0.1, 0.2, -0.1]))}
        assert stats[("short", 20)].n == 3 and stats[("short", 20)].avg_ret is None

    def test_평균_이긴_비율_초과_터치(self) -> None:
        stats = {(s.horizon, s.window): s for s in oc.summarize(self._outcomes([0.2, 0.1, -0.1, 0.05, 0.0]))}
        s20 = stats[("short", 20)]
        assert s20.n == 5 and s20.avg_ret == pytest.approx(0.05) and s20.win_rate == pytest.approx(0.6)
        assert s20.avg_excess == pytest.approx(0.04)
        assert s20.hit_target_rate is None  # 터치는 60일 창에서만
        s60 = stats[("short", 60)]
        assert s60.hit_target_rate == pytest.approx(0.2) and s60.hit_stop_rate == pytest.approx(0.2)
        assert stats[("short", 5)].n == 0


class Test적재:
    def test_열_개수(self) -> None:
        o = oc.evaluate(SIG, series("2026-01-02", [100.0] * 70))
        from batch.core import db

        assert len(job.to_row(o, "now")) == db.column_count(job._OUT_COLS) == 16


def test_잡이_원래_종가를_함께_읽어_넘긴다() -> None:
    """배선까지 본다 — 서비스만 고치고 잡이 `raw_closes` 를 안 넘기면 운영에서는 그대로다 (25.208)."""
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    c.conn.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, target_price, stop_price, size_reduction, sector_cap_applied, rationale_text, rationale_data,"
        " calc_version, created_at)"
        " VALUES (1, '2026-01-02', 'short', 't', 95, 105, 'KRW', '[]', 110, 93, 1, 0, 'x', '{\"ref_close\": 99}', 1, 't')"
    )
    for d, raw in series("2026-01-02", Test신호_뒤의_분할.원래):
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'KRW', 't', 't')",
            [d, raw, raw / 5],
        )

    outcomes, _stats = job.compute(c, "KR", "2026-03-20")  # type: ignore[arg-type]

    assert len(outcomes) == 1
    assert outcomes[0].hit_stop is False


def test_잡이_미국_분할도_기준_종가로_맞춘다() -> None:
    """미국은 close 도 다시 받으면 분할이 반영된다 — 신호 행의 ref_close 를 잡이 읽어 넘기는지 본다 (25.215)."""
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    c.conn.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, target_price, stop_price, size_reduction, sector_cap_applied, rationale_text, rationale_data,"
        " calc_version, created_at)"
        " VALUES (1, '2026-01-02', 'short', 't', 95, 105, 'USD', '[]', 110, 93, 1, 0, 'x', '{\"ref_close\": 99}', 1, 't')"
    )
    for d, raw in series("2026-01-02", Test신호_뒤의_분할.원래):
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'USD', 't', 't')",
            [d, raw / 5, raw / 5],  # 야후가 분할을 반영해 다시 준 계열 — close 도 1/5
        )

    outcomes, _ = job.compute(c, "US", "2026-03-20")  # type: ignore[arg-type]

    assert outcomes[0].hit_stop is False


def test_신호가_기준_종가를_남긴다() -> None:
    """성적표가 단위를 맞추려면 신호 행에 그날 종가가 있어야 한다 (25.215)."""
    from batch.services import signals as sg
    from tests.test_signals import mid_input

    신호들 = sg.evaluate(mid_input(), max_weight_per_stock=10.0, total_investable=0.0)
    assert 신호들, "표본이 신호를 못 냈다 — 아래가 공짜로 통과한다"
    assert all(s.rationale_data.get("ref_close") == mid_input().close for s in 신호들)


def test_미국_수익률은_지수와_같은_가격_수익률이다() -> None:
    """지수(^GSPC)는 배당이 빠진 가격 지수다. 미국 Adj Close 로 재면 초과수익이 배당률만큼 부푼다 (25.216)."""
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
        " VALUES (1, '2026-01-05', 100, 97, 'USD', 't', 't')"
    )

    closes = job.load_closes(c, [1], "2026-01-01")  # type: ignore[arg-type]

    assert closes == {1: [("2026-01-05", 100.0)]}


class Test굳은_성적:
    """창(400일) 밖으로 나간 신호의 성적은 지우지 않고 집계에도 넣는다 (docs/infra.md 25.336)."""

    def _db(self):
        from tests.test_report_picks import SqliteClient

        c = SqliteClient()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        for i in range(5):
            c.conn.execute(
                f"INSERT INTO signal_outcomes ({job._OUT_COLS})"
                " VALUES (1, ?, 'short', ?, 100, 0.01, 0.02, 0.03, 0.1, -0.1, 0, 0, 0.01, 0.01, 60, 't')",
                [f"2024-01-0{i + 1}", f"2024-01-0{i + 2}"],
            )
        return c

    def test_창_밖의_성적을_남긴다(self) -> None:
        c = self._db()
        results, stats = job.compute(c, "KR", "2026-03-20")  # type: ignore[arg-type]
        job.store(c, "KR", [job.to_row(o, "now") for o in results], stats, "now", job.since_of("2026-03-20"))  # type: ignore[arg-type]
        assert c.conn.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == 5

    def test_집계에_함께_넣는다(self) -> None:
        c = self._db()
        _results, stats = job.compute(c, "KR", "2026-03-20")  # type: ignore[arg-type]
        by_window = {s.window: s for s in stats if s.horizon == "short"}
        assert by_window[20].n == 5
        assert by_window[20].avg_ret == pytest.approx(0.02)


class Test먼저_닿은_쪽만:
    """손절 뒤 목표를 넘은 신호가 "목표 도달" 에도 들어갔다 (docs/infra.md 25.680, 감사)."""

    def test_손절이_먼저면_목표는_아니다(self) -> None:
        계열 = series("2026-01-02", [100.0, 100.0, 90.0] + [100.0] * 30 + [115.0] * 30)
        o = oc.evaluate(SIG, 계열, raw_closes=dict(계열))
        assert o.hit_stop is True and o.hit_target is False

    def test_목표가_먼저면_손절은_아니다(self) -> None:
        계열 = series("2026-01-02", [100.0, 100.0, 112.0] + [100.0] * 30 + [90.0] * 30)
        o = oc.evaluate(SIG, 계열, raw_closes=dict(계열))
        assert o.hit_target is True and o.hit_stop is False


class Test시세가_끊긴_종목:
    """상장폐지·거래정지로 끊긴 신호가 NULL 로 빠져 승률 100%·손절률 0% 가 나왔다 (docs/infra.md 25.696·25.697)."""

    지수 = series("2026-01-02", [1000.0] * 120)
    시장_끝 = "2026-05-01"  # 2026-01-02 + 119일

    def test_시장이_창을_지났으면_마지막_종가로_채운다(self) -> None:
        계열 = series("2026-01-02", [100.0, 100.0] + [50.0] * 9)  # 진입 뒤 10행만 있고 끊김
        o = oc.evaluate(SIG, 계열, self.지수, dict(계열), self.시장_끝)
        assert o.rets[5] == pytest.approx(-0.5)
        assert o.rets[20] == pytest.approx(-0.5) and o.rets[60] == pytest.approx(-0.5)
        assert o.hit_stop is True
        stats = {s.window: s for s in oc.summarize([o] * 5)}
        assert stats[60].n == 5 and stats[60].win_rate == 0.0 and stats[60].hit_stop_rate == 1.0
        # 지수 대비도 같은 표본으로 — 지수 보합이니 초과 = −50% (25.698)
        assert o.bench[60] == pytest.approx(0.0) and stats[60].avg_excess == pytest.approx(-0.5)

    def test_나라_시세가_통째로_멈췄으면_끊김이_아니다(self) -> None:
        """D1 에서 미국 배치가 쉬는 동안 SP500 만 갱신돼 정상 종목이 전부 끊김이 됐다 (25.697, 교차검증)."""
        계열 = series("2026-01-02", [100.0, 100.0] + [110.0] * 9)
        o = oc.evaluate(SIG, 계열, self.지수, dict(계열), 계열[-1][0])  # 시장 전체도 같은 날 끝
        assert o.rets[20] is None and o.rets[60] is None
        # 종목은 7행(진입 뒤 4행)에서 멈췄고 나라 시세도 사흘 뒤 멈췄다 — 지수만 계속. 5일 창도 채우지 않는다
        짧은 = series("2026-01-02", [100.0, 100.0, 90.0, 90.0, 90.0, 90.0])
        o = oc.evaluate(SIG, 짧은, self.지수, dict(짧은), "2026-01-10")
        assert o.rets[5] is None

    def test_창의_끝날이_아직이면_채우지_않는다(self) -> None:
        """60일 창을 3일치로 채웠다 (25.697, 교차검증)."""
        계열 = series("2026-01-02", [100.0, 100.0, 91.0, 91.0, 91.0])
        o = oc.evaluate(SIG, 계열, self.지수, dict(계열), "2026-01-30")  # 진입 뒤 시장 거래일 27일
        assert o.rets[20] == pytest.approx(-0.09)
        assert o.rets[60] is None

    def test_진입일만_있고_끊기면_0퍼센트(self) -> None:
        계열 = series("2026-01-02", [100.0, 100.0])
        o = oc.evaluate(SIG, 계열, self.지수, dict(계열), self.시장_끝)
        assert o.rets[60] == pytest.approx(0.0)

    def test_시장_끝을_모르면_채우지_않는다(self) -> None:
        계열 = series("2026-01-02", [100.0, 100.0] + [50.0] * 9)
        o = oc.evaluate(SIG, 계열, self.지수, dict(계열))
        assert o.rets[20] is None


def test_잡이_나라_시세의_마지막_날을_넘긴다() -> None:
    """market_last 를 안 넘기면 끊김 채우기가 조용히 꺼진다 (docs/infra.md 25.697)."""
    import inspect

    원본 = inspect.getsource(job.compute)
    assert "market_last_dates(client, country, today)" in 원본
    assert '끝날.get(str(sig.get("market")))' in 원본


def test_시장별로_대부분이_들어온_날을_끝날로_본다() -> None:
    """KOSDAQ 이 멈췄는데 KOSPI 가, 또는 늦은 한 종목이 끝날을 끌어올려 정상 종목이 끊김이 됐다 (docs/infra.md 25.702)."""
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    for sid, market in [(1, "KOSPI"), (2, "KOSPI"), (3, "KOSPI"), (4, "KOSDAQ"), (5, "KOSDAQ"), (6, "KOSDAQ")]:
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, ?, 'KR', 'KRW', 'active', 't', 't')",
            [sid, f"{sid:06d}", market],
        )

    def 넣기(sid: int, 날: str) -> None:
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, 100, 'KRW', 't', 't')",
            [sid, 날],
        )

    for 날 in ["2026-09-21", "2026-09-22", "2026-09-23"]:
        for sid in (1, 2, 3):
            넣기(sid, 날)
    for 날 in ["2026-09-21"]:
        for sid in (4, 5, 6):
            넣기(sid, 날)
    넣기(1, "2026-09-25")  # 한 종목만 늦게 들어온 행
    끝 = job.market_last_dates(c, "KR", "2026-09-29")  # type: ignore[arg-type]
    assert 끝 == {"KOSPI": "2026-09-23", "KOSDAQ": "2026-09-21"}


def test_지수_대비는_표본이_같을_때만_낸다() -> None:
    """굳은 성적엔 5일 지수값이 없어 "지수 대비" 가 다른 표본에서 났다 (25.702, 교차검증)."""
    있는 = oc.Outcome(1, "2026-01-02", "short", rets={5: 0.1, 20: None, 60: None}, bench={5: 0.0, 20: None, 60: None})
    없는 = oc.Outcome(2, "2026-01-02", "short", rets={5: -0.1, 20: None, 60: None}, bench={5: None, 20: None, 60: None})
    s5 = {s.window: s for s in oc.summarize([있는] * 5 + [없는] * 5)}[5]
    assert s5.n == 10 and s5.avg_excess is None
    s5 = {s.window: s for s in oc.summarize([있는] * 5)}[5]
    assert s5.avg_excess == pytest.approx(0.1)


def test_채운_창의_지수는_시장_거래일_w_번째_날이다() -> None:
    """지수가 보합이면 어떤 날을 골라도 통과했다 (25.702, 교차검증 — 25.698 테스트 약점)."""
    계열 = series("2026-01-02", [100.0, 100.0] + [50.0] * 9)
    지수 = series("2026-01-02", [1000.0 + i for i in range(120)])  # 날마다 1씩 오른다
    o = oc.evaluate(SIG, 계열, 지수, dict(계열), "2026-05-01")
    # 진입 01-03(지수 1001), 60번째 시장 거래일은 03-04(지수 1061)
    assert o.bench[60] == pytest.approx(1061 / 1001 - 1)


def test_미국은_나라_하나로_끝날을_센다() -> None:
    """한 종목뿐인 미국 시장은 그 종목의 끝이 곧 시장 끝이라 끊김 감지가 꺼졌다 (docs/infra.md 25.706, 교차검증)."""
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    for sid, market in [(1, "NASDAQ"), (2, "NASDAQ"), (3, "NASDAQ"), (4, "IEX")]:
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, ?, 'US', 'USD', 'active', 't', 't')",
            [sid, f"T{sid}", market],
        )
    for 날 in ["2026-09-21", "2026-09-22", "2026-09-23"]:
        for sid in (1, 2, 3):
            c.conn.execute(
                "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, 10, 'USD', 't', 't')",
                [sid, 날],
            )
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (4, '2026-09-01', 10, 'USD', 't', 't')"
    )
    끝 = job.market_last_dates(c, "US", "2026-09-29")  # type: ignore[arg-type]
    assert 끝["IEX"] == "2026-09-23"  # IEX 한 종목이 09-01 에 끊겼어도 끝날은 나라 기준


def test_지수_대비는_한두_건_빠짐은_받아들인다() -> None:
    있는 = oc.Outcome(1, "2026-01-02", "short", rets={5: 0.1, 20: None, 60: None}, bench={5: 0.0, 20: None, 60: None})
    없는 = oc.Outcome(2, "2026-01-02", "short", rets={5: 0.1, 20: None, 60: None}, bench={5: None, 20: None, 60: None})
    s5 = {s.window: s for s in oc.summarize([있는] * 29 + [없는])}[5]
    assert s5.n == 30 and s5.avg_excess == pytest.approx(0.1)


def test_미국_작은_시장은_나라_끝날로_물러난다() -> None:
    """40일 넘게 행이 없는 작은 시장은 키가 없어 끊김 보정이 꺼졌다 (docs/infra.md 25.710, 교차검증)."""
    import inspect

    assert '(끝날.get(country) if country != "KR" else None)' in inspect.getsource(job.compute)
