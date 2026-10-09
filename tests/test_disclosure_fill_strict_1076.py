"""Turso 복귀 메우기의 공시 단계는 DART 점검 중이면 실패로 끝난다 (docs/infra.md 25.1076).

메우기 끝 표시(`turso-return.yml` 5단계, `success()`)는 한 번 적히면 다시 돌지 않는다. 2-0b 가 DART 점검(상태 800)으로
모두 실패해도 0 으로 끝나면 표시가 적혀, D1 운영 기간의 국내 공시 구멍이 영영 남았다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from batch.jobs import disclosures_kr

뿌리 = Path(__file__).resolve().parent.parent


class _가짜:
    def close(self) -> None:
        pass


@pytest.fixture()
def 조용히(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disclosures_kr, "TursoClient", _가짜)
    monkeypatch.setattr(disclosures_kr.db, "apply_migrations", lambda c: None)
    monkeypatch.setattr(disclosures_kr.db, "start_batch_run", lambda c, **k: 1)
    monkeypatch.setattr(disclosures_kr.db, "finish_batch_run", lambda c, i, **k: None)


def test_경고가_있으면_strict_는_1_일일_배치는_0(monkeypatch: pytest.MonkeyPatch, 조용히: None) -> None:
    경고 = ["공시 수집: 5/5사 실패", "상태 800: 시스템 점검으로 인한 서비스가 중지 중입니다."]
    monkeypatch.setattr(disclosures_kr, "collect", lambda c, d, e: (5, 0, 경고, []))
    assert disclosures_kr.run(7, strict=True) == 1
    assert disclosures_kr.run(7) == 0  # 일일 배치 안에서는 다음 날 7일 창이 겹쳐 메운다


def test_경고가_없으면_strict_도_0(monkeypatch: pytest.MonkeyPatch, 조용히: None) -> None:
    monkeypatch.setattr(disclosures_kr, "collect", lambda c, d, e: (5, 12, [], []))
    assert disclosures_kr.run(30, strict=True) == 0


def test_복귀_메우기가_strict_로_부른다() -> None:
    글 = (뿌리 / ".github" / "workflows" / "turso-return.yml").read_text(encoding="utf-8")
    줄 = next(x for x in 글.splitlines() if "batch.jobs.disclosures_kr" in x and "python -m" in x)
    assert "--strict" in 줄
    # 끝 표시는 앞 단계가 모두 성공했을 때만 — 이것이 strict 가 구멍을 막는 이유다
    assert "if: ${{ success() && steps.ret.outputs.moved == 'true' }}" in 글.split("5. 메우기 끝 표시")[1][:600]
