"""리포트 대표 종목 줄 (docs/infra.md 25.326)."""

from __future__ import annotations

from datetime import UTC, datetime

from batch.core import db
from batch.jobs import daily
from batch.notify import formatter
from tests.test_portfolio_job import MemClient


def _client() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    return mem


def _price(mem: MemClient, day: str, close: float, adj: float | None, chg: float | None) -> None:
    mem.conn.execute(
        "INSERT INTO prices (stock_id, date, close, adj_close, change_pct, volume, currency, source, fetched_at)"
        " VALUES (1, ?, ?, ?, ?, 100, 'KRW', 'krx_openapi', 't')",
        [day, close, adj, chg],
    )


TARGET = [{"ticker": "005930", "country": "KR", "name_ko": "삼성전자", "name_en": None}]


def test_분할_다음_날은_저장된_등락률을_쓴다() -> None:
    """1:50 분할 — 원 종가로 다시 계산하면 −98% 였다."""
    mem = _client()
    _price(mem, "2026-10-12", 2_500_000, 50_000, 0.0)
    _price(mem, "2026-10-13", 49_000, 49_000, -2.0)
    rows = daily._report_rows_from_db(mem, "2026-10-13", TARGET)  # type: ignore[arg-type]
    assert rows[0]["change_pct"] == -2.0


def test_저장값이_없으면_수정종가로_계산한다() -> None:
    mem = _client()
    _price(mem, "2026-10-12", 2_500_000, 50_000, None)
    _price(mem, "2026-10-13", 49_000, 49_000, None)
    rows = daily._report_rows_from_db(mem, "2026-10-13", TARGET)  # type: ignore[arg-type]
    assert round(rows[0]["change_pct"], 6) == -2.0


def test_지난_종가는_그_날짜를_적는다() -> None:
    mem = _client()
    _price(mem, "2026-10-12", 50_000, 50_000, 1.0)
    rows = daily._report_rows_from_db(mem, "2026-10-13", TARGET)  # type: ignore[arg-type]
    글 = formatter.daily_report(
        market="KR", trade_date="2026-10-13", rows=rows, started_at=datetime(2026, 10, 13, 23, 30, tzinfo=UTC),
        warnings=[],
    )
    assert "삼성전자 (2026-10-12 종가)" in 글
