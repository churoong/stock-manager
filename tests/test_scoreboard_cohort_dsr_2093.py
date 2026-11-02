"""판정판 코호트 V DSR (25.2093, funnel.md 2.2 "DSR(N = 누적, V = 코호트)").

예전 판정판 `dsr` 칸은 그 실행의 곡선들로 낸 V(`luck.judge`)라 등록 정의(V = 코호트)와 달랐다. 같은 (시장, 코호트) 시도 줄들의
월 초과 계열(`metrics_json.monthly_excess`)로 월 초과 샤프를 내고 그 분산을 V 로 `dsr_cohort` 를 싣는다. 0→1 점검은
`dsr`·`dsr_cohort` 가운데 낮은 쪽을 본다(보수적).
"""

from __future__ import annotations

import json
import math
import statistics
from typing import Any

import pytest

from batch.services import luck
from batch.services import scoreboard as scb


def _계열(seed: int, n: int = 40) -> list[list[Any]]:
    """결정적 월 초과 계열 — 종목마다 평균·변동이 다르게(샤프가 갈리게)"""
    out = []
    for k in range(n):
        y, m = 2021 + (k // 12), k % 12 + 1
        out.append([f"{y}-{m:02d}", 0.002 * seed + 0.01 * math.sin(k * (seed + 1) * 0.7) + 0.003 * ((k * seed) % 5 - 2)])
    return out


def _실행(줄: dict[str, dict[str, Any]], dsr_n: int = 6) -> dict[str, Any]:
    return {"job_name": "backtest_explore", "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "trial_floor": dsr_n, "years": 5, "risk_every": "week"}, "group_id": "g"},
            "results": [{"strategy": n, "market": "KR", "group_id": "g", "warnings_json": "[]",
                         "metrics_json": json.dumps({"mdd": -0.2, **mj})} for n, mj in 줄.items()]}  # fmt: skip


def _기대(series: dict[str, list[list[Any]]], n: int) -> dict[str, float]:
    """`luck` 의 식을 그대로 — 코호트 시도 줄들의 월 초과 샤프 분산이 V"""
    m = {k: luck.moments([float(v) for _d, v in xs]) for k, xs in series.items()}
    v = statistics.pvariance([x[0] for x in m.values() if x])
    bar = luck.expected_max_sr(n, v)
    return {k: round(luck.psr(sr, bar, len(series[k]), sk, ku), 4) for k, (sr, sk, ku) in m.items()}


def test_코호트_V_로_DSR_을_다시_낸다() -> None:
    series = {"core_d": _계열(1), "core_e": _계열(2), "core_a": _계열(3)}
    줄 = {k: {"monthly_excess": xs, "luck": {"dsr": 0.99, "n_trials": 6, "months": 40, "luck_pct": 1.0}}
         for k, xs in series.items()}  # fmt: skip
    rows = {r["strategy"]: r for r in scb.rows([_실행(줄)])}
    기대 = _기대(series, 6)
    for k in series:
        assert rows[k]["dsr_cohort"] == pytest.approx(기대[k]), (k, rows[k]["dsr_cohort"], 기대[k])
        assert "_excess" not in rows[k] and "_excess_why" not in rows[k]  # 내부 재료는 내보내지 않는다
    # 실행 V(저장된 `luck.dsr` 0.99)와 다르다 — 등록 정의 칸이 따로 있다
    assert any(rows[k]["dsr_cohort"] != rows[k]["dsr"] for k in series)
    실림 = {r["strategy"]: r for r in scb.step_log_rows(list(rows.values()))}
    assert 실림["core_d"]["dsr_cohort"] == rows["core_d"]["dsr_cohort"]


def test_계열_있는_시도_줄이_하나면_모름() -> None:
    줄 = {"core_d": {"monthly_excess": _계열(1), "luck": {"dsr": 0.99, "n_trials": 6, "months": 40}},
         "core_e": {"luck": {"dsr": 0.5, "n_trials": 6, "months": 40}}}  # 계열 없음(25.1947 전 실행)
    rows = {r["strategy"]: r for r in scb.rows([_실행(줄)])}
    for k in ("core_d", "core_e"):
        assert rows[k]["dsr_cohort"] is None
        assert rows[k]["missing"]["dsr_cohort"] == scb.WHY_DSR_COHORT_FEW


def test_0_1_점검은_두_DSR_가운데_낮은_쪽() -> None:
    기준 = {"strategy": "core_b", "mdd": -0.1}
    줄 = {"strategy": "core_d", "cohort": "2", "market": "KR", "dsr": 0.95, "dsr_cohort": 0.80}

    def dsr칸(row: dict[str, Any]) -> dict[str, Any]:
        return next(c for c in scb.stage01(row, 기준)["checks"] if c["key"] == "dsr")

    assert dsr칸(줄)["now"] == 0.80 and dsr칸(줄)["ok"] is False
    assert dsr칸({**줄, "dsr_cohort": 0.97})["now"] == 0.95 and dsr칸({**줄, "dsr_cohort": 0.97})["ok"] is True
    # 코호트 V 를 못 낸 줄은 실행 V 만(예전과 같다)
    assert dsr칸({**줄, "dsr_cohort": None})["now"] == 0.95
    assert dsr칸({**줄, "dsr": None, "dsr_cohort": None})["ok"] is None


def test_시도_아닌_줄은_코호트_V_밖() -> None:
    줄 = {"core_d": {"monthly_excess": _계열(1), "luck": {"dsr": 0.9, "n_trials": 6, "months": 40}},
         "core_e": {"monthly_excess": _계열(2), "luck": {"dsr": 0.9, "n_trials": 6, "months": 40}},
         "core_r1": {"monthly_excess": _계열(5), "luck": {"dsr": 0.9, "n_trials": 6, "months": 40}}}  # fmt: skip
    rows = {r["strategy"]: r for r in scb.rows([_실행(줄)])}
    assert rows["core_r1"]["dsr_cohort"] is None
    assert rows["core_r1"]["missing"]["dsr_cohort"] == scb.WHY_DSR_COHORT_OUT
    # R1 계열은 V 에 들지 않는다 — 시도 둘의 V 로 낸 값과 같다
    기대 = _기대({"core_d": _계열(1), "core_e": _계열(2)}, 6)
    assert rows["core_d"]["dsr_cohort"] == pytest.approx(기대["core_d"])


def test_짧은_계열·계열_없음·N_없음의_까닭() -> None:
    """25.2096 (25.2093 교차검증 [권고]) — 시험 밖이던 갈래 셋. 20개월 줄은 V 에는 들고(`luck.judge` 와 같게) 판정은 모름,
    계열 없는 줄은 그 까닭, 누적 시도 N 이 없는 줄은 실행 줄 수로 물러나지 않고 모름(등록 정의 N = 누적)"""
    짧은 = _계열(4, n=20)
    줄 = {"core_d": {"monthly_excess": _계열(1), "luck": {"dsr": 0.9, "n_trials": 6, "months": 40}},
         "core_e": {"monthly_excess": _계열(2), "luck": {"dsr": 0.9, "n_trials": 6, "months": 40}},
         "core_a": {"monthly_excess": 짧은, "luck": {"dsr": 0.9, "n_trials": 6, "months": 20}},
         "core_c": {"luck": {"dsr": 0.9, "n_trials": 6, "months": 40}},  # 계열 없음(25.1947 전)
         "core_g2": {"monthly_excess": _계열(3)}}  # luck 없음 — N 모름
    rows = {r["strategy"]: r for r in scb.rows([_실행(줄)])}
    assert rows["core_a"]["dsr_cohort"] is None
    assert rows["core_a"]["missing"]["dsr_cohort"] == f"월 20개 < {luck.MIN_MONTHS}"
    assert rows["core_c"]["dsr_cohort"] is None
    assert rows["core_c"]["missing"]["dsr_cohort"].startswith("이 줄 계열 없음 — ")
    assert rows["core_g2"]["dsr_cohort"] is None
    assert rows["core_g2"]["missing"]["dsr_cohort"] == "누적 시도 N 없음(luck.n_trials)"
    # V 에는 계열 있는 시도 줄 넷(짧은 줄·N 없는 줄 포함)이 든다 — `luck.judge` 가 MIN_MONTHS 미만 계열도 V 에 넣는 것과 같게
    기대 = _기대({"core_d": _계열(1), "core_e": _계열(2), "core_a": 짧은, "core_g2": _계열(3)}, 6)
    assert rows["core_d"]["dsr_cohort"] == pytest.approx(기대["core_d"])
