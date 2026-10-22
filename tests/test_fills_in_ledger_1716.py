"""주간·월간 요약의 "장부에 든 체결" — 체결일이 아니라 장부에 쓴 시각으로 (docs/infra.md 25.1716, 평가 감사 [권고] 1)."""

from __future__ import annotations

import pytest

from batch.services import paper as pp


def _f(oid: int, d: str) -> pp.Fill:
    return pp.Fill(order_id=oid, account_id="A", stock_id=1, side="buy", fill_date=d, price=1.0, quantity=1,
                   currency="KRW", fx_rate=1.0, fee=0.0, tax=0.0, slippage=0.0, cash_krw=-1.0)  # fmt: skip


def test_늦게_든_체결도_든_주에_센다() -> None:
    """10-29 장 체결이 종목 행을 기다려(25.1713) 11-02 아침 실행(11-01T23:40Z)에 장부에 들었다. 11-08 주간 창은 주초
    행(10-30, 10-29T23:40Z 기록) 뒤 ~ 끝 행(11-06) — 체결일로 가르면 [10-30, 11-06) 밖이라 어느 주에도 안 셌다"""
    fills = [_f(1, "2026-10-29"), _f(2, "2026-10-27")]
    만든 = {1: "2026-11-01T23:40:00+00:00", 2: "2026-10-27T23:40:00+00:00"}
    든 = pp.장부에_든(fills, "2026-10-30", "2026-11-06", 만든, "2026-10-29T23:40:00+00:00", "2026-11-05T23:40:00+00:00")
    assert [f.order_id for f in 든] == [1]
    assert pp.장부에_든(fills, "2026-10-30", "2026-11-06") == []  # 예전 잣대 — 빠졌다
    # 앞 행의 기록 시각을 모르면 예전처럼 체결일
    assert [f.order_id for f in pp.장부에_든(fills, "2026-10-27", "2026-11-06", 만든, None, "x")] == [1, 2]


def test_같은_실행이_쓴_체결은_그_행에_든다() -> None:
    """체결 행과 평가 행은 같은 실행의 같은 시각(`지금`)으로 적힌다 — 끝 행 시각과 같으면 그 행에 든 것"""
    fills = [_f(1, "2026-11-05")]
    만든 = {1: "2026-11-05T23:40:00+00:00"}
    assert len(pp.장부에_든(fills, "a", "b", 만든, "2026-11-04T23:40:00+00:00", "2026-11-05T23:40:00+00:00")) == 1
    assert pp.장부에_든(fills, "a", "b", 만든, "2026-11-05T23:40:00+00:00", "2026-11-06T23:40:00+00:00") == []


def _행(d: str) -> dict:
    return {"account_id": "A", "as_of": d, "invested_krw": 1_000_000, "equity_krw": 1_000_000, "pnl_krw": 0,
            "twr_index": 1.0, "kospi_krw": None, "sp500_krw": None, "positions_json": "[]", "attribution_json": None,
            "updated_at": f"{d}T13:30:00.5+00:00"}  # fmt: skip


def test_첫_달과_다음_달에_두_번_세지_않는다() -> None:
    """25.1719 (25.1716 교차검증 [권고] 1) — 10-16 에 연 계좌(10월 앞 행 없음)의 10-29 장 매도가 11-02 아침 실행에 장부에
    들었다. 10월 복기(11-08 따라잡기)는 체결일, 11월 복기는 기록 시각으로 세어 둘 다에 들었다 — 든 달(11월)에만"""

    def f(oid: int, side: str, d: str, cash: float) -> pp.Fill:
        return pp.Fill(order_id=oid, account_id="A", stock_id=1, side=side, fill_date=d, price=100.0, quantity=10,
                       currency="KRW", fx_rate=1.0, fee=0.0, tax=0.0, slippage=0.0, cash_krw=cash)  # fmt: skip

    fills = [f(1, "buy", "2026-10-19", -1000.0), f(2, "sell", "2026-10-29", 1500.0)]
    만든 = {1: "2026-10-19T23:40:00.100000+00:00", 2: "2026-11-01T23:40:00.100000+00:00"}
    한도 = {"max_stock_pct": 100, "max_sector_pct": 100, "stop_pct": {}}
    시월 = [_행(d) for d in ("2026-10-16", "2026-10-19", "2026-10-20", "2026-10-30", "2026-11-02", "2026-11-06")]
    십일월 = [r for r in 시월 if r["as_of"] >= "2026-10-18"] + [_행("2026-11-30"), _행("2026-12-04")]
    줄10 = pp.summary_lines({"A": 시월}, {"A": fills}, "2026-11-01", "2026-10-01", {1: "종목1"}, {}, 한도, created=만든)
    줄11 = pp.summary_lines({"A": 십일월}, {"A": fills}, "2026-11-29", "2026-11-01", {1: "종목1"}, {}, 한도, created=만든)
    assert not [x for x in 줄10 if "이익 셋" in x]
    assert [x for x in 줄11 if "이익 셋" in x and "종목1" in x]


def test_주간_요약이_기록_시각을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1719 (25.1716 교차검증 [권고] 2) — 배선(`PAPER_SUMMARY_SQL` 의 `updated_at`·`summary_section` 의 `created`·
    주초 행 시각)을 끝에서 끝까지. 10-29 장 매수가 1번 종목 행을 기다려 11-02 아침 실행에 들면 11-08 주간 요약이 센다"""
    from datetime import UTC, date, datetime

    from batch.core import calendar as cal
    from batch.jobs import paper as job
    from tests.test_paper_job_1445 import Client, 설정

    def 시세(c: Client, sid: int, day: str, v: float) -> None:
        c.conn.execute("INSERT OR REPLACE INTO prices (stock_id, date, open, high, low, close, volume, currency, source,"
                       " fetched_at) VALUES (?, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [sid, day, v, v, v, v])

    def 돌림(c: Client, td: str, kst: str, now: str) -> None:
        monkeypatch.setattr(cal, "local_today", lambda m=None: date.fromisoformat(kst))
        job.run_step(c, market="KR", trade_date=td, late=False, signal_rows=[], settings=설정, price_series={},
                     series_unknown={}, allocate=False, now=datetime.fromisoformat(now))  # fmt: skip

    c = Client(accounts=("A",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-20', 1000000, 'deposit', '2026-10-20T00:00:00+00:00', 'k1')")
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c.conn.execute("INSERT INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, fill_basis, tranche, reason, rule_version, created_at)"
                   " VALUES ('A', 'KR', 1, 'buy', 'limit', 10000, 10, 'KRW', '2026-10-28', '2026-10-29', 'open', 1,"
                   " 't', 1, 't')")  # fmt: skip
    for d in ("2026-10-27", "2026-10-28"):
        시세(c, 1, d, 10000)
        시세(c, 3, d, 5000)
    돌림(c, "2026-10-28", "2026-10-29", "2026-10-28T23:40:00+00:00")
    시세(c, 3, "2026-10-29", 5000)  # 1번 행만 빠짐
    돌림(c, "2026-10-29", "2026-10-30", "2026-10-29T23:40:00+00:00")
    for sid, v in ((1, 9900), (3, 5000)):
        시세(c, sid, "2026-10-29", v)
        시세(c, sid, "2026-10-30", v)
    돌림(c, "2026-10-30", "2026-11-02", "2026-11-01T23:40:00+00:00")
    assert c.q("SELECT fill_date FROM paper_fills") == [("2026-10-29",)]
    for td, kst in (("2026-11-02", "2026-11-03"), ("2026-11-03", "2026-11-04"), ("2026-11-04", "2026-11-05"),
                    ("2026-11-05", "2026-11-06")):  # fmt: skip
        시세(c, 1, td, 9900)
        시세(c, 3, td, 5000)
        돌림(c, td, kst, f"{td}T23:40:00+00:00")
    전체 = job.summary_section(c, datetime(2026, 11, 8, 1, tzinfo=UTC))
    i = next(k for k, x in enumerate(전체) if x.startswith("  A 평가 "))  # 계좌마다 두 줄(25.1727)
    assert "이번 주 체결 매수 1·" in 전체[i + 1], 전체
