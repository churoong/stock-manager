"""S·T 리플레이 실행기 (깔때기 ⑦-6, 25.2044) — `run_core(explore=True, st=True)` 가 탐색 실행에 st_s·st_t 를 더한다.

- 탐색 코호트에만(핵심 판정·코호트 1 시도 수는 그대로), 시도 수 하한 = 코호트 1 + 탐색 변형 + 둘
- 실행 행 `strategy` 는 st_s·st_t, 계좌 칸 S·T, 경고 `WARN_ST`
- 실행 기록 `st` 에 시가 덮개·계좌별 체결 합
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import backtest as jb
from batch.jobs import core_backtest as cj
from batch.services import core_strategy as cs


def test_탐색_실행에_S_T_리플레이를_더한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True, st=True) == 0
    행 = mem.execute("SELECT strategy, top_n, warnings_json, metrics_json FROM backtest_runs ORDER BY id").dicts()
    assert [r["strategy"] for r in 행] == [*cs.EXPLORE_VARIANTS, *cj.ST_VARIANTS]
    st = {r["strategy"]: r for r in 행 if r["strategy"] in cj.ST_VARIANTS}
    assert all(r["top_n"] == 10 for r in st.values())
    assert {json.loads(r["metrics_json"])["account"] for r in st.values()} == {"S", "T"}
    assert all(cj.WARN_ST in json.loads(r["warnings_json"]) for r in 행)
    assert all(json.loads(r["metrics_json"])["luck"]["n_trials"] >= cj.explore_trial_floor(True) for r in 행)
    assert cj.explore_trial_floor(True) == jb.REGISTERED_TRIALS + len(cs.EXPLORE_VARIANTS) + 2
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?",
                                 [cj.EXPLORE_JOB_NAME]).scalar())  # fmt: skip
    assert 로그["params"]["st"] is True and 로그["params"]["trial_floor"] == cj.explore_trial_floor(True)
    assert set(로그["st"]["accounts"]) == {"S", "T"} and "open_coverage" in 로그["st"]
    # 결정 단위 증거는 핵심·탐색 변형만(S·T 는 DayLog 가 아니다)
    assert set(로그["evidence"]) <= set(cs.EXPLORE_VARIANTS)


def test_핵심_실행에는_붙일_수_없다() -> None:
    with pytest.raises(ValueError, match="탐색 실행"):
        cj.run_core("KR", st=True)
