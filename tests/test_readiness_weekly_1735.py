"""주간 요약의 실전 준비도 한 장 (docs/infra.md 25.1735, 2026-10-23 사용자 "그렇게 진행해줘" — 아이디어 ④)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.core import db
from batch.services import desk
from tests.test_portfolio_job import MemClient


def _관문() -> dict:
    return desk.gate(
        {"H": [("2026-10-16", 1.0), ("2026-10-17", 0.999), ("2026-10-20", 0.996)],
         "R1": [("2026-10-16", 1.0), ("2026-10-20", 1.001)], "R2": [("2026-10-16", 1.0), ("2026-10-20", 0.997)]},
        "H", version=2, recon_bad_days=0, batch_failed=0, kis={"kept_pct": 88.0, "n": 40},
    )  # fmt: skip


def test_관문_항목마다_지금_목표_남은_거리() -> None:
    줄 = desk.readiness_lines(_관문(), desk.readiness(runs=None, kis=[]))
    assert 줄[0].startswith("🧭 실전 준비도 — 1단계 관문(모의 성적·무사고) 1/8 충족 · 지주사 판 2, ")
    assert "  ⬜ 모의 기간: 3 / 126회 (123회 남음)" in 줄
    assert "  ⬜ R(무작위) 대비: -0.30%p (목표 > 0)" in 줄
    assert "  ✅ 운영 무사고: 사고 0일 (최근 60일, 목표 0)" in 줄
    assert "  ⬜ KIS 실현율: 88% / 90% (표본 40/100)" in 줄
    assert "  ⬜ 지수 대비: 아직 모름" in 줄  # 모르는 값을 0 이라 하지 않는다
    assert 줄[-2] == "  준비 목록 갖춤 2 · 일부 1 · 연습 중 1 · 아직 1 · 사용자 결정 2"
    assert 줄[-1].startswith("  그다음: 2단계")
    assert desk.readiness_lines(None, None, "읽기 실패") == ["🧭 실전 준비도 — 관문을 세지 못했습니다 (읽기 실패)"]


def test_주간_요약_맨_위에_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import paper as jp
    from batch.jobs import weekly_summary as ws

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    mem.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES ('2026-10-23', ?, 't')",
                     [json.dumps({"gate": _관문(), "readiness": desk.readiness(runs=None, kis=[])})])  # fmt: skip
    보낸: list[str] = []
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    monkeypatch.setattr(ws.telegram, "send", lambda t, *a, **k: 보낸.append(t) or [1])
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))
    monkeypatch.setattr(jp, "summary_section", lambda *_a, **_k: ["[가상모의투자] 2026-10-23 평가"])

    class 고정(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 10, 25, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(ws, "datetime", 고정)
    assert ws.run(force=True) == 0
    본문 = 보낸[0]
    assert "🧭 실전 준비도 — 1단계 관문" in 본문
    assert 본문.index("🧭 실전 준비도") < 본문.index("[가상모의투자]")
