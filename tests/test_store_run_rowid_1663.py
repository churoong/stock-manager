"""25.1663 — 백테스트 결과 행 번호를 못 받았을 때 대비 질의는 그 실행·그 전략으로 좁힌다 (25.1662 교차검증 [권고]).

핵심 H 와 월 백테스트가 나란히 돌면(25.1662) 표 전체의 MAX(id) 가 상대 실행의 행을 집어 자본곡선을 엉뚱한 행에 붙였다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import backtest as job
from batch.services import backtest as bt


class _행번호없음:
    """INSERT 뒤 행 번호를 주지 않는 백엔드 흉내 — 대비 질의 길을 탄다."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        return ResultSet(columns=[d[0] for d in cur.description or []], rows=[tuple(r) for r in cur.fetchall()],
                         last_insert_rowid=None)  # fmt: skip

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]


def test_상대_실행이_뒤에_행을_넣어도_자기_행에_곡선을_붙인다(monkeypatch) -> None:  # noqa: ANN001
    c = _행번호없음()
    db.apply_migrations(c)  # type: ignore[arg-type]
    결과 = bt.BacktestResult(curve=[("2026-01-02", 1.0), ("2026-01-05", 1.1)], rebalances=[], costs=bt.Costs.zero())
    요약 = bt.summarize(결과)
    진짜 = c.execute

    def 끼어듦(sql: str, args: list[Any] | None = None) -> ResultSet:
        rs = 진짜(sql, args)
        if sql.startswith("INSERT INTO backtest_runs"):  # 내 행을 넣은 바로 뒤 상대 실행(핵심 H)이 행을 하나 넣는다
            칸 = [x.strip() for x in job._RUN_COLS.split(",")]
            고른 = ", ".join("'core_h'" if x == "strategy" else "'other'" if x == "group_id" else x for x in 칸)
            진짜(f"INSERT INTO backtest_runs ({', '.join(칸)}) SELECT {고른} FROM backtest_runs ORDER BY id LIMIT 1")
        return rs

    monkeypatch.setattr(c, "execute", 끼어듦)
    job.store_run(c, "KR", "momentum", 결과, 요약, 20, "mine", [], "2026-10-22T00:00:00+00:00")  # type: ignore[arg-type]
    내행 = c.conn.execute("SELECT id FROM backtest_runs WHERE group_id = 'mine'").fetchone()[0]
    곡선 = {r[0] for r in c.conn.execute("SELECT DISTINCT run_id FROM backtest_curves")}
    assert 곡선 == {내행}
