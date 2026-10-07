"""교차검증 25.606·607 의 반박 (docs/infra.md 25.609)."""

from __future__ import annotations

from typing import Any

import pytest

from batch.core import db
from batch.jobs import backfill_us, sectors
from batch.sources import dart, yfinance_src
from batch.sources.yfinance_src import DailyBar, FetchResult
from tests.test_portfolio_job import MemClient


def test_백필은_일부만_막힌_조각_뒤에_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.606 부터 일부 막힘은 ok=True·blocked 로 온다 — `not ok` 안에서만 보면 남은 조각을 다 불렀다."""
    mem = MemClient()
    monkeypatch.setattr(backfill_us, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(backfill_us, "_us_symbol_ids", lambda c, **_: {s: i for i, s in enumerate("ABCDEF", 1)})
    monkeypatch.setattr(backfill_us, "_store_us_bars", lambda *a, **k: (1, 1, 0))
    불린: list[list[str]] = []

    def 받기(group: list[str], **_: Any) -> FetchResult:
        불린.append(group)
        return FetchResult(ok=True, data=[DailyBar(group[0], "2026-09-25", 1.0)], limit_state="blocked",
                           error="야후 호출 제한: 1/2종목")  # fmt: skip

    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", 받기)
    assert backfill_us.run(lookback=10, start=0, count=None, chunk=2) == 1
    assert len(불린) == 1
    step = mem.conn.execute("SELECT status, step_log FROM batch_runs WHERE job_name = ?", [backfill_us.JOB_NAME]).fetchone()
    assert step[0] == "partial" and '"blocked": true' in step[1]


class Test업종:
    @staticmethod
    def _돌림(monkeypatch: pytest.MonkeyPatch, 결과들: list[FetchResult], 카운터) -> tuple[MemClient, list[str]]:
        mem = MemClient()
        monkeypatch.setattr(sectors, "TursoClient", lambda: mem)
        monkeypatch.setattr(sectors.time, "sleep", lambda _s: None)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        monkeypatch.setattr(sectors, "target_corps", lambda _c: [(f"C{i}", i + 1) for i in range(len(결과들))])
        monkeypatch.setattr(sectors.db, "record_and_guard", 카운터)
        불린: list[str] = []
        반복 = iter(결과들)
        monkeypatch.setattr(dart, "fetch_company_industry", lambda c: 불린.append(c) or next(반복))
        sectors.run("KR")
        return mem, 불린

    def test_실패한_호출에서도_우리_카운터가_차면_멈춘다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        결과 = [FetchResult(ok=True, data="264")] * 99 + [FetchResult(ok=False, error="HTTP 503")] + \
            [FetchResult(ok=True, data="264")] * 150
        _mem, 불린 = self._돌림(monkeypatch, 결과, lambda *a, **k: "blocked")
        assert len(불린) == 100

    def test_잇단_실패로_멈추면_failed_라_따라잡기가_다시_돈다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        결과 = [FetchResult(ok=True, data="264")] + [FetchResult(ok=False, error="HTTP 503")] * 10
        mem, _ = self._돌림(monkeypatch, 결과, lambda *a, **k: "ok")
        assert mem.conn.execute("SELECT status FROM batch_runs WHERE job_name = ?", [sectors.JOB_NAME]).fetchone()[0] \
            == "failed"
        assert not db.ran_within(mem, sectors.JOB_NAME, 30, "KR")  # type: ignore[arg-type]


def test_수정주가_재수집도_일부만_막힌_조각_뒤에_멈춘다() -> None:
    import inspect

    from batch.jobs import refresh_us_adjusted

    src = inspect.getsource(refresh_us_adjusted)
    i = src.index("done.extend(ids[sym] for sym in group if sym in got)")
    assert 'if result.limit_state == "blocked":' in src[i : i + 400], "받은 것을 담은 뒤 막힘을 봐야 한다"


def test_업종이_한도로_도중에_멈춰도_failed_라_다시_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.609 는 잇단 실패만 failed 로 바꿨다 — 한도 멈춤은 partial 로 30일 건너뛰었다 (docs/infra.md 25.611)."""
    결과 = [FetchResult(ok=True, data="264")] * 250
    mem, 불린 = Test업종._돌림(monkeypatch, 결과, lambda *a, **k: "blocked")
    assert len(불린) == 100
    assert mem.conn.execute("SELECT status FROM batch_runs WHERE job_name = ?", [sectors.JOB_NAME]).fetchone()[0] \
        == "failed"
