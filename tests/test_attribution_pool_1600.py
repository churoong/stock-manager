"""성과 분해 표본 — 판 종목에 hold_flag 를 함께 적지 않고, 이미 든 종목의 skip 은 "안 낸 새 후보" 가 아니다
(docs/paper.md 9·10장, docs/infra.md 25.1600 — 매도·성과 분해 감사 [권고] 2·3)."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _신호, 설정


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, signals: bool = True) -> None:
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    job.run_step(c, market="KR", trade_date=trade_date, late=False, signal_rows=[_신호()] if signals else [],
                 settings=설정, price_series={}, series_unknown={}, allocate=signals,
                 now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"))  # fmt: skip


def _시세(c: Client, day: str, o: float, cl: float) -> None:
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (1, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [day, o, max(o, cl), min(o, cl), cl])  # fmt: skip


def test_판_날에는_hold_flag_를_함께_적지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """G(trend) 가 고점 +50% 뒤 추적손절로 파는 날 — 같은 판정의 목표도달(+39%) 을 "들고 갔다" 로 또 적었다."""
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c = Client(accounts=("G",))
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 15_000, 15_000)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    _시세(c, "2026-10-19", 13_900, 13_900)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    그날 = c.q("SELECT kind FROM paper_decisions WHERE decided_on = '2026-10-19' AND kind IN ('sell', 'hold_flag')")
    assert 그날 == [("sell",)]


def test_이미_든_종목의_skip_은_새_후보_풀_밖() -> None:
    def 줄(sid: int, kind: str, reason: str, ret: float, held: bool | None) -> dict:
        det = {} if held is None else {"held": held}
        return {"stock_id": sid, "market": "KR", "decided_on": "2026-10-01", "kind": kind, "reason": reason,
                "ret_20d": ret, "bench_20d": 0.0, "detail_json": json.dumps(det), "control_pct": None}  # fmt: skip

    결정 = [줄(1, "buy", "2부 배분", 5.0, False), 줄(2, "skip", "투자 여력 부족", 0.0, False),
            줄(3, "skip", "투자 여력 부족", 15.0, True)]  # fmt: skip
    a = pp.attribution(결정, [])
    assert a["skipped"]["n"] == 1 and a["skipped"]["avg"] == pytest.approx(0.0)  # 든 3 은 새 후보 아님
    assert 3 not in {d["stock_id"] for d in pp._에피소드(결정) if d["kind"] == "skip" and not pp._추가매수(d)}


def test_결정_단위_선정_증거의_후보_풀에서도_뺀다() -> None:
    """산 것 +5, 새 후보 셋 0, 이미 든 종목 skip +10 — 든 종목이 풀에 들면 분위가 100 이 아니라 75 였다."""
    줄들 = []
    for day in ("2026-10-01", "2026-10-02", "2026-10-05"):
        줄들.append({"stock_id": 1, "market": "KR", "decided_on": day, "kind": "buy", "reason": "2부 배분",
                    "ret_20d": 5.0, "bench_20d": 0.0, "detail": {"held": False}})  # fmt: skip
        for sid in (2, 3, 4):
            줄들.append({"stock_id": sid, "market": "KR", "decided_on": day, "kind": "skip", "reason": "투자 여력 부족",
                        "ret_20d": 0.0, "bench_20d": 0.0, "detail": {"held": False}})  # fmt: skip
        줄들.append({"stock_id": 9, "market": "KR", "decided_on": day, "kind": "skip", "reason": "투자 여력 부족",
                    "ret_20d": 10.0, "bench_20d": 0.0, "detail": {"held": True}})  # fmt: skip
    r = pp.selection_evidence(줄들)
    assert r["days"] == 3 and list(r["blocks"].values()) == [pytest.approx(100.0)]


def test_제외_skip_줄에도_보유_여부를_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """2부 제외 skip 줄에는 `held` 가 없어 이미 든 종목인지 몰랐다."""
    import inspect

    src = inspect.getsource(job.buy_orders)
    assert '"note": e.detail[:200],' in src and '"held": r0.stock_id in 든}' in src
