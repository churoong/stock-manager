"""전체 백업에도 KIS 접근 토큰·로그인 시도 기록을 담지 않는다 (docs/infra.md 25.1243, 백업 감사 재현)."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from batch.core import db
from scripts import backup_db as b
from scripts.restore_drill import SqliteClient


@pytest.mark.parametrize("범위", ["essential", "full"])
def test_비밀_표는_어느_범위에도_없다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, 범위: str) -> None:
    p = str(tmp_path / "src.db")
    c = SqliteClient(p)
    db.apply_migrations(c)  # type: ignore[arg-type]
    c.conn.execute("INSERT INTO api_tokens VALUES ('kis','SECRET-KIS-TOKEN-xyz','2099-01-01T00:00:00Z','kis_openapi','2026-10-12')")
    c.conn.commit()
    c.close()
    monkeypatch.setattr(b, "TursoClient", lambda: SqliteClient(p))
    out = tmp_path / "out"
    assert b.run(범위, out) == 0
    with gzip.open(next(out.glob("*.sql.gz")), "rt", encoding="utf-8") as f:
        글 = f.read()
    assert "SECRET-KIS-TOKEN-xyz" not in 글
    assert "INSERT INTO login_attempts" not in 글
