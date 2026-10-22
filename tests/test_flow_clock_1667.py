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
    assert 행[2] == 1
    # 시각 비교였다면 달랐을 몫은 진단으로 남는다
    assert 기록["accounts"]["A"]["flow_clock_gap"] == pytest.approx(1_000_000.0)
