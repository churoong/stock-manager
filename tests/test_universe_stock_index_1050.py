"""종목별 유니버스 이력 찾기가 색인을 탄다 (docs/infra.md 25.1050, 마이그레이션 0060).

2026-10-08 미국 일일 배치 한 번이 Turso 3,399만 행을 읽었는데 하위 작업 합은 약 210만 행이었다. 남은 몫은 못 받은 심볼마다
`universe_members` 전체를 훑던 `COUNT(*)` 였다(로컬 EXPLAIN: SCAN … COVERING INDEX (snapshot_date, stock_id)).
"""

from __future__ import annotations

import inspect

from batch.jobs import analyze_extra, daily
from tests import test_sql_schema as t


def _계획(sql: str, n: int) -> str:
    conn = t.스키마올리기()
    return " / ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, [None] * n).fetchall())


def test_못_받은_심볼의_스냅샷_수_세기() -> None:
    assert "NEVER_PRICED_SQL" in inspect.getsource(daily._drop_never_priced)
    계획 = _계획(daily.NEVER_PRICED_SQL, daily.NEVER_PRICED_SQL.count("?"))
    assert "SCAN u" not in 계획 and "idx_universe_stock" in 계획, 계획


def test_참고_분석의_유니버스_판정() -> None:
    sql = next(v for k, v in vars(analyze_extra).items() if isinstance(v, str) and "u2.stock_id = s.id" in v)
    계획 = _계획(sql, sql.count("?"))
    assert "idx_universe_stock" in 계획, 계획
