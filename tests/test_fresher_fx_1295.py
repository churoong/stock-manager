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


def test_날짜별_평가액도_체결_환율() -> None:
    """TWR 계열도 같은 규칙 — 산 날 지수에 묵은 환율만큼 가짜 환차손이 들지 않는다 (25.1308)."""
    # 기존 미국 보유 10주(10-01 $100·1,400원) + 10-12 에 한 주 더($110·1,425원). 환율 표는 10-08(1,400) 까지
    trades = [pf.Trade(1, 2, "buy", "2026-10-01", 100.0, 10, "USD", 1400.0, fee=0),
              pf.Trade(2, 2, "buy", "2026-10-12", 110.0, 1, "USD", 1425.0, fee=0)]  # fmt: skip
    closes = {2: {"2026-10-01": 100.0, "2026-10-08": 110.0, "2026-10-12": 110.0}}
    fx = {"USD": {"2026-10-01": 1400.0, "2026-10-08": 1400.0}}
    rows, _ = pf.value_series(trades, [], closes, fx, "2026-10-12", {"USD": pf.CostRates(0, 0, None, has_sell_tax=False)})
    마지막 = rows[-1]
    assert 마지막.date == "2026-10-12"
    assert abs(마지막.value_krw - 11 * 110.0 * 1425.0) < 1e-6


def test_자동으로_채운_환율은_체결_환율이_아니다() -> None:
    """웹이 비운 환율 칸에 넣은 표 값(auto)은 이미 표에 제 날짜로 있다 — 체결일 날짜를 붙이면 묵음 경고가 사라진다 (25.1353)."""
    자동 = pf.Trade(1, 2, "buy", "2026-10-07", 255.0, 1, "USD", 1400.0, fee=0, fx_rate_source="auto")
    assert pf.fresher_fx(("2026-10-01", 1400.0), [자동]) == ("2026-10-01", 1400.0)
    # 증권사 체결 환율(manual)과 출처를 모르는 옛 행은 예전처럼
    수동 = pf.Trade(1, 2, "buy", "2026-10-07", 255.0, 1, "USD", 1425.0, fee=0, fx_rate_source="manual")
    assert pf.fresher_fx(("2026-10-01", 1400.0), [수동]) == ("2026-10-07", 1425.0)
    assert pf.fresher_fx(("2026-10-01", 1400.0), [_buy("2026-10-07", 1425.0)]) == ("2026-10-07", 1425.0)
    # 묵음 경고가 다시 뜬다 — 10-14 기준 표 환율 13일 전
    pos = pf.Position(2, 1, "USD", 255.0, 1400.0, 255.0, 357000.0, "2026-10-07", None)
    pf.valuate(pos, 260.0, "2026-10-13", 1400.0, pf.fresher_fx(("2026-10-01", 1400.0), [자동])[0])
    assert any("환율" in w for w in pf.stale_notes([pos], "2026-10-14"))
