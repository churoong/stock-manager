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


def _실행(줄: dict[str, dict[str, Any]], job: str = "backtest_explore", **params: Any) -> dict[str, Any]:
    return {"job_name": job, "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "years": 5, "risk_every": "week", **params}, "group_id": "g"},
            "results": [{"strategy": n, "market": "KR", "group_id": "g", "warnings_json": "[]",
                         "metrics_json": json.dumps({"mdd": -0.2, **mj})} for n, mj in 줄.items()]}  # fmt: skip


def test_ST_이름이_운영_변형_목록과_같다() -> None:
    assert frozenset(cb.ST_VARIANTS) == scb.ST_REPLAY


def test_월_실행의_기준_줄은_시도_아님() -> None:
    """실제로 바뀌는 곳은 월 실행(`backtest` — trial_floor 를 싣지 않고 기준 줄에 luck 이 없다)의 benchmark 줄 하나다"""
    줄 = {"composite": {"luck": {"dsr": 0.9, "n_trials": 17, "months": 60}}, "benchmark": {}}
    rows = {r["strategy"]: r for r in scb.rows([_실행(줄, job=scb.JOB_MONTHLY)])}
    assert rows["composite"]["trials_cumulative"] == 17
    assert rows["benchmark"]["trials_cumulative"] is None
    assert rows["benchmark"]["missing"]["trials_cumulative"] == "기준·무작위 대조 — 시도 아님"


def test_핵심_실행의_대조_줄은_floor_를_받는다() -> None:
    """25.2106 교차검증 [권고] — 핵심·탐색 실행은 늘 `params.trial_floor` 를 싣고(`jobs/core_backtest.py`) R1·R2 도 운
    판정을 받는다. 그래서 R1·R2 줄의 누적 시도는 floor(그 줄 DSR 의 N) 그대로이고 '시도 아님' 갈래에 들지 않는다"""
    줄 = {"core_h": {"luck": {"dsr": 0.9, "n_trials": 17, "months": 60}},
         "core_r1": {"luck": {"dsr": 0.5, "n_trials": 17, "months": 60}}}  # fmt: skip
    rows = {r["strategy"]: r for r in scb.rows([_실행(줄, job=scb.JOB_CORE, trial_floor=17)])}
    assert rows["core_r1"]["trials_cumulative"] == 17 and "trials_cumulative" not in rows["core_r1"]["missing"]
    assert rows["core_r1"]["stage01_verdict"] is None  # 0→1 점검은 시도 줄만


def test_시도_줄인데_N_이_없으면_예전_까닭() -> None:
    rows = scb.rows([_실행({"core_d": {}})])
    assert rows[0]["missing"]["trials_cumulative"].startswith("params.trial_floor·luck.n_trials 둘 다 없음")


def test_빈_증거는_묶음_None개가_아니라_실제_사정() -> None:
    빈 = {"evidence": dict.fromkeys(_증거칸)}
    rows = {r["strategy"]: r for r in scb.rows([_실행({"st_s": 빈, "core_d": 빈})])}
    for k in ("control_lower", "control_p", "gate_lower"):
        assert rows["st_s"][k] is None and rows["st_s"]["missing"][k] == scb.WHY_EV_ST
        assert rows["core_d"]["missing"][k] == scb.WHY_EV_FAILED
    assert "None개" not in json.dumps(rows["st_s"]["missing"], ensure_ascii=False)
    # 매수·묶음 칸도 같은 까닭(25.2107) — 한 줄에 두 까닭이 함께 찍히지 않게
    for k in ("buys", "control_blocks", "gate_blocks", "sells_scored"):
        assert rows["st_s"]["missing"][k] == scb.WHY_EV_ST and rows["core_d"]["missing"][k] == scb.WHY_EV_FAILED


def test_빈_dict_증거는_옛_기록_까닭() -> None:
    rows = scb.rows([_실행({"core_d": {"evidence": {}}})])
    assert rows[0]["missing"]["control_lower"].startswith("metrics_json.evidence 없음")
    assert rows[0]["missing"]["buys"] == "결정 단위 증거 없음(월 백테스트 또는 판정 실패)"


def test_채워진_증거는_그대로() -> None:
    ev = dict.fromkeys(_증거칸) | {"n_blocks": 3, "lower": None}
    rows = scb.rows([_실행({"core_d": {"evidence": ev}})])
    assert rows[0]["missing"]["control_lower"] == "하한 없음 — 묶음 3개(block_bound 최소 미달)"
