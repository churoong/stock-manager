"""손으로 기준일을 안 줬을 때의 기준일이 **아침 배치와 같은 직전 거래일**인가 (docs/infra.md 25.234).

2026-09-26 까지 점수·신호·밴드는 기준일이 없으면 `datetime.now(UTC).date()` 를 썼다. 매일 09:05 KST 에 도는
D1 따라잡기가 **토·일에도** 그 날짜로 신호를 썼고, 월요일 아침 배치(기준 금요일)보다 일요일 것이
`MAX(as_of_date)` 가 되어 리포트·화면이 그것을 읽었다.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from batch.core import calendar as cal

뿌리 = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("market", "오늘", "기대"),
    [
        ("KR", date(2026, 10, 17), "2026-10-16"),  # 토요일 → 금요일
        ("KR", date(2026, 10, 18), "2026-10-16"),  # 일요일 → 금요일
        ("KR", date(2026, 10, 19), "2026-10-16"),  # 월요일 아침 → 금요일 (아침 배치의 trade_date 와 같다)
        ("KR", date(2026, 10, 20), "2026-10-19"),
        ("KR", date(2026, 9, 28), "2026-09-23"),  # 추석 연휴(9/24~26) 뒤 월요일 → 연휴 앞 수요일
        ("US", date(2026, 9, 26), "2026-09-25"),
    ],
)
def test_기본_기준일은_직전_거래일이다(market: str, 오늘: date, 기대: str) -> None:
    assert cal.default_as_of(market, 오늘) == 기대


def test_아침_배치의_trade_date_와_같은_정의다() -> None:
    from datetime import UTC, datetime

    # 월요일 08:27 KST = 일요일 23:27 UTC
    지금 = datetime(2026, 10, 18, 23, 27, tzinfo=UTC)
    판단 = cal.decide("KR", now=지금, force=True)
    assert cal.default_as_of("KR", date(2026, 10, 19)) == 판단.trade_date


#: 기준일을 열쇠로 쌓고 아침 배치·화면이 `MAX(as_of_date)` 로 읽는 작업들
_기준일_작업 = ["scores.py", "signals.py", "valuation_bands.py", "sentiment.py"]


@pytest.mark.parametrize("파일", _기준일_작업)
def test_UTC_날짜를_기본_기준일로_쓰지_않는다(파일: str) -> None:
    글 = (뿌리 / "batch" / "jobs" / 파일).read_text(encoding="utf-8")
    나무 = ast.parse(글)
    for 노드 in ast.walk(나무):
        # `as_of = as_of or …` 모양의 **기본값 대입**만 본다
        if not (
            isinstance(노드, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "as_of" for t in 노드.targets)
        ):
            continue
        값 = 노드.value
        if isinstance(값, ast.BoolOp) and isinstance(값.op, ast.Or):
            뒤 = ast.unparse(값.values[1])
            assert "now(UTC)" not in 뒤, f"{파일}: 기본 기준일이 UTC 날짜다 — cal.default_as_of 를 써라"
    assert "cal.default_as_of(" in 글


def test_감성_기준일의_끝은_그_시장의_현지_자정_직전이다() -> None:
    """docs/infra.md 25.237 — UTC 23:59:59 이면 국내는 다음날 08:59 KST 기사까지 섞였다."""
    from datetime import UTC, datetime

    from batch.jobs import sentiment

    # 목요일 — 다음 거래일 전날이 기준일이라 그날 끝 (25.744 전과 같다)
    assert sentiment.as_of_moment("2026-10-15", "KR") == datetime(2026, 10, 15, 14, 59, 59, tzinfo=UTC)
    # 미국은 서머타임(EDT, UTC-4)
    assert sentiment.as_of_moment("2026-10-15", "US") == datetime(2026, 10, 16, 3, 59, 59, tzinfo=UTC)


def test_금요일_기준은_주말_기사까지_넣는다() -> None:
    """docs/infra.md 25.744 — 금 23:59 에 끊겨 토·일 기사가 월요일 리포트에 안 들어갔다."""
    from datetime import UTC, datetime

    from batch.jobs import sentiment

    # 2026-10-16(금) → 다음 거래일 10-19(월) → 창 끝 = 10-18(일) 23:59:59 현지
    assert sentiment.as_of_moment("2026-10-16", "KR") == datetime(2026, 10, 18, 14, 59, 59, tzinfo=UTC)
    assert sentiment.as_of_moment("2026-10-16", "US") == datetime(2026, 10, 19, 3, 59, 59, tzinfo=UTC)
    # 추석 연휴 앞(2026-09-23 수 → 다음 거래일 9-28 월 [확인필요: 달력]) — 연휴 끝까지
    m = sentiment.as_of_moment("2026-09-23", "KR")
    assert m.date() >= datetime(2026, 9, 26).date()
