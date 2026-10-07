"""업종·배당 수집: 한도 아닌 DART 실패 (docs/infra.md 25.607, 감사)."""

from __future__ import annotations

import dataclasses

import pytest

from batch import config
from batch.core import db
from batch.jobs import dividends, sectors
from batch.sources import dart, dart_dividends
from batch.sources.yfinance_src import FetchResult
from tests.test_portfolio_job import MemClient

키오류 = "DART 오류 011: 사용할 수 없는 키입니다"


def _mem(monkeypatch: pytest.MonkeyPatch, 모듈) -> MemClient:
    mem = MemClient()
    monkeypatch.setattr(모듈, "TursoClient", lambda: mem)
    monkeypatch.setattr(모듈.time, "sleep", lambda _s: None)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    return mem


class Test업종:
    def _돌림(self, monkeypatch: pytest.MonkeyPatch, 결과들: list[FetchResult]) -> tuple[MemClient, list[str]]:
        mem = _mem(monkeypatch, sectors)
        monkeypatch.setattr(sectors, "target_corps", lambda _c: [(f"C{i}", i + 1) for i in range(len(결과들))])
        불린: list[str] = []
        반복 = iter(결과들)
        monkeypatch.setattr(dart, "fetch_company_industry", lambda c: 불린.append(c) or next(반복))
        sectors.run("KR")
        return mem, 불린

    def test_잇달면_멈추고_원인을_남긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem, 불린 = self._돌림(monkeypatch, [FetchResult(ok=False, error=키오류)] * 20)
        assert len(불린) == dart.MAX_CONSECUTIVE_FAILURES
        status, error = mem.conn.execute("SELECT status, error_text FROM batch_runs WHERE job_name = ?",
                                         [sectors.JOB_NAME]).fetchone()  # fmt: skip
        assert status == "failed" and "011" in error

    def test_일부_실패면_success_가_아니다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """한 건만 채워도 success 로 닫혀 따라잡기가 30일 동안 다시 돌지 않았다."""
        결과 = [FetchResult(ok=True, data="264")] + [FetchResult(ok=False, error="HTTP 503")]
        mem, _ = self._돌림(monkeypatch, 결과)
        status, error = mem.conn.execute("SELECT status, error_text FROM batch_runs WHERE job_name = ?",
                                         [sectors.JOB_NAME]).fetchone()  # fmt: skip
        assert status == "partial" and "503" in error


class Test배당:
    def test_멈춘_까닭이_기록_맨_앞에_남는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _mem(monkeypatch, dividends)
        monkeypatch.setattr(dividends, "targets", lambda _c: [(i, f"C{i}") for i in range(1, 30)])
        monkeypatch.setattr(dividends.db, "usage_blocked_today", lambda *_a: False)
        monkeypatch.setattr(dividends.dd, "fetch_alot_matter", lambda *_a: FetchResult(ok=False, error=키오류))
        assert dividends.run(latest=2025) == 1
        status, error = mem.conn.execute("SELECT status, error_text FROM batch_runs WHERE job_name = ?",
                                         [dividends.JOB_NAME]).fetchone()  # fmt: skip
        assert status == "failed" and "연속" in error and "요청 제한" not in error


def test_부르지_않은_실패는_세지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, offline_mode=False))
    monkeypatch.setattr(config, "get", lambda name, default=None: "")
    assert dart.fetch_company_industry("00126380").attempts == 0
    assert dart_dividends.fetch_alot_matter("00126380", 2025).attempts == 0


def test_기업개황_013_은_업종코드_없음이다(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패로 세면 개황 없는 회사 다섯이 잇달 때 잇단 실패 멈춤이 헛걸린다."""
    import types

    monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, offline_mode=False))
    monkeypatch.setattr(config, "get", lambda name, default=None: "키")
    응답 = types.SimpleNamespace(status_code=200, json=lambda: {"status": "013", "message": "조회된 데이타가 없습니다"})
    monkeypatch.setattr(dart.requests, "get", lambda *a, **k: 응답)
    r = dart.fetch_company_industry("00126380")
    assert r.ok and r.data is None
