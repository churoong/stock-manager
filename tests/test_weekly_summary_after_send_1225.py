"""주간 요약: 보낸 뒤 사후 분석 진척 기록이 실패해도 실행은 실패가 아니고, 다시 보내지 않는다 (docs/infra.md 25.1225)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import weekly_summary as ws
from tests.test_portfolio_job import MemClient


def _준비(monkeypatch: pytest.MonkeyPatch) -> tuple[MemClient, list]:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    보냄: list = []
    monkeypatch.setattr(ws.telegram, "send", lambda t, *a, **k: 보냄.append(t) or [1])
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (_끝(끝), 0, []))

    def 실패(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("Turso 시간 초과")

    monkeypatch.setattr(ws.db, "set_setting", 실패)
    return mem, 보냄


def _끝(끝: list) -> int:
    끝.append("2026-10-11")
    return 0


def test_보낸_뒤_기록_실패는_partial_이고_다시_보내지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem, 보냄 = _준비(monkeypatch)
    assert ws.run() == 0
    상태 = mem.conn.execute("SELECT status FROM batch_runs WHERE job_name = 'weekly_summary'").fetchall()
    assert 상태 == [("partial",)]
    assert ws.run() == 0  # 따라잡기·재실행
    assert len(보냄) == 1
