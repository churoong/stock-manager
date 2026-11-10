"""고점 대비 익절(`bm.REASON_GUARD_TRAIL`, 25.2220) 줄이 장중 줄로 읽히는지 — 부르는 자리마다 행동으로 (docs/infra.md 25.2222).

25.2220 이 사유를 `장중익절(고점)` 으로 가르고 `*bm.GUARD_REASON_LIST` 를 넘기게 했지만, 시험은 SQL 에 `IN (?, ?, ?)` 가
있는지만 봤다 — 어느 자리의 인자를 (손절, 익절, 익절) 로 바꿔도 다 통과했다(25.2220 교차검증 [권고]). 여기서는 고점 익절
줄을 실제로 넣고 그 자리가 낳는 결과를 본다:

- `jobs/broker_mirror.guard_held`(GUARD_SELLS_SQL) — 고점 익절로 판 뒤 H 가 같은 보유를 들면 그 종목을 돌려준다
- `jobs/broker_mirror.run`(SENT_TODAY_SQL) — 하루 주문 상한이 고점 익절 줄을 세지 않는다
- `jobs/intraday_guard.run` 오늘줄(GUARDED_TODAY_SQL) — 순간 거부된 고점 익절 줄을 같은 줄로 다시 보내고, 낸 줄은
  "이미" 로 본다
- 같은 함수의 INSERT 겹침 다시 읽기(GUARDED_TODAY_SQL) — 다른 실행이 먼저 적은 고점 익절 줄은 멈춤·알림이 아니다
- 같은 함수의 보내지 않음 닫기(UNSENT_ORDER_SQL) — 기록 확인이 깨진 고점 익절 줄을 닫아 다음 회차가 다시 보낸다
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from batch.jobs import broker_mirror as bmj
from batch.jobs import intraday_guard as ig
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from tests.test_intraday_guard_1768 import _h, 월
from tests.test_intraday_guard_1768 import c as _감시_픽스처
from tests.test_kis_vts_1475 import _at, _Sess, job
from tests.test_kis_vts_1475 import c as _베끼기_픽스처
from tests.test_paper_job_1445 import Client


@pytest.fixture()
def gc(monkeypatch: pytest.MonkeyPatch) -> Client:
    """감시선: 1(005930) 손절 9,300 · 3(000660) 손절 50,000·고점 익절 90,000 — 000660 89,000 이면 고점 익절에 닿는다"""
    return _감시_픽스처.__wrapped__(monkeypatch)  # type: ignore[attr-defined]


@pytest.fixture()
def jc(monkeypatch: pytest.MonkeyPatch) -> Client:
    """B 를 베끼는 국내 흐름 — 2026-10-16 회차에 낼 B 주문 셋(101·102·103)"""
    return _베끼기_픽스처.__wrapped__(monkeypatch)  # type: ignore[attr-defined]


def _고점줄(c: Client, sid: int, day: str, status: str, *, code: str = "000660", filled: int = 0,
           message: str | None = None, created: str = "t", account: str = "H") -> None:  # fmt: skip
    c.conn.execute(
        "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
        " quantity, trade_date, reason, status, filled_qty, message, source, created_at)"
        " VALUES (?, NULL, ?, 'KR', ?, ?, 'sell', 'market', 10, ?, ?, ?, ?, ?, 'kis_vts', ?)",
        [bmj.BROKER, account, sid, code, day, bm.REASON_GUARD_TRAIL, status, filled, message, created],
    )


# ── jobs/broker_mirror ────────────────────────────────────────────────────────


def test_guard_held_는_고점_익절로_판_종목도_돌려준다() -> None:
    c = Client(accounts=None)
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
        " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
        " VALUES (900, 'H', 'KR', 1, 'buy', 'limit', 100, 10, 'KRW', '2026-10-20', '2026-10-20', 'r', 1, 1, 't')")
    c.conn.execute(
        "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
        " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (900, 'H', 1, 'buy', '2026-10-20', 100, 10,"
        " 'KRW', 1, 0, 0, 0, -1000, 't')")  # fmt: skip
    _고점줄(c, 1, "2026-10-26", "filled", code="005930", filled=10)
    assert bmj.guard_held(c, bmj.BROKER, "H") == {1}


def test_하루_주문_상한은_오늘_고점_익절_줄을_세지_않는다(jc: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """상한 3 에 낼 주문 3 — 오늘 고점 익절 줄 하나를 상한에 세면 하나가 "하루 주문 상한" 으로 빠진다"""
    원래 = bm.plan
    monkeypatch.setattr(bm, "plan", lambda *a, **k: 원래(*a, **{**k, "max_orders": 3}))
    # 이 흐름 픽스처는 A·B 계좌만 있다(H 행 없음) — 상한 세기는 계좌를 보지 않는다
    _고점줄(jc, 2, "2026-10-16", "filled", code="X", filled=10, account="B")
    s = _Sess(holdings=[kv.Holding("000660", 2, 2, 20_000.0)])
    job.run(jc, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert s.orders == [("sell", "000660", 2, None), ("buy", "005930", 3, 9_800.0), ("buy", "005930", 2, 9_600.0)]


# ── jobs/intraday_guard ───────────────────────────────────────────────────────


def test_순간_거부된_고점_익절_줄은_같은_줄로_다시_보낸다(gc: Client) -> None:
    _고점줄(gc, 3, "2026-10-26", "rejected", message="장중 … · EGW00201 초당 거래건수 초과")
    s = _Sess([_h("000660", 89_000.0)])
    r = ig.run(gc, 월, session=s, clock=lambda: 월)
    assert r["sent"] == {"retried": 1, "accepted": 1} and s.orders == [("sell", "000660", 10, None)]
    assert gc.q("SELECT COUNT(*), MAX(status), MAX(reason) FROM paper_broker_orders") == [
        (1, "accepted", bm.REASON_GUARD_TRAIL)]


def test_오늘_낸_고점_익절_종목은_이미_판_것으로_본다(gc: Client) -> None:
    _고점줄(gc, 3, "2026-10-26", "accepted")
    s = _Sess([_h("000660", 89_000.0)])
    r = ig.run(gc, 월, session=s, clock=lambda: 월)
    assert r["hits"] == {} and "sent" not in r and s.orders == []


def test_다른_실행이_먼저_적은_고점_익절_줄은_멈춤도_알림도_아니다(gc: Client) -> None:
    s = _Sess([_h("000660", 89_000.0)])
    진짜 = gc.execute

    def execute(sql: str, args: list[Any] | None = None) -> Any:
        if sql == bmj.INSERT_SQL and not gc.q("SELECT 1 FROM paper_broker_orders"):  # 내 INSERT 직전 다른 실행
            _고점줄(gc, 3, "2026-10-26", "sending", created="다른실행")
        return 진짜(sql, args)

    gc.execute = execute  # type: ignore[method-assign]
    r = ig.run(gc, 월, session=s, clock=lambda: 월)
    assert s.orders == [] and r["sent"] == {"skipped_other_run": 1} and "alert" not in r


def test_기록_확인이_깨진_고점_익절_줄은_닫고_다음_회차가_다시_보낸다(gc: Client) -> None:
    진짜, 깸 = gc.execute, {"n": 0}

    def execute(sql: str, args: list[Any] | None = None) -> Any:
        if sql == bmj.MINE_SQL and not 깸["n"]:
            깸["n"] += 1
            raise RuntimeError("Turso 잠깐 끊김")
        return 진짜(sql, args)

    gc.execute = execute  # type: ignore[method-assign]
    s = _Sess([_h("000660", 89_000.0)])
    with pytest.raises(ig.GuardRecordError):
        ig.run(gc, 월, session=s, clock=lambda: 월)
    assert s.orders == []
    assert gc.q("SELECT status, reason, substr(message, 1, 12) FROM paper_broker_orders") == [
        ("rejected", bm.REASON_GUARD_TRAIL, ig.UNSENT_ORDER_MARK)]
    다음 = 월 + timedelta(minutes=10)
    s2 = _Sess([_h("000660", 88_000.0)])
    r = ig.run(gc, 다음, session=s2, clock=lambda: 다음)
    assert r["sent"] == {"retried": 1, "accepted": 1} and len(s2.orders) == 1
