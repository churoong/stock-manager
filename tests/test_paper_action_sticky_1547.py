"""보유 중 기업행위(분할·병합) — 경계 창(20 거래일)이 지나도 장부 수량을 고치기 전까지 멈춤을 이어 간다
(docs/paper.md 2.3, docs/infra.md 25.1547). 실제 마이그레이션을 올린 메모리 SQLite(`test_paper_job_1445.Client`)."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client


def _날들(n: int) -> list[str]:
    out, d = [], date(2026, 6, 2)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d = date.fromordinal(d.toordinal() + 1)
    return out


날 = _날들(40)
분할 = 5


def _시세(c: Client, upto: int, 뒤: float) -> None:
    for i, day in enumerate(날[: upto + 1]):
        if c.q("SELECT 1 FROM prices WHERE stock_id = 1 AND date = ?", [day]):
            continue
        close = 10_000 if i < 분할 else 뒤
        c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at,"
                       " change_pct) VALUES (1, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't', 0.0)",
                       [day, close, close, close, close])  # fmt: skip
        c.conn.execute("INSERT OR IGNORE INTO index_prices (index_code, date, close, source, fetched_at)"
                       " VALUES ('KOSPI', ?, 2500, 't', 't')", [day])  # fmt: skip


def _장부(첫매수: str) -> Client:
    c = Client(accounts=("B",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-06-01', 1000000, 'deposit', '2026-06-01T00:00:00+00:00', 'k1')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, fill_basis, tranche, reason, horizon, status, rule_version, created_at)"
                   " VALUES (1, 'B', 'KR', 1, 'buy', 'limit', 10000, 10, 'KRW', ?, ?, 'open', 1, 't', 'short', 'filled', 1,"
                   " 't')", [첫매수, 첫매수])  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, horizon, created_at) VALUES (1, 'B', 1, 'buy', ?, 10000, 10,"
                   " 'KRW', 1, 0, 0, 0, -100000, 'short', '2026-06-02T00:00:00+00:00')", [첫매수])  # fmt: skip
    return c


def _돌림(c: Client, monkeypatch: pytest.MonkeyPatch, i: int) -> dict:
    trade = 날[i]
    다음 = cal.next_session("KR", date.fromisoformat(trade))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    _줄, 기록 = job.run_step(c, market="KR", trade_date=trade, late=False, signal_rows=[], settings=None, price_series={},
                            series_unknown={}, allocate=False,
                            now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"))  # fmt: skip
    return 기록


@pytest.mark.parametrize("뒤", [5_000.0, 100_000.0], ids=["2:1 분할", "1:10 병합"])
def test_분할_전에_산_보유는_창이_지나도_멈춘다(monkeypatch: pytest.MonkeyPatch, 뒤: float) -> None:
    c = _장부(날[0])
    _시세(c, 분할 + 2, 뒤)
    _돌림(c, monkeypatch, 분할 + 2)  # 경계 창 안 — 멈추고 표에 적는다
    assert json.loads(c.q("SELECT value FROM settings WHERE key = ?", [job.ACTION_STICKY_KEY])[0][0]) == {"1": 날[분할]}
    _시세(c, 분할 + 22, 뒤)
    기록 = _돌림(c, monkeypatch, 분할 + 22)  # 창 밖(21행 넘게 지남)
    assert 기록.get("action_sticky") == [1]
    # 거짓 손절(−50%)·거짓 목표도달(+900%) 매도가 나지 않는다 — 평가는 원가(모름)
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE side = 'sell'") == [(0,)]


def test_분할_뒤에_산_보유는_적지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _장부(날[분할 + 1])  # 분할 뒤에 샀다 — 수량이 맞다
    _시세(c, 분할 + 2, 5_000.0)
    _돌림(c, monkeypatch, 분할 + 2)
    assert c.q("SELECT value FROM settings WHERE key = ?", [job.ACTION_STICKY_KEY]) in ([], [("{}",)])


def test_다_팔린_종목은_표에서_지운다() -> None:
    c = Client(accounts=("B",))
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, 't')",
                   [job.ACTION_STICKY_KEY, json.dumps({"1": "2026-06-09", "2": "2026-06-09"})])  # fmt: skip
    멈춤, 표 = job.sticky_frozen(c, set(), {1: "2026-06-02"}, {1: "KR"})
    assert 멈춤 == {1} and 표 == {"1": "2026-06-09"}


def test_결정_원장_성과는_분할만_반영_계열로() -> None:
    """분할 전 기준 종가(10,000)를 분할 뒤 원 종가(5,000)와 견주면 −50% — 수정주가(분할 전 행도 5,000)로 같은 단위 (25.1547)"""
    c = _장부(날[0])
    for i, day in enumerate(날[:30]):
        close = 10_000 if i < 분할 else 5_000
        c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, adj_close, volume, currency, source,"
                       " fetched_at) VALUES (1, ?, ?, ?, ?, ?, 5000, 1000, 'KRW', 't', 't')",
                       [day, close, close, close, close])  # fmt: skip
        c.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, 2500,"
                       " 't', 't')", [day])  # fmt: skip
    c.conn.execute("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
                   " created_at, rule_version) VALUES ('B', 'KR', ?, 1, 'buy', 't', 10000, ?, 't', 1)",
                   [날[0], 날[0]])  # fmt: skip
    job.fill_outcomes(c, "KR", 날[29], "t")
    ret5, ret20 = c.q("SELECT ret_5d, ret_20d FROM paper_decisions")[0]
    assert ret5 == pytest.approx(0.0) and ret20 == pytest.approx(0.0)


def test_성과_분해_진입_칸은_지금_판의_체결만() -> None:
    """판 1 체결이 판 2 성과 분해의 진입 칸에 섞이지 않는다 (25.1547 감사 [참고] 4)"""
    c = Client(accounts=("B",))
    for oid, 판, 체결가 in ((1, 1, 100.0), (2, 2, 200.0)):
        c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                       " decided_on, fill_session, reason, rule_version, status, created_at)"
                       " VALUES (?, 'B', 'KR', ?, 'buy', 'market', 1, 'KRW', '2026-10-01', '2026-10-02', 'r', ?, 'filled', 't')",
                       [oid, oid, 판])  # fmt: skip
        c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                       " fx_rate, fee, tax, slippage, cash_krw, created_at, day_close)"
                       " VALUES (?, 'B', ?, 'buy', '2026-10-02', ?, 1, 'KRW', 1, 0, 0, 0, -1, 't', ?)",
                       [oid, oid, 체결가, 체결가 * 1.01])  # fmt: skip
    c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
                   " twr_index, positions_json, recon_ok, updated_at) VALUES ('B', '2026-10-19', 1, 1, 0, 1, 0, 1, '[]', 1, 't')")  # fmt: skip
    a = job.save_attribution(c, "B", "2026-10-19", rule_version=2)
    assert a["entry"]["n"] == 1



def test_경계_안_실행을_놓쳐도_수집_감지_기록으로_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    """가상모의투자 단계가 경계 창(21행) 내내 못 돌았어도 일일 수집이 남긴 감지 기록으로 멈춘다 (25.1548 반박 [권고] 1)"""
    from batch.services import adjust_drift as adjd

    c = _장부(날[0])
    _시세(c, 분할 + 22, 5_000.0)  # 창 안 실행 없이 창 밖으로
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, 't')",
                   [adjd.KR_ACTION_LOG_KEY, json.dumps({"1": 날[분할]})])  # fmt: skip
    기록 = _돌림(c, monkeypatch, 분할 + 22)
    assert 기록.get("action_sticky") == [1]
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE side = 'sell'") == [(0,)]


def test_멈춤은_분할_전에_산_계좌만() -> None:
    """한 계좌가 분할 전에 샀다고 다른 계좌의 새 주문까지 막지 않는다 (25.1548 반박 [권고] 2)"""
    c = Client(accounts=("A", "B"))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-06-01', 1000000, 'deposit', '2026-06-01T00:00:00+00:00', 'k1')")  # fmt: skip
    for oid, aid in ((1, "A"), (2, "B")):
        c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                       " decided_on, fill_session, reason, rule_version, status, created_at)"
                       " VALUES (?, ?, 'KR', 1, 'buy', 'market', 1, 'KRW', '2026-07-01', '2026-07-02', 'r', 1, 'open', 't')",
                       [oid, aid])  # fmt: skip
    기록: dict = {}
    job.fill_orders(c, "KR", "2026-07-02", [], [], None, None, set(), "t", 기록, frozen_by_account={"A": {1}})
    상태 = dict(c.q("SELECT account_id, status FROM paper_orders ORDER BY id"))
    assert 상태["A"] == "expired" and 상태["B"] != "expired"


def test_미국_분할_재수집_대기_종목은_성과를_미룬다() -> None:
    c = _장부(날[0])
    for i, day in enumerate(날[:30]):
        c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                       " VALUES (2, ?, 100, 100, 100, ?, 1000, 'USD', 't', 't')", [day, 100 if i < 20 else 50])  # fmt: skip
        c.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('SP500', ?, 5000,"
                       " 't', 't')", [day])  # fmt: skip
    c.conn.execute("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
                   " created_at, rule_version) VALUES ('B', 'US', ?, 2, 'buy', 't', 100, ?, 't', 1)", [날[0], 날[0]])  # fmt: skip
    c.conn.execute("INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
                   " VALUES (2, '2026-07-01', '2026-07-01', 1.0, 1.0)")  # fmt: skip
    job.fill_outcomes(c, "US", 날[29], "t")
    assert c.q("SELECT ret_5d, ret_20d FROM paper_decisions") == [(None, None)]
    c.conn.execute("UPDATE adjust_refresh_queue SET done_at = 't'")
    job.fill_outcomes(c, "US", 날[29], "t")
    assert c.q("SELECT ret_5d FROM paper_decisions")[0][0] == pytest.approx(0.0)


def test_기준일_수정주가가_비어도_앞_행으로_메운다() -> None:
    """창 맨 앞(기준일) 수정주가 행이 비면 원 종가가 들어가 −50% 였다 (25.1548 반박 [참고] 5)"""
    c = _장부(날[0])
    for i, day in enumerate(날[:30]):
        close = 10_000 if i < 분할 else 5_000
        adj = None if i == 3 else 5000  # 기준일(날[3]) 행만 수정주가가 비었다
        c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, adj_close, volume, currency, source,"
                       " fetched_at) VALUES (1, ?, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')",
                       [day, close, close, close, close, adj])  # fmt: skip
        c.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, 2500,"
                       " 't', 't')", [day])  # fmt: skip
    c.conn.execute("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
                   " created_at, rule_version) VALUES ('B', 'KR', ?, 1, 'buy', 't', 10000, ?, 't', 1)",
                   [날[3], 날[3]])  # fmt: skip
    job.fill_outcomes(c, "KR", 날[29], "t")
    assert c.q("SELECT ret_5d FROM paper_decisions")[0][0] == pytest.approx(0.0)
