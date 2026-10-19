"""시세 표(`prices`)와 잇는 질의가 시세를 통째로 훑지 않는다 — 질의 계획으로 고정 (docs/infra.md 25.1536).

`sell_flags.LOW_TOUCH_SQL`(25.1419)이 `positions p JOIN prices pr` 이라 SQLite 플래너가 `positions.stock_id`(정수 기본 키)를
안쪽에 두려고 `prices` 를 통째로 훑었다 — 실행마다 약 1,370만 행, 국내·미국 하루 두 번. 2026-10-15 부터 Turso 월 읽기
한도(5억 행)를 나흘 만에 9할 넘게 채웠다. 실제 마이그레이션으로 만든 빈 DB 의 계획은 운영(libSQL, 같은 SQLite 플래너)과
같은 모양을 고른다(통계 없음 — `ANALYZE` 를 하지 않는다).
"""

from __future__ import annotations

import glob
import sqlite3
from pathlib import Path

import pytest

from batch.jobs import desk, paper, sell_flags, signals, verdicts, weekly_summary

뿌리 = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    for f in sorted(glob.glob(str(뿌리 / "migrations" / "*.sql"))):
        c.executescript(Path(f).read_text(encoding="utf-8"))
    return c


def _계획(c: sqlite3.Connection, sql: str) -> list[str]:
    return [str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN " + sql, [None] * sql.count("?"))]


#: 시세와 잇는 배치 질의 상수 — 새로 더하면 여기에도 넣는다
질의들 = {
    "sell_flags.LOW_TOUCH_SQL": sell_flags.LOW_TOUCH_SQL,
    "verdicts.HOLE_SQL": verdicts.HOLE_SQL,
    "desk.ADJ_FILLS_SQL": desk.ADJ_FILLS_SQL,
    "weekly_summary.SECTOR_ROWS_SQL": weekly_summary.SECTOR_ROWS_SQL,
    "signals.RECENT_PRICES_FOR_SQL": signals.RECENT_PRICES_FOR_SQL,
    "paper.SPLIT_FROM_SQL": paper.SPLIT_FROM_SQL,
}


@pytest.mark.parametrize("이름", sorted(질의들))
def test_시세를_통째로_훑지_않는다(db: sqlite3.Connection, 이름: str) -> None:
    계획 = _계획(db, 질의들[이름])
    별칭 = {"sell_flags.LOW_TOUCH_SQL": "pr"}.get(이름, "p")
    assert f"SCAN {별칭}" not in 계획, f"{이름} 가 prices 를 통째로 훑는다: {계획}"
    assert any(x.startswith(f"SEARCH {별칭} USING") for x in 계획), f"{이름} 가 prices 를 색인으로 찾지 않는다: {계획}"


def test_저가_터치는_보유를_먼저_읽는다(db: sqlite3.Connection) -> None:
    """보유(몇 줄)를 바깥에, 시세는 (종목, 날짜) 색인으로 — CROSS JOIN 이 순서를 지킨다"""
    계획 = _계획(db, sell_flags.LOW_TOUCH_SQL)
    assert 계획[0] == "SCAN p" and "(stock_id=? AND date=?)" in 계획[1]
