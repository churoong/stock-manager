"""[확인] 을 누르지 않은 플래그도 속 사유가 나빠지면 NEW 가 다시 붙는다 (docs/infra.md 25.1234, 매도 플래그 감사 재현)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.core import db
from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf
from tests.test_portfolio_job import MemClient


@pytest.mark.parametrize("확인함", [False, True])
def test_나빠지면_NEW(monkeypatch: pytest.MonkeyPatch, 확인함: bool) -> None:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                     " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')")  # fmt: skip
    상태 = {"사유": "시총미달"}
    monkeypatch.setattr(job, "load_holdings", lambda client, today: ([object()], []))
    monkeypatch.setattr(job.sf, "evaluate", lambda h, today, rules: [
        sf.Flag(1, "yellow", "점검", f"빠졌습니다({상태['사유']})", [{"label": "유니버스 제외", "display": 상태["사유"]}])])
    import batch.jobs.portfolio as pfj

    monkeypatch.setattr(pfj, "쓰는_중", lambda c: False)
    job.run(date(2026, 9, 16))
    mem.conn.execute("UPDATE sell_flags SET created_at = '2026-09-15T23:00:00+00:00'")
    if 확인함:
        mem.conn.execute("UPDATE sell_flags SET dismissed_at = '2026-09-16T01:00:00+00:00'")
    상태["사유"] = "관리종목"
    job.run(date(2026, 9, 18))
    rows = job.active_flags_for_report(mem, "KR", "2026-09-18", "2026-09-16T23:40:00+00:00")
    assert [(r["rationale"], r["new"]) for r in rows] == [("빠졌습니다(관리종목)", True)]
