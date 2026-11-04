"""수능일 시간외 단일가는 한 시간 늦다 — 18:17 예약 실행이 진행 중인 중간 시세를 마감 값으로 적지 않는다
(docs/infra.md 25.2134, 11-04 결함 회차 [권고]). 시각은 거래소 공지로 `[확인필요]`."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from batch.jobs import monitor_targets as mt
from batch.services import after_hours as ah


@pytest.mark.parametrize(
    ("utc", "기대"),
    [
        (datetime(2026, 11, 19, 7, 30, tzinfo=UTC), "2026-11-18"),  # 16:30 KST — 수능일 시간외는 아직 안 열림
        (datetime(2026, 11, 19, 9, 17, tzinfo=UTC), None),  # 18:17 KST 예약 — 진행 중(예전엔 마감으로 봄)
        (datetime(2026, 11, 19, 9, 59, tzinfo=UTC), None),  # 18:59 KST
        (datetime(2026, 11, 19, 10, 0, tzinfo=UTC), "2026-11-19"),  # 19:00 KST — 마감
        (datetime(2026, 11, 20, 9, 17, tzinfo=UTC), "2026-11-20"),  # 다음 날(평일) 18:17 은 예전대로 마감 뒤
        (datetime(2026, 11, 20, 7, 30, tzinfo=UTC), None),  # 평일 16:30 — 진행 중
    ],
)
def test_수능일_시간외_창(utc: datetime, 기대: str | None) -> None:
    got = ah.session_day(utc)
    assert (got.isoformat() if got else None) == 기대


def test_창은_수능일_목록과_밀림을_따른다() -> None:
    for d in mt.KR_CSAT_DAYS:
        assert ah.session_window(date.fromisoformat(d)) == (time(17, 0), time(19, 0))
    assert ah.session_window(date(2026, 11, 18)) == (ah.OPEN_KST, ah.CLOSE_KST)
