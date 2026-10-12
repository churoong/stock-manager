"""수집 공백 중 미국 분할을 공백 뒤 첫 수집이 잡는다 (docs/infra.md 25.1247, 미국 시세 감사 재현)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import daily
from batch.services import adjust_drift as drift
from batch.sources.yfinance_src import DailyBar
from tests.test_report_picks import SqliteClient


def _평일(a: str, b: str):  # noqa: ANN202
    d = date.fromisoformat(a)
    while d <= date.fromisoformat(b):
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def _창(끝: str, 종가: float) -> list[DailyBar]:
    시작 = (date.fromisoformat(끝) - timedelta(days=13)).isoformat()
    return [DailyBar("XYZ", d, 종가, adj_close=종가 * 0.98, volume=1000) for d in _평일(시작, 끝)]


def _저장소() -> SqliteClient:
    c = SqliteClient()
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
                   " VALUES (1, 'XYZ', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'XYZ')")  # fmt: skip
    for d in _평일("2026-08-03", "2026-09-04"):
        c.conn.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                       " VALUES (1, ?, 400, 392, 'USD', 't', 't')", [d])  # fmt: skip
    return c


def test_공백_중_4대1_분할을_대기열과_분할_기록에_올린다() -> None:
    c = _저장소()
    daily._store_us_bars(c, _창("2026-10-09", 100.0), "2026-10-09", {"XYZ": 1}, "yfinance", detect_drift=True, notes=[])
    q = c.conn.execute("SELECT ratio_before, ratio_after FROM adjust_refresh_queue WHERE stock_id = 1").fetchall()
    assert len(q) == 1 and q[0][0] == q[0][1]  # 분할 표시
    assert c.conn.execute("SELECT value FROM settings WHERE key = 'us_split_detections'").fetchone() is not None


def test_공백_뒤_보통_등락이면_올리지_않는다() -> None:
    c = _저장소()
    daily._store_us_bars(c, _창("2026-10-09", 380.0), "2026-10-09", {"XYZ": 1}, "yfinance", detect_drift=True, notes=[])
    assert c.conn.execute("SELECT COUNT(*) FROM adjust_refresh_queue").fetchone()[0] == 0


def test_순수_판정() -> None:
    옛 = drift.Sample("2026-09-04", 400.0, 392.0)
    assert drift.gap_drift(1, 옛, drift.Sample("2026-09-28", 100.0, 98.0)) is not None
    assert drift.gap_drift(1, 옛, drift.Sample("2026-09-28", 300.0, 294.0)) is None  # −25% — 문턱 안
