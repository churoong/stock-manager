"""테스트의 SQLite 가 외래키를 켜고 도는지 (docs/infra.md 25.226, `tests/conftest.py`)."""

from __future__ import annotations

import sqlite3


def test_외래키가_켜져_있다() -> None:
    """미끼 — conftest 의 감싸기가 실제로 먹는지. 꺼져 있으면 테스트가 외래키를 한 번도 안 본다."""
    assert sqlite3.connect(":memory:").execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_외래키가_실제로_막는다() -> None:
    연결 = sqlite3.connect(":memory:")
    연결.executescript("CREATE TABLE a (id INTEGER PRIMARY KEY); CREATE TABLE b (a_id INTEGER REFERENCES a (id));")
    try:
        연결.execute("INSERT INTO b VALUES (99)")
    except sqlite3.IntegrityError:
        return
    raise AssertionError("없는 행을 가리키는 행이 들어갔다 — 외래키가 꺼져 있다")
