"""날마다 통째로 훑던 질의가 색인을 탄다 (docs/infra.md 25.1036, 마이그레이션 0056).

Turso 는 훑은 행을 읽기로 센다. 실행 기록·증권사 의견은 날마다 늘어나는 표라 훑기가 달마다 커진다.
"""

from __future__ import annotations

from batch.core import price_replica as rep
from batch.jobs import verdicts
from tests import test_sql_schema as t


def _계획(sql: str) -> str:
    conn = t.스키마올리기()
    return " / ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, [None] * sql.count("?")).fetchall())


def test_사본_맞추기의_실행_기록_찾기가_끝난_시각_색인을_탄다() -> None:
    import inspect

    src = inspect.getsource(rep.touched_ranges)
    sql = "SELECT step_log FROM batch_runs WHERE finished_at >= ? AND step_log LIKE ?"
    assert sql in src.replace('"\n        "', "")  # 같은 질의를 보고 있는지
    계획 = _계획(sql)
    assert "idx_batch_runs_finished" in 계획 and "SCAN batch_runs" not in 계획, 계획


def test_종목_분석의_증권사_의견이_날짜_색인을_탄다() -> None:
    계획 = _계획(verdicts.OPINIONS_SQL)
    assert "idx_kr_opinions_date" in 계획 and "SCAN kr_opinions" not in 계획, 계획
