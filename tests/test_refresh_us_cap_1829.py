"""미국 수정주가 재수집 — 상한은 유입보다 크고, 요청은 100종목씩 나눈다 (docs/infra.md 25.1828·25.1829)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from batch.core import db
from batch.jobs import refresh_us_adjusted as job
from tests.test_metrics_beta import MemClient

YML = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "refresh-us-adjusted.yml"


def test_상한이_워크플로와_같고_10월_유입보다_크다() -> None:
    글 = YML.read_text(encoding="utf-8")
    assert re.search(r"inputs\.max \|\| '(\d+)'", 글).group(1) == str(job.DEFAULT_MAX)  # type: ignore[union-attr]
    assert re.search(r"max:\n\s+description: .+\n\s+type: string\n\s+default: \"(\d+)\"", 글).group(1) == str(job.DEFAULT_MAX)  # type: ignore[union-attr]  # noqa: E501
    assert job.DEFAULT_MAX > 160  # 2026-10 실측 주 유입 어림(25.1828)


def test_요청은_100종목씩_나누고_한쪽이_막혀도_다른_쪽은_든다(monkeypatch: pytest.MonkeyPatch) -> None:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    대기 = [(i, f"S{i}") for i in range(1, 151)]
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(job, "pending", lambda c: 대기)
    monkeypatch.setattr(job, "lookback_for", lambda c, ids, today: job.LOOKBACK_DAYS)
    monkeypatch.setattr(job.db, "remaining_write_budget", lambda c: 100_000_000)
    monkeypatch.setattr(job, "_store_us_bars", lambda *a, **k: (0, set(), None))
    요청: list[int] = []

    def 가짜(group: list[str], lookback_days: int, chunk_size: int) -> SimpleNamespace:
        요청.append(len(group))
        if len(요청) == 1:  # 첫 조각이 막힌다
            return SimpleNamespace(ok=False, data=[], error="429", limit_state="ok", source="t")
        data = [SimpleNamespace(ticker=s, date="2026-10-26") for s in group]
        return SimpleNamespace(ok=True, data=data, error=None, limit_state="ok", source="t")

    monkeypatch.setattr(job.yfinance_src, "fetch_daily_bars", 가짜)
    assert job.run(job.DEFAULT_MAX) == 0
    assert 요청 == [100, 50]
    assert m.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC").fetchone()[0] == "partial"
