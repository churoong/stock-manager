"""KRX 백필의 한도·연속 실패 처리 (docs/infra.md 25.387)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from batch.jobs import backfill_kr as job
from batch.sources import krx
from batch.sources.yfinance_src import FetchResult


def _행(isu: str) -> SimpleNamespace:
    return SimpleNamespace(isu_cd=isu, close=100.0, open=1, high=1, low=1, volume=1, value=1, change_pct=0.0)


def test_한도에_닿은_호출의_하루치도_저장하고_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job.db, "usage_blocked_today", lambda *_a: False)
    monkeypatch.setattr(krx, "fetch_daily", lambda *_a: FetchResult(ok=True, data=[_행("A"), _행("B")], source="krx"))
    monkeypatch.setattr(job.db, "record_api_call", lambda *_a, **_k: {"state": "blocked"})
    저장: list = []
    monkeypatch.setattr(job.db, "bulk_upsert_prices", lambda _c, rows: 저장.extend(rows) or len(rows))
    count, error = job.store_day(None, "KOSPI", date(2026, 9, 25), {"A": 1, "B": 2})  # type: ignore[arg-type]
    assert count == 2 and len(저장) == 2
    assert error is not None and "한도" in error


def test_이미_한도면_부르지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job.db, "usage_blocked_today", lambda *_a: True)
    monkeypatch.setattr(krx, "fetch_daily", lambda *_a: pytest.fail("한도인 날에 불렀다"))
    count, error = job.store_day(None, "KOSPI", date(2026, 9, 25), {})  # type: ignore[arg-type]
    assert count == 0 and error is not None and "한도" in error


def test_거래일인데_빈_응답이면_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """달력은 거래일인데 거래소가 비었으면 성공으로 넘기지 않는다 (docs/infra.md 25.571, 감사)."""
    monkeypatch.setattr(job.db, "usage_blocked_today", lambda *_a: False)
    monkeypatch.setattr(krx, "fetch_daily", lambda *_a: FetchResult(ok=True, data=[], source="krx"))
    monkeypatch.setattr(job.db, "record_api_call", lambda *_a, **_k: {"state": "ok"})
    monkeypatch.setattr(job, "아직_집계_전", lambda day, now=None: day >= date(2026, 9, 28))
    count, error = job.store_day(None, "KOSPI", date(2026, 9, 25), {})  # type: ignore[arg-type]
    assert count == 0 and error is not None and "비었습니다" in error
    # 아직 집계 전인 날은 오류가 아니다 — 따라잡기가 거래일마다 실패로 끝났다 (25.574, 교차검증)
    assert job.store_day(None, "KOSPI", date(2026, 9, 28), {}) == (0, None)  # type: ignore[arg-type]


def test_집계_전_판정() -> None:
    """오늘은 물론, 한국 09시 전에는 직전 거래일도 아직이다 (docs/infra.md 25.575, 교차검증)."""
    from datetime import UTC, datetime

    새벽 = datetime(2026, 9, 28, 17, 0, tzinfo=UTC)  # 9/29(화) 02:00 KST
    assert job.아직_집계_전(date(2026, 9, 29), 새벽)
    assert job.아직_집계_전(date(2026, 9, 28), 새벽)  # 월요일 시세는 화요일 08시 무렵에 나온다
    assert not job.아직_집계_전(date(2026, 9, 23), 새벽)
    낮 = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)  # 9/29 12:00 KST
    assert not job.아직_집계_전(date(2026, 9, 28), 낮)
    assert job.아직_집계_전(date(2026, 9, 29), 낮)
    # 토요일 낮에도 금요일은 아직(다음 영업일 아침까지) — 거짓 오류를 내지 않는다 (25.576, 교차검증)
    토요일_낮 = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)
    assert job.아직_집계_전(date(2026, 10, 2), 토요일_낮)
    assert not job.아직_집계_전(date(2026, 10, 1), 토요일_낮)


def test_유니버스_시총_갱신도_빈_응답을_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import universe

    monkeypatch.setattr(krx, "fetch_daily", lambda *_a: FetchResult(ok=True, data=[], source="krx"))
    monkeypatch.setattr(universe.db, "record_api_call", lambda *_a, **_k: {"state": "ok"})
    경고 = universe.refresh_kr_market_cap(None, "20260925")  # type: ignore[arg-type]
    assert len(경고) == 2 and all("비었습니다" in w for w in 경고)


def test_연속_실패면_멈춘다() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "backfill_kr.py").read_text(encoding="utf-8")
    assert "if 연속실패 >= MAX_CONSECUTIVE_FAILURES:" in src
    assert job.MAX_CONSECUTIVE_FAILURES == 5
