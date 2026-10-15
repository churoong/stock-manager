"""모의 투자사 — 일일 모의 단계에 투자위원회 연결 (docs/company.md 3·5장, docs/infra.md 25.1466).

실제 마이그레이션을 올린 메모리 SQLite 에서: 헌장이 있는 회사만 위원회를 열고, 가결 종목만 사고, 회의록·조사 자료·부결 결정이 한 묶음으로
남고, 같은 날 다시 돌아도 두 번 쓰지 않고, 헌장이 틀린 회사는 판 1 그대로 도는지.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _신호, 설정

헌장 = {
    "name": "가치투자사", "sell_rule": "core", "weights": {"research": 1, "quant": 1, "sentiment": 1},
    "research_mix": {"value": 1}, "lead": "research", "veto": [], "max_mdd": None,
    "threshold": 1.0, "agenda": 20, "top_n": 5,
}  # fmt: skip


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    c = Client()
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    c.conn.execute("UPDATE paper_accounts SET charter_json = ?, rule_version = 2 WHERE id = 'A'", [json.dumps(헌장)])
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    return c


def _신호들() -> list[report_picks.SignalRow]:
    좋은 = replace(_신호(), factor_scores={"value": 90.0, "momentum": 90.0})
    나쁜 = replace(_신호(), stock_id=3, ticker="000660", name="종목3", factor_scores={"value": 10.0, "momentum": 10.0})
    return [좋은, 나쁜]


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str) -> tuple[list[str], dict]:
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market="KR", trade_date=trade_date, late=False, signal_rows=_신호들(), settings=설정, price_series={},
        series_unknown={}, allocate=True, now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip


def test_company_buys_only_what_committee_passed(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    # A(가치투자사): 리서치·퀀트 찬성(신호 두 종목 안 100 백분위) → 1 가결. 3 은 둘 다 반대 → 부결
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'A'")} == {1}
    assert {r[0] for r in c.q("SELECT DISTINCT reason FROM paper_orders WHERE account_id = 'A'")} == {"위원회 가결"}
    # B(헌장 없음)는 판 1 그대로 두 종목 다
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'B'")} == {1, 3}
    # 회의록 한 장 · 조사 자료(시장·날짜·종목마다 한 번) · 부결 결정(판 번호와 위원회 기록)
    회의 = c.q("SELECT account_id, minutes_json FROM paper_meetings")
    assert [a for a, _m in 회의] == ["A"]
    assert json.loads(회의[0][1])["memo"].startswith("가치투자사 위원회: 안건 2 · 가결 1")
    assert c.q("SELECT stock_id FROM paper_dossiers ORDER BY stock_id") == [(1,), (3,)]
    부결 = c.q("SELECT kind, reason, rule_version, detail_json FROM paper_decisions WHERE account_id = 'A' AND stock_id = 3")
    assert 부결[0][0] == "skip" and 부결[0][1].startswith("위원회:") and 부결[0][2] == 2
    assert json.loads(부결[0][3])["committee"]["opinions"]["research"][0] == -1
    산것 = c.q("SELECT detail_json, rule_version FROM paper_decisions WHERE account_id = 'A' AND stock_id = 1")
    assert json.loads(산것[0][0])["committee"]["vote"] == pytest.approx(2.0) and 산것[0][1] == 2
    assert c.q("SELECT DISTINCT rule_version FROM paper_decisions WHERE account_id = 'B'") == [(1,)]
    # 운용역 메모 한 줄
    assert any(x.startswith("🏛 A 가치투자사 위원회:") for x in 줄)
    assert 기록["accounts"]["A"]["committee"]["approved"] == 1

    # 같은 날 다시 — 회의록·결정·주문이 두 번 생기지 않는다
    전 = c.q("SELECT (SELECT COUNT(*) FROM paper_orders), (SELECT COUNT(*) FROM paper_decisions),"
             " (SELECT COUNT(*) FROM paper_meetings), (SELECT COUNT(*) FROM paper_dossiers)")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-14")
    assert c.q("SELECT (SELECT COUNT(*) FROM paper_orders), (SELECT COUNT(*) FROM paper_decisions),"
               " (SELECT COUNT(*) FROM paper_meetings), (SELECT COUNT(*) FROM paper_dossiers)") == 전  # fmt: skip


def test_meeting_alone_blocks_rerun(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """회의록만 먼저 써진 날(결정 없음) 다시 돌아도 새로 쓰지 않는다 — 가드가 회의록도 센다 (docs/company.md 3장 8)."""
    c.conn.execute("INSERT INTO paper_meetings VALUES ('A', 'KR', '2026-10-14', 'h', '{}', 't')")
    _, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert 기록["accounts"]["A"]["orders"] == "이미 냄"
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'A'") == [(0,)]


def test_bad_charter_runs_rule_one_and_says_why(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """헌장이 위원회 규칙에 어긋나면(한 부서 40% 넘음) 그 회사는 판 1 그대로 돌고 기록에 까닭을 남긴다."""
    나쁜 = {**헌장, "weights": {"research": 5, "quant": 1}}
    c.conn.execute("UPDATE paper_accounts SET charter_json = ? WHERE id = 'A'", [json.dumps(나쁜)])
    _, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert any("40%" in e for e in 기록["accounts"]["A"]["charter_errors"])
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'A'")} == {1, 3}
    assert c.q("SELECT COUNT(*) FROM paper_meetings") == [(0,)]
