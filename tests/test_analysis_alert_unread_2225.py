"""같은 날 다시 낸 '분석 완료' 알림은 안 읽음으로 되돌린다 (docs/infra.md 25.2225, 11-11 결함 회차 [권고]).

예전엔 upsert 가 `is_read` 를 두어, 사용자가 읽은 옛 행 위에 새 분석 결과가 써져도 알림 센터 '안 읽음' 에서 빠졌다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from batch.jobs import analyze_extra

ROOT = Path(__file__).resolve().parents[1]


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    for p in sorted((ROOT / "migrations").glob("*.sql")):
        c.executescript(p.read_text(encoding="utf-8"))
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    return c


def test_같은_날_다시_쓰면_안_읽음으로() -> None:
    c = _db()
    c.execute(analyze_extra.ALERT_UPSERT, [1, "KR", "2026-11-11", "분석1", "{}", "t1", "t1"])
    c.execute("UPDATE alerts SET is_read = 1")
    c.execute(analyze_extra.ALERT_UPSERT, [1, "KR", "2026-11-11", "분석2", "{}", "t2", None])
    assert c.execute("SELECT message, is_read FROM alerts").fetchall() == [("분석2", 0)]


def test_다른_날은_따로_행() -> None:
    c = _db()
    c.execute(analyze_extra.ALERT_UPSERT, [1, "KR", "2026-11-10", "분석1", "{}", "t1", "t1"])
    c.execute("UPDATE alerts SET is_read = 1")
    c.execute(analyze_extra.ALERT_UPSERT, [1, "KR", "2026-11-11", "분석2", "{}", "t2", "t2"])
    assert c.execute("SELECT trade_date, is_read FROM alerts ORDER BY trade_date").fetchall() == [
        ("2026-11-10", 1), ("2026-11-11", 0)]
