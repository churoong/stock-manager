"""국내 개장·마감 판정의 수능일 늦춤 — 정의처 하나 (docs/infra.md 25.2355).

예전엔 KIS 주문 창·잔고 기준점·장중 감시 창만 수능일 늦춤을 알고, 가상모의투자 "이미 열렸나"(`daily._장_열림`·
`paper` 다시잰기준)·"이미 닫혔나"(`daily._장_끝남`)·지수 확정 거래일(`index_prices.settled_days`)은 달력 그대로였다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from batch.jobs import daily, index_prices, intraday_guard
from batch.jobs import monitor_targets as mt

수능 = date(2026, 11, 19)
평일 = date(2026, 11, 18)


def test_정의처() -> None:
    assert mt.open_utc("KR", 수능) == datetime(2026, 11, 19, 1, 0, tzinfo=UTC)  # 10:00 KST
    assert mt.close_utc("KR", 수능) == datetime(2026, 11, 19, 7, 30, tzinfo=UTC)  # 16:30 KST
    assert mt.open_utc("KR", 평일) == datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
    assert mt.close_utc("KR", 평일) == datetime(2026, 11, 18, 6, 30, tzinfo=UTC)
    # 미국은 달력 그대로(같은 날짜여도 늦추지 않는다)
    assert mt.open_utc("US", 수능) == datetime(2026, 11, 19, 14, 30, tzinfo=UTC)


class _시계(datetime):
    지금 = datetime(2026, 11, 19, 0, 35, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN206
        return cls.지금


@pytest.mark.parametrize(("지금", "열림", "닫힘"), [
    (datetime(2026, 11, 19, 0, 35, tzinfo=UTC), False, False),  # 수능일 09:35 KST — 아직 안 열림(시가로 차야)
    (datetime(2026, 11, 19, 1, 5, tzinfo=UTC), True, False),
    (datetime(2026, 11, 19, 6, 45, tzinfo=UTC), True, False),  # 15:45 KST — 아직 장중
    (datetime(2026, 11, 19, 7, 31, tzinfo=UTC), True, True),
])  # fmt: skip
def test_가상모의투자_장_열림_닫힘(monkeypatch: pytest.MonkeyPatch, 지금: datetime, 열림: bool, 닫힘: bool) -> None:
    _시계.지금 = 지금
    monkeypatch.setattr(daily, "datetime", _시계)
    assert daily._장_열림("KR", "2026-11-19") is 열림
    assert daily._장_끝남("KR", "2026-11-19") is 닫힘


def test_지수_확정_거래일() -> None:
    # 수능일 16:20 KST — 아직 장중(16:30 마감)이라 확정 거래일은 전날. 달력(15:30 마감)이면 여유를 더해도 넘었다
    when = datetime(2026, 11, 19, 7, 20, tzinfo=UTC)
    assert when >= mt.cal.session_close_utc("KR", 수능) + index_prices.SETTLE_MARGIN  # 달력 잣대로는 확정
    assert index_prices.settled_days(when)["KR"] == "2026-11-18"
    # 마감 + 여유 뒤엔 그날
    assert index_prices.settled_days(mt.close_utc("KR", 수능) + index_prices.SETTLE_MARGIN)["KR"] == "2026-11-19"


def test_장중_감시_창은_정의처를_쓴다() -> None:
    창 = intraday_guard.window("KR", 수능)
    assert 창 is not None and 창[0] == mt.open_utc("KR", 수능) + intraday_guard.OPEN_MARGIN
    assert 창[1] == mt.close_utc("KR", 수능) - intraday_guard.CLOSE_MARGIN


def test_실행_판정도_수능일_개장을_안다() -> None:
    """25.2356 (25.2355 교차검증 [필수] — 세 렌즈 같은 지적) — 가상모의투자의 늦음은 `decide.minutes_to_open <= 0 OR
    _장_열림` 이다. `decide` 가 달력 그대로면 수능일 09:35 예비 실행이 "개장 35분 뒤" 로 늦음이 되어 주문이 종가로 찼고
    리포트 머리도 틀렸다. 15:35 실행은 "이미 장이 끝났습니다" 로 통째로 건너뛰었다(실제 마감 16:30)"""
    from batch.core import calendar as cal

    아침 = cal.decide("KR", now=datetime(2026, 11, 19, 0, 35, tzinfo=UTC))
    assert 아침.should_run and 아침.minutes_to_open == 25 and 아침.late_minutes is None  # 개장 25분 전 — 제때 실행
    오후 = cal.decide("KR", now=datetime(2026, 11, 19, 6, 35, tzinfo=UTC))  # 15:35 KST — 아직 장중
    assert 오후.should_run
    assert not cal.decide("KR", now=datetime(2026, 11, 19, 7, 31, tzinfo=UTC)).should_run  # 16:31 — 장 끝남
    # 평일 09:35 는 그대로 개장 뒤
    assert cal.decide("KR", now=datetime(2026, 11, 18, 0, 35, tzinfo=UTC)).minutes_to_open == -35


def test_정의처는_core_에_하나() -> None:
    from batch.core import calendar as cal

    assert mt.KR_CSAT_DAYS is cal.KR_CSAT_DAYS and mt.CSAT_SHIFT == cal.CSAT_SHIFT
    assert mt.open_utc("KR", 수능) == cal.market_open_utc("KR", 수능)


def test_다시잰기준이_정의처를_쓴다() -> None:
    import inspect

    from batch.jobs import paper

    글 = inspect.getsource(paper.run_step)
    assert "mt.open_utc(market, date.fromisoformat(다음장))" in 글
    assert "cal.session_open_utc(market, date.fromisoformat(다음장))" not in 글
