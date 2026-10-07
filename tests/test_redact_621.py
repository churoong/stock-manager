"""오류 문구에서 비밀값을 가린다 (docs/infra.md 25.621, 교차검증)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
import requests

from batch import config
from batch.core.redact import 가림
from batch.sources import dart, dart_disclosures, dart_dividends, dart_insider

키 = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"


def test_DART_키와_봇_토큰을_가린다() -> None:
    글 = (f"HTTPSConnectionPool: Max retries exceeded with url: /api/list.json?crtfc_key={키}&corp_code=1 (Caused by x)"
         " /bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage")  # fmt: skip
    가린 = 가림(글)
    assert 키 not in 가린 and "AAHdqTcv" not in 가린
    assert "crtfc_key=***&corp_code=1" in 가린 and "bot***" in 가린


def test_가릴_것이_없으면_그대로() -> None:
    assert 가림("HTTP 503") == "HTTP 503"


@pytest.mark.parametrize(
    "부르기",
    [
        lambda: dart.fetch_company_industry("00126380"),
        lambda: dart_dividends.fetch_alot_matter("00126380", 2025),
        lambda: dart_insider.fetch_reports("00126380"),
        lambda: dart_disclosures.fetch_list("00126380", "20260901", "20260925"),
    ],
)
def test_DART_호출_실패_문구에_키가_없다(monkeypatch: pytest.MonkeyPatch, 부르기) -> None:
    """requests 예외 문구는 URL(쿼리의 crtfc_key)을 담는다 — 그 문구가 경고·리포트·실행 기록으로 흘렀다."""
    monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, offline_mode=False))
    monkeypatch.setattr(config, "get", lambda name, default=None: 키)

    def 터짐(url: str, params: dict | None = None, **_: Any) -> Any:
        쿼리 = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}?{쿼리}")

    monkeypatch.setattr(requests, "get", 터짐)
    r = 부르기()
    assert r.ok is False and 키 not in (r.error or "")


def test_이슈_코멘트로_올리기_전에_가린다() -> None:
    """tee 로 쓴 run.log 는 Actions 가 가리지 않는다 — 코멘트 본문에서 가린다 (docs/infra.md 25.625)."""
    import importlib.util
    from pathlib import Path

    경로 = Path(__file__).resolve().parents[1] / "scripts" / "publish_output.py"
    spec = importlib.util.spec_from_file_location("publish_output", 경로)
    assert spec and spec.loader
    모듈 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(모듈)
    본문 = 모듈.comment_body(f"url: /api/list.json?crtfc_key={키}&x=1", "메모", None, pinned="/bot1:ABCdef_ghi/send")
    assert 키 not in 본문 and "ABCdef" not in 본문
