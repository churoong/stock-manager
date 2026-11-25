"""KIS 잔고 대보기 — 연결 뒤 다 팔고 다시 산 종목은 "연결 전 보유" 로 숨지 않는다 (docs/infra.md 25.2349 — 11-24 결함
루프 건너뛴 ⓐ). 25.1564 의 "연결 뒤 산 가상 보유는 늘 대 본다" 를 지킨다.
"""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from tests.test_kis_vts_1475 import _at, _Sess, c  # noqa: F401
from tests.test_paper_job_1445 import Client


def test_한_번이라도_0_이_되면_뺀다() -> None:
    시작 = {3: 2, 4: 5}
    체결 = [{"stock_id": 3, "side": "sell", "quantity": 2}, {"stock_id": 3, "side": "buy", "quantity": 2},
          {"stock_id": 4, "side": "sell", "quantity": 3}, {"stock_id": 9, "side": "sell", "quantity": 1}]  # fmt: skip
    assert job.emptied_after_link(시작, 체결) == {3}  # 4 는 2 남음, 9 는 연결 전 보유가 아님
    assert job.emptied_after_link({}, 체결) == set()


def _체결(c: Client, oid: int, side: str, 날: str) -> None:  # noqa: F811
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, created_at)"
                   " VALUES (?, 'B', 'KR', 3, ?, 'market', 2, 'KRW', ?, ?, 'r', 1, 'filled', 't')",
                   [oid, side, 날, 날])  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (?, 'B', 3, ?, ?, 100, 2, 'KRW', 1, 0, 0, 0, 0, 't')", [oid, side, 날])  # fmt: skip


def test_연결_뒤_다_팔고_다시_산_몫의_거부는_어긋남으로(c: Client) -> None:  # noqa: F811
    """연결 전(10-14) 2주 → 연결 뒤(10-15) 다 팖 → 10-16 다시 2주(가상 참, KIS 거부). 예전엔 연결 때 집합에 3 이 있어
    `before_link` 로 빠지고 어긋남 0 이었다"""
    c.conn.execute("DELETE FROM paper_orders WHERE id = 102")  # 3 을 파는 기본 주문은 뺀다
    _체결(c, 191, "buy", "2026-10-14")
    _체결(c, 192, "sell", "2026-10-15")
    _체결(c, 193, "buy", "2026-10-16")
    # 연결날 = 첫 베낀 줄의 날 — 10-15 매도를 베낀 줄(체결)로 연결한다
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, trade_date, reason, status, source, created_at)"
                   " VALUES ('kis_vts', 192, 'B', 'KR', 3, '000660', 'sell', 'market', 2, '2026-10-15', '베낌',"
                   " 'filled', 't', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, trade_date, reason, status, message, source, created_at)"
                   " VALUES ('kis_vts', 193, 'B', 'KR', 3, '000660', 'buy', 'market', 2, '2026-10-16', '베낌',"
                   " 'rejected', 'EGW00201 초당 거래건수 초과', 't', 't')")  # fmt: skip
    assert c.q("SELECT MIN(trade_date) FROM paper_broker_orders WHERE account_id = 'B'")[0][0] == "2026-10-15"
    기록 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 기록["diff"] == 1 and 기록.get("before_link", 0) == 0


def test_연결_뒤에도_계속_든_종목은_그대로_연결_전_보유(c: Client) -> None:  # noqa: F811
    """25.1564 시험과 같은 꼴 — 0 을 거치지 않았으면 그대로 빼 둔다"""
    c.conn.execute("DELETE FROM paper_orders WHERE id = 102")
    _체결(c, 191, "buy", "2026-10-14")
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, trade_date, reason, status, source, created_at)"
                   " VALUES ('kis_vts', NULL, 'B', 'KR', 1, '005930', 'buy', 'market', 1, '2026-10-15', '베낌',"
                   " 'rejected', 't', 't')")  # fmt: skip  # 연결날 10-15
    기록 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 기록["diff"] == 0 and 기록["before_link"] == 1
