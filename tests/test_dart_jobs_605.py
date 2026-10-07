"""공시·내부자 수집: 한도가 아닌 DART 실패 (docs/infra.md 25.605, 감사).

키 오류(011)·점검(800)·5xx 가 잇달면 멈추고, 원인을 기록에 남기고, 부르지 않은 호출은 세지 않는다.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from batch import config
from batch.core import db
from batch.jobs import disclosures_kr
from batch.jobs import insider_kr as job
from batch.sources import dart, dart_insider
from batch.sources.yfinance_src import FetchResult
from tests.test_disclosures import Test적재
from tests.test_portfolio_job import MemClient

키오류 = "DART 오류 011: 사용할 수 없는 키입니다"


class Test공시:
    def _돌림(self, monkeypatch: pytest.MonkeyPatch, 결과들: list[FetchResult], 회사수: int) -> tuple[list, list]:
        불린: list[str] = []
        반복 = iter(결과들)

        def 가짜(corp_code: str, bgn: str, end: str) -> FetchResult:
            불린.append(corp_code)
            return next(반복)

        monkeypatch.setattr(disclosures_kr.src, "fetch_list", 가짜)
        monkeypatch.setattr(disclosures_kr.time, "sleep", lambda _s: None)
        monkeypatch.setattr(db, "record_and_guard", lambda *a, **k: "ok")
        monkeypatch.setattr(disclosures_kr, "store", lambda _c, rows: len(rows))
        monkeypatch.setattr(disclosures_kr, "pick_targets", lambda _c, _e=False: [(i, f"C{i}") for i in range(회사수)])
        _대상, _저장, 경고, _덮음 = disclosures_kr.collect(Test적재()._client())  # type: ignore[arg-type]
        return 불린, 경고

    def test_잇달면_멈추고_원인을_남긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        n = dart.MAX_CONSECUTIVE_FAILURES
        불린, 경고 = self._돌림(monkeypatch, [FetchResult(ok=False, error=키오류) for _ in range(30)], 30)
        assert len(불린) == n
        assert any("잇달았습니다" in w for w in 경고)
        assert any("011" in w for w in 경고), "원인(키 오류)이 경고에 있어야 한다"

    def test_사이에_성공이_있으면_다시_센다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        n = dart.MAX_CONSECUTIVE_FAILURES
        결과 = [FetchResult(ok=False, error=키오류)] * (n - 1) + [FetchResult(ok=True, data=[])] + \
            [FetchResult(ok=False, error=키오류)] * (n - 1)
        불린, 경고 = self._돌림(monkeypatch, 결과, len(결과))
        assert len(불린) == len(결과)
        assert not any("잇달았습니다" in w for w in 경고)

    def test_일일_배치가_공시_경고를_리포트에_올린다(self) -> None:
        from batch.jobs import daily

        src = inspect.getsource(daily)
        assert "하위.append(disclosures_kr.JOB_NAME)" in src


def _준비(monkeypatch: pytest.MonkeyPatch, 회사수: int) -> MemClient:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job.time, "sleep", lambda _: None)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sid in range(1, 회사수 + 1):
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, dart_corp_code)"
            " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't', ?)",
            [sid, f"{sid:06d}", f"{sid:08d}"],
        )
        mem.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES ('2026-09-14', ?, 1, 'KRW', 't')",
            [sid],
        )
    return mem


class Test내부자:
    def test_잇달면_멈추고_failed_로_원인을_남긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _준비(monkeypatch, 12)
        불린: list[str] = []

        def 실패(corp_code: str, since: str | None = None) -> FetchResult:
            불린.append(corp_code)
            return FetchResult(ok=False, source=dart_insider.SOURCE, error=키오류, limit_state="unknown")

        monkeypatch.setattr(dart_insider, "fetch_reports", 실패)
        assert job.run(as_of="2026-09-18") == 1
        assert len(불린) == dart.MAX_CONSECUTIVE_FAILURES
        status, error = mem.conn.execute(
            "SELECT status, error_text FROM batch_runs WHERE job_name = 'insider_kr'"
        ).fetchone()
        assert status == "failed" and "011" in error

    def test_일부_실패는_partial_에_원인을_남긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = _준비(monkeypatch, 3)
        결과 = iter([
            FetchResult(ok=True, source=dart_insider.SOURCE, data=([], 0)),
            FetchResult(ok=False, source=dart_insider.SOURCE, error="HTTP 503"),
            FetchResult(ok=True, source=dart_insider.SOURCE, data=([], 0)),
        ])
        monkeypatch.setattr(dart_insider, "fetch_reports", lambda c, since=None: next(결과))
        assert job.run(as_of="2026-09-18") == 0
        status, error = mem.conn.execute(
            "SELECT status, error_text FROM batch_runs WHERE job_name = 'insider_kr'"
        ).fetchone()
        assert status == "partial" and "503" in error

    def test_키가_비면_세지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, offline_mode=False))
        monkeypatch.setattr(config, "get", lambda name, default=None: "")
        assert dart_insider.fetch_reports("00126380").attempts == 0

    def test_기준일은_한국_날짜다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import date

        mem = _준비(monkeypatch, 1)
        monkeypatch.setattr(job.cal, "local_today", lambda market: date(2030, 1, 2) if market == "KR" else None)
        monkeypatch.setattr(dart_insider, "fetch_reports",
                            lambda c, since=None: FetchResult(ok=True, source="d", data=([], 0)))  # fmt: skip
        job.run()
        assert mem.conn.execute("SELECT trade_date FROM batch_runs WHERE job_name = 'insider_kr'").fetchone()[0] \
            == "2030-01-02"


def test_멈춤_원인은_지금_실패다(monkeypatch: pytest.MonkeyPatch) -> None:
    """앞의 503 과 멈추게 한 011 이 다르면 011 을 말해야 한다 (docs/infra.md 25.606, 교차검증)."""
    n = dart.MAX_CONSECUTIVE_FAILURES
    결과 = [FetchResult(ok=False, error="HTTP 503")] * 3 + [FetchResult(ok=True, data=[])] + \
        [FetchResult(ok=False, error=키오류)] * n
    _불린, 경고 = Test공시()._돌림(monkeypatch, 결과, len(결과))
    멈춤 = [w for w in 경고 if "잇달았습니다" in w]
    assert 멈춤 and "011" in 멈춤[0]

    mem = _준비(monkeypatch, 10)
    반복 = iter([FetchResult(ok=False, source="d", error="HTTP 503"), FetchResult(ok=True, source="d", data=([], 0))]
               + [FetchResult(ok=False, source="d", error=키오류)] * n)  # fmt: skip
    monkeypatch.setattr(dart_insider, "fetch_reports", lambda c, since=None: next(반복))
    job.run(as_of="2026-09-18")
    error = mem.conn.execute("SELECT error_text FROM batch_runs WHERE job_name = 'insider_kr'").fetchone()[0]
    assert "011" in error
