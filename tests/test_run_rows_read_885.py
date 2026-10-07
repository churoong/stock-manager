"""실행마다 읽은 행 수를 step_log 에 남긴다 (docs/infra.md 25.885).

Turso 월 읽기 한도(5억 행)의 어디에 얼마가 드는지 몰라 줄일 곳을 고를 수 없었다. 2026-10-02 복귀 날 하루 5,500만 행.
"""

from __future__ import annotations

import json

from batch.core import db
from batch.core.turso import ReadCounter
from tests.test_scores_job import _sqlite_client


def _로그(c, 번호: int) -> dict:
    return json.loads(c.execute("SELECT step_log FROM batch_runs WHERE id = ?", [번호]).rows[0][0])


def test_끝낼_때_그_사이_읽은_행을_적는다() -> None:
    c = _sqlite_client()
    번호 = db.start_batch_run(c, job_name="scores", market="KR", trade_date="2026-10-01")  # type: ignore[arg-type]
    ReadCounter().add(1234)  # 다른 클라이언트가 읽은 것도 같은 프로세스면 센다
    db.finish_batch_run(c, 번호, status="success", step_log={"scored": 5})  # type: ignore[arg-type]
    assert _로그(c, 번호) == {"scored": 5, db.ROWS_READ_KEY: 1234}


def test_step_log_을_안_주면_있던_것을_그대로_둔다() -> None:
    """실패 정리(`_닫기`)는 step_log 을 안 준다 — 진행 상태를 그대로 둔다(25.668). 읽기량은 적지 않는다."""
    c = _sqlite_client()
    번호 = db.start_batch_run(c, job_name="backtest", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.note_progress(c, 번호, {"done": 3})  # type: ignore[arg-type]
    ReadCounter().add(77)
    db.finish_batch_run(c, 번호, status="failed")  # type: ignore[arg-type]
    assert _로그(c, 번호) == {"progress": {"done": 3}}
