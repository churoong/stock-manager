"""메시지 포맷 테스트.

절대 규칙 확인: 없는 숫자를 만들어 내지 않고, 없으면 없다고 표시한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from batch.notify import formatter

BASE_TIME = datetime(2026, 9, 16, 23, 0, tzinfo=UTC)


class Test일일리포트:
    def test_국내_금액은_원화로_표시한다(self) -> None:
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-15",
            rows=[
                {
                    "name": "삼성전자",
                    "close": 248500.0,
                    "change_pct": 1.01,
                    "volume": 12345678,
                    "currency": "KRW",
                    "source": "yfinance",
                }
            ],
            started_at=BASE_TIME,
        )

        assert "삼성전자" in text
        assert "248,500원" in text
        assert "+1.01%" in text
        assert "2026-09-15" in text

    def test_미국_금액은_달러로_표시한다(self) -> None:
        text = formatter.daily_report(
            market="US",
            trade_date="2026-09-14",
            rows=[
                {
                    "name": "애플",
                    "close": 333.08,
                    "change_pct": 0.24,
                    "volume": 45678901,
                    "currency": "USD",
                    "source": "yfinance",
                }
            ],
            started_at=BASE_TIME,
        )

        assert "$333.08" in text
        assert "+0.24%" in text

    def test_하락은_음수로_표시한다(self) -> None:
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-15",
            rows=[
                {
                    "name": "삼성전자",
                    "close": 240000.0,
                    "change_pct": -3.42,
                    "currency": "KRW",
                    "source": "yfinance",
                }
            ],
            started_at=BASE_TIME,
        )

        assert "-3.42%" in text

    def test_등락률이_없으면_지어내지_않는다(self) -> None:
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-15",
            rows=[
                {
                    "name": "삼성전자",
                    "close": 248500.0,
                    "change_pct": None,
                    "currency": "KRW",
                    "source": "yfinance",
                }
            ],
            started_at=BASE_TIME,
        )

        assert "전일 대비 -" in text
        assert "0.00%" not in text

    def test_데이터_기준_시각을_항상_넣는다(self) -> None:
        text = formatter.daily_report(
            market="KR", trade_date="2026-09-15", rows=[], started_at=BASE_TIME
        )

        assert "KST" in text
        assert "2026-09-17 08:00" in text  # 23:00 UTC 는 KST 로 다음날 08:00

    def test_지연_실행은_리포트에_드러난다(self) -> None:
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-15",
            rows=[],
            started_at=BASE_TIME,
            delay_seconds=2400,
        )

        assert "40분 늦게" in text

    def test_짧은_지연은_표시하지_않는다(self) -> None:
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-15",
            rows=[],
            started_at=BASE_TIME,
            delay_seconds=120,
        )

        assert "늦게" not in text

    def test_경고를_함께_보여준다(self) -> None:
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-15",
            rows=[],
            started_at=BASE_TIME,
            warnings=["애플 시세를 받지 못했습니다"],
        )

        assert "경고" in text
        assert "애플 시세를 받지 못했습니다" in text

    def test_종목이_없어도_무너지지_않는다(self) -> None:
        text = formatter.daily_report(
            market="KR", trade_date="2026-09-15", rows=[], started_at=BASE_TIME
        )

        assert "가져온 종목이 없습니다" in text

    def test_출처를_표시한다(self) -> None:
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-15",
            rows=[
                {"name": "삼성전자", "close": 1.0, "currency": "KRW", "source": "yfinance"}
            ],
            started_at=BASE_TIME,
        )

        assert "출처 yfinance" in text


class Test실패알림:
    def test_마지막_성공_시점을_알려준다(self) -> None:
        text = formatter.failure_alert(
            market="KR",
            job_name="daily_kr",
            error_text="시세 수집 실패",
            last_success={
                "trade_date": "2026-09-14",
                "finished_at": "2026-09-15T23:05:00+00:00",
            },
        )

        assert "배치 실패" in text
        assert "시세 수집 실패" in text
        assert "2026-09-14" in text
        assert "그대로 남아" in text
        # 23:05 UTC 는 국내 시각으로 다음 날 08:05 — 리포트의 "생성" 줄과 같은 달력 (docs/infra.md 25.339)
        assert "2026-09-16 08:05 KST" in text
        assert "UTC" not in text

    def test_발송_문제는_배치_실패라_하지_않는다(self) -> None:
        """발송 모름·일부 발송은 배치가 성공으로 닫힌다 (docs/infra.md 25.504, 교차검증)."""
        text = formatter.failure_alert(
            market="KR", job_name="daily_kr", error_text="시간 초과",
            last_success={"trade_date": "2026-09-14", "finished_at": "2026-09-15T23:05:00+00:00"},
            title="리포트 발송 여부 모름",
        )
        assert "[국내] 리포트 발송 여부 모름" in text or "리포트 발송 여부 모름" in text.splitlines()[0]
        assert "배치 실패" not in text and "마지막 성공" not in text
        assert "웹의 오늘 리포트" in text

    def test_일일_배치가_발송_문제에_제목을_준다(self) -> None:
        import inspect

        from batch.jobs import daily

        src = inspect.getsource(daily)
        assert 'title="리포트 발송 여부 모름"' in src and 'title="리포트 일부만 보냄"' in src

    def test_마지막_성공을_못_읽으면_이력_없음이라_하지_않는다(self) -> None:
        """DB 가 막힌 날 "성공 이력이 아직 없습니다" 가 나갔다 (docs/infra.md 25.818)."""
        text = formatter.failure_alert(market="KR", job_name="daily_kr", error_text="x", last_success=None, last_unread=True)
        assert "읽지 못했습니다" in text and "성공 이력이 아직 없습니다" not in text

    def test_실패_알림_경로가_못_읽음을_넘긴다(self, monkeypatch) -> None:
        from batch.jobs import daily

        받음: dict = {}

        def 가짜(**kw):
            받음.update(kw)
            return "글"

        def 막힘(*a, **k):
            raise RuntimeError("한도")

        monkeypatch.setattr(daily.db, "last_successful_run", 막힘)
        monkeypatch.setattr(daily.formatter, "failure_alert", 가짜)
        daily._notify_failure(None, "KR", "daily_kr", "x", dry_run=True)  # type: ignore[arg-type]
        assert 받음["last_unread"] is True

    def test_성공_이력이_없어도_무너지지_않는다(self) -> None:
        text = formatter.failure_alert(
            market="US", job_name="daily_us", error_text="오류", last_success=None
        )

        assert "성공 이력이 아직 없습니다" in text

    def test_다음_예정을_알려준다(self) -> None:
        text = formatter.failure_alert(
            market="US", job_name="daily_us", error_text="오류", last_success=None
        )

        assert "정규장 시작 1시간 전" in text

    def test_긴_오류는_잘라낸다(self) -> None:
        text = formatter.failure_alert(
            market="KR", job_name="daily_kr", error_text="오" * 2000, last_success=None
        )

        assert len(text) < 1000


class Test거래량표시:
    def test_만주_단위(self) -> None:
        assert formatter._volume(12_340_000) == "1234만주"

    def test_억주_단위(self) -> None:
        assert formatter._volume(250_000_000) == "2.5억주"

    def test_작은_수는_그대로(self) -> None:
        assert formatter._volume(1234) == "1,234주"

    def test_없으면_하이픈(self) -> None:
        assert formatter._volume(None) == "-"


class Test요약과_경고:
    def test_요약은_경고_아래에_두지_않는다(self) -> None:
        # 2026-09-17: "국내 N종목 저장" 이 경고로 찍혀 실행이 늘 partial 이었다
        text = formatter.daily_report(
            market="KR",
            trade_date="2026-09-16",
            rows=[],
            started_at=datetime(2026, 9, 16, 23, 27, tzinfo=UTC),
            summary="국내 2,763종목 저장",
        )
        assert "국내 2,763종목 저장" in text
        assert "경고" not in text


def test_웹에_저장하지_않았으면_웹에_전체가_있다고_하지_않는다() -> None:
    """한 알림 안에서 "저장하지 않았다" 와 "웹에 전체가 있다" 가 부딪혔다 (docs/infra.md 25.575, 교차검증)."""
    text = formatter.failure_alert(
        market="KR", job_name="daily_kr", last_success=None, title="리포트 발송 여부 모름",
        error_text=f"모름 — 단, {formatter.NOT_SAVED_MARK}(웹은 먼저 보낸 리포트)",
    )
    assert "전체가 있습니다" not in text and "먼저 보낸 리포트입니다" in text


def test_작성_끝_시각을_적는다() -> None:
    """실행 시작만 적어 늦게 도착한 리포트가 제때 만든 것처럼 보였다 (docs/infra.md 25.756, 리포트 감사)."""
    from datetime import UTC, datetime

    from batch.notify import formatter as fm

    시작 = datetime(2026, 9, 30, 23, 50, tzinfo=UTC)
    끝 = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
    글 = fm.daily_report(market="KR", trade_date="2026-09-30", rows=[], started_at=시작, composed_at=끝)
    assert "작성 끝" in 글.splitlines()[1]
    짧음 = fm.daily_report(market="KR", trade_date="2026-09-30", rows=[], started_at=시작, composed_at=시작)
    assert "작성 끝" not in 짧음
