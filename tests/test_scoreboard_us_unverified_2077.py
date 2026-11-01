"""판정판 줄의 미국 미검증 칸 (25.2077, 25.2066·2068 교차검증 — handoff "웹 판정판 줄의 미국 미검증 꼬리표").

웹 /backtest 는 묶음 경고를 한 띠로 합쳐 보여, 미국 R1·R2(또는 지정가 판 회차 변형) 줄에만 붙은 미검증 단서가 어느 줄의
것인지 화면에서 보이지 않았다. 판정판 줄(`step_log.scoreboard`)에 `us_unverified` 칸을 싣고 웹이 그 줄 이름 옆에 꼬리표를 단다.
"""

from __future__ import annotations

import json

from batch.jobs import core_backtest as cj
from batch.services import scoreboard as scb


def _실행(경고: dict[str, list[str]]) -> dict:
    return {"job_name": "backtest_core", "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "trial_floor": 21, "years": 5, "risk_every": "week"}, "group_id": "gUS"},
            "results": [{"strategy": n, "market": "US", "group_id": "gUS", "warnings_json": json.dumps(w),
                         "metrics_json": json.dumps({"mdd": -0.2})} for n, w in 경고.items()]}  # fmt: skip


def test_단서_머리는_실행기_문구와_같다() -> None:
    for 글 in (cj.WARN_US_UNVERIFIED, cj.WARN_US_UNVERIFIED_OTHER, cj.WARN_US_LIMIT_UNVERIFIED):
        assert 글.startswith(scb.US_UNVERIFIED_PREFIXES), 글
    # 옛 자리 단서(시장 무관)·다른 경고는 미검증 칸을 켜지 않는다
    assert not cj.WARN_R_OLD_SLOT.startswith(scb.US_UNVERIFIED_PREFIXES)
    assert not cj.WARN_SPLIT_PENDING.startswith(scb.US_UNVERIFIED_PREFIXES)


def test_줄마다_미국_미검증_칸() -> None:
    줄 = {r["strategy"]: r for r in scb.rows([_실행({
        "core_b": [], "core_r1": [cj.WARN_US_UNVERIFIED, cj.WARN_R_OLD_SLOT], "core_h": [cj.WARN_US_LIMIT_UNVERIFIED],
        "core_g": [cj.WARN_R_OLD_SLOT]})])}  # fmt: skip
    assert {k: v["us_unverified"] for k, v in 줄.items()} == {"core_b": False, "core_r1": True, "core_h": True,
                                                              "core_g": False}  # fmt: skip
    실림 = {r["strategy"]: r for r in scb.step_log_rows(list(줄.values()))}
    assert 실림["core_r1"]["us_unverified"] is True and 실림["core_b"]["us_unverified"] is False
