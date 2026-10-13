"""첫 매매가 미국 종목이고 체결일 뒤 환율 행이 아직 없을 때 (docs/infra.md 25.1160, 감사 재현).

종가는 25.883 부터 첫 매매일 11일 앞부터 읽는데 환율은 첫 매매일부터만 읽어 `fx_now` 가 None — 평가 0원, 미국 2부에서
그 보유가 빠졌다(같은 통화 평가액이 있는데도 원화 평가액 None 을 먼저 보고 버렸다)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.core import db
from batch.jobs import daily
from batch.jobs import portfolio as job
from tests.test_portfolio_job import MemClient


@pytest.fixture
def mem(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(job.cal, "user_today", lambda: date(2026, 10, 12))
    c = m.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)"
              " VALUES (2, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't')")  # fmt: skip
    for d, px, fx in (("2026-10-07", 200.0, 1390.0), ("2026-10-08", 205.0, 1400.0)):
        c.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
                  " VALUES (2, ?, ?, 'USD', 't', 't')", [d, px])  # fmt: skip
        c.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')", [d, fx])
    c.execute("INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
              " fee, tax, horizon, created_at, updated_at) VALUES (1, 2, 'buy', '2026-10-09', 210.0, 10, 'USD', 1405.0,"
              " 'manual', 0, NULL, 'long', 't', '2026-10-12T00:00:00')")  # fmt: skip
    return m


def test_첫_매매일_앞_환율로_평가한다(mem: MemClient) -> None:
    job.run()
    mv, mv_krw = mem.conn.execute("SELECT market_value, market_value_krw FROM positions").fetchone()
    # 표의 마지막 환율(1,400)이 체결일보다 앞서므로 체결 환율 1,405 로 평가한다 (25.1295). 이 시험의 요지(평가가 비지 않는다)는 같다
    assert mv == 2100.0 and mv_krw == pytest.approx(2100.0 * 1405.0)
    totals = json.loads(mem.conn.execute("SELECT totals_json FROM portfolio_summary").fetchone()[0])
    assert totals["unvalued_count"] == 0


def test_원화_평가액이_없어도_같은_통화_평가액으로_미국_2부에_넣는다(mem: MemClient) -> None:
    mem.conn.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, market_value_krw, updated_at)"
        " VALUES (2, 10, 'USD', 210, 1405, 2100, 2950500, '2026-10-09', 'long', '2026-10-09', 210, 2100, NULL, 't')"
    )
    경고: list[str] = []
    [h] = daily.load_holdings(mem, "USD", 1400.0, 경고)  # type: ignore[arg-type]
    assert h.value == 2100.0 and not 경고
    # 원화 리포트는 원화 평가액이 없으니 여전히 빼고 말한다
    assert daily.load_holdings(mem, "KRW", None, 경고) == []  # type: ignore[arg-type]
