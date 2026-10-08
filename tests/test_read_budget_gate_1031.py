"""주간·월간 작업도 Turso 월 읽기 진도 문을 지난다 (docs/infra.md 25.1031).

2026-10-08 Turso 월 한도에 걸린 날 주간 작업 12개가 한꺼번에 돌았는데, 진도 문(25.886)은 전체 백업·백테스트에만 있었다.
사용자 "turso 사용량 다른 데서 더 줄일 거 있으면 줄여줘".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from batch.core import client as backend
from batch.core import db, entry

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
#: 며칠 미뤄도 되는 무거운 주간·월간 작업
문을_지나는_것 = (
    "metrics.yml", "valuation-bands.yml", "signal-outcomes.yml", "etf.yml", "accumulation.yml", "refresh-us-adjusted.yml",
    "financials.yml", "us-financials.yml", "universe.yml", "sectors.yml", "dividends.yml", "insider-kr.yml",
    "earnings-calendar.yml", "disclosures-us.yml", "us-shares.yml",
)  # fmt: skip
#: 매일 리포트·장중 재료 — 미루면 리포트가 빠진다. 문을 달지 않는다
문을_달지_않는_것 = ("daily-kr.yml", "daily-us.yml", "kis-flows.yml", "sentiment-kr.yml", "analyze-stock.yml")


class _가짜문:
    def __init__(self, ok: bool) -> None:
        self.ok, self.미룬것 = ok, []

    def decide(self, used, estimate, now):  # noqa: ANN001, ANN201
        return self.ok, f"used={used} est={estimate}"

    def record_skip(self, client, job, text):  # noqa: ANN001, ANN201
        self.미룬것.append((job, text))


@pytest.fixture
def 준비(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.delenv(entry.SKIP_ON_D1_ENV, raising=False)
    monkeypatch.setattr(backend, "resolved_backend", lambda: backend.TURSO)
    monkeypatch.setattr(backend, "TursoClient", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(db, "remaining_read_budget_or_none", lambda c: 100_000_000)

    def 문(ok: bool) -> _가짜문:
        g = _가짜문(ok)
        monkeypatch.setattr(entry, "_load_gate", lambda: g)
        return g

    return 문


def test_진도를_앞서면_시작하지_않고_미룬_것을_남긴다(monkeypatch: pytest.MonkeyPatch, 준비, capsys) -> None:  # noqa: ANN001
    g = 준비(False)
    monkeypatch.setenv(entry.READ_ESTIMATE_ENV, "8000000")
    불림: list = []

    def main() -> int:
        불림.append(1)
        return 0

    main.__module__ = "batch.jobs.metrics"
    assert entry.guard(main) == 0
    assert 불림 == []
    assert g.미룬것 == [("metrics", "used=400000000 est=8000000")]
    assert "미룸:" in capsys.readouterr().out


def test_진도_안이거나_값이_없으면_그대로_돈다(monkeypatch: pytest.MonkeyPatch, 준비) -> None:  # noqa: ANN001
    준비(True)
    monkeypatch.setenv(entry.READ_ESTIMATE_ENV, "8000000")
    assert entry.guard(lambda: 7) == 7
    준비(False)
    monkeypatch.delenv(entry.READ_ESTIMATE_ENV)
    assert entry.guard(lambda: 7) == 7  # 일일 배치처럼 값을 주지 않은 작업은 문을 보지 않는다


def test_재지_못하면_들어간다(monkeypatch: pytest.MonkeyPatch, 준비) -> None:  # noqa: ANN001
    준비(False)
    monkeypatch.setenv(entry.READ_ESTIMATE_ENV, "8000000")

    def 터짐():  # noqa: ANN202
        raise RuntimeError("한도")

    monkeypatch.setattr(backend, "TursoClient", 터짐)
    assert entry.guard(lambda: 7) == 7


def test_무거운_주간_작업에만_문을_단다() -> None:
    for 이름 in 문을_지나는_것:
        data = yaml.safe_load((WORKFLOWS / 이름).read_text(encoding="utf-8"))
        envs = [job.get("env") or {} for job in data["jobs"].values()]
        assert any(int(e.get(entry.READ_ESTIMATE_ENV, 0) or 0) > 0 for e in envs), 이름
    for 이름 in 문을_달지_않는_것:
        assert entry.READ_ESTIMATE_ENV not in (WORKFLOWS / 이름).read_text(encoding="utf-8"), 이름


def test_실제_문_스크립트를_읽는다() -> None:
    g = entry._load_gate()
    assert callable(g.decide) and callable(g.record_skip)
