"""예약보다 일찍 온 실행은 지연 0 이다 — 겨울에 미국 첫 cron 이 예약 시각보다 한 시간 일러 "지연 -32분" 이 적혔다
(docs/infra.md 25.1857, 결함 루프 [참고])."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from batch.core import db
from batch.jobs import daily
from tests.test_portfolio_job import MemClient


def test_예약보다_일찍_시작하면_지연_0() -> None:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    앞 = (datetime.now(UTC) + timedelta(minutes=32)).isoformat()
    뒤 = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    a = db.start_batch_run(m, job_name="x", market="US", trade_date=None, trigger="t", scheduled_for=앞)  # type: ignore[arg-type]
    b = db.start_batch_run(m, job_name="y", market="US", trade_date=None, trigger="t", scheduled_for=뒤)  # type: ignore[arg-type]
    지연 = dict(m.conn.execute("SELECT id, delay_seconds FROM batch_runs").fetchall())
    for rid in (a, b):
        db.finish_batch_run(m, rid, status="success")  # type: ignore[arg-type]
    assert 지연[a] == 0 and 290 <= 지연[b] <= 310


def test_일일_배치의_리포트_지연도_0_아래로_가지_않는다() -> None:
    assert "max(0, int((datetime.now(UTC) - datetime.fromisoformat(예약)).total_seconds()))" in inspect.getsource(
        daily
    )
