"""미국 경로 감사 [필수] (docs/paper.md 3·4장, docs/infra.md 25.1626).

지키는 것: 같은 평가 행 안에서 그림자 지수(같은 돈 KOSPI·S&P500)의 지금 값은 보유와 같은 시점 — 그 시장 보유가 쓴 마지막
시세 날짜 이하의 지수 종가 × 평가와 같은 환율
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, 설정


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    c = Client(accounts=("A",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    return c


def 시세(c, sid, day, o, cl, cur):
    c.conn.execute("INSERT OR REPLACE INTO prices (stock_id, date, open, high, low, close, volume, currency, source,"
                   " fetched_at) VALUES (?, ?, ?, ?, ?, ?, 1000, ?, 't', 't')", [sid, day, o, max(o, cl), min(o, cl), cl, cur])


def 환율(c, day, rate):
    c.conn.execute("INSERT OR REPLACE INTO fx_rates (pair, date, rate, source, fetched_at)"
                   " VALUES ('USDKRW', ?, ?, 't', 't')", [day, rate])


def 지수(c, code, day, v):
    c.conn.execute("INSERT OR REPLACE INTO index_prices (index_code, date, close, source, fetched_at)"
                   " VALUES (?, ?, ?, 't', 't')", [code, day, v])


def 주문(c, market, sid, limit, qty, cur, decided, session):
    c.conn.execute(
        "INSERT INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, fill_basis, tranche, reason, rule_version, created_at)"
        " VALUES ('A', ?, ?, 'buy', 'limit', ?, ?, ?, ?, ?, 'open', 1, 't', 1, 't')",
        [market, sid, limit, qty, cur, decided, session])


def 돌림(c, monkeypatch, market, trade_date, kst, us, now):
    monkeypatch.setattr(cal, "local_today", lambda m=None: date.fromisoformat(kst if (m or "KR") == "KR" else us))
    return job.run_step(c, market=market, trade_date=trade_date, late=False, signal_rows=[], settings=설정,
                        price_series={}, series_unknown={}, allocate=False, now=datetime.fromisoformat(now))


def 행(c, as_of):
    r = c.conn.execute("SELECT cash_krw, holdings_krw, equity_krw, twr_index, fills_today, recon_ok, recon_note,"
                       " updated_at, sp500_krw, kospi_krw FROM paper_daily WHERE account_id='A' AND as_of=?",
                       [as_of]).fetchone()
    return r


def test_저녁_행의_kospi_그림자는_보유와_같은_날(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """저녁(미국 배치) 행: KR 보유는 D-1 종가인데 같은 돈 KOSPI 는 D 지수 — 같은 행 안에서 날짜가 다르다."""
    import json
    # 첫 입금 10-14 → KOSPI 10-14 = 100 에 산 그림자. 계좌는 10-15 장에 국내 종목을 100만 원어치(100주 × 10,000) 산다
    지수(c, "KOSPI", "2026-10-14", 100.0)
    주문(c, "KR", 1, 10_000, 99, "KRW", "2026-10-14", "2026-10-15")
    돌림(c, monkeypatch, "KR", "2026-10-14", "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    # 10-16 아침 국내: 10-15 국내 시세(종목 10,000, KOSPI 100 — 그대로)
    시세(c, 1, "2026-10-15", 10_000, 10_000, "KRW")
    지수(c, "KOSPI", "2026-10-15", 100.0)
    돌림(c, monkeypatch, "KR", "2026-10-15", "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    # 10-16 저녁 미국 배치: 지수 수집이 확정된 KOSPI 10-16(+10%)을 넣는다. 국내 종목 10-16 종가는 내일 아침에야 온다
    지수(c, "KOSPI", "2026-10-16", 110.0)
    돌림(c, monkeypatch, "US", "2026-10-15", "2026-10-16", "2026-10-16", "2026-10-16T12:30:00+00:00")
    e = c.conn.execute("SELECT equity_krw, kospi_krw, positions_json FROM paper_daily WHERE as_of='2026-10-16'").fetchone()
    assert [p["price_date"] for p in json.loads(e[2])] == ["2026-10-15"]
    assert e[1] == pytest.approx(1_000_000)  # 보유와 같은 10-15 지수 — 예전엔 10-16 지수(+10%)로 1,100,000
    # 다음 아침: 국내 종목 10-16 종가 +10%
    시세(c, 1, "2026-10-16", 11_000, 11_000, "KRW")
    돌림(c, monkeypatch, "KR", "2026-10-16", "2026-10-17", "2026-10-16", "2026-10-16T23:30:00+00:00")
    n = 행(c, "2026-10-17")
    assert n[9] == pytest.approx(1_100_000) and n[2] == pytest.approx(1_098_852)


def test_sp500_그림자는_평가와_같은_환율(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """미국 보유는 오늘(한국 날짜) 환율로, 같은 돈 S&P500 은 지수 날짜 환율로 — 환율 움직임이 한쪽에만."""
    지수(c, "SP500", "2026-10-14", 1000.0)
    환율(c, "2026-10-14", 1300)
    # 10-14 장에 100만 원 어치(≈ 7.69주 → 7주) 대신: 7주 × 100달러
    주문(c, "US", 2, 100, 7, "USD", "2026-10-13", "2026-10-14")
    돌림(c, monkeypatch, "US", "2026-10-13", "2026-10-14", "2026-10-14", "2026-10-14T12:30:00+00:00")
    시세(c, 2, "2026-10-14", 100, 100, "USD")
    지수(c, "SP500", "2026-10-15", 1000.0)  # 지수 그대로
    시세(c, 2, "2026-10-15", 100, 100, "USD")  # 종목도 그대로
    환율(c, "2026-10-15", 1300)
    환율(c, "2026-10-16", 1430)  # 오늘(한국 10-16) 환율 +10%
    돌림(c, monkeypatch, "US", "2026-10-15", "2026-10-16", "2026-10-16", "2026-10-16T12:30:00+00:00")
    r = c.conn.execute("SELECT invested_krw, equity_krw, holdings_krw, sp500_krw, positions_json FROM paper_daily"
                       " WHERE as_of='2026-10-16'").fetchone()
    assert r[3] == pytest.approx(1_100_000)  # 평가와 같은 오늘 환율 — 예전엔 지수 날짜 환율이라 1,000,000 그대로


def test_늦게_연_계좌의_여는_날_kospi_그림자(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # 25.1627
    지수(c, "KOSPI", "2026-10-14", 100.0)
    지수(c, "KOSPI", "2026-10-15", 100.0)
    시세(c, 1, "2026-10-15", 10_000, 10_000, "KRW")
    # 계좌 A 는 10-16 에 열었다(입금은 10-14) — 열기 전 입금은 연 날 지수로 산 것으로
    c.conn.execute("UPDATE paper_accounts SET created_at='2026-10-16T01:00:00+00:00' WHERE id='A'")
    돌림(c, monkeypatch, "KR", "2026-10-15", "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    아침 = 행(c, "2026-10-16")[9]
    지수(c, "KOSPI", "2026-10-16", 110.0)  # 저녁: KOSPI 10-16 확정, 국내 시세는 아직 10-15
    돌림(c, monkeypatch, "US", "2026-10-15", "2026-10-16", "2026-10-16", "2026-10-16T12:30:00+00:00")
    저녁 = 행(c, "2026-10-16")
    print("아침 kospi_krw", 아침, "저녁 kospi_krw", 저녁[9], "equity", 저녁[2], "invested?")
    assert 저녁[9] == pytest.approx(1_000_000)  # 연 날 행: 평가 = 넣은 돈이어야


def test_늦게_연_계좌의_여는_날_sp500_그림자(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # 25.1627
    for d in ("2026-10-14", "2026-10-15", "2026-10-16"):
        환율(c, d, 1300)
    지수(c, "SP500", "2026-10-14", 1000.0)
    지수(c, "SP500", "2026-10-15", 1000.0)
    시세(c, 2, "2026-10-15", 100, 100, "USD")
    시세(c, 1, "2026-10-16", 10_000, 10_000, "KRW")
    c.conn.execute("UPDATE paper_accounts SET created_at='2026-10-16T01:00:00+00:00' WHERE id='A'")
    지수(c, "SP500", "2026-10-16", 1100.0)  # 미국 10-16 장 지수는 한국 10-17 아침 전에 들어옴, 미국 종목 시세는 아직 10-15
    환율(c, "2026-10-17", 1300)
    돌림(c, monkeypatch, "KR", "2026-10-16", "2026-10-17", "2026-10-16", "2026-10-16T23:30:00+00:00")
    r = 행(c, "2026-10-17")
    print("sp500_krw", r[8])
    assert r[8] == pytest.approx(1_000_000)
