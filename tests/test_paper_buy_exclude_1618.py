"""매수 배분 감사 [필수] (docs/paper.md 2.1, docs/infra.md 25.1618).

지키는 것: 판 뒤 쉼·기업행위 멈춤 종목은 **배분 전에** 빠진다 — 그 몫이 여력·섹터 자리를 먼저 차지해 살 수 있는 다른
후보가 "섹터 상한 도달"·"투자 여력 부족" 으로 빠지지 않는다. 원장에는 쉼은 skip, 멈춤은 blocked(예전 글 그대로)
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _신호


@pytest.fixture(autouse=True)
def _고른대로(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)


def _두_후보() -> list[report_picks.SignalRow]:
    return [_신호(), dataclasses.replace(_신호(), stock_id=3, ticker="000660", name="종목3")]  # 둘 다 '전자'·10%


def test_쉼_종목이_섹터_자리를_먹지_않는다() -> None:
    v = pp.Valuation(invested_krw=1e6, cash_krw=1e6, holdings_krw=0, equity_krw=1e6, pnl_krw=0, pnl_pct=0, rows=[])
    설정 = {"max_weight_per_sector": 10.0, "max_weight_per_stock": 10.0, "min_order_amount": 0}
    주문, 기록 = job.buy_orders(Client(accounts=("B",)), market="KR", account="B", account_fills=[], valuation=v,
                            open_buys=[], signal_rows=_두_후보(), settings=설정, price_series={}, series_unknown={},
                            fx_rate=None, stop_ids=set(), sold_recently={1}, stock_cache={})  # fmt: skip
    assert sorted({o["stock_id"] for o in 주문}) == [3]
    결정 = {d["stock_id"]: (d["kind"], d["reason"]) for d in 기록["_decisions"]}
    assert 결정 == {3: ("buy", "2부 배분"), 1: ("skip", f"판 뒤 {pp.REBUY_COOLDOWN_SESSIONS}거래일 안")}


def test_멈춘_종목이_섹터_자리를_먹지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: {1})
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date(2026, 10, 15))
    c = Client(accounts=("B",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-13', 1000000, 'deposit', '2026-10-13T00:00:00+00:00', 'k1')")  # fmt: skip
    설정 = {"max_weight_per_sector": 10.0, "max_weight_per_stock": 10.0, "min_order_amount": 0}
    job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=_두_후보(), settings=설정,
                 price_series={}, series_unknown={}, allocate=True,
                 now=datetime.fromisoformat("2026-10-14T23:30:00+00:00"))  # fmt: skip
    assert {r[0] for r in c.q("SELECT stock_id FROM paper_orders")} == {3}
    assert sorted(c.q("SELECT stock_id, kind, reason FROM paper_decisions")) == [
        (1, "blocked", "위험 점검: 기업행위 경계(분할·병합 의심)"), (3, "buy", "2부 배분")]
