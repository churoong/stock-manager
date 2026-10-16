"""사면 바로 팔 후보는 사지 않는다 · 오늘 후보도 기업행위 경계를 본다 (docs/paper.md 2.2, docs/infra.md 25.1480).

감사(10-19 첫 체결 판정 전): 매수 쪽이 계좌의 매도 규칙을 보지 않아, 적자 전환 공시가 이미 난 종목을 A(value)·C(momentum)가 사고
다음 배치에 재무악화로 팔고, 쉼 뒤 다시 사는 회전이 생길 수 있었다.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.jobs import paper as job
from batch.services import report_picks
from tests.test_company_job_1466 import _하루, 헌장
from tests.test_paper_job_1445 import Client, _신호


def _적자전환(c: Client, sid: int) -> None:
    for fy, oi in ((2024, 100.0), (2025, -5.0)):
        c.conn.execute("INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
                       " receipt_no, currency, unit, operating_income, source, fetched_at) VALUES (?, ?, '11011', 'FY', 1,"
                       " '2026-03-15', ?, 'KRW', 'KRW', ?, 't', 't')", [sid, fy, f"r{sid}{fy}", oi])  # fmt: skip


def _종가(c: Client, sid: int, d: str = "2026-10-14") -> None:
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (?, ?, 10000, 10000, 10000, 10000, 1000, 'KRW', 't', 't')", [sid, d])  # fmt: skip


def test_prebuy_flags_by_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client()
    _종가(c, 1)
    _적자전환(c, 1)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    rows = [_신호()]
    오늘 = date(2026, 10, 15)
    for rule, 기대 in (("value", {1: ["재무악화"]}), ("momentum", {1: ["재무악화"]}), ("all", {1: ["재무악화"]}),
                      ("core", {}), ("time", {})):  # fmt: skip
        assert job.prebuy_sell_flags(c, "KR", rule, rows, set(), set(), {}, None, 오늘, {}) == 기대, rule
    # 이미 든 종목·멈춘 종목은 여기서 보지 않는다(보유는 sell_decisions 가 본다)
    assert job.prebuy_sell_flags(c, "KR", "value", rows, {1}, set(), {}, None, 오늘, {}) == {}
    assert job.prebuy_sell_flags(c, "KR", "value", rows, set(), {1}, {}, None, 오늘, {}) == {}


def test_company_skips_candidate_it_would_sell_next_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A(가치투자사, value)가 위원회에서 가결한 1 에 적자 전환 공시가 있다 → 사지 않고 skip 으로 남긴다."""
    c = Client()
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    c.conn.execute("UPDATE paper_accounts SET charter_json = ?, rule_version = 2, sell_rule = 'value' WHERE id = 'A'",
                   [json.dumps(헌장)])  # fmt: skip
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    _종가(c, 1)
    _적자전환(c, 1)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'A' AND stock_id = 1")[0][0] == 0
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'A' AND stock_id = 1") == [
        ("skip", "사면 바로 매도 규칙에 걸림: 재무악화")]
    assert 기록["accounts"]["A"]["prebuy_sell"] == 1
    # B(core)는 재무악화를 따르지 않으니 그대로 산다
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B' AND stock_id = 1")[0][0] > 0


def test_new_candidates_are_checked_for_corporate_action_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    """오늘 후보가 분할 의심이면 사지 않는다 — 예전엔 보유·미체결만 보아 B 가 사고 다음 날 주문만 멈춤으로 닫혔다 (감사 [권장] 2)."""
    c = Client()
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    본 = []

    def 멈춤(_client, ids, *_a, **_k):
        본.append(set(ids))
        return {1} & set(ids)

    monkeypatch.setattr(job, "frozen_ids", 멈춤)
    _하루(c, monkeypatch, "2026-10-14")
    assert 1 in 본[0]
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE stock_id = 1")[0][0] == 0
