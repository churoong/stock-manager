"""폐지·기호 없는 종목의 재수집 대기 항목이 경고·실패를 영영 붙들지 않는다 (docs/infra.md 25.1246, 미국 시세 감사 재현)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import refresh_us_adjusted as refresh
from tests.test_adjust_drift import SqliteClient


def _대기열() -> SqliteClient:
    c = SqliteClient()
    for sid, status, sym in ((1, "active", "AAA"), (2, "delisted", "BBB"), (3, "active", None)):
        c.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
                       " VALUES (?, ?, 'NASDAQ', 'US', 'USD', ?, 't', 't', ?)", (sid, f"T{sid}", status, sym))  # fmt: skip
    옛날 = (date(2026, 10, 12) - timedelta(days=refresh.MAX_PENDING_DAYS + 5)).isoformat()
    for sid in (1, 2, 3):
        c.conn.execute("INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
                       " VALUES (?, ?, '2026-09-01', 0.98, 0.96)", (sid, f"{옛날}T00:00:00+00:00"))  # fmt: skip
    return c


def test_다시_받을_수_있는_것만_센다() -> None:
    c = _대기열()
    assert refresh.pending_summary(c)[0] == 1  # type: ignore[arg-type]
    assert [sid for sid, _ in refresh.pending(c)] == [1]  # type: ignore[arg-type]


def test_못_받는_항목만_남으면_경고하지_않는다() -> None:
    c = _대기열()
    c.conn.execute("UPDATE adjust_refresh_queue SET done_at = 't' WHERE stock_id = 1")
    assert refresh.pending_summary(c) == (0, None)  # type: ignore[arg-type]
    assert refresh.pending_note(c, date(2026, 10, 12)) is None  # type: ignore[arg-type]
