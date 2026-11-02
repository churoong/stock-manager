"""끝 달 부분 관측의 읽기 규칙을 판정판이 자동으로 (25.2107, 25.2105 사전 등록 — funnel.md 3장 0→1).

25.2105 는 11-03 결과 전에 "끝 달을 뺀 계열로 다시 낸 DSR·걷는 창 판정이 원래와 다른 줄은 모름(경계)" 을 등록했지만,
그 규칙을 실행할 코드가 없어 11-04 에 사람이 DSR(V·N·T·왜도·첨도)을 손으로 다시 내야 했다(25.2105 교차검증 [권고]).
엔진은 그대로 두고 판정판(`scoreboard.rows`)이 끝 달이 실행한 달인 줄만 다시 판정한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.services import scoreboard as scb
from tests.test_scoreboard_cohort_dsr_2093 import _계열, _기대


def _달들(끝: str = "2026-11", n: int = 60) -> list[str]:
    y, m = int(끝[:4]), int(끝[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def _월실행(계열: dict[str, list[list[Any]]], started_at: str = "2026-11-03T17:20:00+00:00") -> dict[str, Any]:
    좋은 = {"cagr": 0.1, "mdd": -0.2, "invested_share": 0.9, "avg_holdings": 10.0}
    결과 = [{"strategy": n, "market": "KR", "group_id": "g", "warnings_json": "[]", "excess_cagr": 0.02,
             "metrics_json": json.dumps({**좋은, "monthly_excess": xs,
                                         "luck": {"dsr": 0.99, "n_trials": 17, "months": len(xs), "luck_pct": 1.0}})}
            for n, xs in 계열.items()]  # fmt: skip
    결과.append({"strategy": "benchmark", "market": "KR", "group_id": "g", "warnings_json": "[]",
                 "metrics_json": json.dumps(좋은)})  # fmt: skip
    return {"job_name": scb.JOB_MONTHLY, "started_at": started_at,
            "step_log": {"params": {"default": True, "sha": "x"}, "group_id": "g"}, "results": 결과}  # fmt: skip


def _앞_두_창_음(끝: str = "2026-11") -> list[list[Any]]:
    """60개월 — 원래 창 다섯(오래된 둘이 음)은 3/5(걸림), 끝 달을 뺀 창 넷(가장 오래된 하나만 음)은 3/4(통과)"""
    return [[m, -0.01 if m <= "2023-11" else 0.01] for m in _달들(끝)]


def test_끝_달을_빼면_판정이_바뀌는_줄은_모름_경계() -> None:
    (c,) = [r for r in scb.rows([_월실행({"composite": _앞_두_창_음()})]) if r["strategy"] == "composite"]
    assert c["cohort"] == "1" and (c["walk_positive"], c["walk_total"]) == (3, 5)  # 원래 칸 값은 그대로
    assert c["stage01_verdict"] == "모름"
    assert c["stage01_note"].startswith("경계 — 끝 달 2026-11(부분 달)을 빼면 모름(25.2105) · 걸림: 걷는 창 3/5")
    assert c["stage01"]["partial_month"] == "모름"


def test_끝_달을_빼도_같으면_그대로() -> None:
    (c,) = [r for r in scb.rows([_월실행({"composite": [[m, 0.01] for m in _달들()]})]) if r["strategy"] == "composite"]
    assert c["stage01_verdict"] == "모름" and not c["stage01_note"].startswith("경계")
    assert "partial_month" not in c["stage01"]


def test_끝_달이_실행한_달이_아니면_다시_내지_않는다() -> None:
    """엔진을 고친 뒤(끝 달 = 앞 달) — 같은 계열이라도 실행한 달과 다르면 경계 갈래에 들지 않는다"""
    run = _월실행({"composite": _앞_두_창_음("2026-10")})
    (c,) = [r for r in scb.rows([run]) if r["strategy"] == "composite"]
    assert c["stage01_verdict"] == "탈락" and c["stage01_note"].startswith("걸림: 걷는 창")


def test_코호트_V_는_모든_줄의_끝_달을_뺀_계열로() -> None:
    """같은 코호트 시도 줄 셋 — 다시 낸 DSR 은 끝 달을 뺀 계열 셋의 V 로(`luck` 식 그대로). 실행 V(`dsr`)는 뺀다"""
    달 = _달들()
    series = {k: [[m, v] for m, (_d, v) in zip(달, _계열(i, 60), strict=True)]
              for i, k in ((1, "composite"), (2, "value"), (3, "momentum"))}  # fmt: skip
    run = _월실행(series)
    줄들 = [scb._row(run, r, {}) for r in run["results"]]
    묶음 = {("KR", "1"): [r for r in 줄들 if r["is_trial"]]}
    scb._cohort_dsr(줄들, 묶음)
    뺀 = scb._partial_month_rows(줄들, 묶음)
    assert {r["strategy"] for r in 줄들 if id(r) in 뺀} == set(series)
    기대 = _기대({k: xs[:-1] for k, xs in series.items()}, 17)
    for r in 줄들:
        if id(r) in 뺀:
            c = 뺀[id(r)]
            assert c["dsr_cohort"] == pytest.approx(기대[r["strategy"]]) and c["dsr"] is None
            assert (c["walk_positive"], c["walk_total"]) != (None, None) and c["walk_total"] == 4
            assert r["walk_total"] == 5  # 원래 줄은 건드리지 않는다
