"""25.1690 (결정 원장 감사 [권고]) — 관문·챔피언·월간 복기는 성과 분해가 **적힌** 마지막 평가 행을 읽는다.

그날 성과 분해가 실패해 마지막 행이 비면 선정 칸이 "모름" 이 되고 챔피언 후보가 모두 빠졌다(웹은 이미 적힌 것을 읽었다).
"""

from __future__ import annotations

import json
import sqlite3

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import desk as desk_job


class _M:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def execute(self, sql: str, args: list | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

    def batch(self, statements: list) -> list:
        return [self.execute(s, a) for s, a in statements]

    def close(self) -> None:
        pass


def test_마지막_행이_비면_적힌_마지막_성과_분해를_읽는다() -> None:
    m = _M()
    db.apply_migrations(m)  # type: ignore[arg-type]
    ins = ("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
           " twr_index, positions_json, recon_ok, updated_at, attribution_json) VALUES ('H', ?, 1, 1, 0, 1, 0, 1, ?, 1,"
           " 't', ?)")  # fmt: skip
    m.conn.execute(ins, ["2026-10-20", "[]", json.dumps({"evidence": {"n": 7}})])
    m.conn.execute(ins, ["2026-10-21", '[{"stock_id": 5}]', None])  # 그날 성과 분해 실패
    행 = next(r for r in m.execute(desk_job.LATEST_SQL).dicts() if r["account_id"] == "H")
    assert json.loads(행["positions_json"]) == [{"stock_id": 5}]  # 보유는 마지막 행 그대로
    assert json.loads(행["attribution_json"]) == {"evidence": {"n": 7}}
    계획 = m.conn.execute("EXPLAIN QUERY PLAN " + desk_job.LATEST_SQL).fetchall()
    assert not any("SCAN y" in str(r) or "SCAN paper_daily" in str(r) for r in 계획), 계획


def test_대신_읽은_성과_분해가_옛_판이면_증거는_모름() -> None:
    """25.1696 (25.1690 교차검증 [권고]) — 판을 올린 날부터 성과 분해가 실패하면 옛 판의 증거로 지금 판 선정 칸이 통과할 수
    있었다."""
    assert desk_job._this_version_evidence({"attribution_json": json.dumps({"evidence": {"n": 7}, "rule_version": 2}),
                                            "rule_version": 2}) == {"n": 7}  # fmt: skip
    assert desk_job._this_version_evidence({"attribution_json": json.dumps({"evidence": {"n": 7}, "rule_version": 1}),
                                            "rule_version": 2}) == {}  # fmt: skip
    assert desk_job._this_version_evidence({"attribution_json": json.dumps({"evidence": {"n": 7}}), "rule_version": 1}) \
        == {"n": 7}  # 판을 모르는 옛 행은 판 1
    assert desk_job._this_version_evidence({"attribution_json": None, "rule_version": 3}) == {}

