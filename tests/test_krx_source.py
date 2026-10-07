"""한국거래소 소스 도우미 테스트."""

from __future__ import annotations


def test_직전_거래일은_한국_날짜로_센다(monkeypatch) -> None:
    """한국 00~09시에 UTC 날짜로 세면 하루 전 기준이 된다 (docs/infra.md 25.321)."""
    from datetime import date

    from batch.core import calendar as cal_core
    from batch.sources import krx

    monkeypatch.setattr(cal_core, "local_today", lambda market: date(2026, 10, 14))  # 수요일(한국)
    assert krx.previous_session_yyyymmdd() == "20261013"


def test_주말에_불러도_죽지_않는다() -> None:
    """거래소 달력에 휴장일을 넣으면 예외였다 (docs/infra.md 25.321). 2026-09-27 은 일요일, 24~26 은 추석이다."""
    from datetime import date

    from batch.sources import krx

    assert krx.previous_session_yyyymmdd(date(2026, 9, 27)) == "20260923"
