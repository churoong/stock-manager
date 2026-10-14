"""ETF "우리 종목 집중" — 한 팩터만 있는 종목의 몫을 기록한다(바꾸지 않음) (docs/infra.md 25.1344, 21회차 P5)."""

from __future__ import annotations

import json

from batch.jobs import etf_tilt as job


def test_팩터_수() -> None:
    assert job.factor_count(json.dumps({"value": 60, "quality": 70})) == 2
    assert job.factor_count(json.dumps({"value": 60, "quality": None, "momentum": 90})) == 1
    assert job.factor_count("깨짐") == 0 and job.factor_count(None) == 0


def test_몫() -> None:
    점수 = {1: 90.0, 2: 80.0, 3: 50.0, 4: 40.0}
    몫 = job.single_factor_share(점수, {1, 3}, {1, 2})
    assert 몫 == {"scored": 4, "single_all": 2, "top": 2, "single_top": 1, "share_all": 0.5, "share_top": 0.5}
    assert job.single_factor_share({}, set(), set())["share_all"] is None


class _C:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def execute(self, sql, args=None):  # noqa: ANN001, ANN201
        rows = self.rows

        class R:
            def scalar(self):  # noqa: ANN202
                return "2026-10-13"

            def dicts(self):  # noqa: ANN202
                return rows

        return R()


def test_점수_읽기가_한_팩터_종목을_모은다() -> None:
    rows = [{"stock_id": 1, "calc_version": 3, "factor_scores": json.dumps({"value": 90})},
            {"stock_id": 2, "calc_version": 3, "factor_scores": json.dumps({"value": 60, "quality": 70})}]  # fmt: skip
    한: set[int] = set()
    점수, _ = job.load_scores(_C(rows), "US", 한)  # type: ignore[arg-type]
    assert 점수 == {1: 90.0, 2: 65.0} and 한 == {1}  # 점수는 예전과 같다(기록만)
