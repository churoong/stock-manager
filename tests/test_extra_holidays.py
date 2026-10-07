"""라이브러리에 빠진 휴장일 — 손으로 더하는 자리 (batch/core/calendar.EXTRA_HOLIDAYS, docs/infra.md 25.449)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from batch.core import calendar as cal

ROOT = Path(__file__).resolve().parents[1]


def test_지방선거일은_휴장이다() -> None:
    """exchange_calendars 4.13.2 는 2026-06-03 을 거래일로 준다. 역대 선거일은 모두 휴장이었다."""
    assert "2026-06-03" in cal.EXTRA_HOLIDAYS["KR"]
    assert cal.is_session("KR", date(2026, 6, 3)) is False
    assert cal.is_session("KR", date(2026, 6, 4)) is True
    assert cal.previous_session("KR", date(2026, 6, 4)) == date(2026, 6, 2)
    assert cal.next_session("KR", date(2026, 6, 2)) == date(2026, 6, 4)
    assert cal.session_open_utc("KR", date(2026, 6, 3)) is None


def test_거래일_목록에서도_빠진다() -> None:
    from batch.jobs import backfill_kr

    assert date(2026, 6, 3) not in backfill_kr.sessions_between(date(2026, 6, 1), date(2026, 6, 5))


def test_라이브러리_달력을_직접_부르는_곳이_없다() -> None:
    """함수마다 따로 거르면 한 곳을 빠뜨린다 — 달력은 `cal.exchange_calendar` 하나로 받는다."""
    걸린 = []
    for p in (ROOT / "batch").rglob("*.py"):
        if p.name == "calendar.py" or p.name == "step0_check.py":  # step0 은 라이브러리 자체를 점검한다
            continue
        # 별칭(xcals)·원래 이름·`from … import get_calendar` 모두 (25.454, 교차검증 — 한 모양만 찾던 그물)
        if re.search(r"\bget_calendar\b", p.read_text(encoding="utf-8")):
            걸린.append(str(p.relative_to(ROOT)))
    assert 걸린 == []
