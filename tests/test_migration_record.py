"""마이그레이션 적용 기록은 본문과 같은 묶음에 실린다 (docs/infra.md 25.335)."""

from __future__ import annotations

from typing import Any

from batch.core import db
from tests.test_portfolio_job import MemClient


class 묶음만_받는_클라이언트(MemClient):
    """`batch` 로 온 것만 실행하고, 따로 온 쓰기(`execute`)는 연결이 끊긴 것처럼 잃는다."""

    def __init__(self) -> None:
        super().__init__()
        self.in_batch = False

    def batch(self, statements: list[tuple[str, list[Any]]]):
        self.in_batch = True
        try:
            return super().batch(statements)
        finally:
            self.in_batch = False

    def execute(self, sql: str, args: list[Any] | None = None):
        if not self.in_batch and sql.lstrip().upper().startswith("INSERT"):
            raise ConnectionError("본문 뒤 따로 보낸 기록이 끊겼다")
        return super().execute(sql, args)


def test_적용_기록이_본문과_함께_들어간다() -> None:
    mem = 묶음만_받는_클라이언트()
    applied = db.apply_migrations(mem)  # type: ignore[arg-type]
    assert applied == [p.stem for p in db.migration_files()]
    recorded = {r[0] for r in mem.conn.execute("SELECT version FROM schema_migrations")}
    assert recorded == set(applied)


def test_두_번_돌려도_다시_적용하지_않는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    assert db.apply_migrations(mem) == []  # type: ignore[arg-type]
