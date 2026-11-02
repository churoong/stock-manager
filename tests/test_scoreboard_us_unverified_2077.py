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


def test_미국_미검증_줄은_0_1_점검이_후보가_되지_않는다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """25.2083 (25.2077 교차검증 note) — 이름 옆엔 "미국 미검증(참고로만)" 인데 0→1 점검은 그 칸을 보지 않아, 다른 칸이
    다 통과하면 "후보" 가 찍힐 수 있었다. 미검증 줄에만 "미국 운영 일치 미검증" 칸을 모름으로 더한다(국내·검증 줄은 그대로)"""
    monkeypatch.setattr(scb, "_capacity_ok", lambda _r: True)  # 수용량 목표(사용자 결정) 전이라 늘 모름 — 이 시험만
    다_통과 = {"strategy": "core_d", "cohort": "2", "market": "US", "months": 60.0, "invested_share": 0.9,
             "avg_holdings": 10.0, "excess_cagr": 0.05, "control_lower": 60.0, "bh_q": 0.01, "dsr": 0.99,
             "half_first_cagr": 0.1, "half_second_cagr": 0.1, "walk_positive": 40, "walk_total": 49, "mdd": -0.1}  # fmt: skip
    기준 = {"strategy": "core_b", "mdd": -0.1}
    assert scb.stage01({**다_통과, "us_unverified": False}, 기준)["verdict"] == "후보"
    st = scb.stage01({**다_통과, "us_unverified": True}, 기준)
    assert st["verdict"] == "모름" and st["unknown"] == [scb.US_PARITY_LABEL]
    assert scb.stage01_note(st) == f"재료 없음: {scb.US_PARITY_LABEL}"
    # 옛 실행(칸 없음 — None)은 예전대로
    assert "us_parity" not in {c["key"] for c in scb.stage01({**다_통과, "us_unverified": None}, 기준)["checks"]}
