"""일일 배치의 하위 작업 실패 처리 (docs/infra.md 25.372)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import daily
from tests.test_portfolio_job import MemClient


@pytest.fixture
def mem() -> MemClient:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    db._열린_실행.clear()
    return m


def test_하위_작업이_깨지면_그_기록만_실패로_닫는다(mem: MemClient) -> None:
    바깥 = db.start_batch_run(mem, job_name="daily_kr", market="KR", trade_date="2026-09-25")  # type: ignore[arg-type]
    안쪽: list[int] = []

    def 깨지는_작업() -> int:
        안쪽.append(db.start_batch_run(mem, job_name="scores", market="KR", trade_date="2026-09-25"))  # type: ignore[arg-type]
        raise RuntimeError("질의 오류")

    code, exc = daily._하위작업(깨지는_작업)
    assert code == 1 and isinstance(exc, RuntimeError)
    상태 = dict(mem.conn.execute("SELECT id, status FROM batch_runs").fetchall())
    assert 상태[안쪽[0]] == "failed"
    assert 상태[바깥] == "running"  # 바깥(일일 배치)은 건드리지 않는다
    assert db.open_run_depth() == 1
    db._열린_실행.clear()


def test_감성_경고가_있어도_점수_실패를_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import scores, sentiment, signals

    def 깨진다(*_a, **_k):
        raise RuntimeError("감성 고장")

    monkeypatch.setattr(sentiment, "run", 깨진다)
    monkeypatch.setattr(scores, "run", lambda *_a, **_k: 1)
    monkeypatch.setattr(signals, "run", lambda *_a, **_k: pytest.fail("점수가 없으면 신호를 돌리지 않는다"))
    경고 = daily.refresh_recommendations("KR", "2026-09-25")
    assert any("감성" in w for w in 경고)
    assert any("점수 계산 실패" in w for w in 경고)
