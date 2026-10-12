"""배치의 "나라별 최신 날짜" 질의는 날짜 색인 내림차순 첫 행에서 멈춘다 (docs/infra.md 25.1214).

`SELECT MAX(x.date) FROM t x JOIN stocks s … WHERE s.country = ?` 는 통계(ANALYZE)가 있으면
종목 → (종목, 날짜) 색인 순으로 그 나라 행을 **전부** 읽는다(25.1204·25.1212 와 같은 뒤집힘).
`CROSS JOIN … ORDER BY x.date DESC LIMIT 1` 로 적으면 날짜 색인을 뒤에서부터 훑다 첫 행에서 멈춘다.
"""

from __future__ import annotations

import ast
import pathlib
import re

from batch.core import db
from batch.jobs import verdicts
from tests import test_sql_schema as t
from tests.test_portfolio_job import MemClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAT = re.compile(r"MAX\(\s*\w+\.(\w+)\s*\)\s+FROM\s+(\w+)\s+\w+\s+JOIN\s+(stocks|etfs)\b", re.I)
#: 바꾸지 않는 것 — 판 번호(같은 날짜 안에서 고름)와 날짜 앞 색인이 없는 표(etf_profiles 는 (etf_id, 날짜) 뿐)
EXEMPT_COLS = {"calc_version"}
EXEMPT_TABLES = {"etf_profiles"}


def _모두() -> list[tuple[str, int, str]]:
    out = []
    for p in sorted((ROOT / "batch").rglob("*.py")):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                for m in PAT.finditer(n.value):
                    if m[1] not in EXEMPT_COLS and m[2] not in EXEMPT_TABLES:
                        out.append((str(p.relative_to(ROOT)), n.lineno, m[0]))
    return out


def test_배치에_MAX_JOIN_최신일_질의가_없다() -> None:
    assert _모두() == []


def _통계_계획(sql: str, args: list) -> list[str]:
    con = t.스키마올리기()
    con.execute("ANALYZE")
    con.execute("DELETE FROM sqlite_stat1")
    for 표, 줄 in (("stocks", "9000"), ("scores", "3000000"), ("universe_members", "400000")):
        con.execute("INSERT INTO sqlite_stat1 VALUES (?, NULL, ?)", [표, 줄])
    con.execute("ANALYZE sqlite_master")
    return [r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args)]


def test_통계가_있어도_점수_최신일은_종목별로_훑지_않는다() -> None:
    계획 = _통계_계획(verdicts.SCORES_SQL, ["KR", "KR"])
    assert not any(x.startswith("SEARCH sc2 USING") and "stock_id=?" in x for x in 계획), 계획


def test_통계가_있어도_유니버스_최신일은_종목별로_훑지_않는다() -> None:
    계획 = _통계_계획("SELECT " + db.latest_snapshot_sql(), ["KR"])
    assert not any(x.startswith("SEARCH um USING") and "stock_id=?" in x for x in 계획), 계획


def test_답은_나라를_가린다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid, 나라 in ((1, "KR"), (2, "US")):
        c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                  " VALUES (?, ?, 'X', ?, 'KRW', 't', 't')", [sid, f"T{sid}", 나라])  # fmt: skip
    for sid, d in ((1, "2026-10-05"), (2, "2026-10-09"), (1, "2026-10-02")):
        c.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                  " VALUES (?, ?, 1, 'KRW', 't')", [d, sid])  # fmt: skip
    q = "SELECT " + db.latest_snapshot_sql()
    assert c.execute(q, ["KR"]).fetchone()[0] == "2026-10-05"
    assert c.execute(q, ["US"]).fetchone()[0] == "2026-10-09"
    assert c.execute(q, ["JP"]).fetchone()[0] is None
    q2 = "SELECT " + db.snapshot_as_of_sql()
    assert c.execute(q2, ["KR", "2026-10-04"]).fetchone()[0] == "2026-10-02"
