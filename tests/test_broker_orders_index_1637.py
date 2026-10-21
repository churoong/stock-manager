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
