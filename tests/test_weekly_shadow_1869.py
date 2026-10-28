"""주간 요약 — 단타·스윙 그림자 장부 절 (docs/paper.md 16.2, docs/infra.md 25.1869).

사용자 2026-10-28 "단기간에 성과를 확인할 수가 없어" — 그림자 장부 집계는 공개 Actions 로그 한 줄과 설정에만 있어 사용자가
받아 보는 곳이 없었다. 일요일 요약에 기준일과 함께 싣는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from batch.core import db
from batch.jobs import paper as jp
from batch.jobs import signal_outcomes as so
from batch.jobs import weekly_summary as ws
from batch.services import shadow as sh
from tests.test_portfolio_job import MemClient


@pytest.fixture
def mem() -> MemClient:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    m.close = lambda: None  # type: ignore[method-assign]
    return m


def _집계(as_of: str) -> dict:
    거래 = [sh.Trade(1, "d", "day_v1", "closed", net=0.02, sessions=1, reason="close"),
            sh.Trade(2, "d", "day_v1", "closed", net=-0.01, sessions=1, reason="close"),
            sh.Trade(1, "d", "swing_close_v1", "open")]  # fmt: skip
    return {"as_of": as_of, "stats": {r.name: sh.summarize(거래, r.name).as_dict() for r in sh.RULES}}


def test_집계가_없으면_절을_싣지_않는다(mem: MemClient) -> None:
    assert ws.shadow_lines(mem) == []  # type: ignore[arg-type]


def test_나라마다_기준일과_규칙마다_한_줄(mem: MemClient) -> None:
    db.set_setting(mem, f"{so.SHADOW_KEY}_kr", _집계("2026-11-02"))  # type: ignore[arg-type]
    줄 = ws.shadow_lines(mem)  # type: ignore[arg-type]
    assert 줄[0].startswith("단타·스윙 그림자 장부") and "  국내 (2026-11-02 기준)" in 줄
    assert any(x.strip().startswith("day_v1: 끝 2건(2종목) · 승률 50%") for x in 줄)
    assert any(x.strip().startswith("swing_close_v1: 끝난 거래 없음 (진행 1") for x in 줄)
    assert not any("미국" in x for x in 줄)  # 미국 집계가 없으면 그 나라만 빠진다


def test_요약_본문에_실리고_깨져도_요약은_나간다(monkeypatch: pytest.MonkeyPatch, mem: MemClient) -> None:
    db.set_setting(mem, f"{so.SHADOW_KEY}_us", _집계("2026-11-03"))  # type: ignore[arg-type]
    보낸: list[str] = []
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    monkeypatch.setattr(ws.telegram, "send", lambda t, *a, **k: 보낸.append(t) or [1])
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))
    monkeypatch.setattr(jp, "summary_section", lambda c, now: ["[가상모의투자] 이번 주"])

    class 고정(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 11, 7, 22, 41, tzinfo=UTC)

    monkeypatch.setattr(ws, "datetime", 고정)
    assert ws.run(force=True) == 0
    assert "미국 (2026-11-03 기준)" in 보낸[-1] and "day_v1: 끝 2건" in 보낸[-1]

    def 깨짐(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("그림자본문")

    monkeypatch.setattr(ws, "shadow_lines", 깨짐)
    assert ws.run(force=True) == 0
    assert "그림자 장부 절을 만들지 못했습니다 (RuntimeError)" in 보낸[-1] and "그림자본문" not in 보낸[-1]


def test_웹_규칙_글은_사전_등록_규칙을_모두_덮는다() -> None:
    """25.1875 — 웹 그림자 장부 칸(`web/lib/paper.ts` `SHADOW_RULE_TEXT`)이 규칙 정의처(`shadow.RULES`)와 어긋나지 않게"""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "paper.ts").read_text(encoding="utf-8")
    표 = 글[글.index("SHADOW_RULE_TEXT"): 글.index("};", 글.index("SHADOW_RULE_TEXT"))]
    assert all(f"  {r.name}:" in 표 for r in sh.RULES), [r.name for r in sh.RULES]
