"""읽기 카운터와 예산 가드 (docs/infra.md 24절).

2026-09-18 에 **읽기** 한도(월 5억)를 넘겨 계정이 막혔다. 쓰기만 세고 읽기는 세지 않았다.
여기서 보는 것은 셋이다.
  1. 응답의 rows_read 를 클라이언트 한 곳에서 모으는가 (적재 함수가 각자 세면 빠지는 곳이 생긴다)
  2. 세는 동안 일어난 읽기를 버리지 않는가
  3. 예산이 모자라면 큰 작업이 시작하지 않는가
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.core import db, turso
from tests.test_portfolio_job import MemClient


class Test응답_해석:
    def test_rows_read_를_읽는다(self) -> None:
        rs = turso._to_result_set({"cols": [], "rows": [], "rows_read": 12_345})
        assert rs.rows_read == 12_345

    def test_stat_안에_있어도_읽는다(self) -> None:
        rs = turso._to_result_set({"cols": [], "rows": [], "stat": {"rows_read": 77}})
        assert rs.rows_read == 77

    def test_없으면_0_이다(self) -> None:
        """지어내지 않는다. 서버가 안 주면 0 이고, 그 사실은 문서에 [확인필요] 로 남아 있다."""
        assert turso._to_result_set({"cols": [], "rows": []}).rows_read == 0


class Test카운터:
    def test_문턱을_넘을_때만_내보낸다(self) -> None:
        counter = turso.ReadCounter(flush_rows=100)
        assert counter.add(60) == 0
        assert counter.add(50) == 110  # 넘은 순간 쌓인 것을 통째로
        assert counter.pending == 0 and counter.total == 110

    def test_남은_것은_close_에서_가져간다(self) -> None:
        counter = turso.ReadCounter(flush_rows=1_000)
        counter.add(30)
        assert counter.take() == 30 and counter.take() == 0

    def test_쓰기와_읽기가_같은_구조다(self) -> None:
        assert issubclass(turso.WriteCounter, turso.RowCounter)
        assert issubclass(turso.ReadCounter, turso.RowCounter)


class Test기록:
    def test_이번_달_누계에_더하고_상태를_낸다(self) -> None:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        db.record_rows_read(mem, 400_000_000)  # type: ignore[arg-type]
        row = db.record_rows_read(mem, 10_000_000)  # type: ignore[arg-type]
        assert row is not None and row["call_count"] == 410_000_000
        assert row["limit_value"] == db.TURSO_MONTHLY_READ_LIMIT
        assert row["state"] == "warn"  # 80% 를 넘었다

    def test_쓰기와_읽기가_섞이지_않는다(self) -> None:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        db.record_rows_read(mem, 5)  # type: ignore[arg-type]
        db.record_rows_written(mem, 7)  # type: ignore[arg-type]
        rows = mem.conn.execute(
            "SELECT api_name, call_count FROM api_usage ORDER BY api_name"
        ).fetchall()
        assert rows == [("turso_reads", 5), ("turso_writes", 7)]

    def test_0_이하는_적지_않는다(self) -> None:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        assert db.record_rows_read(mem, 0) is None  # type: ignore[arg-type]
        assert mem.conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 0

    def test_기록이_실패해도_본_작업을_막지_않는다(self) -> None:
        class Broken:
            def execute(self, *_args: Any, **_kwargs: Any):
                raise RuntimeError("DB 막힘")

        assert db.record_rows_read(Broken(), 10) is None  # type: ignore[arg-type]


class Test예산_가드:
    def test_남은_예산보다_크면_막는다(self) -> None:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        db.record_rows_read(mem, db.TURSO_MONTHLY_READ_LIMIT - 1_000)  # type: ignore[arg-type]
        ok, text = db.check_read_budget(mem, 5_000, "백테스트")  # type: ignore[arg-type]
        assert ok is False
        assert "백테스트" in text and "5,000" in text

    def test_여유가_있으면_통과한다(self) -> None:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        ok, _text = db.check_read_budget(mem, 1_000_000, "국내 수정주가")  # type: ignore[arg-type]
        assert ok is True

    def test_아무것도_모르면_한도_전체가_남은_것이다(self) -> None:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        assert db.remaining_read_budget(mem) == db.TURSO_MONTHLY_READ_LIMIT  # type: ignore[arg-type]


def test_예산이_모자라면_백테스트가_시작하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import backtest as job

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    mem.conn.execute(
        "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
        " VALUES ('2026-09-14', 1, 1, 'KRW', 't')"
    )
    db.record_rows_read(mem, db.TURSO_MONTHLY_READ_LIMIT)  # type: ignore[arg-type]

    assert job.run("KR", years=5) == 1
    row = mem.conn.execute(
        "SELECT status, error_text FROM batch_runs WHERE job_name = ? ORDER BY id DESC", [job.JOB_NAME]
    ).fetchone()
    assert row[0] == "skipped" and "남은 읽기" in row[1]


def test_카운터를_못_적으면_이유를_함께_남긴다(caplog: pytest.LogCaptureFixture) -> None:
    """**이유 없는 경고는 가를 수 없다** (2026-10-02, docs/infra.md 25.884).

    복귀 뒤 Turso 실행마다 "turso_reads 카운터를 갱신하지 못했습니다" 가 떴는데 원인이 적혀 있지 않았다.
    """
    from batch.core import db as core_db

    class 깨진:
        def execute(self, sql: str, args: list | None = None) -> None:
            raise RuntimeError("no such table: api_usage")

    with caplog.at_level("WARNING"):
        assert core_db._record_rows(깨진(), "turso_reads", 10, 100) is None  # type: ignore[arg-type]
    assert "no such table: api_usage" in caplog.text
