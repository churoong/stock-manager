"""한도 오류는 작업이 실패로 닫지 않고 `guard()` 가 skipped·0 으로 닫는다 (docs/infra.md 25.1223, 감사 재현)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from batch.core import db
from batch.core.entry import guard
from batch.core.turso import TursoError
from batch.jobs import disclosures_us, universe

한도 = TursoError("Turso HTTP 400: SQL 실패: Operations are forbidden — blocked, upgrade your plan")


class _가짜:
    def close(self) -> None: ...


def _기록(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    닫음: list = []
    건너뜀: list = []
    monkeypatch.delenv("SKIP_ON_D1", raising=False)
    monkeypatch.delenv("TURSO_READ_ESTIMATE", raising=False)
    monkeypatch.setattr(db, "apply_migrations", lambda c: None)
    monkeypatch.setattr(db, "start_batch_run", lambda c, **k: 42)
    monkeypatch.setattr(db, "finish_batch_run", lambda c, rid, **k: 닫음.append(k.get("status")))
    monkeypatch.setattr(db, "close_current_run", lambda status, text: 건너뜀.append(status))
    return 닫음, 건너뜀


def test_유니버스_한도는_건너뜀(monkeypatch: pytest.MonkeyPatch) -> None:
    닫음, 건너뜀 = _기록(monkeypatch)
    monkeypatch.setattr(universe, "TursoClient", _가짜)
    monkeypatch.setattr(universe.cal, "previous_session", lambda m: date(2026, 10, 9))

    def 터짐(client, bas_dd):  # noqa: ANN001, ANN202
        raise 한도

    monkeypatch.setattr(universe, "refresh_kr_master", 터짐)
    assert guard(lambda: universe.run("KR")) == 0
    assert "failed" not in 닫음 and 건너뜀 == ["skipped"]


def test_미국_공시_한도는_실패로_닫지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    닫음, 건너뜀 = _기록(monkeypatch)
    monkeypatch.setattr(disclosures_us, "TursoClient", _가짜)

    def 터짐(client, n):  # noqa: ANN001, ANN202
        raise 한도

    monkeypatch.setattr(disclosures_us, "collect", 터짐)
    assert guard(lambda: disclosures_us.run()) == 0
    assert "failed" not in 닫음 and 건너뜀 == ["skipped"]


@pytest.mark.parametrize("이름", ["disclosures_kr", "weekly_summary"])
def test_나머지도_한도면_실패로_닫지_않는다(이름: str) -> None:
    글 = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / f"{이름}.py").read_text(encoding="utf-8")
    assert "if not db.quota_reason(exc):" in 글


def test_한도를_먼저_실패로_닫고_올리는_자리가_없다() -> None:
    """`failed` 로 닫은 뒤 올리면 `guard()` 는 열린 기록이 없어 건너뜀·0 으로 끝나 DB 와 워크플로가 어긋난다."""
    꼴 = re.compile(
        r"except Exception as exc:\n\s+db\.finish_batch_run\(client, \w+, status=\"failed\", error_text=str\(exc\)\)\n\s+raise\n"
    )
    걸림 = [p.name for p in sorted((Path(__file__).resolve().parents[1] / "batch" / "jobs").glob("*.py"))
            if 꼴.search(p.read_text(encoding="utf-8"))]  # fmt: skip
    assert 걸림 == []
