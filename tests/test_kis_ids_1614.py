"""25.1613 교차검증 [권고] 반영 (docs/paper.md 15장, docs/infra.md 25.1614).

지키는 것(같은 티커 두 행 — 옛 행 30 NYSE excluded, 새 행 2 NASDAQ active)
- 옛 행 매도가 KIS 에선 찼는데 기록이 0(`unknown`)이어도 H 가 든 새 행 주식을 정리로 팔지 않는다
- 주문가능이 모자란 날 베낀 매도(이긴 행)가 먼저 주문가능을 받는다
- 정리 매도 줄·연결 전 보유는 어긋남·주문을 만들지 않는다
"""

from __future__ import annotations

import json

from batch.jobs import broker_mirror as job
from batch.sources import kis_vts as kv
from tests.test_kis_ids_1608 import _두_행
from tests.test_kis_vts_us_1485 import _kst, _UsSess, c  # noqa: F401
from tests.test_paper_job_1445 import Client


def _bo(c: Client, sid: int, side: str, status: str, filled: int | None, qty: int, day: str,  # noqa: F811
        oid: int | None, reason: str = "베낌") -> None:
    c.conn.execute(
        "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
        " limit_price, quantity, trade_date, reason, status, filled_qty, source, created_at) VALUES ('kis_vts_us', ?,"
        " 'B', 'US', ?, 'AAPL', ?, 'limit', 200, ?, ?, ?, ?, ?, 't', 't')",
        [oid, sid, side, qty, day, reason, status, filled])


def _fill(c: Client, oid: int, sid: int, side: str, qty: int, day: str) -> None:  # noqa: F811
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, tranche, created_at, status) VALUES (?, 'B', 'US', ?, ?,"
        " 'limit', 200, ?, 'USD', ?, ?, 'r', 1, ?, 't', 'filled')", [oid, sid, side, qty, day, day, oid])
    c.conn.execute(
        "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
        " fee, tax, slippage, cash_krw, created_at) VALUES (?, 'B', ?, ?, ?, 200, ?, 'USD', 1400, 0, 0, 0, 0, 't')",
        [oid, sid, side, day, qty])


def _run(c: Client, qty: int, sellable: int | None = None) -> tuple:  # noqa: F811
    s = _UsSess(holdings=[kv.Holding("AAPL", qty, qty if sellable is None else sellable, 200.0)])
    기록 = job.run(c, "US", _kst("21:27"), session=s, clock=lambda: _kst("21:27"))
    diff = json.loads(c.q("SELECT diff_json FROM paper_broker_daily")[0][0])
    return [(o[0], o[2], o[3]) for o in s.orders], 기록.get("not_sent"), diff


def _base(c: Client) -> None:  # noqa: F811
    # 옛 행 30(NYSE, excluded): H·KIS 3주 / 새 행 2(NASDAQ, active): H·KIS 2주. 오늘 매도 없음
    _두_행(c, 30, "NYSE", 2, "NASDAQ")
    c.conn.execute("DELETE FROM paper_orders WHERE id = 301")
    _fill(c, 310, 2, "buy", 2, "2026-10-15")
    _bo(c, 2, "buy", "filled", 2, 2, "2026-10-15", 310)


def test_2_정리매도_null(c: Client) -> None:  # noqa: F811
    # 옛 행은 예전에 정리(paper_order_id NULL)로 다 팔았다. H 도 옛 행 0. KIS 2주(새 행) → 할 일 없음
    _base(c)
    _fill(c, 320, 30, "sell", 3, "2026-10-14")
    _bo(c, 30, "sell", "filled", 3, 3, "2026-10-15", None, "정리")
    assert _run(c, 2) == ([], None, {})


def test_5_연결전_보유(c: Client) -> None:  # noqa: F811
    # H 는 연결 전부터 옛 행 3주(KIS 매수 기록 없음). 새 행은 연결 뒤 H·KIS 2주. KIS 2주 → 할 일 없음·어긋남 없음
    _base(c)
    c.conn.execute("DELETE FROM paper_broker_orders WHERE stock_id = 30")
    c.conn.execute("UPDATE paper_fills SET fill_date = '2026-10-01' WHERE stock_id = 30")
    assert _run(c, 2) == ([], None, {})


def test_6_옛행_주문가능_모자람(c: Client) -> None:  # noqa: F811
    # KIS 5주 중 주문가능 2주(나머지 미결제 등). 오늘 H 가 새 행 2주를 판다 — 주문가능이 옛 행에 먼저 붙나
    _base(c)
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, reason, rule_version, tranche, created_at) VALUES (311, 'B',"
                   " 'US', 2, 'sell', 'market', NULL, 2, 'USD', '2026-10-15', '2026-10-16', 'r', 1, 1, 't')")
    assert _run(c, 5, sellable=2)[0] == [("sell", "NASD", 2)]


def test_7_unknown_매도_실제로_참(c: Client) -> None:  # noqa: F811
    # H 는 옛 행 3주를 팔았고 KIS 베낀 매도는 unknown(filled 0)인데 실제론 찼다. KIS 2주(새 행, H 2주)
    _base(c)
    _fill(c, 320, 30, "sell", 3, "2026-10-15")
    _bo(c, 30, "sell", "unknown", 0, 3, "2026-10-15", 320)
    assert _run(c, 2) == ([], None, {})
