"""25.1620 교차검증 [필수] 1·2 (docs/paper.md 2.3·15장, docs/infra.md 25.1621).

지키는 것
- 미룬 주문은 판정만 시가 — 저장된 `fill_basis` 는 그대로라 KIS 비교의 첫날(종가 판정) 줄은 시가 표본 밖에 남는다
- 두 장을 한 실행에서 판정할 때 앞 장 매수의 현금 검사에 뒤 장 매수 예약을 빼지 않는다
"""

from __future__ import annotations

from batch.jobs import broker_mirror as jbm
from batch.jobs import paper as job
from batch.services import broker_mirror as bm
from tests.test_paper_fill_1620 import 시세, 주문, 판정
from tests.test_paper_job_1445 import Client


def _입금(c: Client, day: str) -> None:
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES (?, 1000000, 'deposit', 't', 'k1')", [day])  # fmt: skip


def test_미룬_종가_매도는_시가로_차고_kis_비교_표본_밖에_남는다() -> None:
    c = Client()
    _입금(c, "2026-10-13")
    주문(c, aid="B", side="buy", limit=10_000, q=4)
    시세(c, 1, "2026-10-15", 10_100, 9_900)
    판정(c, "KR", "2026-10-15")
    s = 주문(c, aid="B", side="sell", typ="market", q=4, decided="2026-10-15", session="2026-10-16", basis="close",
           tranche=0)  # fmt: skip
    시세(c, 1, "2026-10-16", 9_500, 6_930, chg=-30.0)  # 장 열린 뒤 낸 매도 — 종가 하한가 → 미룸
    판정(c, "KR", "2026-10-16")
    시세(c, 1, "2026-10-19", 6_500, 7_000, chg=1.0)
    판정(c, "KR", "2026-10-19")
    assert c.q("SELECT fill_basis, deferrals FROM paper_orders WHERE id = ?", [s]) == [("close", 1)]
    assert c.q("SELECT price FROM paper_fills WHERE order_id = ?", [s]) == [(6500.0,)]  # 다음 장 시가
    # KIS: 10-16 장중 시장가 매도가 찼다, 10-19 다시 낸 매도는 보유 없음으로 거부
    for td, st, fq, ap in (("2026-10-16", "filled", 4, 9000), ("2026-10-19", "rejected", 0, None)):
        c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code,"
                       " side, order_type, quantity, trade_date, reason, status, source, created_at, filled_qty,"
                       " avg_price) VALUES ('kis_vts', ?, 'B', 'KR', 1, '005930', 'sell', 'market', 4, ?, '베낌', ?,"
                       " 'kis_vts', 't', ?, ?)", [s, td, st, fq, ap])  # fmt: skip
    비교 = bm.compare(c.execute(jbm.COMPARE_SQL, ["kis_vts", "2026-10-01", "2026-10-31", "B"]).dicts())
    assert (비교["n"], 비교["excluded"]) == (0, 1)  # 25.1620 은 n 1·KIS만 참 — 괴리 판정이 거꾸로 섰다


def test_앞_장_매수는_뒤_장_매수_예약_때문에_취소되지_않는다() -> None:
    c = Client()
    _입금(c, "2026-10-12")
    주문(c, sid=3, side="buy", limit=10_000, q=10, decided="2026-10-12", session="2026-10-13")
    시세(c, 3, "2026-10-13", 10_000, 9_900)
    판정(c, "KR", "2026-10-13")
    m = 주문(c, sid=1, side="buy", typ="market", q=80, decided="2026-10-14", session="2026-10-15", ref=10_000)
    주문(c, sid=3, side="sell", typ="market", q=10, decided="2026-10-15", session="2026-10-16", tranche=0)
    주문(c, sid=1, side="buy", limit=10_000, q=1, decided="2026-10-15", session="2026-10-16", tranche=2)
    시세(c, 1, "2026-10-15", 11_200, 11_000)  # 시가가 기준가보다 12% 높다(틈 여유 10% 넘음)
    시세(c, 3, "2026-10-15", 10_000, 10_000)
    시세(c, 1, "2026-10-16", 11_000, 10_500)
    시세(c, 3, "2026-10-16", 10_000, 10_000)
    판정(c, "KR", "2026-10-16")
    assert c.q("SELECT status FROM paper_orders WHERE id = ?", [m]) == [("filled",)]
    assert job.DEFER_SQL.count("fill_basis") == 0
