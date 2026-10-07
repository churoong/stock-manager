"""KRX 호출은 재시도까지 센다 (docs/infra.md 25.390)."""

from __future__ import annotations

from pathlib import Path

import pytest

from batch.sources import krx


class _응답:
    def __init__(self, code: int, body: dict) -> None:
        self.status_code = code
        self._body = body
        self.content = b"x"
        self.text = ""

    def json(self) -> dict:
        return self._body


def test_5xx_두_번_뒤_성공이면_세_번(monkeypatch: pytest.MonkeyPatch) -> None:
    차례 = iter([_응답(503, {}), _응답(502, {}), _응답(200, {"OutBlock_1": []})])
    monkeypatch.setattr(krx.requests, "get", lambda *_a, **_k: next(차례))
    monkeypatch.setattr(krx.time, "sleep", lambda _s: None)
    monkeypatch.setattr(krx.config, "get", lambda _k: "key")
    import dataclasses

    monkeypatch.setattr(krx.config, "SETTINGS", dataclasses.replace(krx.config.SETTINGS, offline_mode=False))
    result = krx.fetch("kospi_daily", "20260925")
    assert result.ok and result.attempts == 3


def test_키가_없으면_보내지_않았으니_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(krx.config, "get", lambda _k: "")
    import dataclasses

    monkeypatch.setattr(krx.config, "SETTINGS", dataclasses.replace(krx.config.SETTINGS, offline_mode=False))
    assert krx.fetch("kospi_daily", "20260925").attempts == 0


def test_부르는_곳이_모두_재시도_수로_센다() -> None:
    뿌리 = Path(__file__).resolve().parents[1] / "batch" / "jobs"
    for 이름 in ("daily.py", "universe.py", "etf.py", "backfill_kr.py"):
        본문 = (뿌리 / 이름).read_text(encoding="utf-8")
        assert '"krx_openapi", limit_value' not in 본문.replace("\n", " ").replace("  ", " "), 이름
