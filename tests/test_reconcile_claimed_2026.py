"""번호 없는 줄이 이미 닫힌 줄의 체결을 다시 잡지 않는다 (25.2026, 25.2016 KIS 결함 루프 [참고] ③).

`bm.reconcile` 의 `쓴` 집합은 호출 하나 안에서만 산다. 쪽 상한으로 잘린 맞추기(`complete=False`)가 한 줄만 닫고 다른
번호 없는 줄을 남기면, 다음 날 맞추기에서 그 줄이 같은 날·종목·방향·수량인 **이미 다른 줄이 잡은** 체결을 다시 잡았다 —
실현손익·일치율이 한 체결을 두 번 센다. 이제 배치가 맞추기 창 안의 닫힌 줄 번호(`CLAIMED_SQL`)를 `claimed` 로 넘긴다
"""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from tests.test_kis_vts_1475 import _at, _Sess, c  # noqa: F401 — c 는 픽스처
from tests.test_paper_job_1445 import Client

E = kv.Execution


def _줄(**kw: object) -> dict:
    return {"id": 1, "code": "005930", "side": "sell", "quantity": 3, "trade_date": "2026-10-16",
            "broker_order_no": None, "limit_price": None} | kw  # fmt: skip


def test_닫힌_줄의_번호는_번호_없는_줄이_잡지_않는다() -> None:
    ex = [E("20261016", "0005", "005930", "sell", 3, 3, 9_800.0, 0, False)]
    # 예전 꼴(claimed 없음) — 잡는다
    assert [o.status for o in bm.reconcile([_줄()], ex)] == ["filled"]
    # 전날 닫힌 줄이 이미 그 번호를 가졌다 — 앞 0·날짜 꼴이 달라도 같은 체결로 본다
    got = bm.reconcile([_줄()], ex, claimed=[("2026-10-16", "5")])
    assert [(o.status, o.order_no, o.message) for o in got] == [
        ("unknown", None, "체결 조회에 없음 — 같은 꼴 체결(번호 0005)은 다른 줄이 이미 가져감")
    ]
    # 같은 꼴 체결이 아예 없으면 예전 글 그대로
    got = bm.reconcile([_줄(quantity=4)], ex, claimed=[("2026-10-16", "5")])
    assert [o.message for o in got] == ["체결 조회에 없음"]
    # 잘린 맞추기면 닫지 않고 다음 날 다시 본다
    assert bm.reconcile([_줄()], ex, complete=False, claimed=[("2026-10-16", "5")]) == []
    # 다른 날 같은 번호는 상관없다, 번호 없는 닫힌 줄(None)은 아무것도 막지 않는다
    assert [o.status for o in bm.reconcile([_줄()], ex, claimed=[("2026-10-15", "5"), ("2026-10-16", None)])] == [
        "filled"
    ]


def test_번호_있는_줄은_claimed_와_상관없이_제_번호로_찾는다() -> None:
    ex = [E("20261016", "5", "005930", "sell", 3, 3, 9_800.0, 0, False)]
    got = bm.reconcile([_줄(broker_order_no="5")], ex, claimed=[("20261016", "5")])
    assert [o.status for o in got] == ["filled"]


def test_claimed_질의는_창_안의_닫힌_번호만() -> None:
    cl = Client(accounts=None)
    # 내부 주문 없는 줄은 날·종목·방향·정리 갈래마다 하나(0093 색인 — `reason = '정리'` 참·거짓) — 종목·방향을 바꿔 넣는다
    for 날, 종목, 방향, 상태, 번호, *칸 in [
        ("2026-10-16", 1, "sell", "filled", "N5"),  # 들어감
        ("2026-10-16", 1, "buy", "sending", None),  # 아직 안 닫힘
        ("2026-10-16", 3, "sell", "accepted", "N7"),  # 아직 안 닫힘
        ("2026-10-16", 3, "buy", "unknown", None),  # 번호 없음
        ("2026-10-15", 1, "sell", "filled", "N3"),  # 창 앞
        ("2026-10-19", 1, "sell", "filled", "N9"),  # 오늘(창 끝 밖)
        ("2026-10-17", 1, "sell", "filled", "U1", "kis_vts_us"),  # 다른 증권사 칸(미국)
    ]:  # fmt: skip
        cl.conn.execute(
            "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
            " order_type, quantity, trade_date, reason, status, broker_order_no, source, created_at)"
            " VALUES (?, NULL, 'H', 'KR', ?, 'X', ?, 'market', 1, ?, 'r', ?, ?, 'kis_vts', 't')",
            [(칸 or [job.BROKER])[0], 종목, 방향, 날, 상태, 번호],
        )
    assert cl.q(job.CLAIMED_SQL, [job.BROKER, "2026-10-16", "2026-10-19"]) == [("2026-10-16", "N5")]
    assert cl.q(job.CLAIMED_SQL, ["kis_vts_us", "2026-10-16", "2026-10-19"]) == [("2026-10-17", "U1")]


def test_배치는_전날_닫힌_줄의_체결을_남은_줄에_주지_않는다(c: Client) -> None:  # noqa: F811
    """잘린 맞추기 다음 날: 정리 줄(번호 N5 로 닫힘)과 응답 없던 장중 손절 줄(같은 종목·방향·수량)이 남은 꼴."""
    for 사유, 상태, 번호 in ((bm.REASON_CLEANUP, "filled", "N5"), (bm.REASON_GUARD_STOP, "sending", None)):
        c.conn.execute(
            "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
            " order_type, quantity, trade_date, reason, status, broker_order_no, source, created_at)"
            " VALUES (?, NULL, 'B', 'KR', 1, '005930', 'sell', 'market', 3, '2026-10-16', ?, ?, ?, 'kis_vts', 't')",
            [job.BROKER, 사유, 상태, 번호],
        )
    다음 = _Sess(executions=[E("20261016", "N5", "005930", "sell", 3, 3, 9_800.0, 0, False)])
    기록 = job.run(c, "KR", _at("08:40", "2026-10-19"), session=다음, sleep=lambda _x: None,
                 clock=lambda: _at("08:40", "2026-10-19"))  # fmt: skip
    assert 기록["reconciled"] == {"unknown": 1}
    assert c.q("SELECT message FROM paper_broker_orders WHERE reason = ?", [bm.REASON_GUARD_STOP]) == [
        ("체결 조회에 없음 — 같은 꼴 체결(번호 N5)은 다른 줄이 이미 가져감",)
    ]
    assert c.q("SELECT reason, status, broker_order_no FROM paper_broker_orders WHERE trade_date = '2026-10-16'"
               " AND paper_order_id IS NULL ORDER BY id") == [(bm.REASON_CLEANUP, "filled", "N5"),
                                                              (bm.REASON_GUARD_STOP, "unknown", None)]  # fmt: skip


def test_미국_배치도_전날_닫힌_정리_줄의_체결을_베낀_매도에_주지_않는다(monkeypatch) -> None:
    """미국 예약 줄은 처음부터 번호가 없다(예약번호는 message 에만) — 정리와 같은 지정가·수량의 베낀 매도가 잘린 맞추기
    다음 날 남으면 그 체결을 다시 잡았다(0093 전부터 열린 길, 25.2026 교차검증 [권고])."""
    from tests import test_kis_vts_us_1485 as us

    uc = us.c.__wrapped__(monkeypatch)
    uc.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, status, created_at)"
        " VALUES (204, 'B', 'US', 2, 'sell', 'limit', 180.9, 1, 'USD', '2026-10-15', '2026-10-16', 'r', 1, 'expired', 't')"
    )  # fmt: skip
    for 내부, 사유, 상태, 번호 in ((None, bm.REASON_CLEANUP, "filled", "5501"), (204, "베낌", "accepted", None)):
        uc.conn.execute(
            "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
            " order_type, limit_price, quantity, trade_date, reason, status, broker_order_no, source, created_at)"
            " VALUES ('kis_vts_us', ?, 'B', 'US', 2, 'AAPL', 'sell', 'limit', 180.9, 1, '2026-10-16', ?, ?, ?,"
            " 'kis_vts', 't')",
            [내부, 사유, 상태, 번호],
        )  # fmt: skip
    다음 = us._UsSess(executions=[E("20261016", "5501", "AAPL", "sell", 1, 1, 180.9, 0, False)])
    기록 = job.run(uc, "US", us._kst("21:27", "2026-10-19"), session=다음, clock=lambda: us._kst("21:27", "2026-10-19"))
    assert 기록["reconciled"] == {"unknown": 1}
    assert uc.q("SELECT status, message FROM paper_broker_orders WHERE paper_order_id = 204") == [
        ("unknown", bm.MISSING + " — 같은 꼴 체결(번호 5501)은 다른 줄이 이미 가져감")
    ]
