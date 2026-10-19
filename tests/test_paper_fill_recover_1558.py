"""체결 행은 들어갔는데 주문만 `open` 으로 끊긴 상태 — 다시 돌면 상태만 맞추고 취소하지 않는다
(docs/paper.md 2.4, docs/infra.md 25.1558 — 25.1547 감사 남은 것).

예전엔 이 복구 검사가 현금·보유 검사 뒤에 있어, 이미 든 체결이 장부에 한 번 더 얹혀 매수는 "현금 부족",
매도는 "팔 보유가 모자람" 으로 체결 행이 있는 주문을 취소했다.
"""

from __future__ import annotations

from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client

날 = "2026-07-02"


def _준비(side: str) -> Client:
    c = Client(accounts=("B",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-06-01', 100100, 'deposit', '2026-06-01T00:00:00+00:00', 'k1')")  # fmt: skip
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (1, ?, 10000, 10000, 10000, 10000, 1000, 'KRW', 't', 't')", [날])  # fmt: skip
    # 매수 10주 10,000 — 현금을 거의 다 쓴다
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, fill_basis, tranche, reason, horizon, status, rule_version,"
                   " created_at) VALUES (1, 'B', 'KR', 1, 'buy', 'limit', 10000, 10, 'KRW', '2026-07-01', ?, 'open', 1,"
                   " 't', 'short', ?, 1, 't')", [날, "open" if side == "buy" else "filled"])  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, horizon, created_at) VALUES (1, 'B', 1, 'buy', ?, 10000, 10,"
                   " 'KRW', 1, 15, 0, 0, -100015, 'short', 't')", [날])  # fmt: skip
    if side == "sell":
        # 전량 매도 — 체결 행은 들어갔고 주문은 open 으로 남았다
        c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                       " decided_on, fill_session, fill_basis, reason, status, rule_version, created_at)"
                       " VALUES (2, 'B', 'KR', 1, 'sell', 'market', 10, 'KRW', '2026-07-01', ?, 'open', '손절', 'open', 1,"
                       " 't')", [날])  # fmt: skip
        c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                       " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (2, 'B', 1, 'sell', ?, 10000, 10,"
                       " 'KRW', 1, 15, 150, 100, 99735, 't')", [날])  # fmt: skip
    return c


def _돌림(c: Client) -> dict:
    flows, _ = job._flows(c)
    fills, _ = job._fills(c)
    기록: dict = {}
    새것 = job.fill_orders(c, "KR", 날, flows, fills, None, None, set(), "t", 기록)
    assert 새것 == []  # 두 번 세지 않는다
    return 기록


def test_매수_체결_행이_있으면_현금_부족으로_취소하지_않는다() -> None:
    c = _준비("buy")
    _돌림(c)
    assert c.q("SELECT status FROM paper_orders WHERE id = 1") == [("filled",)]
    assert c.q("SELECT COUNT(*) FROM paper_fills") == [(1,)]


def test_매도_체결_행이_있으면_보유_모자람으로_취소하지_않는다() -> None:
    c = _준비("sell")
    _돌림(c)
    assert c.q("SELECT status FROM paper_orders WHERE id = 2") == [("filled",)]
    assert c.q("SELECT COUNT(*) FROM paper_fills") == [(2,)]
