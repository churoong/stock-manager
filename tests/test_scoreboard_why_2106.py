"""판정판 빈 칸의 까닭이 실제 사정과 맞는다 (25.2106, 11-02 결함 회차 [참고] — 11-03 첫 판정판 전).

- 기준·무작위 대조 줄의 누적 시도: 운 판정에 들지 않아 `luck` 이 없을 뿐이다. 예전 까닭은 "운 판정 없는 실행" 이었는데,
  같은 실행의 다른 줄에는 운 판정(dsr_n)이 있었다
- 핵심·탐색 실행은 증거 없는 변형에도 `evidence = {k: None}` 을 싣는다(`jobs/core_backtest.py`). 그 꼴이 "하한 없음 —
  묶음 None개(block_bound 최소 미달)" 로 적혔다. S·T 리플레이면 "증거를 내지 않는다", 다른 변형이면 "판정 실패"
"""

from __future__ import annotations

import json
from typing import Any

from batch.jobs import core_backtest as cb
from batch.services import scoreboard as scb

_증거칸 = ("buys", "scored", "no_control", "days", "n_blocks", "mean", "se", "lower", "gate", "after_sell")


def _실행(줄: dict[str, dict[str, Any]], job: str = "backtest_explore") -> dict[str, Any]:
    return {"job_name": job, "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "years": 5, "risk_every": "week"}, "group_id": "g"},
            "results": [{"strategy": n, "market": "KR", "group_id": "g", "warnings_json": "[]",
                         "metrics_json": json.dumps({"mdd": -0.2, **mj})} for n, mj in 줄.items()]}  # fmt: skip


def test_ST_이름이_운영_변형_목록과_같다() -> None:
    assert frozenset(cb.ST_VARIANTS) == scb.ST_REPLAY


def test_기준_줄의_누적_시도는_시도_아님() -> None:
    줄 = {"core_b": {"luck": {"dsr": 0.9, "n_trials": 17, "months": 60}}, "benchmark": {}, "core_r1": {}}
    rows = {r["strategy"]: r for r in scb.rows([_실행(줄)])}
    assert rows["core_b"]["trials_cumulative"] == 17
    for k in ("benchmark", "core_r1"):
        assert rows[k]["trials_cumulative"] is None
        assert rows[k]["missing"]["trials_cumulative"] == "기준·무작위 대조 — 시도 아님"


def test_시도_줄인데_N_이_없으면_예전_까닭() -> None:
    rows = scb.rows([_실행({"core_d": {}})])
    assert rows[0]["missing"]["trials_cumulative"].startswith("params.trial_floor·luck.n_trials 둘 다 없음")


def test_빈_증거는_묶음_None개가_아니라_실제_사정() -> None:
    빈 = {"evidence": dict.fromkeys(_증거칸)}
    rows = {r["strategy"]: r for r in scb.rows([_실행({"st_s": 빈, "core_d": 빈})])}
    for k in ("control_lower", "control_p", "gate_lower"):
        assert rows["st_s"][k] is None and rows["st_s"]["missing"][k] == scb.WHY_EV_ST
        assert rows["core_d"]["missing"][k].startswith("결정 단위 증거 칸이 모두 비었다 — 판정 실패")
    assert "None개" not in json.dumps(rows["st_s"]["missing"], ensure_ascii=False)


def test_채워진_증거는_그대로() -> None:
    ev = dict.fromkeys(_증거칸) | {"n_blocks": 3, "lower": None}
    rows = scb.rows([_실행({"core_d": {"evidence": ev}})])
    assert rows[0]["missing"]["control_lower"] == "하한 없음 — 묶음 3개(block_bound 최소 미달)"
