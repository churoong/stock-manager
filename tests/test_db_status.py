"""운영 DB 상태 스크립트: 쓰기 질의 거절과, 기본 점검 질의가 실제 스키마에서 도는지."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import db_status  # noqa: E402

from batch.core import db  # noqa: E402
from tests.test_portfolio_job import MemClient  # noqa: E402


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from stocks limit 3;",
        "WITH x AS (SELECT 1) SELECT * FROM x",
    ],
)
def test_읽기_질의는_허용(sql: str) -> None:
    assert db_status.is_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "DELETE FROM trades",
        "SELECT 1; DELETE FROM trades",
        "WITH x AS (DELETE FROM trades RETURNING *) SELECT * FROM x",
        "UPDATE stocks SET name_ko = 'x'",
        "PRAGMA table_info(stocks)",
        "ATTACH DATABASE 'x' AS y",
    ],
)
def test_쓰기_질의는_거절(sql: str) -> None:
    assert not db_status.is_read_only(sql)


def test_기본_점검이_실제_스키마에서_돈다(capsys: pytest.CaptureFixture[str]) -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    assert db_status.run(db_status.CHECKS, client=mem) == 0
    out = capsys.readouterr().out
    assert "오류" not in out and "거절" not in out
    assert out.count("== ") == len(db_status.CHECKS)
