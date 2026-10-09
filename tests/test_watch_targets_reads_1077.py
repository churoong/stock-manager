"""참고 분석 대상 고르기가 미국 주식 행마다 최신 스냅샷을 다시 구하지 않는다 (docs/infra.md 25.1077).

2026-10-08·09 미국 일일 배치 한 번이 Turso 3,400만 행을 읽었다. 하위 작업 합은 약 214만 — 나머지는 일일 배치 안에서
`analyze_extra.run` 이 **실행 기록을 열기 전에** 부르는 `TARGETS_SQL` 이었다. 바깥 루프가 stocks 전체였고 최신 스냅샷 MAX 가
`s2.country = s.country` 상관 서브쿼리라 미국 주식 행마다 다시 돌았다.
"""

from __future__ import annotations

import sqlite3

from batch.jobs import analyze_extra
from tests import test_sql_schema as t

옛_SQL = (
    "SELECT s.id, s.ticker, s.country, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.asset_type"
    " FROM watchlist w JOIN stocks s ON s.id = w.stock_id"
    " WHERE s.country = ? AND s.asset_type = 'stock' AND NOT EXISTS (SELECT 1 FROM universe_members u"
    "   WHERE u.stock_id = s.id AND u.included = 1 AND u.snapshot_date = (SELECT MAX(u2.snapshot_date)"
    "   FROM universe_members u2 JOIN stocks s2 ON s2.id = u2.stock_id WHERE s2.country = s.country))"
    " ORDER BY w.added_at LIMIT ?"
)


def _세계() -> sqlite3.Connection:
    """미국 600·국내 300 종목. 최신 스냅샷 날짜는 국내가 늦다 — 색인 맨 위가 국내 행이라 미국 MAX 가 그 행들을 걸어 내려간다."""
    db = t.스키마올리기()
    행 = []
    for i in range(1, 901):
        나라 = "US" if i <= 600 else "KR"
        행.append((i, f"T{i}", "NYSE" if 나라 == "US" else "KOSPI", 나라, "USD" if 나라 == "US" else "KRW"))
    db.executemany("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                   " VALUES (?, ?, ?, ?, ?, 't', 't')", 행)  # fmt: skip
    for 날, 나라 in (("2026-10-07", "US"), ("2026-10-08", "KR")):
        ids = range(1, 601) if 나라 == "US" else range(601, 901)
        db.executemany("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                       " VALUES (?, ?, 1, 'X', 't')", [(날, i) for i in ids if i not in (5, 7, 650)])  # fmt: skip
    # 관심 종목: 유니버스 밖 미국 5·7, 안 10, 유니버스 밖 국내 650
    db.executemany("INSERT INTO watchlist (stock_id, added_at) VALUES (?, ?)",
                   [(5, "a"), (7, "b"), (10, "c"), (650, "d")])  # fmt: skip
    return db


def _단계(db: sqlite3.Connection, sql: str, args: list) -> tuple[list, int]:
    n = [0]

    def 셈() -> int:
        n[0] += 1
        return 0

    db.set_progress_handler(셈, 1)
    try:
        rows = db.execute(sql, args).fetchall()
    finally:
        db.set_progress_handler(None, 1)
    return rows, n[0]


def test_결과는_같고_읽는_양은_훨씬_적다() -> None:
    db = _세계()
    옛, 옛단계 = _단계(db, 옛_SQL, ["US", 50])
    새, 새단계 = _단계(db, analyze_extra.TARGETS_SQL, ["US", "US", 50])
    assert [r[0] for r in 새] == [r[0] for r in 옛] == [5, 7]
    assert 새단계 * 20 < 옛단계, (새단계, 옛단계)  # 미국 행 수 × 국내 행 수 꼴이 사라졌다
    국내, _ = _단계(db, analyze_extra.TARGETS_SQL, ["KR", "KR", 50])
    assert [r[0] for r in 국내] == [650]


def test_관심_종목이_바깥이고_최신_스냅샷은_상관이_아니다() -> None:
    db = t.스키마올리기()
    계획 = [r[-1] for r in db.execute("EXPLAIN QUERY PLAN " + analyze_extra.TARGETS_SQL, ["US", "US", 50])]
    assert 계획[0].startswith("SCAN w"), 계획
    assert not any("CORRELATED SCALAR SUBQUERY" in x and "MAX" in x for x in 계획)
    assert sum("CORRELATED" in x for x in 계획) <= 1, 계획  # NOT EXISTS 하나만 상관이다
