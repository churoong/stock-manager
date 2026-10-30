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



@pytest.mark.parametrize("오류", ["일부", "모름"])
def test_일부만_갔거나_모르면_partial_이고_다시_보내지_않는다(monkeypatch: pytest.MonkeyPatch, 오류: str) -> None:
    """25.1982 (결함 루프 10-29) — 예전엔 `failed` 로 닫혀 재실행이 요약 전체를 한 번 더 보냈다. 진척 표시는 적지 않는다"""
    mem, 보냄 = _준비(monkeypatch)
    적음: list = []
    monkeypatch.setattr(ws.db, "set_setting", lambda c, k, v: 적음.append(k))

    def 깨진_보냄(t, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        보냄.append(t)
        if 오류 == "일부":
            raise ws.telegram.TelegramPartialError("2조각 가운데 1조각만 보냈습니다", [11], 2)
        모름 = ws.telegram.TelegramUncertainError("첫 조각 응답 시간 초과 — 2·3번째는 보냄")
        모름.sent_ids = [12, 13]  # type: ignore[attr-defined]  # `_모름으로` 처럼 확실히 간 뒤 조각
        raise 모름

    monkeypatch.setattr(ws.telegram, "send", 깨진_보냄)
    assert ws.run() == 0
    줄 = mem.conn.execute("SELECT status, step_log FROM batch_runs WHERE job_name = 'weekly_summary'").fetchall()
    assert [r[0] for r in 줄] == ["partial"]
    assert ("1조각만 보냄" if 오류 == "일부" else "발송 여부 모름") in 줄[0][1]
    import json

    assert json.loads(줄[0][1])["sent_chunks"] == (1 if 오류 == "일부" else 2)
    assert 적음 == []  # 사후 분석·월간·실행 품질 진척을 적지 않는다
    assert ws.run() == 0  # 재실행·따라잡기
    assert len(보냄) == 1


def test_사후_분석이_깨진_주는_보고_기록을_넘기지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2018 (결함 루프 10-30) — `load_losses` 는 첫 질의 직후 새 보고 기록을 넣는다. 손실별 질의가 깨져도 발송이
    성공하면 그 기록이 적혀, 그 주 손실이 다음 주부터 읽히지 않고 영영 빠졌다"""
    mem, 보냄 = _준비(monkeypatch)
    적음: list = []
    monkeypatch.setattr(ws.db, "set_setting", lambda c, k, v: 적음.append(k))

    def 깨짐(c, now, through, 끝):  # noqa: ANN001, ANN202
        끝.append({"KR": "2026-09-22", "US": "2026-09-15"})  # 첫 질의 뒤 기록을 넣은 다음
        raise RuntimeError("시세 질의 시간 초과")  # 손실별 질의에서 깨짐

    monkeypatch.setattr(ws, "load_losses", 깨짐)
    assert ws.run() == 0
    assert "지난 추천 사후 분석을 못 했습니다" in 보냄[0]
    assert ws.POSTMORTEM_THROUGH_KEY not in 적음
