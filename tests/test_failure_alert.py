"""DB 가 막혔을 때도 실패 알림이 나가는지 (docs/infra.md 23절).

2026-09-17 에 Turso 월 한도를 넘겨 읽기가 막혔다. 그때 마지막 성공을 조회하다가
예외가 나면서 알림이 통째로 사라졌다. **배치가 죽은 데다 그 사실조차 전해지지 않는
것이 가장 나쁘다.**
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.jobs import daily
from batch.notify import formatter


class 막힌DB:
    """어떤 조회든 거부한다. 한도에 걸린 Turso 와 같다."""

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Operation was blocked: SQL read operations are forbidden")

    def batch(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Operation was blocked: SQL read operations are forbidden")

    def close(self) -> None:
        pass


def test_DB_를_못_읽어도_텔레그램은_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(daily.telegram, "send", lambda text: sent.append(text))

    daily._notify_failure(막힌DB(), "KR", "daily_kr", "쓰기가 막혔습니다", dry_run=False)  # type: ignore[arg-type]

    assert len(sent) == 1
    assert "배치 실패" in sent[0]
    assert "쓰기가 막혔습니다" in sent[0]


def test_마지막_성공을_모르면_모른다고_적는다() -> None:
    message = formatter.failure_alert(
        market="KR", job_name="daily_kr", error_text="읽기가 막혔습니다", last_success=None
    )
    assert "배치 실패" in message
    assert "읽기가 막혔습니다" in message


def test_알림_자체가_실패해도_예외가_새지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 텔레그램까지 죽은 상황. 배치 결과를 덮으면 안 된다
    def 폭발(_text: str) -> None:
        raise RuntimeError("텔레그램 실패")

    monkeypatch.setattr(daily.telegram, "send", 폭발)
    daily._notify_failure(막힌DB(), "KR", "daily_kr", "오류", dry_run=False)  # type: ignore[arg-type]
