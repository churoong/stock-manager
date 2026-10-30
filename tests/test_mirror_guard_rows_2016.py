"""장중 손절·익절 줄과 아침 베끼기 (25.2016, KIS 결함 루프 [참고] 1·2).

0093 이 정리·장중 갈래를 나눈 뒤로 같은 날 같은 종목에 내부 주문 없는 줄이 둘(정리·장중)일 수 있다. 아침 베끼기는
① 하루 주문 상한에 장중 줄을 세지 않고(규칙서 15.11 "장중 매도는 상한 밖") ② "오늘 정리함" 에 장중 줄을 넣지 않는다"""

from __future__ import annotations

from batch.jobs import broker_mirror as bmj
from batch.services import broker_mirror as bm
from tests.test_paper_job_1445 import Client


def _줄(c: Client, sid: int, reason: str, poid: int | None = None) -> None:
    c.conn.execute(
        "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
        " quantity, trade_date, reason, status, source, created_at) VALUES ('kis_vts', ?, 'H', 'KR', ?, 'X', 'sell',"
        " 'market', 1, '2026-10-30', ?, 'accepted', 'kis_vts', 't')",
        [poid, sid, reason],
    )


def test_하루_주문_상한은_장중_줄을_세지_않는다() -> None:
    c = Client(accounts=None)
    _줄(c, 1, bm.REASON_CLEANUP)
    _줄(c, 1, bm.REASON_GUARD_STOP)
    _줄(c, 3, bm.REASON_GUARD_TAKE)
    수 = c.q(bmj.SENT_TODAY_SQL, [bmj.BROKER, "2026-10-30", bm.REASON_GUARD_STOP, bm.REASON_GUARD_TAKE])
    assert 수 == [(1,)]  # 정리 줄 하나만


def test_오늘_정리함은_정리_사유_줄만() -> None:
    c = Client(accounts=None)
    _줄(c, 1, bm.REASON_GUARD_STOP)  # 장중 매도만 한 종목
    _줄(c, 3, bm.REASON_CLEANUP)
    보냄 = c.execute(bmj.SENT_SQL, [bmj.BROKER, "2026-10-30"]).dicts()
    assert bmj.오늘_정리함(보냄) == {3}
