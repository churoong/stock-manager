"""일일 배치 예약 시각 (docs/infra.md 25.327)."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from batch.core import calendar as cal

뿌리 = Path(__file__).resolve().parent.parent


def _cron_utc(파일: str) -> list[tuple[int, int]]:
    글 = (뿌리 / ".github" / "workflows" / 파일).read_text(encoding="utf-8")
    return [(int(h), int(m)) for m, h in re.findall(r'cron: "(\d+) (\d+) ', 글)]


def test_국내_예약은_cron_과_같다() -> None:
    # 23:27 UTC = 08:27 KST, 개장 09:00 → 33분 전
    (h, m), _예비 = _cron_utc("daily-kr.yml")  # 둘째는 한도가 풀린 뒤의 예비(25.870) — 예약 시각은 첫째
    예약 = datetime.fromisoformat(cal.scheduled_for("KR", date(2026, 10, 14)))  # type: ignore[arg-type]
    assert (예약.hour, 예약.minute) == (h, m)


def test_미국_예약은_서머타임에_맞는_cron_하나와_같다() -> None:
    예약들 = set(_cron_utc("daily-us.yml"))
    for day in (date(2026, 10, 14), date(2026, 12, 15)):  # 서머타임 · 표준시
        예약 = datetime.fromisoformat(cal.scheduled_for("US", day))  # type: ignore[arg-type]
        assert (예약.hour, 예약.minute) in 예약들


def test_휴장일은_예약이_없다() -> None:
    assert cal.scheduled_for("KR", date(2026, 10, 3)) is None  # 개천절(토)


def test_리포트가_지연을_적는다() -> None:
    import inspect

    from batch.jobs import daily

    원본 = inspect.getsource(daily.run)
    assert "scheduled_for=예약" in 원본 and "delay_seconds=지연" in 원본


def test_새해_첫_거래일도_예약은_cron_시각() -> None:
    """첫 거래일은 10:00 개장이지만 cron 은 08:27 그대로다 — 예약을 그날 개장에서 빼면
    제때 돈 배치가 "−60분 지연" 으로 적혔다 (docs/infra.md 25.458, 스케줄 감사 #7)."""
    (h, m), _예비 = _cron_utc("daily-kr.yml")  # 둘째는 한도가 풀린 뒤의 예비(25.870) — 예약 시각은 첫째
    for day in (date(2026, 1, 2), date(2027, 1, 4)):
        assert cal.session_open_utc("KR", day).hour == 1  # type: ignore[union-attr]  # 라이브러리는 10:00 KST 로 안다
        예약 = datetime.fromisoformat(cal.scheduled_for("KR", day))  # type: ignore[arg-type]
        assert (예약.hour, 예약.minute) == (h, m) and 예약.date() == date.fromordinal(day.toordinal() - 1)


def test_국내_예비_실행은_D1_한도가_풀린_뒤다() -> None:
    """08:27 KST 실행은 전날 UTC 몫의 D1 읽기 한도를 쓴다 — 예비는 자정 UTC 뒤여야 뜻이 있다 (docs/infra.md 25.870)."""
    _본, (h, m) = _cron_utc("daily-kr.yml")
    assert (h, m) == (0, 35)
