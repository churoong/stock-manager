"""국내 영업현금흐름 채우기는 실행 시간 예산을 넘으면 남은 행을 다음으로 넘기고 성공으로 닫는다. 재무 워크플로에서는 새 편입
배당 뒤에 돈다 (docs/infra.md 25.1855, 결함 루프).

첫 실행(11-09 KST)은 할 일이 수만 건이라 5000회 상한을 다 쓰는데, 회차마다 DART 전체 재무제표 한 번 + Turso 왕복 넷이라
50~80분 어림 — 재무 잡 상한 60분을 넘겨 같은 잡 뒤의 새 편입 배당·시점 조회 확인이 몇 주 돌지 못할 뻔했다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from batch.core import db
from batch.jobs import kr_cashflow as job
from batch.sources import dart
from batch.sources.yfinance_src import FetchResult
from tests.test_kr_cashflow_1317 import _db, 행

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "financials.yml"


def test_시간_예산을_넘으면_남기고_성공으로_닫는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db()
    mem.conn.execute(
        "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code, consolidated,"
        " payload, source, fetched_at) VALUES (1, '2025-03-10', '20250310000100', 2024, '11011', 1, '{}', 't', 't')"
    )
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(dart, "fetch_full_statement", lambda *a, **k: FetchResult(ok=True, source=dart.SOURCE, data=행))
    시각 = iter([0.0, 0.0, 9999.0, 9999.0])  # 시작, 첫 행(예산 안), 둘째 행부터 예산 밖
    assert job.run(date(2026, 11, 9), budget_seconds=60, clock=lambda: next(시각, 9999.0)) == 0
    상태, 기록 = mem.conn.execute("SELECT status, step_log FROM batch_runs ORDER BY id DESC").fetchone()
    기록 = json.loads(기록)
    assert 상태 == "success" and 기록["calls"] == 1 and 기록["remaining"] == 1 and 기록["time_budget_hit"] is True
    assert not db.get_setting(mem, job.DONE_KEY)  # type: ignore[arg-type]  # 남았으니 끝 아님


def test_예산은_잡_상한_안이다() -> None:
    글 = yaml.safe_load(WF.read_text(encoding="utf-8"))
    assert job.BUDGET_SECONDS <= 25 * 60 < int(글["jobs"]["run"]["timeout-minutes"]) * 60


def test_새_편입_배당이_영업현금흐름보다_먼저_돈다() -> None:
    글 = yaml.safe_load(WF.read_text(encoding="utf-8"))
    단계 = [str(s.get("run", "")) for s in 글["jobs"]["run"]["steps"]]
    배당 = next(i for i, r in enumerate(단계) if "dividends --only-missing" in r)
    현금 = next(i for i, r in enumerate(단계) if "kr_cashflow" in r)
    assert 배당 < 현금
    조건 = 글["jobs"]["run"]["steps"][현금]["if"]
    assert "steps.collect.outcome == 'success'" in 조건 and "!cancelled()" in 조건


def test_호출_상한으로_멈추면_시간_예산_표시가_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1859 (25.1855 교차검증 [참고]) — 두 멈춤 까닭을 가른다(운영 출력으로 회차당 시간을 잴 때 쓴다)"""
    mem = _db()
    mem.conn.execute(
        "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code, consolidated,"
        " payload, source, fetched_at) VALUES (1, '2025-03-10', '20250310000100', 2024, '11011', 1, '{}', 't', 't')"
    )
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(dart, "fetch_full_statement", lambda *a, **k: FetchResult(ok=True, source=dart.SOURCE, data=행))
    assert job.run(date(2026, 11, 9), max_calls=1, clock=lambda: 0.0) == 0
    기록 = json.loads(mem.conn.execute("SELECT step_log FROM batch_runs ORDER BY id DESC").fetchone()[0])
    assert 기록["calls"] == 1 and 기록["remaining"] == 1 and 기록["time_budget_hit"] is False
