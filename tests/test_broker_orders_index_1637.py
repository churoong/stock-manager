"""KIS모의투자 주문 표를 날짜 범위로 읽는 질의가 증권사 주문 전체를 훑지 않는다 (0089, docs/infra.md 25.1637)."""

from __future__ import annotations

from batch.core import db
from batch.jobs import broker_mirror as bmj
from batch.jobs import desk as jd
from tests.test_portfolio_job import MemClient


def test_날짜_범위_질의는_broker_trade_date_색인을_탄다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sql in (jd.KIS_ORDERS_SQL, bmj.COMPARE_SQL):
        계획 = " ".join(r[-1] for r in mem.conn.execute("EXPLAIN QUERY PLAN " + sql, ["x"] * sql.count("?")).fetchall())
        assert "idx_paper_broker_orders_trade (broker=? AND trade_date" in 계획, 계획


def test_맞추기_종목_질의는_종목_색인을_탄다() -> None:
    """교차검증 [필수] 2 — 0089 가 생기자 `IDS_SQL` 의 EXISTS 가 (broker) 앞자리로 바뀌어 종목마다 주문을 처음부터 훑었다."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    계획 = " ".join(r[-1] for r in mem.conn.execute("EXPLAIN QUERY PLAN " + bmj.IDS_SQL, ["KR", "[]", "kis_vts"]).fetchall())
    assert "idx_paper_broker_orders_stock (stock_id=?)" in 계획, 계획


def test_웹_거래내역_질의도_날짜_색인을_탄다() -> None:
    """교차검증 [권고] — 이 커밋이 고치려던 것이 바로 웹 질의다(`web/lib/kisPaper.ts` `KIS_ORDERS`)."""
    import re
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "kisPaper.ts").read_text(encoding="utf-8")
    sql = re.search(r"export const KIS_ORDERS = `([^`]+)`", 글)
    assert sql, "KIS_ORDERS 를 못 찾음"
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    계획 = " ".join(r[-1] for r in mem.conn.execute("EXPLAIN QUERY PLAN " + sql.group(1), ["x", "2026-01-01"]).fetchall())
    assert "idx_paper_broker_orders_trade (broker=? AND trade_date>?)" in 계획, 계획
