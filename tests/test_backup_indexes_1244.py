"""문서의 `gunzip | sqlite3` 복원도 색인을 되살린다 (docs/infra.md 25.1244, 백업 감사 재현).

백업이 `schema_migrations` 를 담으므로 되살린 뒤 마이그레이션은 아무것도 다시 만들지 않는다 — 색인은 덤프에 있어야 한다.
"""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

from batch.core import db
from scripts import backup_db as b
from scripts.restore_backup import statements
from scripts.restore_drill import SqliteClient


def test_전체_백업을_sqlite3_로_되살리면_색인이_그대로다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = str(tmp_path / "src.db")
    c = SqliteClient(src)
    db.apply_migrations(c)  # type: ignore[arg-type]
    c.conn.commit()
    원본 = {r[0] for r in c.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    c.close()
    monkeypatch.setattr(b, "TursoClient", lambda: SqliteClient(src))
    out = tmp_path / "out"
    assert b.run("full", out) == 0
    덤프 = next(out.glob("*.sql.gz"))
    dst = sqlite3.connect(str(tmp_path / "dst.db"))
    with gzip.open(덤프, "rt", encoding="utf-8") as f:
        dst.executescript(f.read())  # == gunzip -c | sqlite3
    되살린 = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    assert len(원본) > 30 and 원본 <= 되살린
    assert "idx_signal_checks_date" in 되살린
    # 되살리기 스크립트는 여전히 INSERT 만 읽는다
    assert all(sql.upper().startswith("INSERT") for _, sql in statements(덤프))
