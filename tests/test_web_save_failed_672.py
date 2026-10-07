"""리포트를 웹에 저장하지 못했으면 "웹에서 보세요" 라 하지 않는다 (docs/infra.md 25.672, 교차검증)."""

from __future__ import annotations

import inspect

from batch.jobs import daily
from batch.notify import formatter


def test_알림은_웹에_없다고_말한다() -> None:
    글 = formatter.failure_alert(
        market="KR", job_name="daily_kr", error_text=f"리포트 뒷부분 발송 실패: x — {formatter.WEB_SAVE_FAILED_MARK}",
        last_success=None, title="리포트 일부만 보냄",
    )
    assert "웹에서는 볼 수 없습니다" in 글 and "전체가 있습니다" not in 글


def test_일부_발송·모름_경로가_저장_여부를_본다() -> None:
    src = inspect.getsource(daily.run)
    웹 = src[src.index("def _웹에_없음"):src.index("try:", src.index("def _웹에_없음"))]
    assert "formatter.WEB_SAVE_FAILED_MARK" in 웹 and "formatter.NOT_SAVED_MARK" in 웹 and "보낸_것_있음" in 웹
    일부 = src[src.index("except telegram.TelegramPartialError"):src.index("except telegram.TelegramUncertainError")]
    assert 일부.count("_웹에_없음()") == 2  # 경고와 알림
    모름 = src[src.index("except telegram.TelegramUncertainError as exc:"):]
    assert 모름.count("_웹에_없음()") >= 2


def test_재실행_본문을_보냈는데_저장이_막히면_알린다() -> None:
    """폰과 웹이 달라진 것을 실행 기록에만 남겨 사용자가 몰랐다 (docs/infra.md 25.756, 리포트 감사)."""
    import inspect

    from batch.jobs import daily

    src = inspect.getsource(daily.run)
    성공 = src[src.index("message_ids = telegram.send(message)"):src.index("except telegram.TelegramPartialError")]
    assert "report_id is None" in 성공 and 'title="리포트 저장 실패"' in 성공
