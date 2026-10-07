"""미국 백필 조각 나누기 테스트."""

from __future__ import annotations

from batch.jobs import backfill_us as job


def test_구간을_조각으로_자른다() -> None:
    symbols = [f"S{i:04d}" for i in range(1000)]
    groups = job.plan_chunks(symbols, start=200, count=450, chunk=200)
    assert [len(g) for g in groups] == [200, 200, 50]
    assert groups[0][0] == "S0200" and groups[-1][-1] == "S0649"


def test_개수를_비우면_끝까지() -> None:
    symbols = [f"S{i}" for i in range(10)]
    assert sum(len(g) for g in job.plan_chunks(symbols, start=7, count=None, chunk=2)) == 3
