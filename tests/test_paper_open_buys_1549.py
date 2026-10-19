"""아직 안 찬 매수 주문도 종목·섹터 상한에 보유처럼 센다 — 같은 종목 두 벌 막기 (docs/paper.md 2.1, docs/infra.md 25.1549).
감사 재현: 아침 배치 때 어제 시세가 아직 없으면 어제 장 주문을 열어 둔 채 오늘 장 주문을 또 내, 두 벌이 모두 차 비중 19%."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _시세, _신호, 설정


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c = Client(accounts=("B",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-13', 1000000, 'deposit', '2026-10-13T00:00:00+00:00', 'k1')")  # fmt: skip
    return c


def _실행(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, today: str, now: str) -> dict:
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date.fromisoformat(today))
    _줄, 기록 = job.run_step(c, market="KR", trade_date=trade_date, late=False, signal_rows=[_신호()], settings=설정,
                            price_series={}, series_unknown={}, allocate=True,
                            now=datetime.fromisoformat(now))  # fmt: skip
    return 기록


def test_어제_시세가_늦은_아침에_같은_종목을_두_벌_사지_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    _시세(c, "2026-10-14", 10_000, 10_000)
    _실행(c, monkeypatch, "2026-10-14", "2026-10-15", "2026-10-14T23:30:00+00:00")  # 10-15 장 주문 세 회차
    _실행(c, monkeypatch, "2026-10-15", "2026-10-16", "2026-10-15T23:30:00+00:00")  # 10-15 시세가 아직 없다
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE side = 'buy'") == [(3,)]
    결정 = c.q("SELECT reason FROM paper_decisions WHERE decided_on = '2026-10-15' AND stock_id = 1")
    assert (job.OPEN_BUY_SKIP_REASON,) in 결정
    _시세(c, "2026-10-15", 9_600, 9_500)
    _시세(c, "2026-10-16", 9_600, 9_500)
    _실행(c, monkeypatch, "2026-10-16", "2026-10-19", "2026-10-18T23:30:00+00:00")
    p = json.loads(c.q("SELECT positions_json FROM paper_daily WHERE as_of = '2026-10-19'")[0][0])
    # 한 벌(10주) — 예전엔 두 벌 20주·19.1%
    assert [x["quantity"] for x in p] == [10] and p[0]["weight_pct"] <= 10.0


def test_종목별_예약은_현금_예약과_같은_식() -> None:
    o = [pp.Order(id=1, account_id="B", stock_id=1, side="buy", order_type="limit", limit_price=100.0, quantity=10,
                  currency="KRW", fill_session="s"),
         pp.Order(id=2, account_id="B", stock_id=1, side="buy", order_type="limit", limit_price=90.0, quantity=10,
                  currency="KRW", fill_session="s"),
         pp.Order(id=3, account_id="B", stock_id=2, side="buy", order_type="limit", limit_price=10.0, quantity=1,
                  currency="USD", fill_session="s")]  # fmt: skip
    by = pp.reserved_by_stock(o, {"USD": 1000.0})
    assert by is not None and sum(by.values()) == pytest.approx(pp.reserved_krw(o, {"USD": 1000.0}))
    assert set(by) == {1, 2}
    assert pp.reserved_by_stock(o, {"USD": None}) is None


def test_판_2_회사의_헌장이_깨지면_판_1_로_사지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """헌장 검사에 걸린 판 2 회사는 위원회 없이 점수 상위로 사면서 판 2 표시를 붙였다 — 사지 않고 까닭만 (25.1549 [권고] 2)"""
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c = Client(accounts=("A",), charters=True)
    ch = json.loads(c.q("SELECT charter_json FROM paper_accounts WHERE id = 'A'")[0][0])
    ch["weights"]["research"] = 50  # 한 부서 가중 40% 초과 — 검사에 걸림
    c.conn.execute("UPDATE paper_accounts SET charter_json = ? WHERE id = 'A'", [json.dumps(ch)])
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-13', 1000000, 'deposit', '2026-10-13T00:00:00+00:00', 'k1')")  # fmt: skip
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date(2026, 10, 15))
    줄, 기록 = job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[_신호()], settings=설정,
                           price_series={}, series_unknown={}, allocate=True,
                           now=datetime.fromisoformat("2026-10-14T23:30:00+00:00"))  # fmt: skip
    assert c.q("SELECT COUNT(*) FROM paper_orders") == [(0,)]
    assert 기록["accounts"]["A"]["committee"]["error"].startswith("헌장 검사 실패")
    assert any("헌장 검사 실패로 오늘 사지 않음" in x for x in 줄)


def test_다른_시장_열린_매수가_여력에서_두_번_빠지지_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """국내 아침엔 늘 전날 밤 미국 매수가 열려 있다 — 보유 목록과 예약에 같이 넣어 여력이 두 번 깎였다 (25.1550 반박 [필수] 1).
    입금 100만, 미국 미체결 $470(환율 1,000 → 예약 약 48.9만), 국내 신호 10만 → 예전엔 "투자 여력 부족" 으로 0건"""
    c.conn.execute("UPDATE stocks SET sector = 'Technology' WHERE id = 2")
    c.conn.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', '2026-10-14', 1000, 't', 't')")
    c.conn.execute(job.INSERT_ORDER_SQL, ["B", "US", 2, "buy", "limit", 1.0, 470, "USD", "2026-10-13", "2026-10-14",
                                          "open", 1, "2부 배분", "short", 70, "2026-10-13", "{}", 1, "t", None])  # fmt: skip
    _시세(c, "2026-10-14", 10_000, 10_000)
    _실행(c, monkeypatch, "2026-10-14", "2026-10-15", "2026-10-14T23:30:00+00:00")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE market = 'KR' AND side = 'buy'") == [(3,)]


def test_열린_매수_skip_은_안_산_후보와_견주지_않는다() -> None:
    assert job.OPEN_BUY_SKIP_REASON.startswith(pp.NONCOMPARABLE_SKIP_PREFIXES)


def test_KIS_베끼기는_베끼는_계좌의_멈춤만() -> None:
    """다른 계좌의 이어 멈춤이 H 의 정상 매수 베끼기를 막지 않는다 (25.1550 반박 [권고] 3)"""
    from batch.jobs import daily

    log = {"frozen": [1, 2], "frozen_by_account": {"H": [2], "A": [1, 2]}}
    assert daily.mirror_frozen(log, "H") == {2}
    assert daily.mirror_frozen(log, None) == {1, 2} and daily.mirror_frozen({"frozen": [1]}, "H") == {1}


def test_두_번째_분할이면_이어_멈춤_날을_늦춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    """d1 분할 뒤 산 계좌도 d2 분할 전에 샀으면 멈춰야 한다 — 표의 날을 d2 로 (25.1550 [참고] 2)"""
    from batch.jobs import monitor_targets

    c = Client(accounts=("B",))
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, 't')",
                   [job.ACTION_STICKY_KEY, json.dumps({"1": "2026-06-09"})])  # fmt: skip
    monkeypatch.setattr(monitor_targets, "action_dates", lambda *_a, **_k: ["2026-06-09", "2026-08-03"])
    _멈춤, 표 = job.sticky_frozen(c, {1}, {1: "2026-06-02"}, {1: "KR"})
    assert 표 == {"1": "2026-08-03"}
