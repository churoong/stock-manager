"""미국 공시 수집 (docs/data-sources.md 14.4). submissions 의 열 배열을 안전하게 읽는다."""

from __future__ import annotations

from batch.jobs import disclosures_us as job
from batch.sources import sec_edgar as sec
from tests.test_report_picks import SqliteClient

PAYLOAD = {
    "cik": 320193,
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-26-000010", "0000320193-26-000009", "0000320193-26-000008", "bad"],
            "form": ["10-Q", "4", "8-K", "10-K"],
            "filingDate": ["2026-08-01", "2026-07-20", "2026-07-01", "nope"],
            "reportDate": ["2026-06-27", "", "2026-07-01", ""],
            "primaryDocument": ["aapl-20260627.htm", "xslF345X05/wk.xml", "", "x.htm"],
            "primaryDocDescription": ["10-Q", "FORM 4", "8-K", ""],
        }
    },
}


class Test파싱:
    def test_고른_폼만_열_단위로_맞춰_읽는다(self) -> None:
        rows = sec.parse_filings(PAYLOAD, "0000320193")
        # Form 4 는 목록에 없고, 접수일이 날짜가 아닌 행은 버린다
        assert [r.form for r in rows] == ["10-Q", "8-K"]
        first = rows[0]
        assert first.accession == "0000320193-26-000010" and first.filed == "2026-08-01"
        assert first.title == "10-Q (2026-06-27) — 10-Q"
        assert first.url == (
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl-20260627.htm"
        )

    def test_문서_이름이_없으면_묶음_주소로(self) -> None:
        rows = sec.parse_filings(PAYLOAD, "0000320193")
        assert rows[1].url.endswith("/000032019326000008/")

    def test_폼을_비우면_전부(self) -> None:
        assert len(sec.parse_filings(PAYLOAD, "0000320193", forms=())) == 3

    def test_모양이_다르면_빈_목록(self) -> None:
        assert sec.parse_filings({}, "1") == []
        assert sec.parse_filings({"filings": {"recent": {"accessionNumber": "x"}}}, "1") == []

    def test_건수를_제한한다(self) -> None:
        assert len(sec.parse_filings(PAYLOAD, "0000320193", limit=1)) == 1


class Test대상과적재:
    def _client(self) -> SqliteClient:
        c = SqliteClient()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
            " VALUES (1, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'AAPL'),"
            " (2, 'MSFT', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'MSFT'),"
            " (3, 'NVDA', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'NVDA')"
        )
        c.conn.execute(
            "INSERT INTO monitor_targets (market, stock_id, yahoo_symbol, name, currency, reasons, built_at)"
            " VALUES ('US', 1, 'AAPL', 'Apple', 'USD', '[]', 't')"
        )
        c.conn.execute("INSERT INTO watchlist (stock_id, added_at) VALUES (2, 't')")
        return c

    def test_지켜보는_종목만(self) -> None:
        assert job.pick_targets(self._client(), 10) == [(1, "AAPL"), (2, "MSFT")]  # type: ignore[arg-type]

    def test_티커_목록에_없으면_빼고_알린다(self) -> None:
        pairs, missing = job.with_cik([(1, "AAPL"), (2, "MSFT")], {"AAPL": "0000320193"})
        assert pairs == [(1, "0000320193")] and missing == ["MSFT"]

    def test_같은_접수번호는_두_번_들어가지_않는다(self) -> None:
        client = self._client()
        original = client.execute
        client.execute = lambda sql, args=None: original("SELECT 1", []) if "api_usage" in sql else original(sql, args)
        rows = [job.to_row(1, f, "now") for f in sec.parse_filings(PAYLOAD, "0000320193")]
        assert len(rows[0]) == 10
        job.store(client, rows)  # type: ignore[arg-type]
        job.store(client, rows)  # type: ignore[arg-type]
        stored = client.conn.execute("SELECT receipt_no, report_name, source FROM disclosures ORDER BY receipt_no").fetchall()
        assert len(stored) == 2
        assert stored[0][1] == "8-K" and stored[0][2] == job.SOURCE
