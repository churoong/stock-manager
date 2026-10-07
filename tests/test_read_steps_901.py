"""단계별 읽은 행 (docs/infra.md 25.898 점수, 25.901 신호).

실행별 합계(25.885)로는 어느 질의가 비싼지 모른다. 25.899 의 152만 행 날짜 걷기도 이 기록으로 찾았다.
"""

from __future__ import annotations

import inspect

import pytest

from batch.core import db
from batch.core.turso import ReadCounter
from batch.jobs import scores, signals


def test_표시_사이에_읽은_행을_그_이름에_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ReadCounter, "process_total", 100)
    단계 = db.ReadSteps()
    monkeypatch.setattr(ReadCounter, "process_total", 130)
    단계.mark("a")
    monkeypatch.setattr(ReadCounter, "process_total", 180)
    단계.mark("b")
    monkeypatch.setattr(ReadCounter, "process_total", 185)
    단계.mark("a")
    assert 단계.steps == {"a": 35, "b": 50}


@pytest.mark.parametrize("job", [scores, signals])
def test_점수와_신호는_단계별_읽기를_실행_기록에_싣는다(job) -> None:
    src = inspect.getsource(job.run)
    assert "db.ReadSteps()" in src
    assert '"reads_by_step": 단계.steps' in src


def test_신호는_시세_밴드_쓰기를_나눠_적는다() -> None:
    src = inspect.getsource(signals.run)
    for 이름 in ("candidates", "prices", "growth", "metrics", "bands", "trend", "write"):
        assert f'단계.mark("{이름}")' in src
