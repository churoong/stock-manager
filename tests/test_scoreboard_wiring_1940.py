"""판정판 연결 — 실행이 끝나면 그 실행의 판정판 줄을 `step_log["scoreboard"]` 에 싣는다 (docs/funnel.md 2.2, 25.1940).

- 핵심 H(`backtest_core`)·탐색(`backtest_explore`)·월 백테스트(`backtest`) 실행을 메모리 DB 로 끝까지 돌린다
- 실린 줄 = DB 에 남은 실행 기록(batch_runs + backtest_runs)을 `scoreboard.rows` 에 넣고 `step_log_rows` 로 줄인 것과 같다
  (실행이 Turso 를 다시 읽지 않고 메모리의 행으로 낸 값이 저장된 행으로 낸 값과 같다)
- 판정판이 깨져도 실행은 성공으로 닫히고 `scoreboard_error` 에 까닭이 남는다
- 웹 `lib/scoreboard.ts` 의 칸 목록이 배치 `STEP_LOG_COLS` 와, 작업 목록이 배치 작업 이름 상수와 같다
- 월 기본 파라미터 실행이 2026-11-03 부터 코호트 1 이 된다 — 실행 기록의 params 가 판정판까지 간다(25.1941)
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from batch.core import db
from batch.jobs import backtest as jb
from batch.jobs import core_backtest as cj
from batch.services import core_strategy as cs
from batch.services import scoreboard as scb
from tests.test_core_backtest_job_1534 import _db, 오늘

뿌리 = Path(__file__).resolve().parent.parent


def _기록(mem: Any, job: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """그 작업의 마지막 실행 기록(step_log 푼 것)과 같은 묶음의 결과 행들 — DB 에서"""
    run = mem.execute("SELECT job_name, started_at, step_log, status FROM batch_runs WHERE job_name = ?"
                      " ORDER BY id DESC LIMIT 1", [job]).dicts()[0]  # fmt: skip
    run["step_log"] = json.loads(run["step_log"])
    rows = mem.execute("SELECT * FROM backtest_runs WHERE group_id = ? ORDER BY id", [run["step_log"]["group_id"]]).dicts()
    return run, rows


def _다시(run: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """저장된 행으로 판정판을 다시 낸다 — 실행이 실은 값의 대조 기준"""
    log = {k: v for k, v in run["step_log"].items() if k not in ("scoreboard", "scoreboard_error")}
    record = {"job_name": run["job_name"], "started_at": run["started_at"], "step_log": log, "results": rows}
    return scb.step_log_rows(scb.rows([record]))


@pytest.fixture(scope="module")
def 핵심() -> Iterator[Any]:
    mp = pytest.MonkeyPatch()
    try:
        mem = _db(mp)
        assert cj.run_core("KR", years=1, today=오늘, force=True) == 0
        yield mem
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def 탐색() -> Iterator[Any]:
    mp = pytest.MonkeyPatch()
    try:
        mem = _db(mp)
        assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True, variants=("core_d", "core_e")) == 0
        yield mem
    finally:
        mp.undo()


def test_핵심_실행이_판정판_줄을_싣는다(핵심: Any) -> None:
    run, rows = _기록(핵심, cj.JOB_NAME)
    줄들 = run["step_log"]["scoreboard"]
    assert "scoreboard_error" not in run["step_log"]
    assert sorted(r["strategy"] for r in 줄들) == sorted(cs.VARIANTS) == sorted(r["strategy"] for r in rows)
    # 저장된 행으로 다시 낸 것과 같다(JSON 왕복 뒤 값까지)
    assert 줄들 == _다시(run, rows)
    by = {r["strategy"]: r for r in 줄들}
    for 줄 in 줄들:
        assert set(줄) == set(scb.STEP_LOG_COLS) | {"missing"}
        for c in scb.STEP_LOG_COLS:
            # 빈 칸은 모두 까닭이 있고, 값이 있는 칸은 까닭이 없다
            assert (줄[c] is None) == (c in 줄["missing"]), (줄["strategy"], c)
        assert all(0 < len(w) <= scb.WHY_MAX for w in 줄["missing"].values())
        assert 줄["market"] == "KR" and 줄["trials_cumulative"] == jb.REGISTERED_TRIALS
    # 코호트 — 핵심 H 는 1b, 무작위 대조는 시도 아님(까닭)
    assert by["core_h"]["cohort"] == "1b" and by["core_b"]["cohort"] == "1b"
    assert by["core_r1"]["cohort"] is None and "시도 아님" in by["core_r1"]["missing"]["cohort"]
    # 저장된 값 그대로 — 행의 metrics_json·열과 같다
    for r in rows:
        mj, 줄 = json.loads(r["metrics_json"]), by[r["strategy"]]
        assert 줄["cagr"] == mj["cagr"] and 줄["mdd"] == mj["mdd"] and 줄["excess_cagr"] == r["excess_cagr"]
        assert 줄["rebalances"] == r["rebalances"] and 줄["buys"] == mj["evidence"]["buys"]
        assert 줄["control_lower"] == mj["evidence"]["lower"] and 줄["gate_lower"] == mj["evidence"]["gate"]["lower"]
        assert 줄["control_blocks"] == mj["evidence"]["n_blocks"] and 줄["gate_blocks"] == mj["evidence"]["gate"]["n_blocks"]
        assert 줄["half_first_cagr"] == mj["split_half"]["first"]["cagr"]
        assert 줄["half_second_cagr"] == mj["split_half"]["second"]["cagr"]
        assert 줄["sells_scored"] == mj["evidence"]["after_sell"]["sells"]
        # 1년이라 운 판정은 "판정 안 함" — DSR 은 비고 그 문장이 까닭
        assert 줄["dsr"] is None and "판정 안 함" in 줄["missing"]["dsr"] and 줄["dsr_n"] == jb.REGISTERED_TRIALS
    # 헛돌지 않게 — 값이 찬 칸이 있다(합성 1년은 CAGR 창이 짧아 비고 까닭이 남는다)
    assert any(by[v]["buys"] for v in by) and all(by[v]["half_first_cagr"] is not None for v in by)
    assert all(by[v]["months"] and by[v]["rebalances"] for v in by)


def test_판정판은_작다(핵심: Any) -> None:
    """step_log 크기 — 줄마다 웹이 보이는 칸과 그 까닭만. 걷는 창·수용량처럼 늘 비는 칸은 싣지 않는다"""
    run, _rows = _기록(핵심, cj.JOB_NAME)
    줄들 = run["step_log"]["scoreboard"]
    크기 = len(json.dumps(줄들, ensure_ascii=False).encode())
    assert 크기 < 1500 * len(줄들), 크기
    assert not {"walk", "capacity", "warnings", "sha", "job_name", "group_id"} & set(줄들[0])


def test_탐색_실행은_코호트_2_와_탐색_하한(탐색: Any) -> None:
    run, rows = _기록(탐색, cj.EXPLORE_JOB_NAME)
    줄들 = run["step_log"]["scoreboard"]
    assert 줄들 == _다시(run, rows)
    assert sorted(r["strategy"] for r in 줄들) == ["core_d", "core_e"]
    for 줄 in 줄들:
        assert 줄["cohort"] == "2" and 줄["trials_cumulative"] == cj.explore_trial_floor()


def test_판정판이_깨져도_실행은_성공이고_까닭을_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db(monkeypatch)

    def 망(*_a: object, **_k: object) -> None:
        raise RuntimeError("판정판 망")

    monkeypatch.setattr(scb, "rows", 망)
    assert cj.run_core("KR", years=1, today=오늘, force=True, variants=("core_b",)) == 0
    run, rows = _기록(mem, cj.JOB_NAME)
    assert run["status"] == "success" and [r["strategy"] for r in rows] == ["core_b"]
    assert "scoreboard" not in run["step_log"]
    assert run["step_log"]["scoreboard_error"] == "RuntimeError: 판정판 망"
    # 다른 기록은 그대로 실린다
    assert run["step_log"]["verdict"] and run["step_log"]["evidence"]


def test_월_백테스트도_판정판_줄을_싣는다() -> None:
    """월 실행(`backtest`) — 비용 0 사본은 줄이 아니라 composite 의 짝. 결정 단위·반 나눈 기간은 까닭과 함께 빈다"""
    from tests.test_backtest_run_harness import _준비

    with pytest.MonkeyPatch.context() as mp:
        client = _준비(mp)
        assert jb.run("KR", years=2, top_n=10) == 0
        run, rows = _기록(client, jb.JOB_NAME)
    줄들 = run["step_log"]["scoreboard"]
    assert 줄들 == _다시(run, rows)
    assert sorted(r["strategy"] for r in 줄들) == sorted(r["strategy"] for r in rows if r["strategy"] != "composite_no_cost")
    by = {r["strategy"]: r for r in 줄들}
    # 2년은 기본 파라미터가 아니라 코호트 밖 — 까닭이 남는다
    assert by["composite"]["cohort"] is None and "기본 파라미터" in by["composite"]["missing"]["cohort"]
    assert by["composite"]["gate_lower"] is None and "결정 원장" in by["composite"]["missing"]["gate_lower"]
    assert by["composite"]["half_first_cagr"] is None and "반 나눈" in by["composite"]["missing"]["half_first_cagr"]
    mj = json.loads(next(r for r in rows if r["strategy"] == "composite")["metrics_json"])
    assert by["composite"]["cagr"] == mj["cagr"]


def test_월_기본_실행은_사전_등록일부터_코호트_1() -> None:
    """월 실행(`backtest`)을 기본 파라미터(인자 없이 — `params.default` 참)로 2026-11-03 에 돌리면 composite 가 코호트 1 (25.1941).

    `scoreboard_log` 에 실행 기록의 params 가 가지 않으면(빈 step_log) 기본 실행인지 몰라 코호트 밖이 된다 — 그것을 잡는다"""
    from tests.test_backtest_run_harness import _준비

    with pytest.MonkeyPatch.context() as mp:
        client = _준비(mp)
        mp.setattr(db, "now_iso", lambda: "2026-11-03T17:11:00+00:00")
        assert jb.run("KR") == 0
        run, rows = _기록(client, jb.JOB_NAME)
    assert run["step_log"]["params"]["default"] is True and run["started_at"] == "2026-11-03T17:11:00+00:00"
    줄들 = run["step_log"]["scoreboard"]
    assert 줄들 == _다시(run, rows)
    by = {r["strategy"]: r for r in 줄들}
    assert by["composite"]["cohort"] == "1" and "cohort" not in by["composite"]["missing"]
    # 이 실행의 전략 수(10)는 등록 전략 수 하한보다 적어 누적 시도 = 하한
    assert by["composite"]["trials_cumulative"] == jb.REGISTERED_TRIALS
    assert all(r["cohort"] == "1" for n, r in by.items() if n != "benchmark")
    assert by["benchmark"]["cohort"] is None and "시도 아님" in by["benchmark"]["missing"]["cohort"]


def test_step_log_rows_는_칸만_고르고_긴_까닭은_자른다() -> None:
    """값은 그대로(반올림 없음), 보이지 않는 칸과 그 까닭은 빠지고, 까닭은 `WHY_MAX` 자까지"""
    긴 = "가" * (scb.WHY_MAX + 40)
    줄 = {c: None for c in scb.STEP_LOG_COLS} | {"strategy": "core_h", "cagr": 0.123456789, "walk": None,
                                               "missing": {"mdd": 긴, "walk": scb.WHY_WALK, "dsr": "짧은 까닭"}}  # fmt: skip
    (나온,) = scb.step_log_rows([줄])
    assert 나온["cagr"] == 0.123456789 and 나온["strategy"] == "core_h"
    assert set(나온) == set(scb.STEP_LOG_COLS) | {"missing"}
    assert 나온["missing"] == {"mdd": "가" * scb.WHY_MAX, "dsr": "짧은 까닭"}


def test_웹_칸_목록이_배치와_같다() -> None:
    """웹 `SCOREBOARD_KEYS` 가 배치 `STEP_LOG_COLS` 와 어긋나면 화면 칸이 늘 "없음" 이 된다"""
    글 = (뿌리 / "web" / "lib" / "scoreboard.ts").read_text(encoding="utf-8")
    m = re.search(r"SCOREBOARD_KEYS = \[(.*?)\] as const", 글, re.S)
    assert m, "web/lib/scoreboard.ts 에 SCOREBOARD_KEYS 가 없다"
    assert tuple(re.findall(r'"(\w+)"', m.group(1))) == scb.STEP_LOG_COLS


def test_웹_작업_목록이_배치_작업_이름과_같다() -> None:
    """웹 `SCOREBOARD_JOBS` 가 배치 작업 이름 상수와 어긋나면 그 작업의 판정판이 화면에 안 뜬다(질의 `job_name IN`, 25.1941)"""
    글 = (뿌리 / "web" / "lib" / "scoreboard.ts").read_text(encoding="utf-8")
    m = re.search(r"SCOREBOARD_JOBS = \[(.*?)\] as const", 글, re.S)
    assert m, "web/lib/scoreboard.ts 에 SCOREBOARD_JOBS 가 없다"
    배치 = (jb.JOB_NAME, cj.JOB_NAME, cj.EXPLORE_JOB_NAME, cj.LIMIT_JOB_NAME)
    assert tuple(re.findall(r'"(\w+)"', m.group(1))) == 배치
