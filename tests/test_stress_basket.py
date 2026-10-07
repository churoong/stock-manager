"""스트레스 바스켓의 기준일은 "마지막으로 계산한 날" 이다 (docs/infra.md 25.359, 25.337 과 같은 규칙)."""

from __future__ import annotations

from batch.core import db
from batch.jobs import stress as job
from tests.test_portfolio_job import MemClient


def _db() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, target_price, stop_price, suggested_weight_pct, size_reduction, sector_cap_applied,"
        " rationale_text, rationale_data, calc_version, created_at)"
        " VALUES (1, '2026-09-24', 'long', 't', 1, 2, 'KRW', '[]', 3, 1, 10.0, 1, 0, 'x', '{}', 1, 't')"
    )
    return mem


def test_어제_걸린_신호만_있으면_어제_바스켓이다() -> None:
    basket, as_of = job.load_basket(_db(), "KR")  # type: ignore[arg-type]
    assert basket == {1: 0.1} and as_of == "2026-09-24"


def test_오늘_계산했는데_안_걸렸으면_빈_바스켓이다() -> None:
    mem = _db()
    mem.conn.execute(
        "INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
        " calc_version, created_at) VALUES (1, '2026-09-25', 'long', 0, 1, '[]', 1, 't')"
    )
    basket, as_of = job.load_basket(mem, "KR")  # type: ignore[arg-type]
    assert basket == {} and as_of == "2026-09-25"
