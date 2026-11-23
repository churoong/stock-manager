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
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    지금 = datetime.now(UTC) - timedelta(hours=db.STALE_RUN_HOURS + 1)
    _행(mem, 지금, "running")  # 강제 종료로 열린 채 남은 행
    _행(mem, 지금 + timedelta(hours=1, minutes=8), "success")  # 예비 성공
    assert db.reap_stale_runs(mem, "daily_kr") == 1  # type: ignore[arg-type]  # 실제 정리 문구로 닫는다
    줄 = mem.execute(jd.BATCH_RUNS_SQL, ["daily_kr", "daily_us", "2026-11-01"]).dicts()
    assert desk.batch_incident_days(줄, 기대) == 1


def test_한도로_건너뛴_행은_예비가_성공하면_예전처럼_사고가_아니다() -> None:
    """정리 문구가 아닌 건너뜀(한도·이미 성공 등)은 그대로 — 같은 날 성공이 있으면 무사고"""
    기대 = {"daily_kr": ["2026-11-20"]}
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    지금 = datetime(2026, 11, 22, 23, 27, tzinfo=UTC)
    _행(mem, 지금, "skipped", "DB 한도로 건너뜀: 월 읽기 한도")  # entry.guard 의 문구
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
    # 안쪽 작업·다른 시장의 열린 행은 닫지 않는다(관문 밖 — 정리에 맡김, 25.2255)
    for 이름 in ("sentiment", "daily_us"):
        mem.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, started_at, status)"
                         " VALUES (?, 'KR', '2026-11-20', ?, 'running')", [이름, (지금 - timedelta(minutes=14)).isoformat()])  # fmt: skip
    assert db.fail_timed_out_runs(mem, "daily_kr", 15, "15분 시간 제한") == 1  # type: ignore[arg-type]
    assert [s for s, _ in _상태(mem)] == ["running", "failed", "success", "running", "running"]
    assert _상태(mem)[1][1] == "15분 시간 제한"


def test_시간_초과_알림을_먼저_보내고_그_뒤에_기록을_실패로_닫는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """닫기가 먼저면 Turso 가 느린 날 재시도가 남은 잡 시간을 먹어 알림을 잃는다 (25.2253 교차검증 [필수])"""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _행(mem, datetime.now(UTC) - timedelta(minutes=16), "running")
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    순서: list[str] = []
    monkeypatch.setattr(daily, "_notify_failure", lambda c, m, n, 글, dry_run, **k: 순서.append("알림:" + 글))
    옛닫기 = db.fail_timed_out_runs
    monkeypatch.setattr(db, "fail_timed_out_runs", lambda *a, **k: (순서.append("닫기"), 옛닫기(*a, **k))[1])
    daily.notify_timeout("KR", 15, dry_run=False)
    assert [x.split(":")[0] for x in 순서] == ["알림", "닫기"]
    assert [s for s, _ in _상태(mem)] == ["failed"]
    # 알림·저장 본문이 같은 말 — 닫았다고 단정하지 않는다
    assert "이 알림 뒤 실패로 닫습니다" in 순서[0] and "이 알림 뒤 실패로 닫습니다" in (_상태(mem)[0][1] or "")


def test_시간_초과_알림_단계는_Turso_를_짧게_재시도_없이_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """텔레그램 전의 마지막 성공 읽기·뒤의 닫기가 60초 × 재시도 셋에 매달리면 남은 잡 시간 안에 알림을 잃었다
    (25.2255, 25.2254 교차검증 [필수]) — 알림 전에 짧은 상한이 켜져 있어야 한다"""
    켬: list[float] = []
    순서: list[str] = []

    class 느림:
        def set_quick(self, t: float) -> None:
            켬.append(t)
            순서.append("짧게")

        def execute(self, *a: object, **k: object) -> None:
            순서.append("db")
            raise TimeoutError("Read timed out")

    monkeypatch.setattr(daily, "TursoClient", lambda: 느림())
    monkeypatch.setattr(daily.telegram, "send", lambda text, *a, **k: 순서.append("tg") or [1])
    daily.notify_timeout("KR", 15, dry_run=False)  # 실제 `_notify_failure` 를 탄다
    assert 켬 == [daily.TIMEOUT_DB_REQUEST_S] and 순서[0] == "짧게" and "tg" in 순서
    assert daily.TIMEOUT_DB_REQUEST_S <= 10


def test_짧은_상한이면_한_번만_보내고_그_제한을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    from batch.core import turso

    c = turso.TursoClient("libsql://x.example", "t")
    보냄: list[float] = []

    def post(url: str, json: object, timeout: float) -> None:
        보냄.append(timeout)
        raise requests.ConnectionError("Read timed out")

    monkeypatch.setattr(c._session, "post", post)
    monkeypatch.setattr(turso.time, "sleep", lambda s: 보냄.append(-s))
    c.set_quick(8.0)
    with pytest.raises(turso.TursoError):
        c._post_with_retry({})
    assert 보냄 == [8.0]  # 재시도·기다림 없음


def test_닫을_행이_없으면_닫았다고_말하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """행을 열기 전에 죽은 실행 — 알림 글은 '기록이 있으면' 으로만 말한다 (25.2255)"""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    알림: list[str] = []
    monkeypatch.setattr(daily, "_notify_failure", lambda c, m, n, 글, dry_run, **k: 알림.append(글))
    daily.notify_timeout("KR", 15, dry_run=False)
    assert "실행 기록이 있으면 이 알림 뒤 실패로 닫습니다" in 알림[0]
    assert _상태(mem) == []


def test_dry_run_이면_기록을_건드리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _행(mem, datetime.now(UTC) - timedelta(minutes=16), "running")
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: None)
    daily.notify_timeout("KR", 15, dry_run=True)
    assert [s for s, _ in _상태(mem)] == ["running"]


def test_관문_질의의_정리_문구는_db_상수와_같다() -> None:
    """질의는 정적 문자열이라(동적 질의 상한, test_sql_schema) 문구를 손으로 적는다 — 어긋나면 정리 행 세기가 꺼진다"""
    assert f"LIKE '{db.REAPED_PREFIX}%'" in jd.BATCH_RUNS_SQL
