"""실적 일정 (docs/portfolio.md 5장). 야후 예정일이 먼저고, 없는 종목만 법정 기한으로 추정한다."""

from __future__ import annotations

import datetime as dt
from datetime import date

import pytest

from batch.jobs import earnings_calendar as job
from batch.services import portfolio as pf
from batch.sources import yfinance_src
from tests.test_portfolio_job import MemClient
from tests.test_report_picks import SqliteClient


class Test기한목록:
    def test_다음_네_개를_차례로_준다(self) -> None:
        out = pf.upcoming_deadlines("2025-12-31", date(2026, 9, 17), 4)
        kinds = [k for k, _d, _n in out]
        dates = [d for _k, d, _n in out]
        assert dates == ["2026-11-14", "2027-03-31", "2027-05-15", "2027-08-14"]
        assert kinds == ["분기", "연간", "분기", "분기"]
        # D-day 는 전부 오늘 기준이다 (이어 부르면서 기준이 밀리지 않는다)
        assert [n for _k, _d, n in out] == [(date.fromisoformat(d) - date(2026, 9, 17)).days for d in dates]

    def test_6월_결산도_같은_규칙(self) -> None:
        out = pf.upcoming_deadlines("2026-06-30", date(2026, 9, 17), 2)
        assert [d for _k, d, _n in out] == ["2026-09-28", "2026-11-14"]


class Test행:
    def test_추정임을_행마다_남긴다(self) -> None:
        rows = job.rows_for(7, "2025-12-31", date(2026, 9, 17), "now")
        assert len(rows) == job.EVENTS_PER_STOCK
        stock_id, event_type, scheduled, confirmed, note, source, _now = rows[0]
        assert (stock_id, scheduled, confirmed, source) == (7, "2026-11-14", 0, job.SOURCE)
        assert event_type == "실적발표(분기)" and "추정" in note

    def test_결산일이_너무_오래되면_빈_목록(self) -> None:
        assert job.rows_for(7, "2000-12-31", date(2026, 9, 17), "now") == []


class Test적재:
    def _client(self) -> SqliteClient:
        c = SqliteClient()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        c.conn.execute("INSERT INTO watchlist (stock_id, added_at) VALUES (1, 't')")
        return c

    def test_대상은_유니버스_보유_관심(self) -> None:
        client = self._client()
        assert job.pick_targets(client, "KR") == [(1, None)]  # type: ignore[arg-type]

    def test_추정만_지우고_다시_넣는다(self) -> None:
        client = self._client()
        client.conn.execute(
            "INSERT INTO earnings_calendar (stock_id, event_type, scheduled_date, is_confirmed, note, source, fetched_at)"
            " VALUES (1, '실적발표', '2026-12-01', 1, '확정', 'krx', 't'),"
            "        (1, '실적발표(분기)', '2026-11-14', 0, '추정', ?, 't')",
            [job.SOURCE],
        )
        rows = job.rows_for(1, "2025-12-31", date(2026, 9, 17), "now")
        job.store(client, rows, "2026-09-17", yahoo_ids=set(), estimate_ids=[1])  # type: ignore[arg-type]
        kept = client.conn.execute(
            "SELECT source, COUNT(*) FROM earnings_calendar GROUP BY source ORDER BY source"
        ).fetchall()
        assert kept == [(job.SOURCE, job.EVENTS_PER_STOCK), ("krx", 1)]  # 확정 일정은 건드리지 않는다


class Test야후_예정일:
    def test_실제_응답_모양을_읽는다(self) -> None:
        """2026-09-18 실측: calendar 는 dict 이고 'Earnings Date' 가 date 목록이다."""
        calendar = {
            "Dividend Date": dt.date(2026, 8, 13),
            "Earnings Date": [dt.date(2026, 10, 30)],
            "Earnings Average": 1.98,
        }
        assert yfinance_src.parse_earnings_dates(calendar) == ["2026-10-30"]

    @pytest.mark.parametrize("calendar", [None, {}, {"Earnings Date": None}, {"Earnings Date": []}, "이상한 값"])
    def test_없으면_빈_목록(self, calendar) -> None:
        assert yfinance_src.parse_earnings_dates(calendar) == []

    def test_구간은_정렬해서_돌려준다(self) -> None:
        calendar = {"Earnings Date": [dt.date(2026, 10, 31), dt.date(2026, 10, 27)]}
        assert yfinance_src.parse_earnings_dates(calendar) == ["2026-10-27", "2026-10-31"]

    def test_날짜가_하나면_확정(self) -> None:
        rows = job.yahoo_rows(3, ["2026-10-28"], "2026-09-18", "now")
        stock_id, event, scheduled, confirmed, note, source, _now = rows[0]
        assert (stock_id, event, scheduled, confirmed, source) == (3, "실적발표", "2026-10-28", 1, job.SOURCE_YAHOO)
        assert note == job.NOTE_YAHOO

    def test_구간이면_추정으로_적고_구간을_남긴다(self) -> None:
        rows = job.yahoo_rows(3, ["2026-10-27", "2026-10-31"], "2026-09-18", "now")
        _sid, _event, scheduled, confirmed, note, _source, _now = rows[0]
        assert (scheduled, confirmed) == ("2026-10-27", 0)
        assert "2026-10-27~2026-10-31" in note

    def test_지난_날짜만_있으면_넣지_않는다(self) -> None:
        assert job.yahoo_rows(3, ["2026-07-30"], "2026-09-18", "now") == []

    def test_429_가_이어지면_그만둔다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(job.time, "sleep", lambda _: None)
        calls: list[str] = []

        def blocked(symbol: str):
            calls.append(symbol)
            return yfinance_src.FetchResult(ok=False, error="429 Too Many Requests", limit_state="blocked")

        monkeypatch.setattr(yfinance_src, "fetch_earnings_dates", blocked)
        targets = [(i, f"S{i}") for i in range(20)]
        rows, counts = job.collect_yahoo(targets, "2026-09-18", "now")
        assert rows == [] and len(calls) == job.MAX_RATE_LIMIT_HITS
        assert counts["rate_limited"] == job.MAX_RATE_LIMIT_HITS

    def test_심볼이_없으면_부르지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(job.time, "sleep", lambda _: None)
        monkeypatch.setattr(
            yfinance_src, "fetch_earnings_dates",
            lambda symbol: yfinance_src.FetchResult(ok=True, data=["2026-10-28"], limit_state="ok"),
        )  # fmt: skip
        rows, counts = job.collect_yahoo([(1, None), (2, "AAPL")], "2026-09-18", "now")
        assert counts["asked"] == 1 and [r[0] for r in rows] == [2]


class Test두_출처의_우선순위:
    def test_야후가_있는_종목은_추정하지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from batch.core import db

        client = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: client)
        monkeypatch.setattr(job.time, "sleep", lambda _: None)
        db.apply_migrations(client)  # type: ignore[arg-type]
        c = client.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
            " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '005930.KS'),"
            "        (2, '000660', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '000660.KS')"
        )
        c.execute("INSERT INTO watchlist (stock_id, added_at) VALUES (1, 't'), (2, 't')")
        for sid in (1, 2):
            c.execute(
                "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
                " receipt_no, currency, unit, source, fetched_at)"
                " VALUES (?, 2025, '11011', 'A', 1, '2026-03-10', ?, 'KRW', 'KRW', 't', 't')",
                [sid, f"r{sid}"],
            )

        def fake(symbol: str):
            if symbol == "005930.KS":
                return yfinance_src.FetchResult(ok=True, data=["2036-10-28"], limit_state="ok")
            return yfinance_src.FetchResult(ok=True, data=[], limit_state="ok")

        monkeypatch.setattr(yfinance_src, "fetch_earnings_dates", fake)
        assert job.run() == 0
        rows = c.execute(
            "SELECT stock_id, source, is_confirmed FROM earnings_calendar ORDER BY stock_id, scheduled_date"
        ).fetchall()
        assert (1, job.SOURCE_YAHOO, 1) in rows
        assert not [r for r in rows if r[0] == 1 and r[1] == job.SOURCE]  # 야후가 있으면 추정 없음
        assert [r for r in rows if r[0] == 2 and r[1] == job.SOURCE]      # 없으면 추정으로 채운다

    def test_429_로_멈춰도_못_물은_종목의_야후_일정은_남는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """docs/infra.md 25.252 — 지우기가 종목을 가리지 않아 못 물은 종목의 확정 일정이 사라졌다."""
        from batch.core import db

        client = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: client)
        monkeypatch.setattr(job.time, "sleep", lambda _: None)
        db.apply_migrations(client)  # type: ignore[arg-type]
        c = client.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
            " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '005930.KS'),"
            "        (2, '000660', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '000660.KS')"
        )
        c.execute("INSERT INTO watchlist (stock_id, added_at) VALUES (1, 't'), (2, 't')")
        # 지난주에 받아 둔 2번의 확정 일정
        c.execute(
            "INSERT INTO earnings_calendar (stock_id, event_type, scheduled_date, is_confirmed, note, source, fetched_at)"
            " VALUES (2, '실적발표', '2036-11-05', 1, '야후', ?, 't')",
            [job.SOURCE_YAHOO],
        )
        monkeypatch.setattr(job, "MAX_RATE_LIMIT_HITS", 1)

        def fake(symbol: str):
            if symbol == "005930.KS":
                return yfinance_src.FetchResult(ok=True, data=["2036-10-28"], limit_state="ok")
            return yfinance_src.FetchResult(ok=False, error="429 Too Many Requests", limit_state="blocked")

        monkeypatch.setattr(yfinance_src, "fetch_earnings_dates", fake)
        assert job.run() == 0
        rows = c.execute("SELECT stock_id, source, scheduled_date FROM earnings_calendar ORDER BY stock_id").fetchall()
        assert (2, job.SOURCE_YAHOO, "2036-11-05") in rows, "못 물은 종목의 확정 일정이 지워졌다"
        assert not [r for r in rows if r[0] == 2 and r[1] == job.SOURCE], "확정 일정이 있는데 추정까지 넣었다"
        assert (1, job.SOURCE_YAHOO, "2036-10-28") in rows
