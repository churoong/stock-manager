"""주간 요약 확인 실행(dry-run)은 `dryrun` — 그 주 일요일 발송을 막지 않고, 경고 되풀이에 세지 않는다 (docs/infra.md 25.1304)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import weekly_summary as ws
from tests.test_portfolio_job import MemClient


def test_확인_실행이_예약_발송을_막지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    보냄: list = []
    monkeypatch.setattr(ws.telegram, "send", lambda t, *a, **k: 보냄.append(t) or [1])
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))
    assert ws.run(dry_run=True) == 0
    assert mem.conn.execute("SELECT status FROM batch_runs WHERE job_name = 'weekly_summary'").fetchall() == [
        ("dryrun",)
    ]
    assert ws.run() == 0  # 예약 실행
    assert len(보냄) == 1


def test_확인_실행의_경고는_되풀이가_아니다() -> None:
    경고 = ("KIS 수급 응답이 비어 있습니다 — 이 줄은 머리말 길이를 넘기려고 조금 길게 적는다",)
    runs = [ws.Run("daily_kr", "KR", "success", "2026-10-07T00:00:00", None, 경고),
            ws.Run("daily_kr", "KR", "dryrun", "2026-10-08T00:00:00", None, 경고)]  # fmt: skip
    assert ws.repeated_reasons(runs) == []
    assert ws.repeated_reasons([*runs, runs[0]]) != []
