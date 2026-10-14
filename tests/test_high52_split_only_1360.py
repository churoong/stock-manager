"""미국 52주 고점 근접도 — 배당 반영 Adj Close 대 분할만 반영한 종가, 기록만 (docs/infra.md 25.1360, 23회차 P3 1단계)."""

from __future__ import annotations

import pytest

from batch.jobs import scores as sj
from batch.services import scoring as sc


def _행(sid: int, n: int) -> list[dict]:
    return [{"stock_id": sid, "date": f"d{i:04d}", "close": 100.0 + i, "adj_close": 90.0 + i, "value": 1.0,
             "volume": 1, "change_pct": None} for i in range(n)]  # fmt: skip


def test_원_종가를_같은_자리로_담는다() -> None:
    raw: dict[int, list[float]] = {}
    out = sj.series_from_rows(_행(1, 300), 260, None, raw)
    assert len(raw[1]) == len(out[1][1]) == 260
    assert raw[1][-1] == 399.0 and out[1][1][-1] == 389.0


def test_진단은_차이를_세고_점수는_그대로() -> None:
    # 과거 고점이 원 종가로 105(배당 전), 지금 100 — 수정주가에선 지금이 고점(1.0), 분할만 반영에선 100/105
    n = sc.HIGH_52W_DAYS
    raw = {1: [105.0] * 10 + [100.0] * (n - 10)}
    series = {1: ([f"d{i}" for i in range(n)], [100.0] * n, [1.0] * n)}
    기록 = sj.high52_split_only_log(series, raw, set())
    assert 기록["n"] == 1 and 기록["median_delta"] == pytest.approx(1 - 100 / 105, abs=1e-4)
    assert 기록["share_over_2pct"] == 1.0
    # 분할 재수집 대기는 뺀다
    assert sj.high52_split_only_log(series, raw, {1}) is None
    # 점수 계열(수정주가) 근접도는 그대로 1.0
    assert sc.high_52w_proximity(series[1][1]) == 1.0
