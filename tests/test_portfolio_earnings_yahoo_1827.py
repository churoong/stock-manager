"""포트폴리오 실적 D-day 는 연간 재무가 없는 보유 종목도 야후 예정일이 있으면 싣는다 (docs/infra.md 25.1827).

예전에는 결산일(연간 재무)이 있는 종목만 돌아, 새 상장 종목처럼 연간 재무가 아직 없으면 야후가 날짜를 줘도 빠졌다.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.core import db
from batch.jobs import earnings_calendar as ec
from batch.jobs import portfolio as job
from tests.test_portfolio_job import MemClient


def test_야후_예정일만_있는_보유_종목도_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(job.cal, "user_today", lambda: date(2026, 10, 27))
    c = m.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, asset_type)"
        " VALUES (1, 'NEWCO', 'NASDAQ', 'US', '새회사', 'USD', 'active', 't', 't', 'stock')"
    )
    c.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
        " VALUES (1, '2026-10-26', 50, 'USD', 't', 't')"
    )
    c.execute(
        "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', '2026-10-26', 1390, 't', 't')"
    )
    c.execute(
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " fee, tax, horizon, created_at, updated_at)"
        " VALUES (1, 1, 'buy', '2026-10-20', 48, 1, 'USD', 1390, 'manual', 0, 0, 'long', 't', 't')"
    )
    c.execute(
        "INSERT INTO earnings_calendar (stock_id, event_type, scheduled_date, is_confirmed, source, fetched_at)"
        " VALUES (1, ?, '2026-11-05', 1, ?, 't')",
        [ec.YAHOO_EVENT, ec.SOURCE_YAHOO],
    )
    assert job.run() == 0
    다가옴 = json.loads(c.execute("SELECT upcoming_json FROM portfolio_summary").fetchone()[0])
    assert [(u["ticker"], u["deadline"], u["d_day"], u["basis"]) for u in 다가옴] == [
        ("NEWCO", "2026-11-05", 9, "yahoo")
    ]
    assert 다가옴[0]["fiscal_year_end"] is None


def test_종목_분석의_다음_실적_발표는_법정_기한_추정도_읽는다() -> None:
    """25.1827 (결함 회차 [참고]) — `event_type = '실적발표'` 만 골라 추정 행('실적발표(분기)')을 못 읽었다"""
    from batch.jobs import verdicts

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 't', 't')"
    )
    c.execute(
        "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
        " weights_json, rank_in_market, calc_version, created_at) VALUES (1, '2026-10-07', 60, '{}', 0, '{}', 1,"
        " 10, 't')"
    )
    mem.batch([("INSERT INTO earnings_calendar (stock_id, event_type, scheduled_date, is_confirmed, note, source,"
                " fetched_at) VALUES " + ", ".join(["(?, ?, ?, ?, ?, ?, ?)"] * len(rows)), [x for r in rows for x in r])
               for rows in [ec.rows_for(1, "2025-12-31", date(2026, 10, 8), "t")] if rows])  # fmt: skip
    mem.batch(verdicts.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    assert d["earnings"]["source"] == ec.SOURCE
    assert any(r.startswith("다음 실적 발표 ") and "법정 기한(늦어도)" in r for r in d["reasons"])
