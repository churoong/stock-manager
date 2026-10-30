"""`fill_orders` 가 순수 판정·점검을 **바르게 부르는지** (25.2038, 25.2033~2035 교차검증 [권고]).

`pp.judge_order`·`pp.settle_check` 의 본체는 순수 시험이 묶지만, 운영 `fill_orders` 의 연결부(마지막 종가 클로저,
계좌별 체결 범위, 다른 미체결 매수 예약)는 어떤 시험도 지나지 않았다 — 검증 에이전트가 셋을 바꿔도 가상모의투자 시험
384건이 다 지났다. 연결이 틀어지면 운영 장부만 틀리고 ⑦-5 일치 시험(같은 순수 함수)으로도 안 잡힌다.
"""

from __future__ import annotations

from batch.jobs import paper as job
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client


def _주문(c: Client, oid: int, aid: str, sid: int, side: str, q: int, session: str, *, status: str = "open",
          deferrals: int = 0, ref: float | None = None) -> None:  # fmt: skip
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, tranche, status, deferrals, ref_price, created_at)"
        " VALUES (?, ?, 'KR', ?, ?, 'market', NULL, ?, 'KRW', '2026-10-13', ?, 'r', 1, ?, ?, ?, ?, 't')",
        [oid, aid, sid, side, q, session, 1 if side == "buy" else 0, status, deferrals, ref],
    )  # fmt: skip


def _보유(c: Client, oid: int, aid: str, sid: int, q: int, price: float, day: str = "2026-10-13") -> None:
    _주문(c, oid, aid, sid, "buy", q, day, status="filled")
    c.conn.execute(
        "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
        " fee, tax, slippage, cash_krw, created_at) VALUES (?, ?, ?, 'buy', ?, ?, ?, 'KRW', 1, 0, 0, 0, ?, 't')",
        [oid, aid, sid, day, price, q, -price * q],
    )  # fmt: skip


def _시세(c: Client, sid: int, day: str, open_: float, close: float, volume: int = 1_000) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'KRW', 't', 't')", [sid, day, open_, max(open_, close), min(open_, close), close,
                                                           volume])  # fmt: skip


def _판정(c: Client, day: str, 입금: float = 1_000_000.0) -> dict:
    기록: dict = {}
    flows = [pp.CashFlow(1, "2026-10-13", 입금, "deposit")]
    job.fill_orders(c, "KR", day, flows, job._fills(c)[0], None, None, set(), "t", 기록)
    return 기록


def _상태(c: Client, oid: int) -> tuple:
    return tuple(c.q("SELECT status, close_note FROM paper_orders WHERE id = ?", [oid])[0])


def test_정지로_오래_미룬_매도는_그날까지의_마지막_종가로_청산한다() -> None:
    c = Client()
    _보유(c, 10, "B", 1, 5, 10_000.0)
    _시세(c, 1, "2026-10-15", 9_100, 9_000)
    _시세(c, 1, "2026-10-16", 8_800, 8_800, volume=0)  # 정지 — 그날 행 종가가 앞날과 달라 어느 종가인지 묶는다(25.2039)
    _시세(c, 3, "2026-10-16", 100, 100)  # 시장엔 그날 시세가 있다
    _주문(c, 11, "B", 1, "sell", 5, "2026-10-16", deferrals=pp.HALT_LIQUIDATE_SESSIONS - 1)
    _판정(c, "2026-10-16")
    상태, 글 = _상태(c, 11)
    assert 상태 == "filled" and "마지막 종가로 청산" in 글
    # 운영 `LAST_CLOSE_SQL` 은 그날 행까지(date <= 날) — 리플레이도 같다(25.2039)
    assert c.q("SELECT price FROM paper_fills WHERE order_id = 11") == [(8_800.0,)]


def test_다른_계좌가_든_종목은_팔지_못한다() -> None:
    c = Client()
    _보유(c, 10, "B", 1, 5, 10_000.0)
    _시세(c, 1, "2026-10-16", 10_100, 10_000)
    _주문(c, 11, "A", 1, "sell", 5, "2026-10-16")  # A 는 1 을 들지 않았다
    _판정(c, "2026-10-16")
    assert _상태(c, 11) == ("expired", "팔 보유가 모자람")
    assert c.q("SELECT COUNT(*) FROM paper_fills WHERE order_id = 11") == [(0,)]


def test_같은_장_다른_매수_예약을_빼고_현금을_본다() -> None:
    """입금 100만 · 같은 장 시장가 매수 둘(각 60만 남짓) — 첫째를 판정할 때 둘째의 예약을 빼면 현금이 모자라 첫째가
    만료되고 둘째가 찬다. 예약을 빼지 않으면(0) 첫째가 차고 둘째가 만료된다 — 순서가 뒤집힌다"""
    c = Client()
    _시세(c, 1, "2026-10-16", 6_000, 6_000)
    _시세(c, 3, "2026-10-16", 6_000, 6_000)
    _주문(c, 21, "B", 1, "buy", 100, "2026-10-16", ref=6_000.0)
    _주문(c, 22, "B", 3, "buy", 100, "2026-10-16", ref=6_000.0)
    _판정(c, "2026-10-16")
    assert _상태(c, 21) == ("expired", "현금 부족 (다른 미체결 매수 예약 포함)")
    assert _상태(c, 22)[0] == "filled"
