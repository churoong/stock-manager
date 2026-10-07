"""재계산이 쓰다 말았으면 보유를 읽는 모든 곳이 안다 (docs/infra.md 25.648, 교차검증)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import daily, monitor_targets, portfolio
from tests.test_monitor_targets import MemClient
from tests.test_report_picks import SqliteClient


def _요약(conn, 지문: str) -> None:  # noqa: ANN001
    conn.execute(
        "INSERT INTO portfolio_summary (id, as_of_date, totals_json, allocation_json, metrics_json, upcoming_json,"
        " warnings_json, trades_version, calc_version, created_at) VALUES (1, 'd', '{}', '{}', '{}', '[]', '[]', ?, 1, 't')",
        [지문],
    )


def test_감시_목록은_어제_것을_둔다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    monkeypatch.setattr(monitor_targets, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _요약(mem.conn, portfolio.REBUILDING)
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (9, 'X', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    mem.conn.execute(
        "INSERT INTO monitor_targets (market, stock_id, yahoo_symbol, name, currency, reasons, built_at)"
        " VALUES ('KR', 9, 'X.KS', '어제', 'KRW', '[\"holding\"]', 't')"
    )
    assert monitor_targets.run("KR") == 1
    assert mem.conn.execute("SELECT name FROM monitor_targets").fetchall() == [("어제",)]
    # 세션은 보유와 무관하다 — 그래도 새로 쓴다 (25.650, 교차검증)
    assert mem.conn.execute("SELECT COUNT(*) FROM market_sessions WHERE market = 'KR'").fetchone()[0] > 0


def test_리포트_2부는_보유가_반쪽일_수_있다고_말한다() -> None:
    c = SqliteClient()
    _요약(c.conn, portfolio.REBUILDING)
    경고: list[str] = []
    daily.load_holdings(c, "KRW", None, 경고)  # type: ignore[arg-type]
    assert any("재계산이 끝나지 않아" in w for w in 경고)
    c.conn.execute("UPDATE portfolio_summary SET trades_version = 'v1'")
    경고2: list[str] = []
    daily.load_holdings(c, "KRW", None, 경고2)  # type: ignore[arg-type]
    assert 경고2 == []
