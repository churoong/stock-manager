"""25.2356 교차검증 [권고]·[참고] — 수능일 정의처를 가리키는 글과 대소문자 (docs/infra.md 25.2357).

정의처는 `batch/core/calendar.KR_CSAT_DAYS` 하나다. 해마다 9월에 날짜를 더하는 사람이 옛 위치(`monitor_targets`)를 가리키는
글을 보고 그쪽에 따로 목록을 만들면 실행 판정과 KIS 주문 창이 다른 날을 본다.
"""

from __future__ import annotations

import inspect
from datetime import date
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
    for 길 in ("batch", "docs/paper.md", "docs/intraday.md"):
        대상 = [뿌리 / 길] if 길.endswith(".md") else sorted((뿌리 / 길).rglob("*.py"))
        for 파일 in 대상:
            글 = 파일.read_text()
            for 옛 in ("monitor_targets.KR_CSAT_DAYS", "monitor_targets.open_utc", "monitor_targets.close_utc"):
                assert 옛 not in 글, f"{파일.relative_to(뿌리)}: {옛} → core/calendar"


def test_시간외도_정의처를_읽는다() -> None:
    src = inspect.getsource(after_hours)
    assert "cal.KR_CSAT_DAYS" in src and "mt.KR_CSAT_DAYS" not in src


def test_실패_알림_끝줄은_마감_시각을_박지_않는다() -> None:
    """수능일 마감은 16:30 이라 '15:30 KST' 를 박으면 그날만 한 시간 어긋난다"""
    assert "장 마감(15:30 KST) 전까지" not in inspect.getsource(cal)
