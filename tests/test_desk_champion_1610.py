"""운용 요약 감사 [필수] 3 (docs/desk.md 6장, docs/infra.md 25.1610).

지키는 것: 판 이력을 못 읽은 날은 챔피언 교체 후보도 비운다 — 판 시작을 모르면 옛 판 성적까지 섞어 블록 규칙을 넘었다
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from batch.jobs import desk as job
from tests.test_paper_job_1445 import Client


def _db() -> Client:
    c = Client(accounts=None)
    날 = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(120)]
    for i, d in enumerate(날):
        cc = (1 + 0.004 * i) if i < 100 else (1.4 + 0.001 * (i - 100))  # C: 판 1 에서 크게 이김, 판 3 에선 H 만큼
        for aid, t in (("H", 1 + 0.001 * i), ("C", cc)):
            c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                           " pnl_krw, twr_index, positions_json, recon_ok, updated_at)"
                           " VALUES (?, ?, 1000, 1000, 0, 1000, 0, ?, '[]', 1, 't')", [aid, d, t])  # fmt: skip
    c.conn.execute("UPDATE paper_accounts SET rule_version = 3 WHERE id = 'C'")
    c.conn.executemany("INSERT INTO paper_rule_versions (account_id, rule_version, since, noted_at) VALUES (?, ?, ?, 't')",
                       [("C", 1, ""), ("C", 3, 날[99]), ("H", 2, "")])  # fmt: skip
    return c


def _요약(c: Client) -> dict:
    job.write(c, "KR", datetime(2026, 4, 30, 23, 40, tzinfo=UTC), trade_date="2026-04-30", session_date="2026-05-01",
              step_log={}, warnings=[])  # fmt: skip
    return json.loads(c.q("SELECT summary_json FROM paper_desk ORDER BY as_of DESC LIMIT 1")[0][0])


def test_판_이력을_못_읽으면_챔피언_후보도_비운다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "H")
    정상 = {r["id"]: r for r in _요약(_db())["champion"]}["C"]
    assert not 정상["blocks_ok"]  # 지금 판(판 3)만 보면 H 만큼
    monkeypatch.setattr(job, "_판이력", lambda *_a, **_k: ({}, "OperationalError: 표 없음"))
    s = _요약(_db())
    assert s["champion"] == [] and "챔피언 후보도 비움" in s["gate_error"]
    assert s["risk"]  # 위험판은 판과 무관해 그대로
