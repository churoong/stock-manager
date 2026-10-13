"""포트폴리오 CAGR·MDD 가 첫날(체결가 → 첫 종가) 수익을 빠뜨리지 않는다 (docs/infra.md 25.1260, 손익 감사 재현)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from batch.core import calendar as cal
from batch.core import db
from batch.jobs import portfolio as job
from tests.test_portfolio_job import MemClient


def test_첫날_하락이_MDD_에_잡히고_CAGR_은_0(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, sector)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'X', 'KRW', 'active', 't', 't', 'S')")  # fmt: skip
    days: list[date] = []
    d = date(2025, 9, 1)
    while len(days) < 260:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    for i, day in enumerate(days):  # 첫날 9,000(체결가 10,000 대비 −10%) → 마지막 날 10,000
        c.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
                  " VALUES (1, ?, ?, 'KRW', 't', 't')", [day.isoformat(), 9000 + 1000 * i / (len(days) - 1)])  # fmt: skip
    c.execute("INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
              " fee, tax, horizon, created_at, updated_at) VALUES (1, 1, 'buy', ?, 10000, 100, 'KRW', 1, 'none', 0, 0,"
              " 'long', 't', 't')", [days[0].isoformat()])  # fmt: skip
    monkeypatch.setattr(cal, "user_today", lambda: days[-1])
    job.run()
    m = json.loads(c.execute("SELECT metrics_json FROM portfolio_summary WHERE id = 1").fetchone()[0])
    assert abs(m["twr_total_return"]) < 1e-9
    assert abs(m["cagr"]) < 1e-6  # 예전엔 +11.2%
    assert m["mdd"] == pytest.approx(-0.10)  # 예전엔 0
    assert m["start_date"] == days[0].isoformat()
