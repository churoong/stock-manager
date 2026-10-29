"""25.1987 (결함 루프 10-29 [참고]) — 첫 화면 '오늘 볼 것' 줄은 매도 플래그 판정일이 오늘이 아니면 그 날짜를 적는다.

그날 매도 플래그 판정이 실패하면 리포트는 MAX(as_of_date) 인 어제 판정을 싣는다. 플래그 절 머리에는 판정일이 붙지만
(25.492) 제목 바로 아래 이 줄엔 없어, 알림 미리보기만 보면 어제 판정(오늘은 풀렸을 수 있음)을 오늘 손절로 읽었다
"""

from __future__ import annotations

from batch.notify import formatter


def _f(as_of: str, level: str = "red") -> dict:
    return {"name": "가나다", "level": level, "new": False, "as_of_date": as_of}


def test_오늘_판정이면_날짜_없음() -> None:
    줄 = formatter.todo_line([_f("2026-10-29")], "KRW", "2026-10-29")
    assert 줄 is not None and "판정일" not in 줄


def test_어제_판정이면_날짜를_적는다() -> None:
    줄 = formatter.todo_line([_f("2026-10-28"), _f("2026-10-28", "yellow")], "KRW", "2026-10-29")
    assert 줄 is not None and 줄.endswith(" (판정일 10-28 — 오늘 판정 아님)")


def test_오늘을_모르면_예전대로() -> None:
    줄 = formatter.todo_line([_f("2026-10-28")], "KRW")
    assert 줄 is not None and "판정일" not in 줄


def test_플래그_읽기와_첫_화면이_같은_오늘을_쓴다() -> None:
    """25.1989 (25.1987 교차검증 [참고]) — daily 가 오늘을 한 번 잡아 두 곳에 넘긴다"""
    import inspect

    from batch.jobs import daily

    src = inspect.getsource(daily)
    assert "_sell_flags_for_report(client, \"KR\" if market == \"KR\" else \"US\", warnings, 플래그_오늘)" in src
    assert "todo=formatter.todo_line(sell_flag_rows, currency, 플래그_오늘)" in src
