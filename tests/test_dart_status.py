"""DART 상태 코드 — 일일 한도는 020 하나다 (docs/infra.md 25.389)."""

from __future__ import annotations

from pathlib import Path

from batch.sources import dart

뿌리 = Path(__file__).resolve().parents[1]


def test_일일_한도는_020_하나다() -> None:
    assert dart.STATUS_DAILY_LIMIT == "020"


def test_021_을_한도로_보는_곳이_없다() -> None:
    for 길 in [뿌리 / "batch" / "sources" / "dart.py", 뿌리 / "batch" / "sources" / "dart_insider.py",
               뿌리 / "web" / "app" / "api" / "cron" / "intraday" / "route.ts"]:  # fmt: skip
        본문 = 길.read_text(encoding="utf-8")
        assert '("020", "021")' not in 본문 and '["020", "021"]' not in 본문, 길.name


def test_021_응답은_blocked_가_아니다(monkeypatch) -> None:
    class 응답:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"status": "021", "message": "조회 가능한 회사 개수가 초과하였습니다"}

    monkeypatch.setattr(dart.requests, "get", lambda *_a, **_k: 응답())
    monkeypatch.setenv("DART_API_KEY", "x")
    result = dart.fetch_company_industry("00126380")
    assert not result.ok and result.limit_state != "blocked"
