"""실행 시점 판단 테스트.

예약 실행이 지연되거나 중복으로 뜨는 상황을 견디는지 본다.
네트워크를 타지 않는다. 캘린더 라이브러리는 로컬 계산만 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from batch.core import calendar as cal


class Test시장설정:
    def test_알_수_없는_시장은_거부한다(self) -> None:
        with pytest.raises(cal.UnknownMarket):
            cal.is_session("JP")

    def test_두_시장을_안다(self) -> None:
        assert set(cal.MARKETS) == {"KR", "US"}


class Test거래일:
    def test_주말은_휴장이다(self) -> None:
        # 2026-09-19 는 토요일, 20 은 일요일
        assert not cal.is_session("KR", datetime(2026, 9, 19).date())
        assert not cal.is_session("KR", datetime(2026, 9, 20).date())
        assert not cal.is_session("US", datetime(2026, 9, 19).date())

    def test_평일은_개장이다(self) -> None:
        # 2026-09-16 은 수요일
        assert cal.is_session("KR", datetime(2026, 9, 16).date())
        assert cal.is_session("US", datetime(2026, 9, 16).date())

    def test_직전_거래일은_주말을_건너뛴다(self) -> None:
        # 월요일(2026-09-21)의 직전 거래일은 금요일(2026-09-18)
        monday = datetime(2026, 9, 21).date()
        assert cal.previous_session("KR", monday) == datetime(2026, 9, 18).date()


class Test개장시각:
    def test_국내_개장은_09시_KST다(self) -> None:
        from zoneinfo import ZoneInfo

        day = datetime(2026, 9, 16).date()
        open_utc = cal.session_open_utc("KR", day)

        assert open_utc is not None
        local = open_utc.astimezone(ZoneInfo("Asia/Seoul"))
        assert (local.hour, local.minute) == (9, 0)

    def test_미국_개장은_09시반_ET다(self) -> None:
        from zoneinfo import ZoneInfo

        day = datetime(2026, 9, 16).date()
        open_utc = cal.session_open_utc("US", day)

        assert open_utc is not None
        local = open_utc.astimezone(ZoneInfo("America/New_York"))
        assert (local.hour, local.minute) == (9, 30)

    def test_서머타임이_자동_반영된다(self) -> None:
        # 7월은 서머타임, 1월은 아니다. ET 로는 둘 다 09:30 이지만 UTC 로는 1시간 다르다.
        summer = cal.session_open_utc("US", datetime(2026, 7, 15).date())
        winter = cal.session_open_utc("US", datetime(2026, 1, 15).date())

        assert summer is not None and winter is not None
        assert summer.hour == 13  # EDT 는 UTC-4
        assert winter.hour == 14  # EST 는 UTC-5

    def test_휴장일에는_개장_시각이_없다(self) -> None:
        assert cal.session_open_utc("KR", datetime(2026, 9, 19).date()) is None


def _at(market: str, day: tuple[int, int, int], minutes_before_open: int) -> datetime:
    """그 거래일 개장 N분 전의 UTC 시각을 만든다."""
    open_utc = cal.session_open_utc(market, datetime(*day).date())
    assert open_utc is not None
    return open_utc - timedelta(minutes=minutes_before_open)


class Test실행판단:
    def test_휴장일에는_돌지_않는다(self) -> None:
        saturday = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)
        decision = cal.decide("KR", now=saturday)

        assert not decision.should_run
        assert "휴장" in decision.reason

    def test_개장_1시간_전이면_돈다(self) -> None:
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), 60))

        assert decision.should_run
        assert decision.minutes_to_open == 60

    def test_개장_뒤라도_장_마감_전이면_늦은_실행으로_돈다(self) -> None:
        # 2026-10-01 사용자 결정 (docs/infra.md 25.845) — 예전에는 그날을 통째로 건너뛰어 손절 플래그·시세 수집이 빠졌다
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), -30))

        assert decision.should_run and decision.late_minutes == -30
        assert "늦은 실행 (개장 30분 뒤)" in decision.reason

    def test_장이_끝난_뒤에는_건너뛴다(self) -> None:
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), -400))  # 15:40 KST

        assert not decision.should_run and "장이 끝났습니다" in decision.reason

    def test_너무_이르면_다음_예약에_맡긴다(self) -> None:
        # 미국 워크플로는 두 시각에 걸려 있고 한쪽은 1시간 이릅니다
        decision = cal.decide("US", now=_at("US", (2026, 9, 16), 300))

        assert not decision.should_run
        assert "이릅니다" in decision.reason

    def test_예약이_지연돼도_구간_안이면_진행한다(self) -> None:
        # 예약이 30분 밀려 개장 30분 전에 떴다. 리포트로서 아직 쓸모가 있다
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), 30))

        assert decision.should_run
        assert decision.minutes_to_open == 30

    def test_개장_직전이면_늦은_실행이다(self) -> None:
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), 5))

        assert decision.should_run and decision.late_minutes == 5
        assert "개장 5분 전" in decision.reason

    def test_국내는_예약이_밀려_개장_15분_전이어도_돈다(self) -> None:
        # 국내 예약은 한국거래소 공개 시각 때문에 개장 33분 전이다.
        # 그만큼 여유가 적어 아래쪽 문턱을 10분으로 낮췄다 (infra.md 16.1절)
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), 15))

        assert decision.should_run

    def test_미국은_개장_15분_전이면_늦은_실행이다(self) -> None:
        # 예약 두 개 중 개장 3분 전 쪽은 이제 "늦은 실행" — 앞 실행이 성공했으면 중복 검사가 건너뛴다(daily.run)
        decision = cal.decide("US", now=_at("US", (2026, 9, 16), 15))

        assert decision.should_run and decision.late_minutes == 15

    def test_force_는_시각_판단을_건너뛴다(self) -> None:
        # 개장 후여도 수동 실행은 돈다
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), -120), force=True)

        assert decision.should_run
        assert "수동" in decision.reason

    def test_force_여도_휴장일에는_돌지_않는다(self) -> None:
        # 휴장일 검사는 force 보다 우선한다. 없는 거래일을 만들어 낼 수 없다.
        saturday = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)
        decision = cal.decide("KR", now=saturday, force=True)

        assert not decision.should_run

    def test_다루는_거래일은_직전_거래일이다(self) -> None:
        # 장 시작 전에 도는 배치이므로 전일 종가를 다룬다
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 16), 60))

        assert decision.session_date == "2026-09-16"
        assert decision.trade_date == "2026-09-15"

    def test_월요일_배치는_금요일_거래일을_다룬다(self) -> None:
        decision = cal.decide("KR", now=_at("KR", (2026, 9, 21), 60))

        assert decision.session_date == "2026-09-21"
        assert decision.trade_date == "2026-09-18"


class Test미국_예약_두_시각:
    """UTC 고정 예약 두 개 중 하나만 실제로 도는지 확인한다."""

    @pytest.mark.parametrize(
        ("day", "expected_hour"),
        [
            ((2026, 7, 15), 12),  # 서머타임: 12:27 UTC 쪽이 개장 1시간 전
            ((2026, 1, 15), 13),  # 표준시: 13:27 UTC 쪽이 개장 1시간 전
        ],
    )
    def test_해당_시기에_맞는_예약만_실행된다(
        self, day: tuple[int, int, int], expected_hour: int
    ) -> None:
        # 제 시각에 도는(늦지 않은) 쪽은 하나뿐이다. 다른 쪽은 "늦은 실행"(서머타임 13:27 = 개장 3분 전)이거나 "너무 이름"이다 —
        # 늦은 실행은 앞 실행이 이미 성공했으면 daily.run 의 중복 검사가 건너뛴다 (25.845)
        ran = []
        for hour in (12, 13):
            moment = datetime(*day, hour, 27, tzinfo=UTC)
            d = cal.decide("US", now=moment)
            if d.should_run and d.late_minutes is None:
                ran.append(hour)

        assert ran == [expected_hour]


class Test캘린더의심:
    """캘린더가 틀렸을 때 사후에 알아채는 장치.

    한국투자증권의 공식 휴장일 조회를 쓰지 않기로 해서 대조할 상대가 없다.
    실제 데이터와 판정이 어긋나는 것으로 의심한다.
    """

    def test_개장일인데_시세가_없으면_의심한다(self) -> None:
        message = cal.suspect_calendar(is_open=True, rows_collected=0)
        assert message is not None
        assert "캘린더" in message

    def test_휴장일인데_시세가_들어오면_의심한다(self) -> None:
        message = cal.suspect_calendar(is_open=False, rows_collected=2700)
        assert message is not None
        assert "캘린더" in message

    def test_개장일에_시세가_있으면_정상(self) -> None:
        assert cal.suspect_calendar(is_open=True, rows_collected=2700) is None

    def test_휴장일에_시세가_없으면_정상(self) -> None:
        assert cal.suspect_calendar(is_open=False, rows_collected=0) is None


class Test라이브러리버전:
    def test_버전을_문자열로_남긴다(self) -> None:
        # 임시공휴일 반영이 늦은 날을 나중에 추적하려면
        # 어떤 버전이 판정했는지 알아야 한다
        version = cal.library_version()
        assert "exchange_calendars" in version
