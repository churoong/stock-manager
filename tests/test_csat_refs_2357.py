"""25.2356 교차검증 [권고]·[참고] — 수능일 정의처를 가리키는 글과 대소문자 (docs/infra.md 25.2357).

정의처는 `batch/core/calendar.KR_CSAT_DAYS` 하나다. 해마다 9월에 날짜를 더하는 사람이 옛 위치(`monitor_targets`)를 가리키는
글을 보고 그쪽에 따로 목록을 만들면 실행 판정과 KIS 주문 창이 다른 날을 본다.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, date, datetime
from pathlib import Path

from batch.core import calendar as cal
from batch.services import after_hours

뿌리 = Path(__file__).resolve().parent.parent


def test_소문자_시장도_수능일을_안다() -> None:
    d = date(2026, 11, 19)
    assert cal.market_open_utc("kr", d) == cal.market_open_utc("KR", d)
    assert cal.market_close_utc("kr", d) == cal.market_close_utc("KR", d)
    assert cal.market_open_utc("kr", d) != cal.session_open_utc("KR", d)


def test_옛_정의처를_가리키는_글이_없다() -> None:
    for 길 in ("batch", "docs/paper.md", "docs/intraday.md", "docs/handoff.md"):
        대상 = [뿌리 / 길] if 길.endswith(".md") else sorted((뿌리 / 길).rglob("*.py"))
        for 파일 in 대상:
            # 취소선(~~…~~) 구간만 고친 옛 기록으로 빼고 나머지는 다 본다(25.2359 — 줄째 빼면 handoff 의 긴 살아 있는
            # 문단이 통째로 빠졌다)
            글 = re.sub(r"~~.*?~~", "", 파일.read_text())
            for 옛 in ("monitor_targets.KR_CSAT_DAYS", "monitor_targets.open_utc", "monitor_targets.close_utc"):
                assert 옛 not in 글, f"{파일.relative_to(뿌리)}: {옛} → core/calendar"


def test_시간외도_정의처를_읽는다() -> None:
    src = inspect.getsource(after_hours)
    assert "cal.KR_CSAT_DAYS" in src and "mt.KR_CSAT_DAYS" not in src
    assert "cal.CSAT_SHIFT" in src and "mt.CSAT_SHIFT" not in src and "monitor_targets" not in src


def test_실패_알림_끝줄은_마감_시각을_박지_않는다() -> None:
    """수능일 마감은 16:30 이라 평일 마감 '15:30' 을 박으면 그날만 한 시간 어긋난다 — 글자 대신 결과를 본다(25.2358).
    함수는 날짜를 보지 않고 시각(09:35 지났나)만 본다 — 두 갈래를 다 부른다(25.2359)"""
    뒤 = cal.next_scheduled_hint("KR", datetime(2026, 11, 19, 1, 0, tzinfo=UTC))  # 10:00 KST — 09:35 예비 뒤
    앞 = cal.next_scheduled_hint("KR", datetime(2026, 11, 18, 23, 40, tzinfo=UTC))  # 08:40 KST — 예비 앞
    assert "약속하지 않습니다" in 뒤 and "09:35 KST 예비" in 앞
    assert "15:30" not in 뒤 and "15:30" not in 앞
