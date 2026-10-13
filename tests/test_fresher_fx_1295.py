"""표의 마지막 환율이 마지막 체결보다 묵었으면 체결 환율로 평가한다 — `fresher_price` 의 짝 (docs/infra.md 25.1295)."""

from __future__ import annotations

from batch.services import portfolio as pf


def _buy(day: str, fx: float, tid: int = 1) -> pf.Trade:
    return pf.Trade(tid, 2, "buy", day, 255.0, 40, "USD", fx, fee=0)


def test_체결이_더_새것이면_체결_환율() -> None:
    assert pf.fresher_fx(("2026-10-08", 1400.0), [_buy("2026-10-12", 1425.0)]) == ("2026-10-12", 1425.0)


def test_표가_같거나_더_새것이면_표() -> None:
    for day in ("2026-10-12", "2026-10-13"):
        assert pf.fresher_fx((day, 1410.0), [_buy("2026-10-12", 1425.0)]) == (day, 1410.0)


def test_체결_환율을_모르면_표() -> None:
    assert pf.fresher_fx(("2026-10-08", 1400.0), [_buy("2026-10-12", 0.0)]) == ("2026-10-08", 1400.0)


def test_표가_비면_체결_환율() -> None:
    assert pf.fresher_fx((None, None), [_buy("2026-10-12", 1425.0)]) == ("2026-10-12", 1425.0)


def test_평가_잡이_체결_환율을_쓴다(monkeypatch) -> None:
    """잡 배선 — 사자마자 환차손이 뜨지 않는다 (손익 감사 #2 재현)."""
    from datetime import date

    from batch.core import calendar as cal
    from batch.core import db
    from batch.jobs import portfolio as job
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(cal, "user_today", lambda: date(2026, 10, 13))
    c = mem.conn
    c.execute("INSERT INTO stocks (id,ticker,market,country,name_en,currency,status,source,fetched_at)"
              " VALUES (2,'AAPL','NASDAQ','US','Apple','USD','active','t','t')")  # fmt: skip
    for d, px, fx in (("2026-10-07", 250.0, 1395.0), ("2026-10-08", 252.0, 1400.0)):
        c.execute("INSERT INTO prices (stock_id,date,close,currency,source,fetched_at) VALUES (2,?,?,'USD','t','t')",
                  [d, px])  # fmt: skip
        c.execute("INSERT INTO fx_rates (pair,date,rate,source,fetched_at) VALUES ('USDKRW',?,?,'t','t')", [d, fx])
    c.execute("INSERT INTO trades (id,stock_id,side,trade_date,price,quantity,currency,fx_rate,fx_rate_source,fee,tax,"
              "horizon,created_at,updated_at) VALUES (1,2,'buy','2026-10-12',255.0,40,'USD',1425.0,'manual',0,NULL,"
              "'long','t','t')")  # fmt: skip
    job.run()
    fx_now, fx_date, fx_krw = c.execute("SELECT fx_now, fx_date, unrealized_fx_pnl_krw FROM positions").fetchone()
    assert (fx_now, fx_date) == (1425.0, "2026-10-12") and fx_krw == 0
