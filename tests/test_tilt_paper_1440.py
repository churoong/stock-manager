"""ETF 집중 순위 종이 성적표 — 기록만 (docs/infra.md 25.1440, docs/etf.md 11.5, factors.md 12.31 P5)."""

from __future__ import annotations

import json

import pytest

from batch.jobs import etf_tilt as job
from batch.services import etf_tilt as et


def test_같은_비중_가격_수익과_기준_대비() -> None:
    r = et.paper_excess({"A": 100.0, "B": 50.0}, {"A": 110.0, "B": 50.0, "VTI": 102.0}, 100.0, 102.0)
    assert r["mean"] == pytest.approx(0.05) and r["bench"] == pytest.approx(0.02) and r["ex"] == pytest.approx(0.03)


def test_분할_의심과_빈_종가는_모름() -> None:
    # 2:1 분할 + 5% 오름 = 0.525 — 예전 띠(0.5~2.0) 안이었다
    r = et.paper_excess({"A": 100.0, "B": 50.0, "C": 10.0}, {"A": 52.5, "B": 51.0}, 100.0, 101.0)
    assert (r["known"], r["unknown"]) == (1, 2) and r["mean"] == pytest.approx(0.02)
    # 셋 넘게 모르면 그 달 모름
    r = et.paper_excess({s: 1.0 for s in "ABCD"}, {}, 1.0, 1.0)
    assert r["mean"] is None and r["ex"] is None
    # 기준이 없으면 평균만(원화 상장 미국 지수 무리)
    assert et.paper_excess({"A": 100.0}, {"A": 101.0}, None, None)["ex"] is None


class _C:
    def __init__(self, 앞: dict | None) -> None:
        self.앞 = 앞

    def execute(self, sql, args=None):  # noqa: ANN001, ANN201
        assert "etf_tilt" in sql and args == [7]
        글 = json.dumps(self.앞) if self.앞 is not None else None

        class R:
            def scalar(self):  # noqa: ANN202
                return 글

        return R()


def test_지난_실행의_열과_견준다() -> None:
    앞 = {"leaders_close": {"us": {"A": 100.0}, "kr_us": {}, "kr_kr": {"K": 10.0}},
          "bench_close": {"us": 100.0, "kr_kr": 20.0}}  # fmt: skip
    r = job._종이_성적(_C(앞), 7, {"A": 105.0, "VTI": 101.0, "K": 10.0, et.KR_BENCHMARK: 21.0})
    assert r["us"]["ex"] == pytest.approx(0.04) and r["kr_kr"]["ex"] == pytest.approx(-0.05)
    assert job._종이_성적(_C(None), 7, {})["skipped"].startswith("지난 실행")
