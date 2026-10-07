"""실행 번호는 INSERT 응답에서 받는다 — 같은 작업이 겹쳐 돌아도 제 번호다 (docs/infra.md 25.869)."""

from __future__ import annotations

from batch.core import db
from tests.test_scores_job import _sqlite_client


def test_겹쳐_돈_같은_작업도_제_번호를_받는다() -> None:
    c = _sqlite_client()
    읽음: list[str] = []
    원래 = c.execute
    c.execute = lambda sql, args=None: (읽음.append(sql), 원래(sql, args))[1]  # type: ignore[method-assign]
    첫째 = db.start_batch_run(c, job_name="scores", market="KR", trade_date="2026-10-01")  # type: ignore[arg-type]
    둘째 = db.start_batch_run(c, job_name="scores", market="KR", trade_date="2026-10-01")  # type: ignore[arg-type]
    assert 첫째 != 둘째
    assert not [q for q in 읽음 if "MAX(id)" in q]
    for 번호 in (첫째, 둘째):
        db.finish_batch_run(c, 번호, status="success")  # type: ignore[arg-type]
