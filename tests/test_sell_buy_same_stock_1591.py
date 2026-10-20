"""같은 종목의 열린 매도와 열린 매수가 서로를 몰라 판 종목을 새로 들던 두 길 (docs/paper.md 2.2, docs/infra.md 25.1591 — 위험 점검·배분 감사 [권고] 1).

지키는 것
- 길 A: 장 끝난 뒤 손 실행이 낸 매수 회차가 열린 채 다음 아침 같은 종목 손절 매도가 나가면, 남은 회차를 거둔다(만료) —
  예전엔 매도는 그때 보유만 팔고 회차가 같은 장에 함께 차서 손절 종목을 새로 들었다
- 길 B: 하한가로 미뤄진 열린 매도가 있는 종목은 "판 뒤" 쉼으로 — 예전엔 플래그가 풀린 다음 배치가 다시 사 같은 장에 함께 찼다
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _시세, _신호, 설정


@pytest.fixture(autouse=True)
def _틀(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


def _돌림(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, today: str, now: str, s: object | None,
          after_close: bool = False) -> dict:  # fmt: skip
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date.fromisoformat(today))
    _줄, 기록 = job.run_step(c, market="KR", trade_date=trade_date, late=False, signal_rows=[s] if s else [],
                            settings=설정, price_series={}, series_unknown={}, allocate=s is not None,
                            now=datetime.fromisoformat(now), after_close=after_close)  # fmt: skip
    return 기록


def _계좌() -> Client:
    c = Client(accounts=("B",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-01', 1000000, 'deposit', '2026-10-01T00:00:00+00:00', 'k1')")  # fmt: skip
    return c


def test_길A_손절_매도를_내면_남은_매수_회차를_거둔다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    신호 = lambda d: dataclasses.replace(_신호(), as_of_date=d, close_date=d)  # noqa: E731
    _시세(c, "2026-10-13", 10_000, 10_000)
    _돌림(c, monkeypatch, "2026-10-13", "2026-10-14", "2026-10-13T23:30:00+00:00", 신호("2026-10-13"))
    _시세(c, "2026-10-14", 10_000, 9_900)  # 1회차만 참
    # 10-15 아침 실패 → 장 끝난 뒤 손 실행(10-16 장 회차)
    _돌림(c, monkeypatch, "2026-10-14", "2026-10-15", "2026-10-15T07:00:00+00:00", 신호("2026-10-14"), after_close=True)
    _시세(c, "2026-10-15", 9_300, 9_200)  # 원가 대비 −8% — 단기 손절
    기록 = _돌림(c, monkeypatch, "2026-10-15", "2026-10-16", "2026-10-15T23:30:00+00:00", 신호("2026-10-15"))
    assert 기록["accounts"]["B"].get("buys_withdrawn", 0) >= 1
    assert not c.q("SELECT id FROM paper_orders WHERE side = 'buy' AND status = 'open'")
    _시세(c, "2026-10-16", 9_100, 9_000)
    _돌림(c, monkeypatch, "2026-10-16", "2026-10-19", "2026-10-18T23:30:00+00:00", 신호("2026-10-16"))
    산 = sum(q for s, q in c.q("SELECT side, quantity FROM paper_fills") if s == "buy")
    판 = sum(q for s, q in c.q("SELECT side, quantity FROM paper_fills") if s == "sell")
    assert 산 == 판  # 판 종목을 새로 들고 남지 않는다


def _봉(c: Client, day: str, o: float, cl: float, chg: float | None) -> None:
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at,"
                   " change_pct) VALUES (1, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't', ?)",
                   [day, o, max(o, cl), min(o, cl), cl, chg])  # fmt: skip


def _중기(day: str, close: float) -> report_picks.SignalRow:
    hi, mid, lo = close, round(close * 0.98), round(close * 0.96)
    return dataclasses.replace(_신호(), horizon="mid", as_of_date=day, close_date=day, close=close, buy_zone_high=hi,
                               buy_zone_low=lo, tranche_plan=[{"step": 1, "ratio": 0.4, "price": hi, "amount": None},
                                                              {"step": 2, "ratio": 0.3, "price": mid, "amount": None},
                                                              {"step": 3, "ratio": 0.3, "price": lo, "amount": None}])  # fmt: skip


def test_길B_미뤄진_매도가_있는_종목은_다시_사지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    _봉(c, "2026-10-06", 10_000, 10_000, 0.0)
    _돌림(c, monkeypatch, "2026-10-06", "2026-10-07", "2026-10-07T00:00:00+00:00", _중기("2026-10-06", 10_000))
    _봉(c, "2026-10-07", 10_000, 9_700, -3.0)
    _돌림(c, monkeypatch, "2026-10-07", "2026-10-08", "2026-10-08T00:00:00+00:00", None)
    _봉(c, "2026-10-08", 12_500, 12_800, 30.9)  # 중기 목표 도달 → 매도
    _돌림(c, monkeypatch, "2026-10-08", "2026-10-09", "2026-10-09T00:00:00+00:00", None)
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE side = 'sell'") == [(1,)]
    _봉(c, "2026-10-12", 8_960, 8_960, -30.0)  # 하한가 — 매도 미룸
    _돌림(c, monkeypatch, "2026-10-12", "2026-10-13", "2026-10-13T00:00:00+00:00", _중기("2026-10-12", 8_960))
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE side = 'sell' AND status = 'open'") == [(1,)]
    assert not c.q("SELECT id FROM paper_orders WHERE side = 'buy' AND status = 'open'")
    assert c.q("SELECT kind FROM paper_decisions WHERE decided_on = '2026-10-12' AND reason LIKE '판 뒤%'") == [("skip",)]
