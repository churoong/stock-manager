"""리포트를 보낸 뒤 KIS 단계에서 실행이 죽으면 예비 실행이 발송만 건너뛰고 이어 돈다 (docs/infra.md 25.2144,
11-05 결함 회차 [권고]).

예전엔 `reports.sent_exists` 가 참이면 무조건 "이미 보냈다" 로 빠져, 25.1475 가 발송 뒤로 옮긴 KIS모의투자 베끼기(첫째·도전자)와
운용 요약이 그날 끝내 돌지 않았다. 이제 그날 그 시장 운용 요약 칸(`paper_desk.markets.<시장>.trade_date`)이 있을 때만 빠진다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import daily
from batch.jobs import desk as desk_job
from tests.test_portfolio_job import MemClient

지금 = datetime(2026, 11, 5, 0, 35, tzinfo=UTC)  # 09:35 KST 예비


def _desk(mem: MemClient, as_of: str, market: str, trade_date: str) -> None:
    mem.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, 't')",
                     [as_of, json.dumps({"markets": {market: {"trade_date": trade_date}}})])  # fmt: skip


def test_운용_요약_칸으로_발송_뒤_단계가_끝났는지_본다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    assert desk_job.post_send_done(mem, "KR", "2026-11-04", 지금) is False
    _desk(mem, "2026-11-05", "US", "2026-11-04")  # 다른 시장 칸만 — 아직
    assert desk_job.post_send_done(mem, "KR", "2026-11-04", 지금) is False
    _desk(mem, "2026-11-04", "KR", "2026-11-04")  # 전날(자정 넘긴 실행) 줄도 본다
    assert desk_job.post_send_done(mem, "KR", "2026-11-04", 지금) is True


def test_못_읽으면_예전처럼_건너뛴다() -> None:
    class 깨짐:
        def execute(self, *a: object, **k: object) -> None:
            raise RuntimeError("x")

    assert desk_job.post_send_done(깨짐(), "KR", "2026-11-04", 지금) is True


def test_보냈지만_발송_뒤_단계가_안_끝났으면_이어_돈다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from batch.core import calendar as cal

    mem = MemClient()
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]

    monkeypatch.setattr(daily.cal, "decide", lambda market, now=None, force=False: cal.RunDecision(
        should_run=True, reason="예약", trade_date="2026-11-04", session_date="2026-11-05"))  # fmt: skip
    monkeypatch.setattr(daily.db, "has_successful_run", lambda *a, **k: False)
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: True)
    monkeypatch.setattr(desk_job, "post_send_done", lambda *a, **k: False)

    class 이어돎(Exception):
        pass

    def 시작(*a: object, **k: object) -> None:
        raise 이어돎

    monkeypatch.setattr(daily.db, "start_batch_run", 시작)
    with pytest.raises(이어돎):
        daily.run("KR")
    assert "발송 없이 이어 돕니다" in capsys.readouterr().out


def test_이어_돌_때는_발송_블록을_건너뛰고_발송_뒤_단계는_돈다() -> None:
    """발송 블록 첫 줄이 표지를 던지고, 그 except 는 보낸 것으로 적은 뒤 아래 `_kis와_요약()` 로 간다(정적)"""
    글 = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    블록 = 글.index("            if 발송끝남:\n                raise _이미보냄")
    assert 블록 < 글.index("message_ids = telegram.send(message)", 블록)
    잡음 = 글.index("        except _이미보냄:\n            step_log[\"sent\"] = ALREADY_SENT_NOTE")
    assert 잡음 < 글.index("        _kis와_요약()\n        try:\n            for 줄 in _주의_출력", 잡음)
    assert "KIS모의투자 베끼기·운용 요약이 빠졌을 수 있습니다" in daily.timeout_text(15)
