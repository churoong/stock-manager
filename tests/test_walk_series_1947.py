"""25.1947 (전략 깔때기 2.2 판정판 걷는 창) — 월 초과 계열을 결과 행에 남기고 판정판이 걷는 창을 낸다.

- `luck.excess_months` 는 `luck.excess_series` 와 같은 값에 달 이름을 붙인다(반올림 없음 — 운 판정과 같은 계열)
- `scoreboard.rows` 는 `metrics_json.monthly_excess` 로 `walk_windows` 를 내 `walk`·`walk_positive`·`walk_total` 에 싣는다.
  없으면(25.1947 전 실행) `WHY_WALK`, 기준 전략은 "기준 전략 자체", 모양이 틀리면 읽지 못함 — 빈 칸은 모두 까닭이 있다
- 실행 끝까지의 배선(핵심 H·월 실행이 계열을 싣고 줄이 그것으로 난다)은 tests/test_scoreboard_wiring_1940.py
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.services import luck
from batch.services import scoreboard as scb


def test_달_이름_붙은_초과는_운_판정_계열과_같다() -> None:
    전략 = {"2024-01": 0.02, "2024-02": -0.01, "2024-03": 0.005, "2024-05": 0.01}
    기준 = {"2024-01": 0.01, "2024-02": 0.0, "2024-03": 0.005, "2024-04": 0.02}
    달 = luck.excess_months(전략, 기준)
    assert [m for m, _v in 달] == ["2024-01", "2024-02", "2024-03"]  # 둘 다 있는 달만, 달 순
    assert [v for _m, v in 달] == luck.excess_series(전략, 기준)
    assert 달[0][1] == pytest.approx(0.01) and 달[2][1] == 0.0


def _줄(mj: dict[str, Any], strategy: str = "core_h") -> dict[str, Any]:
    run = {"job_name": "backtest_core", "started_at": "2026-11-04T00:00:00+00:00",
           "step_log": {"params": {"sha": "x"}, "group_id": "g"},
           "results": [{"strategy": strategy, "market": "KR", "group_id": "g", "metrics_json": json.dumps(mj),
                        "warnings_json": "[]"}]}  # fmt: skip
    return scb.rows([run])[0]


def test_계열이_있으면_걷는_창을_낸다() -> None:
    # 30달 — 가장 이른 6달은 버리고 12달 창 둘. 앞 창 합 +0.12, 뒤 창 합 −0.012
    계열 = [[f"{2022 + (k // 12)}-{k % 12 + 1:02d}", 0.01 if k < 18 else -0.001] for k in range(30)]
    r = _줄({"monthly_excess": 계열})
    assert r["walk"] == scb.walk_windows(계열)
    assert (r["walk_positive"], r["walk_total"]) == (1, 2) and r["walk"]["dropped"] == 6
    assert not {"walk", "walk_positive", "walk_total"} & set(r["missing"])


@pytest.mark.parametrize(("mj", "strategy", "까닭"), [
    ({}, "core_h", scb.WHY_WALK),
    ({"monthly_excess": [["2024-01", 0.01]]}, "benchmark", "기준 전략 자체 — 초과가 0"),
    ({"monthly_excess": [["2024-01", "x"]]}, "core_h", "metrics_json.monthly_excess 를 읽지 못함"),
    ({"monthly_excess": [["2024-01"]]}, "core_h", "metrics_json.monthly_excess 를 읽지 못함"),
    ({"monthly_excess": {"2024-01": 0.01}}, "core_h", scb.WHY_WALK),
    # 25.1952 — 달 이름이 YYYY-MM 이 아니면 판정판 전체가 깨지던 것을 칸 하나로, NaN·무한은 조용히 "> 0 아님" 이던 것을
    ({"monthly_excess": [["x", 0.01]]}, "core_h", "metrics_json.monthly_excess 를 읽지 못함"),
    ({"monthly_excess": [[202401, 0.01]]}, "core_h", "metrics_json.monthly_excess 를 읽지 못함"),
    ({"monthly_excess": [["2024-13", 0.01]]}, "core_h", "metrics_json.monthly_excess 를 읽지 못함"),
    ({"monthly_excess": [["2024-01", float("nan")]]}, "core_h", "metrics_json.monthly_excess 를 읽지 못함"),
    ({"monthly_excess": [["2024-01", float("inf")]]}, "core_h", "metrics_json.monthly_excess 를 읽지 못함"),
])
def test_계열이_없거나_틀리면_까닭과_함께_빈다(mj: dict[str, Any], strategy: str, 까닭: str) -> None:
    r = _줄(mj, strategy)
    for c in ("walk", "walk_positive", "walk_total"):
        assert r[c] is None and r["missing"][c] == 까닭, c


def test_12달_미만은_창_0개() -> None:
    r = _줄({"monthly_excess": [[f"2024-{m:02d}", 0.01] for m in range(1, 12)]})
    assert (r["walk_positive"], r["walk_total"]) == (0, 0) and "walk_total" not in r["missing"]
