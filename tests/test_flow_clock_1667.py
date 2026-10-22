"""25.1667 — 새 입금을 시계 비교가 아니라 "오늘 읽은 입금 합 − 어제 행이 센 입금 합" 으로 (웹 쓰기 경로 감사 [필수] 3).

웹(Vercel)은 입금 행의 `created_at` 을 Turso 에 저장하기 **전**에 찍는다. 그 시각이 배치 `지금` 보다 앞인데 행이 배치의 입금
읽기 **뒤**에 저장되면, 오늘 실행은 못 읽고(행 없음) 다음 날은 "어제 행보다 앞" 이라 새 입금으로 세지 않아 지수가 뛰었다.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client, 설정


def _하루(c: Client, mp: pytest.MonkeyPatch, 오늘: str, trade_date: str, now: str) -> dict:
    mp.setattr(cal, "local_today", lambda _m=None: date.fromisoformat(오늘))
    _, 기록 = job.run_step(c, market="KR", trade_date=trade_date, late=False, signal_rows=[], settings=설정,
                          price_series={}, series_unknown={}, allocate=False, now=datetime.fromisoformat(now))  # fmt: skip
    return 기록


def test_웹_시각이_앞서도_늦게_저장된_입금은_다음_날_흐름이다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=("A",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    # 웹이 23:29:59.900 에 시각을 찍었지만 저장은 배치가 입금을 읽은 뒤에 끝났다 — 그 실행은 못 봤다(행이 없었다)
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-15', 1000000, 'deposit', '2026-10-14T23:29:59.900Z', 'k2')")  # fmt: skip
    기록 = _하루(c, monkeypatch, "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    행 = c.q("SELECT invested_krw, twr_index, recon_ok FROM paper_daily WHERE as_of = '2026-10-16'")[0]
    assert 행[0] == 2_000_000.0
    assert 행[1] == pytest.approx(1.0)  # 예전엔 2.0 — 입금이 수익으로 셌다
    # 검산은 어긋남으로 남긴다 — 시계가 어긋난 날을 사람이 보게(25.1668)
    assert 행[2] == 0
    assert "입금 장부 어긋남" in c.q("SELECT recon_note FROM paper_daily WHERE as_of = '2026-10-16'")[0][0]
    # 시각 비교였다면 달랐을 몫은 진단으로 남는다
    assert 기록["accounts"]["A"]["flow_clock_gap"] == pytest.approx(1_000_000.0)



@pytest.mark.parametrize("고침", [
    "UPDATE paper_cash_flows SET amount_krw = 300000 WHERE request_key = 'k1'",
    "DELETE FROM paper_cash_flows WHERE request_key = 'k1'",
])
def test_지난_입금_행을_고치거나_지우면_검산이_잡는다(monkeypatch: pytest.MonkeyPatch, 고침: str) -> None:
    """25.1668 — 합 차이만 검산에 넘기면 입금 항이 지워져 이것도 "맞음" 이었다(교차검증 [필수])."""
    c = Client(accounts=("A",))
    for 키 in ("k0", "k1"):  # 입금 둘 — 하나를 지워도 계좌가 "입금 없음" 으로 쉬지 않게
        c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                       " VALUES ('2026-10-14', 500000, 'deposit', '2026-10-14T00:00:00+00:00', ?)", [키])  # fmt: skip
    _하루(c, monkeypatch, "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    c.conn.execute(고침)
    _하루(c, monkeypatch, "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    assert c.q("SELECT recon_ok FROM paper_daily WHERE as_of = '2026-10-16'")[0][0] == 0


def test_평소에는_맞음(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=("A",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 500000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-15', 200000, 'deposit', '2026-10-15T05:00:00.000Z', 'k2')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    assert c.q("SELECT recon_ok, twr_index FROM paper_daily WHERE as_of = '2026-10-16'")[0] == (1, 1.0)


def test_취소는_시각이_실행_시작_뒤로_찍혀도_이번_실행에_든다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1668 — 웹 시계가 러너보다 앞서 단계 전에 들어간 취소가 `지금` 뒤로 찍혀도 빼지 않는다(빼면 취소된 돈으로 샀다)."""
    c = Client(accounts=("A",))
    for 키 in ("k0", "k1"):
        c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                       " VALUES ('2026-10-14', 500000, 'deposit', '2026-10-14T00:00:00+00:00', ?)", [키])  # fmt: skip
    _하루(c, monkeypatch, "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, cancels_id, created_at, request_key)"
                   " VALUES ('2026-10-15', -500000, 'cancel', 2, '2026-10-15T23:30:05.000Z', 'k2')")  # fmt: skip
    # 입금은 여전히 실행 시작 뒤면 다음 실행 몫
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-15', 300000, 'deposit', '2026-10-15T23:30:05.000Z', 'k3')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    assert c.q("SELECT invested_krw FROM paper_daily WHERE as_of = '2026-10-16'")[0][0] == 500_000.0


def test_입금과_그_취소가_둘_다_실행_시작_뒤면_둘_다_다음_실행(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1669 — 취소만 남으면 입금·현금이 −50만으로 셌다(교차검증 [권고])."""
    c = Client(accounts=("A",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k0')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-15', 500000, 'deposit', '2026-10-15T23:30:01.000Z', 'k1')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, cancels_id, created_at, request_key)"
                   " VALUES ('2026-10-15', -500000, 'cancel', 2, '2026-10-15T23:30:02.000Z', 'k2')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    행 = c.q("SELECT invested_krw, cash_krw FROM paper_daily WHERE as_of = '2026-10-16'")[0]
    assert 행 == (1_000_000.0, 1_000_000.0)


def test_취소가_가리키는_입금_행이_지워지면_취소는_남아_검산이_잡는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1673 — 25.1669 는 가리키는 입금이 이번 목록에 없으면 취소를 뺐다. 입금 행이 지워진 경우도 빼서 시각 입금·장부
    입금이 둘 다 0 이 돼 "맞음" 이었고, 투자금이 웹(장부 전체 합)과 영구히 어긋났다(교차검증 [필수])."""
    c = Client(accounts=("A",))
    for 키, 금액 in (("k0", 1000000), ("k1", 500000)):
        c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                       " VALUES ('2026-10-14', ?, 'deposit', '2026-10-14T00:00:00+00:00', ?)", [금액, 키])  # fmt: skip
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, cancels_id, created_at, request_key)"
                   " VALUES ('2026-10-14', -500000, 'cancel', 2, '2026-10-14T00:00:01+00:00', 'k2')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-15", "2026-10-14", "2026-10-14T23:30:00+00:00")
    c.conn.commit()
    c.conn.execute("PRAGMA foreign_keys = OFF")  # 손 SQL 로 지운 장면 — 외래키가 막지 않는 DB 를 흉내(`[확인필요]` Turso)
    c.conn.execute("DELETE FROM paper_cash_flows WHERE request_key = 'k1'")
    _하루(c, monkeypatch, "2026-10-16", "2026-10-15", "2026-10-15T23:30:00+00:00")
    행 = c.q("SELECT invested_krw, recon_ok FROM paper_daily WHERE as_of = '2026-10-16'")[0]
    웹합 = c.q("SELECT SUM(amount_krw) FROM paper_cash_flows")[0][0]
    assert 행 == (웹합, 0) and 웹합 == 500_000.0
