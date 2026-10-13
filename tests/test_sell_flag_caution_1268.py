"""유니버스 밖에서 산 보유가 투자주의환기로 지정되면 점검 플래그 (docs/infra.md 25.1268, 매도 플래그 감사 재현)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf
from tests.test_report_picks import SqliteClient


def _플래그(사유: str) -> list[tuple[str, str]]:
    c = SqliteClient()
    k = c.conn
    k.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')")  # fmt: skip
    k.execute("INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
              " horizon, price_date, close, market_value, updated_at)"
              " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-09-01', 'long', '2026-10-12', 100, 1000, 't')")  # fmt: skip
    k.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
              " horizon, created_at, updated_at)"
              " VALUES (1, 'buy', '2026-09-01', 100, 10, 'KRW', 1, 'none', 'long', 't', 't')")  # fmt: skip
    for snap, why in (("2026-08-31", "시총미달"), ("2026-10-12", 사유)):
        k.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, exclude_reason, currency, created_at)"
                  " VALUES (?, 1, 0, ?, 'KRW', 't')", [snap, why])  # fmt: skip
    hs, _ = job.load_holdings(c, date(2026, 10, 13))
    return [(f.reason_code, f.rationale_text) for f in sf.evaluate(hs[0], date(2026, 10, 13))]


@pytest.mark.parametrize("사유", ["관리종목", "투자주의환기"])
def test_결격으로_넘어가면_알린다(사유: str) -> None:
    assert _플래그(사유) == [("점검", f"에이: 유니버스 밖에서 산 종목이 지금 {사유}입니다")]


def test_수치_사유끼리는_조용하다() -> None:
    assert _플래그("거래대금미달") == []
