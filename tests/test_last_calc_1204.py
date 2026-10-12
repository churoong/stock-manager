"""마지막으로 계산한 날 — 판정표 행을 전부 읽지 않고 날짜 색인으로 첫 행에서 멈춘다 (docs/infra.md 25.1204, 마이그레이션 0066)."""

from __future__ import annotations

from batch.core import db
from tests import test_sql_schema as t
from tests.test_portfolio_job import MemClient


def _계획(sql: str, args: list) -> list[str]:
    con = t.스키마올리기()
    return [r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args)]


def test_배치_질의는_날짜_색인을_내림차순으로_탄다() -> None:
    sql = db.LAST_CALC_SQL
    계획 = _계획(sql, ["KR", "9999-12-31", "KR", "9999-12-31"])
    assert any("idx_signal_checks_date" in x for x in 계획), 계획
    assert not any(x.startswith("SEARCH ck USING COVERING INDEX sqlite_autoindex_signal_checks_1") for x in 계획), 계획


def test_답은_예전과_같다_나라를_가린다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid, 나라 in ((1, "KR"), (2, "US")):
        c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                  " VALUES (?, ?, 'X', ?, 'KRW', 't', 't')", [sid, f"T{sid}", 나라])  # fmt: skip
    for sid, d in ((1, "2026-10-08"), (2, "2026-10-09"), (1, "2026-10-07")):
        c.execute("INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
                  " calc_version, created_at) VALUES (?, ?, 'mid', 0, 1, '[]', 1, 't')", [sid, d])  # fmt: skip
    assert db.last_signal_calc_date(mem, "KR", None) == "2026-10-08"  # type: ignore[arg-type]
    assert db.last_signal_calc_date(mem, "US", None) == "2026-10-09"  # type: ignore[arg-type]
    assert db.last_signal_calc_date(mem, "KR", "2026-10-07") == "2026-10-07"  # type: ignore[arg-type]
