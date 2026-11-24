"""기록을 닫을 때 step_log 을 NULL 로 덮지 않는다 (docs/infra.md 25.668, 감사)."""

from __future__ import annotations

import json

from batch.core import db
from tests.test_kr_yahoo_fallback import _client


def test_실패로_닫아도_진행_상태가_남는다() -> None:
    c = _client()
    run_id = db.start_batch_run(c, job_name="backtest", market=None, trade_date="2026-09-29")  # type: ignore[arg-type]
    c.conn.execute("UPDATE batch_runs SET step_log = '{\"진행\": \"3/10\"}' WHERE id = ?", [run_id])
    db.fail_open_runs("죽음")
    상태, 글 = c.conn.execute("SELECT status, step_log FROM batch_runs WHERE id = ?", [run_id]).fetchone()
    # 진행 상태는 그대로 남고, 실행 지문 칸만 더해진다(25.2277)
    assert 상태 == "failed" and json.loads(글) == {"진행": "3/10", db.RUN_FINGERPRINT_KEY: db.run_fingerprint()}


def test_주면_덮는다() -> None:
    c = _client()
    run_id = db.start_batch_run(c, job_name="x", market=None, trade_date="2026-09-29")  # type: ignore[arg-type]
    db.finish_batch_run(c, run_id, status="success", step_log={"a": 1})  # type: ignore[arg-type]
    로그 = json.loads(c.conn.execute("SELECT step_log FROM batch_runs WHERE id = ?", [run_id]).fetchone()[0])
    로그.pop(db.ROWS_READ_KEY)  # 실행별 읽기량은 따로 붙는다 (25.885)
    로그.pop(db.RUN_FINGERPRINT_KEY)  # 실행 지문도 (25.2277)
    assert 로그 == {"a": 1}
