"""시간 제한 강제 종료도 관문 "운영 무사고" 의 사고다 (docs/infra.md 25.2253, 48회차 교차검증 [권고]).

같은 Turso 시간 초과라도 파이썬 예외로 끝나면 `failed` 라 사고였고, `timeout` 이 프로세스를 죽이면 기록이 `running`
으로 남았다가 `reap_stale_runs` 가 `skipped` 로 정리해 — 같은 날 예비 실행이 성공하면 — 사고가 아니었다(11-23).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from batch.core import db
from batch.jobs import daily
from batch.jobs import desk as jd
from batch.services import desk
from tests.test_portfolio_job import MemClient


def _행(mem: MemClient, started: datetime, status: str, error_text: str | None = None) -> None:
    mem.conn.execute(
        "INSERT INTO batch_runs (job_name, market, trade_date, started_at, status, error_text)"
        " VALUES ('daily_kr', 'KR', '2026-11-20', ?, ?, ?)",
        [started.isoformat(), status, error_text],
    )


def _상태(mem: MemClient) -> list[tuple[str, str | None]]:
    return [(r[0], r[1]) for r in mem.conn.execute("SELECT status, error_text FROM batch_runs ORDER BY id").fetchall()]


def test_정리된_끝맺지_못한_행은_예비가_성공해도_사고다() -> None:
    기대 = {"daily_kr": ["2026-11-20"]}
    정리 = f"끝맺지 못한 기록. {db.STALE_RUN_HOURS}시간 뒤 정리했다"
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    지금 = datetime(2026, 11, 22, 23, 27, tzinfo=UTC)
    _행(mem, 지금, "skipped", 정리)  # 08:27 강제 종료 → 6시간 뒤 정리
    _행(mem, 지금 + timedelta(hours=1, minutes=8), "success")  # 09:35 예비 성공
    줄 = mem.execute(jd.BATCH_RUNS_SQL, ["daily_kr", "daily_us", "2026-11-01"]).dicts()
    assert desk.batch_incident_days(줄, 기대) == 1


def test_한도로_건너뛴_행은_예비가_성공하면_예전처럼_사고가_아니다() -> None:
    """정리 문구가 아닌 건너뜀(한도·이미 성공 등)은 그대로 — 같은 날 성공이 있으면 무사고"""
    기대 = {"daily_kr": ["2026-11-20"]}
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    지금 = datetime(2026, 11, 22, 23, 27, tzinfo=UTC)
    _행(mem, 지금, "skipped", "Turso 월 읽기 한도 — 건너뜀")
    _행(mem, 지금 + timedelta(hours=1), "success")
    줄 = mem.execute(jd.BATCH_RUNS_SQL, ["daily_kr", "daily_us", "2026-11-01"]).dicts()
    assert desk.batch_incident_days(줄, 기대) == 0


def test_시간_초과로_죽은_실행의_열린_행만_실패로_닫는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    지금 = datetime.now(UTC)
    _행(mem, 지금 - timedelta(hours=3), "running")  # 창 밖(옛 열린 행) — reap 몫
    _행(mem, 지금 - timedelta(minutes=16), "running")  # 방금 죽은 실행
    _행(mem, 지금 - timedelta(minutes=10), "success")
    assert db.fail_timed_out_runs(mem, "daily_kr", 15, "15분 시간 제한") == 1  # type: ignore[arg-type]
    assert [s for s, _ in _상태(mem)] == ["running", "failed", "success"]
    assert _상태(mem)[1][1] == "15분 시간 제한"


def test_시간_초과_알림은_기록을_실패로_닫고_그렇게_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _행(mem, datetime.now(UTC) - timedelta(minutes=16), "running")
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    알림: list[str] = []
    monkeypatch.setattr(daily, "_notify_failure", lambda c, m, n, 글, dry_run, **k: 알림.append(글))
    daily.notify_timeout("KR", 15, dry_run=False)
    assert [s for s, _ in _상태(mem)] == ["failed"]
    assert len(알림) == 1 and "실행 기록은 실패로 닫았습니다" in 알림[0]
    assert "시간 뒤 정리됩니다" not in 알림[0]


def test_dry_run_이면_기록을_건드리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _행(mem, datetime.now(UTC) - timedelta(minutes=16), "running")
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: None)
    daily.notify_timeout("KR", 15, dry_run=True)
    assert [s for s, _ in _상태(mem)] == ["running"]
