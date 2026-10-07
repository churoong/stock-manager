"""DART 공시 목록은 쪽을 넘긴다 (docs/infra.md 25.477)."""

from __future__ import annotations

from typing import Any

import pytest

from batch.sources import dart_disclosures as src


class 응답:
    def __init__(self, body: dict[str, Any]) -> None:
        self.status_code = 200
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def _행(n: int) -> dict[str, str]:
    return {"rcept_no": f"2026010100{n:04d}", "report_nm": f"공시{n}", "corp_code": "001"}


@pytest.fixture
def 켜기(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    import dataclasses

    monkeypatch.setattr(src.config, "SETTINGS", dataclasses.replace(src.config.SETTINGS, offline_mode=False))
    monkeypatch.setenv("DART_API_KEY", "k")
    부른쪽: list[int] = []
    return 부른쪽


def test_전체_쪽을_모두_받고_부른_수를_센다(monkeypatch: pytest.MonkeyPatch, 켜기: list[int]) -> None:
    def 가짜(url: str, params: dict[str, Any], timeout: float) -> 응답:
        켜기.append(params["page_no"])
        p = params["page_no"]
        return 응답({"status": "000", "total_page": 3, "list": [_행(p * 10 + i) for i in range(2)]})

    monkeypatch.setattr(src.requests, "get", 가짜)
    got = src.fetch_list("001", "20210101", "20260101")
    assert got.ok and len(got.data) == 6 and got.attempts == 3 and 켜기 == [1, 2, 3]


def test_도중에_한도면_받은_쪽까지_담아_실패로(monkeypatch: pytest.MonkeyPatch, 켜기: list[int]) -> None:
    def 가짜(url: str, params: dict[str, Any], timeout: float) -> 응답:
        if params["page_no"] == 2:
            return 응답({"status": "020", "message": "한도"})
        return 응답({"status": "000", "total_page": 3, "list": [_행(1)]})

    monkeypatch.setattr(src.requests, "get", 가짜)
    got = src.fetch_list("001", "20210101", "20260101")
    assert not got.ok and got.limit_state == "blocked" and len(got.data) == 1 and got.attempts == 2


def test_쪽_상한에서_멈춘다(monkeypatch: pytest.MonkeyPatch, 켜기: list[int]) -> None:
    monkeypatch.setattr(src.requests, "get", lambda url, params, timeout: 응답(
        {"status": "000", "total_page": 999, "list": [_행(params["page_no"])]}))
    got = src.fetch_list("001", "20210101", "20260101")
    assert got.ok and got.attempts == src.MAX_PAGES and len(got.data) == src.MAX_PAGES
