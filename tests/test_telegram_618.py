"""교차검증 25.613 의 반박 (docs/infra.md 25.618)."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from batch.jobs import daily
from batch.notify import telegram as tg


def test_실패_알림은_모름이어도_한_번_더_보낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    보냄: list[str] = []

    def 보내기(text: str) -> Any:
        보냄.append(text)
        if len(보냄) == 1:
            raise tg.TelegramUncertainError("끊김")
        return [1]

    monkeypatch.setattr(daily.telegram, "send", 보내기)
    monkeypatch.setattr(daily.time, "sleep", lambda _s: None)
    monkeypatch.setattr(daily.formatter, "failure_alert", lambda **k: "실패")

    class 막힌DB:
        def execute(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("DB 못 읽음")

    daily._notify_failure(막힌DB(), "KR", "daily_kr", "원인", dry_run=False)  # type: ignore[arg-type]
    assert len(보냄) == 2


def test_프록시_실패는_보내기_전이다() -> None:
    assert tg._보내기_전_실패(requests.exceptions.ProxyError("Tunnel connection failed: 403"))
    assert not tg._보내기_전_실패(requests.ConnectionError("Connection aborted. RemoteDisconnected"))


def test_오류_문구에서_봇_토큰을_가린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """requests 예외 문구의 URL `/bot<토큰>/…` 이 error_text 로 흘렀다 (docs/infra.md 25.619, 교차검증)."""
    토큰 = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    monkeypatch.setattr(tg, "_token", lambda: 토큰)
    monkeypatch.setattr(tg.time, "sleep", lambda _s: None)

    def 터짐(url: str, **_: Any) -> Any:
        raise requests.exceptions.ProxyError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")

    monkeypatch.setattr(tg.requests, "post", 터짐)
    with pytest.raises(tg.TelegramError) as 잡음:
        tg._call("sendMessage", {"text": "x"})
    assert 토큰 not in str(잡음.value) and "bot***" in str(잡음.value)


def test_traceback_에도_봇_토큰이_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`from exc` 사슬이 원래 예외(URL)를 traceback 에 찍어 Actions 로그·이슈 코멘트로 흘렀다 (docs/infra.md 25.621)."""
    import traceback

    토큰 = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    monkeypatch.setattr(tg, "_token", lambda: 토큰)
    monkeypatch.setattr(tg.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tg.requests, "post", lambda url, **_: (_ for _ in ()).throw(
        requests.exceptions.ProxyError(f"Max retries exceeded with url: {url}")))
    with pytest.raises(tg.TelegramError) as 잡음:
        tg._call("sendMessage", {"text": "x"})
    assert 토큰 not in "".join(traceback.format_exception(잡음.value))
