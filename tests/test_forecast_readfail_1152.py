"""예측 성적표 — 기준 종가 읽기가 한 번 실패하면 그 기록일이 `through` 만 넘어가 영영 채점되지 않던 것
(docs/infra.md 25.1152, 감사 재현)."""

from __future__ import annotations

import json

from batch.core import db
from batch.jobs import verdicts as job
from tests.test_portfolio_job import MemClient


def _mem(fail_sql: str) -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'x', 'KRW', 'active', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
              " VALUES (1, '2026-09-07', 100, 100, 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
              " VALUES ('2026-09-07', 1, 'KR', 100, ?, 't')",
              [json.dumps({"capm": {"1": {"exp": 0.01, "lo68": -0.05, "hi68": 0.08}}})])  # fmt: skip
    원래 = mem.execute
    남은실패 = [1]

    def 가끔(sql, args=None):
        if sql == fail_sql and 남은실패:
            남은실패.pop()
            raise RuntimeError("Turso 429")
        return 원래(sql, args)

    mem.execute = 가끔  # type: ignore[method-assign]
    return mem


def test_기준_종가_읽기에_실패한_날은_다음에_다시_본다() -> None:
    for 실패 in (job.CLOSE_SQL, job.LOG_ROWS_SQL):
        mem = _mem(실패)
        state: dict = {"logged": ["2026-09-07"]}
        prior: dict = {}
        경고: list[str] = []
        job.evaluate_due(mem, "KR", "2026-10-07", {1: {"close": 60.0}}, prior, state, 경고)  # type: ignore[arg-type]
        assert state["through"] == {} and state["market"] == {} and 경고
        job.evaluate_due(mem, "KR", "2026-10-08", {1: {"close": 60.0}}, prior, state, 경고)  # type: ignore[arg-type]
        assert state["market"], 실패  # 둘째 날 채점됐다
        assert state["through"]["1"] == "2026-09-07"


def test_기다리던_종목도_읽기_실패면_그대로_기다린다() -> None:
    mem = _mem(job.CLOSE_SQL)
    state: dict = {"logged": [], "pending": [["2026-09-07", 1, 1]]}
    job._pending(mem, "KR", "2026-10-07", {1: {"close": 60.0}}, {}, {}, set(), state, [])  # type: ignore[arg-type]
    assert state["pending"] == [["2026-09-07", 1, 1]]
