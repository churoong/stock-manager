"""종가 0 인 옛 행이 공백 뒤 미국 분할 감지를 가리지 않는다 (docs/infra.md 25.1257, 미국 시세 감사 재현 — 25.1251 의 미국 짝)."""

from __future__ import annotations

from batch.jobs import daily
from batch.sources.yfinance_src import DailyBar
from tests.test_report_picks import SqliteClient


def test_공백_앞_마지막_행이_종가_0_이면_그_앞_행과_견준다() -> None:
    c = SqliteClient()
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
                   " VALUES (1, 'XYZ', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'XYZ')")  # fmt: skip
    for d, px in (("2026-09-10", 2.0), ("2026-09-11", 2.0), ("2026-09-14", 0.0)):
        c.conn.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                       " VALUES (1, ?, ?, ?, 'USD', 'yfinance', 't')", [d, px, px])  # fmt: skip
    bars = [DailyBar("XYZ", "2026-10-05", 20.0, adj_close=20.0, volume=10),
            DailyBar("XYZ", "2026-10-06", 20.5, adj_close=20.5, volume=10)]  # fmt: skip
    daily._store_us_bars(c, bars, "2026-10-06", {"XYZ": 1}, "yfinance", detect_drift=True, notes=[])
    줄 = c.conn.execute("SELECT stock_id, ratio_before, ratio_after FROM adjust_refresh_queue").fetchall()
    assert [tuple(r) for r in 줄] == [(1, 1.0, 1.0)]  # 1:10 병합을 분할로 대기열에
