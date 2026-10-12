"""감성 일일 질의가 날짜 색인을 타고, 창 밖 미채점 기사는 고르지 않는다 (docs/infra.md 25.1179, 감사 재현)."""

from __future__ import annotations

from batch.core import db
from batch.jobs import sentiment as job
from tests.test_portfolio_job import MemClient


def _mem() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
              " VALUES (1, 'AAA', 'NASDAQ', 'US', 'USD', 'active', 't', 't')")  # fmt: skip
    for i, day in enumerate(("2025-01-01T00:00:00+00:00", "2026-10-05T00:00:00+00:00"), start=1):
        c.execute("INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
                  " VALUES (?, 1, ?, ?, ?, 'en', 't', 't')", [i, f"t{i}", f"u{i}", day])  # fmt: skip
    return mem


def test_세_질의가_날짜_색인을_탄다() -> None:
    mem = _mem()
    for sql, n in ((job.PENDING_SQL, 6), (job.WINDOW_SQL, 4), (job.UNSCORED_SQL, 4)):
        계획 = " ".join(str(r[3]) for r in mem.conn.execute("EXPLAIN QUERY PLAN " + sql, ["vader"] + [""] * (n - 1)))
        assert "idx_news_published" in 계획, 계획


def test_창_밖_미채점_기사는_채점_대상이_아니다() -> None:
    mem = _mem()
    rows = mem.conn.execute(job.PENDING_SQL, ["vader", "2026-08-12", "US", "en", "", ""]).fetchall()
    assert [r[0] for r in rows] == [2]  # 2025-01-01 기사는 61일 창 밖
