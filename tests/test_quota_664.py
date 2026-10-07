"""한도 오류를 "배치 실패" 로 다루지 않는다 (docs/infra.md 25.664, 감사)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import daily
from tests.test_kr_yahoo_fallback import _client

한도글 = "D1_ERROR: Exceeded daily row write limit for free tier"


def test_하위_작업의_한도는_건너뜀으로_닫는다() -> None:
    c = _client()

    def 작업() -> int:
        db.start_batch_run(c, job_name="scores", market="KR", trade_date="2026-09-29")  # type: ignore[arg-type]
        raise RuntimeError(한도글)

    코드, exc = daily._하위작업(작업)
    assert 코드 == 1 and exc is not None
    상태, 글 = c.conn.execute("SELECT status, error_text FROM batch_runs WHERE job_name = 'scores'").fetchone()
    assert 상태 == "skipped" and "한도" in 글


def test_하위_작업의_다른_오류는_실패로_닫는다() -> None:
    c = _client()

    def 작업() -> int:
        db.start_batch_run(c, job_name="scores", market="KR", trade_date="2026-09-29")  # type: ignore[arg-type]
        raise RuntimeError("0 으로 나눔")

    daily._하위작업(작업)
    assert c.conn.execute("SELECT status FROM batch_runs WHERE job_name = 'scores'").fetchone() == ("failed",)


def test_수집_단계의_한도는_실패_알림_없이_올린다(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    글 = inspect.getsource(daily.run)
    i = 글.index('db.finish_batch_run(\n                client, run_id, status="failed", step_log=step_log')
    assert "if db.quota_reason(exc):\n                raise" in 글[i - 400 : i]


def test_리포트_경고도_한도는_건너뜀이라_적는다() -> None:
    """기록은 skipped 인데 리포트는 "…실패: D1_ERROR" 였다 (docs/infra.md 25.666, 교차검증)."""
    assert daily._실패문("점수 계산", RuntimeError(한도글)).startswith("점수 계산 건너뜀 (DB 한도)")
    assert daily._실패문("점수 계산", RuntimeError("0 으로 나눔")) == "점수 계산 실패: 0 으로 나눔"
    import inspect

    글 = inspect.getsource(daily)
    assert 'f"{name} 실패: {exc}"' not in 글 and 'f"점수 계산 실패: {예외}"' not in 글


def test_지수·환율·리포트_저장도_한도는_건너뜀이라_적는다() -> None:
    """25.666 이 하위 작업만 고쳐 안쪽 except 넷이 "…실패: D1_ERROR" 로 남았다 (docs/infra.md 25.669, 교차검증)."""
    import inspect

    글 = inspect.getsource(daily)
    for 옛 in ('f"지수 수집 실패: {exc}"', 'f"환율 수집 실패: {exc}"', 'f"리포트 저장 실패: {exc}"'):
        assert 옛 not in 글, 옛
