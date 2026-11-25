"""감시의 감시 — 무응답 감시가 멈추면 일일 리포트가 말한다 (docs/roadmap.md 1장 #5, docs/infra.md 25.2344).

무응답 감시(cron-job.org → 웹 `cron/health`)가 멈추면 배치가 안 돈 날 알림이 오지 않는다. 그것을 알리는 곳이 /status 화면
뿐이었다. 일일 배치는 Actions 에서 돌아 길이 따로다 — 심장박동 한 줄을 읽어 오래 멈췄으면 경고 한 줄.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from batch.jobs import daily

뿌리 = Path(__file__).resolve().parent.parent
지금 = datetime(2026, 11, 25, 23, 30, tzinfo=UTC)


class _줄:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def dicts(self) -> list[dict]:
        return self._rows


class _판:
    def __init__(self, rows: list[dict] | None = None, exc: Exception | None = None) -> None:
        self.rows, self.exc, self.sqls = rows or [], exc, []

    def execute(self, sql: str, *a: object) -> _줄:
        self.sqls.append(sql)
        if self.exc:
            raise self.exc
        return _줄(self.rows)


def _분전(분: int, outcome: str = "checked") -> _판:
    return _판([{"called_at": (지금 - timedelta(minutes=분)).isoformat().replace("+00:00", "Z"), "outcome": outcome}])


def test_문턱_안이면_말하지_않는다() -> None:
    assert daily.health_cron_note(_분전(0), 지금) is None  # type: ignore[arg-type]
    assert daily.health_cron_note(_분전(daily.HEALTH_CRON_STALE_MINUTES), 지금) is None  # type: ignore[arg-type]
    # 마지막 호출이 실패여도 돌고는 있다 — /status 가 띄운다(여기선 멈춤만)
    assert daily.health_cron_note(_분전(30, "error"), 지금) is None  # type: ignore[arg-type]


def test_문턱을_넘으면_시간과_함께_말한다() -> None:
    글 = daily.health_cron_note(_분전(daily.HEALTH_CRON_STALE_MINUTES + 1), 지금)  # type: ignore[arg-type]
    assert 글 is not None and "3시간 1분째" in 글 and "cron-job.org" in 글
    assert "26시간" in str(daily.health_cron_note(_분전(26 * 60), 지금))  # type: ignore[arg-type]


@pytest.mark.parametrize("판", [_판([]), _판([{"called_at": "1970-01-01T00:00:00Z", "outcome": "never"}])])
def test_한_번도_안_불렸으면_말한다(판: _판) -> None:
    assert "한 번도" in str(daily.health_cron_note(판, 지금))  # type: ignore[arg-type]


def test_시각을_못_읽거나_읽기가_깨지면_말한다_한도는_올린다() -> None:
    assert "읽지 못했습니다" in str(daily.health_cron_note(_판([{"called_at": "x", "outcome": "checked"}]), 지금))  # type: ignore[arg-type]
    assert "실패" in str(daily.health_cron_note(_판(exc=RuntimeError("끊김")), 지금))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):  # 한도면 건너뜀으로 닫는 바깥 경로로 올린다
        daily.health_cron_note(_판(exc=RuntimeError("Operation was blocked: SQL read operations are forbidden (reads limit exceeded)")), 지금)  # type: ignore[arg-type]


def test_한_행만_읽는다() -> None:
    판 = _분전(0)
    daily.health_cron_note(판, 지금)  # type: ignore[arg-type]
    assert len(판.sqls) == 1 and "job = 'health' AND market = 'ALL'" in 판.sqls[0]


def test_문턱이_웹_판정과_같다() -> None:
    """/status 의 `cronVerdict` 는 주기의 세 배 — 무응답 감시 주기(`CRON_EXPECTED` health)가 바뀌면 여기도 바꾼다"""
    글 = (뿌리 / "web/lib/health.ts").read_text(encoding="utf-8")
    m = re.search(r'job: "health", market: "ALL", label: "무응답 감시", everyMinutes: (\d+)', 글)
    assert m and 3 * int(m.group(1)) == daily.HEALTH_CRON_STALE_MINUTES


def test_일일_배치가_리포트_경고로_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_report_store_fail_1180 import _준비

    _준비(monkeypatch, 0)
    닫음: list = []
    monkeypatch.setattr(daily.db, "finish_batch_run", lambda c, rid, **k: 닫음.append(k))
    monkeypatch.setattr(daily, "health_cron_note", lambda c, now: "⚠️ 무응답 감시 멈춤(시험)")
    daily.run("KR")
    assert "⚠️ 무응답 감시 멈춤(시험)" in 닫음[-1]["step_log"]["warnings"]
