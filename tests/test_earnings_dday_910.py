"""포트폴리오 실적 D-day 는 야후 예정일이 있으면 그것 (docs/portfolio.md, docs/infra.md 25.910, 감사).

법정 기한만 써서 야후가 10-28(D-25)을 주는 종목도 11-14(D-42)로 보였다.
"""

from __future__ import annotations

import inspect
from datetime import date

from batch.jobs import earnings_calendar as ec
from batch.jobs import portfolio as job
from tests.test_scores_job import _sqlite_client


def test_오늘_이후_가장_가까운_야후_일정만_고른다() -> None:
    c = _sqlite_client()
    # 이 클라이언트에는 종목 1·2 가 이미 있다
    for sid, d, conf, src in (
        (1, "2026-09-30", 1, ec.SOURCE_YAHOO),  # 지난 일정
        (1, "2026-10-28", 1, ec.SOURCE_YAHOO),
        (1, "2027-01-28", 0, ec.SOURCE_YAHOO),
        (2, "2026-11-14", 0, ec.SOURCE),  # 추정만 있는 종목 — 야후가 아니다
    ):
        c.execute(
            "INSERT INTO earnings_calendar (stock_id, event_type, scheduled_date, is_confirmed, source, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, 't')",
            [sid, ec.YAHOO_EVENT if src == ec.SOURCE_YAHOO else "실적발표(분기)", d, conf, src],
        )
    got = job.next_yahoo_earnings(c, [1, 2], date(2026, 10, 3))  # type: ignore[arg-type]
    assert got == {1: ("2026-10-28", True)}


def test_요약이_야후_날짜와_근거를_싣는다() -> None:
    src = inspect.getsource(job.run)
    assert "next_yahoo_earnings(" in src and '"basis": basis' in src
