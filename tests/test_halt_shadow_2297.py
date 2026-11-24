"""멈춤 스위치가 켜진 날의 그림자 — "매수만 멈춤" 이었다면 냈을 것 (docs/paper.md 15.14, docs/infra.md 25.2297)."""

from __future__ import annotations

from pathlib import Path

import pytest

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from tests.test_kis_vts_1475 import Client, _at, _Sess, c  # noqa: F401 — `c` 는 픽스처

뿌리 = Path(__file__).resolve().parent.parent


def _켬(db: Client) -> None:
    db.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', '{\"on\": true}', 't')")


def _보유(db: Client) -> None:
    """내부도 종목 3 을 2주 들고 있다 — 그래야 베낀 매도(102)가 정리가 아니라 매도로 나간다"""
    db.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, created_at)"
                   " VALUES (90, 'B', 'KR', 3, 'buy', 'market', 2, 'KRW', '2026-10-14', '2026-10-15', 'r', 1,"
                   " 'filled', 't')")  # fmt: skip
    db.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (90, 'B', 3, 'buy', '2026-10-15', 20000, 2, 'KRW', 1, 0, 0, 0, -40000, 't')")  # fmt: skip


def test_갈래로_센다() -> None:
    낼것 = [bm.Intent(1, 10, "A", "sell", 2, None, bm.REASON_MIRROR), bm.Intent(None, 11, "B", "sell", 1, None, bm.REASON_CLEANUP),
           bm.Intent(2, 12, "C", "buy", 3, 100.0, bm.REASON_MIRROR), bm.Intent(3, 12, "C", "buy", 1, 90.0, bm.REASON_MIRROR),
           bm.Intent(None, 13, "D", "sell", 5, None, bm.REASON_CLEANUP)]  # fmt: skip
    assert bm.halt_shadow(낼것) == {"sells": 1, "cleanups": 2, "buys": 2}
    assert bm.halt_shadow([]) == {"sells": 0, "cleanups": 0, "buys": 0}


def test_켜진_날은_아무것도_안_내고_냈을_것을_센다(c: Client) -> None:  # noqa: F811
    _보유(c)
    _켬(c)
    s = _Sess(holdings=[kv.Holding("000660", 2, 2, 20_000.0)])
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    # 동작은 그대로 — 아무것도 내지 않는다
    assert s.orders == [] and 기록["halted"] and 기록["not_sent"] == {"멈춤 스위치가 켜져 있음": 3}
    assert c.q("SELECT COUNT(*) FROM paper_broker_orders") == [(0,)]
    # 그림자 — 매수만 멈춤이었다면 매도 1건은 나갔고 매수 2건만 막혔다
    assert 기록["halt_shadow"] == {"sells": 1, "cleanups": 0, "buys": 2}
    assert 기록["go_shadow"]["verdict"] == "no_go"  # 주문 허가 판정은 그대로(15.13)


def test_그림자는_꺼진_날_실제로_낸_것과_같다(c: Client) -> None:  # noqa: F811
    """같은 계획 함수를 멈춤 없이 돌린 것이다 — 스위치를 끄고 돌린 실행의 계획과 수가 맞는다"""
    _보유(c)
    s = _Sess(holdings=[kv.Holding("000660", 2, 2, 20_000.0)])
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert "halt_shadow" not in 기록 and not 기록["halted"]  # 꺼진 날에는 칸이 없다
    assert [o[0] for o in s.orders] == ["sell", "buy", "buy"] and 기록["planned"] == {"cleanup": 0, "mirror": 3}


def _갈래(db: Client, 날: str) -> dict[str, int]:
    """그날 실제로 적힌 증권사 줄을 그림자와 같은 갈래로 센다"""
    줄 = db.q("SELECT side, reason FROM paper_broker_orders WHERE trade_date = ?", [날])
    return {"sells": sum(1 for s, r in 줄 if s == "sell" and r != bm.REASON_CLEANUP),
            "cleanups": sum(1 for _s, r in 줄 if r == bm.REASON_CLEANUP),
            "buys": sum(1 for s, _r in 줄 if s == "buy")}  # fmt: skip


def test_그림자는_같은_조건에서_스위치를_끈_실행이_낸_것과_같다() -> None:
    """25.2301 (25.2297 교차검증 [권고]) — 같은 날 앞 실행이 이미 낸 줄(`already`·`sent_today`)과 판정 대기 매수가 있는
    상태에서, 켜진 날 그림자 수 == 같은 상태에서 스위치를 끄고 돌린 실행이 실제로 낸 줄의 갈래 수. 그림자 호출에만 인자를
    빠뜨리면(이미 낸 줄을 모름 등) 그림자가 부푼다"""
    from tests.test_kis_vts_1475 import 키

    mp = pytest.MonkeyPatch()
    try:
        for k, v in 키.items():
            mp.setenv(k, v)
        mp.setattr(job, "MIRROR_ACCOUNT", "B")

        def 판() -> Client:
            db = Client()
            for oid, sid, side, typ, price, q, 회차 in ((101, 1, "buy", "limit", 9_800, 3, 1),
                                                       (102, 3, "sell", "market", None, 2, 0),
                                                       (103, 1, "buy", "limit", 9_600, 2, 2)):  # fmt: skip
                db.conn.execute(
                    "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                    " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
                    " VALUES (?, 'B', 'KR', ?, ?, ?, ?, ?, 'KRW', '2026-10-15', '2026-10-16', 'r', 1, ?, 't')",
                    [oid, sid, side, typ, price, q, 회차],
                )  # fmt: skip
            _보유(db)
            # 같은 날 앞 실행이 101 을 이미 냈다(접수 받음) — 다시 돌 때 그것은 낼 것이 아니다
            db.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code,"
                            " side, order_type, quantity, trade_date, reason, status, source, created_at)"
                            " VALUES ('kis_vts', 101, 'B', 'KR', 1, '005930', 'buy', 'limit', 3, '2026-10-16', '베낌',"
                            " 'accepted', 't', 't')")  # fmt: skip
            # 판정 대기 매수(어제 세션, 아직 판정 전) — KIS 에선 이미 찼다. 정리 기준에 넣지 않으면 "증권사에만 남은 몫" 으로
            # 보여 정리가 생긴다(25.1715)
            db.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity,"
                            " currency, decided_on, fill_session, reason, rule_version, status, tranche, created_at)"
                            " VALUES (95, 'B', 'KR', 3, 'buy', 'market', 2, 'KRW', '2026-10-13', '2026-10-15', 'r', 1,"
                            " 'open', 0, 't')")  # fmt: skip
            db.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code,"
                            " side, order_type, quantity, filled_qty, trade_date, reason, status, source, created_at)"
                            " VALUES ('kis_vts', 95, 'B', 'KR', 3, '000660', 'buy', 'market', 2, 2, '2026-10-15', '베낌',"
                            " 'filled', 't', 't')")  # fmt: skip
            return db

        켬 = 판()
        _켬(켬)
        잔고 = [kv.Holding("000660", 4, 4, 20_000.0)]  # 내부 2 + 판정 대기 2
        그림자 = job.run(켬, "KR", _at("08:45"), session=_Sess(holdings=잔고), sleep=lambda _x: None)["halt_shadow"]
        끔 = 판()
        앞 = _갈래(끔, "2026-10-16")
        job.run(끔, "KR", _at("08:45"), session=_Sess(holdings=잔고), sleep=lambda _x: None)
        뒤 = _갈래(끔, "2026-10-16")
        낸것 = {k: 뒤[k] - 앞[k] for k in 뒤}
        assert 그림자 == 낸것 == {"sells": 1, "cleanups": 0, "buys": 1}  # 101 은 이미 냈다 — 103 만
    finally:
        mp.undo()


def test_두_계획_호출은_멈춤만_다르고_같은_인자를_받는다() -> None:
    """25.2304 (25.2301 교차검증 [권고]) — 직접 대조 시험의 판은 상한·오늘 정리·장중판이 없어, 그림자 호출에만
    `sent_today`·`cleaned_today`·`guard_held` 를 바꿔도 통과했다. 두 호출이 `halted` 만 다르고 위치 인자와 `**인자`
    한 벌을 그대로 받는지 소스로 묶는다"""
    import inspect

    글 = inspect.getsource(job.run)
    진짜 = "bm.plan(주문들, 코드, 정리기준, 증권, 산적, halted=멈춤, **인자)"
    그림자 = "bm.plan(주문들, 코드, 정리기준, 증권, 산적, halted=False, **인자)"
    assert 글.count(진짜) == 1 and 글.count(그림자) == 1 and 글.count("bm.plan(") == 2
    # 묶음을 만든 뒤 고치지 않는다 — 두 호출 사이에서 `인자["sent_today"] = 0` 같은 줄이 끼면 그림자만 다른 인자를
    # 받는데도 호출 글은 같다(25.2305, 25.2304 교차검증 [권고])
    assert 글.count("인자: dict") == 1 and "인자[" not in 글 and "인자." not in 글 and "인자 =" not in 글


def test_증권사에만_남은_몫은_정리로_센다(c: Client) -> None:  # noqa: F811
    # 내부 보유 없이 증권사에만 2주 — 산 적이 있는 종목이어야 정리 대상이다(`BOUGHT_SQL`)
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, filled_qty, trade_date, reason, status, source, created_at)"
                   " VALUES ('kis_vts', NULL, 'B', 'KR', 3, '000660', 'buy', 'market', 2, 2, '2026-10-14', '베낌',"
                   " 'filled', 't', 't')")  # fmt: skip
    _켬(c)
    s = _Sess(holdings=[kv.Holding("000660", 2, 2, 20_000.0)])
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert s.orders == [] and 기록["halt_shadow"]["cleanups"] == 1 and 기록["halt_shadow"]["buys"] == 2


def test_셈이_던져도_단계는_그대로_끝난다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _켬(c)
    진짜 = bm.plan

    def 계획(*a, halted, **k):  # type: ignore[no-untyped-def]
        if not halted:
            raise KeyError("그림자 길에서만 나는 오류")
        return 진짜(*a, halted=halted, **k)

    monkeypatch.setattr(bm, "plan", 계획)
    s = _Sess()
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert 기록["halt_shadow"] == {"error": "KeyError"} and 기록["halted"] and s.orders == []
    assert 기록["not_sent"] == {"멈춤 스위치가 켜져 있음": 3}


def test_문서() -> None:
    문서 = (뿌리 / "docs" / "paper.md").read_text()
    for 조각 in ("### 15.14 멈춤 스위치가 켜진 날의 그림자", "25.2297", "`step_log.broker.halt_shadow`", "`{sells, cleanups, buys}`",
               "**동작은 그대로다**", "멈춤 없이 한 번 더", "스위치가 꺼진 실행에는 이 칸이 없다", "칸이 없다고 꺼진 것이 아니다", "`broker2`", "하루 주문 상한을 적용한 수"):  # fmt: skip
        assert 조각 in 문서, 조각
