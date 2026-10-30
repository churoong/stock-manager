"""25.1956 — 판정판 진단 칸 다지기(25.1951·25.1952·25.1954 교차검증 [권고]·전체 검사 rc=1).

- BH 의 m = 등록 코호트 크기(`COHORT_SIZE`, funnel.md 2.1 "등록할 때 고정") — jobs 상수와 대조, 일부 변형만 돈 실행도 m 이
  줄지 않는다. 시장마다 따로 묶는다. 하한 없는(묶음 미달) 줄은 p 없음
- `_num` 은 NaN·무한을 None 으로 — 칸에 NaN 이 실리지 않고(웹 JSON.parse), 점검표가 "걸림" 대신 "모름"
- `months` 는 숫자로 고정해 싣는다(점검표와 화면이 같은 값)
- 수용량 단위 금액의 **숫자**와 화면 글·funnel 문장이 같다
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from batch.services import core_strategy as cs
from batch.services import scoreboard as scb

뿌리 = Path(__file__).resolve().parent.parent


def test_코호트_크기는_jobs_상수와_같다() -> None:
    assert scb.COHORT_SIZE["1b"] == len([v for v in cs.VARIANTS if v not in scb.NOT_TRIALS])
    # S·T 계좌형 리플레이 둘도 코호트 2 등록(25.2050 — 25.2044 의 `ST_VARIANTS`)
    from batch.jobs import core_backtest as cj

    assert scb.COHORT_SIZE["2"] == len(cs.EXPLORE_VARIANTS) + len(cj.ST_VARIANTS)


def _실행(job: str, 증거: dict[str, dict[str, Any] | None], market: str = "KR", **mj: Any) -> dict[str, Any]:
    return {"job_name": job, "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "trial_floor": 21, "years": 5, "risk_every": "week"},
                         "group_id": f"g{market}"},
            "results": [{"strategy": n, "market": market, "group_id": f"g{market}", "warnings_json": "[]",
                         "metrics_json": json.dumps({"mdd": -0.2, **mj, **({"evidence": e} if e else {})})}
                        for n, e in 증거.items()]}  # fmt: skip


좋음 = {"mean": 58.0, "se": 2.0, "n_blocks": 13, "lower": 54.4}


def test_일부_변형만_돈_실행도_m_은_등록_크기() -> None:
    줄 = scb.rows([_실행("backtest_explore", {"core_d": 좋음})])[0]
    assert 줄["bh_q"] == pytest.approx(min(1.0, scb.COHORT_SIZE["2"] * 줄["control_p"]))


def test_st_줄이_끼어도_탐색_변형_q_는_같다() -> None:
    """25.2050 의 목적 — `--st` 를 붙인 실행(st_s·st_t 줄, 대조 분위 p 없음)과 안 붙인 실행에서 같은 탐색 변형의 BH q 가
    같다(25.2051, 25.2050 교차검증 note: 상수 대조로만 간접 묶였다)"""
    def q(증거: dict) -> float:
        return next(r["bh_q"] for r in scb.rows([_실행("backtest_explore", 증거)]) if r["strategy"] == "core_d")

    assert q({"core_d": 좋음}) == q({"core_d": 좋음, "st_s": None, "st_t": None}) is not None


def test_시장마다_따로_묶는다() -> None:
    줄 = scb.rows([_실행("backtest_explore", {"core_d": 좋음, "core_e": 좋음}, "KR"),
                   _실행("backtest_explore", {"core_d": 좋음}, "US")])  # fmt: skip
    p = 줄[0]["control_p"]
    # 시장마다 m = 코호트 2 크기(6 — 25.2050), 같은 p 들 — 묶음이 섞이면 KR 의 정렬 순위가 바뀐다
    # (KR 둘: q = mp/2, US 하나: q = mp)
    m = scb.COHORT_SIZE["2"]
    by = {(r["market"], r["strategy"]): r["bh_q"] for r in 줄}
    assert by[("KR", "core_d")] == pytest.approx(m * p / 2) and by[("US", "core_d")] == pytest.approx(m * p)


def test_하한_없는_줄은_p_도_없다() -> None:
    """block_bound 는 묶음 2~3개에서도 평균·표준오차를 내지만 하한은 없다 — 그 줄은 p·q 없음"""
    미달 = {"mean": 57.5, "se": 2.5, "n_blocks": 3, "lower": None}
    줄 = scb.rows([_실행("backtest_explore", {"core_d": 미달})])[0]
    assert 줄["control_p"] is None and 줄["bh_q"] is None
    assert 줄["missing"]["bh_q"].startswith("대조 분위 p 없음 — 묶음 3개") and "p 없음 — p 없음" not in 줄["missing"]["bh_q"]


@pytest.mark.parametrize("나쁜", [math.nan, math.inf, "nan"])
def test_유한하지_않은_값은_None_이고_점검은_모름(나쁜: Any) -> None:
    assert scb._num(나쁜) is None
    줄 = scb.rows([_실행("backtest_explore", {"core_d": {**좋음, "mean": 나쁜}})])[0]
    assert 줄["control_p"] is None  # p 가 NaN 이면 BH 정렬이 흐트러졌다
    for mj in ({"mdd": 나쁜}, {"split_half": {"first": {"cagr": 나쁜}, "second": {"cagr": 0.1}}}):
        # 기준 줄(core_b, 유한한 MDD)을 함께 — 없으면 MDD 칸은 기준이 없어 늘 모름이라 단언이 헛돈다(25.1958, 교차검증 note)
        행 = [{"strategy": "core_b", "market": "KR", "group_id": "g", "warnings_json": "[]",
              "metrics_json": json.dumps({"mdd": -0.2})},
             {"strategy": "core_d", "market": "KR", "group_id": "g", "warnings_json": "[]", "metrics_json": json.dumps(mj)}]
        r = next(x for x in scb.rows([{**_실행("backtest_explore", {"core_d": None}), "results": 행}])
                 if x["strategy"] == "core_d")  # fmt: skip
        assert r["stage01"]["base"] == "core_b"
        st = {c["key"]: c["ok"] for c in r["stage01"]["checks"]}
        assert st["mdd_vs_base"] is None and st["half"] is None  # 걸림이 아니라 모름
        assert all(not (isinstance(v, float) and not math.isfinite(v)) for v in r.values())


def test_개월은_숫자로_싣는다() -> None:
    r = scb.rows([{**_실행("backtest_explore", {"core_d": None}), "results": [
        {"strategy": "core_d", "market": "KR", "group_id": "g", "warnings_json": "[]",
         "metrics_json": json.dumps({"luck": {"months": "40"}})}]}])[0]  # fmt: skip
    assert r["months"] == 40.0 and isinstance(r["months"], float)
    assert next(c for c in r["stage01"]["checks"] if c["key"] == "months")["ok"] is True


def test_수용량_단위_숫자와_글과_문서가_같다() -> None:
    """25.1956 (25.1954 교차검증 [권고]) — 글끼리만 대조해 US 단위를 1e6 으로 바꿔도 지나갔다(미국 줄이 10배 어긋남)"""
    assert scb.CAPACITY_UNIT == {"KR": 1e8, "US": 1e5}
    assert scb.CAPACITY_UNIT_LABEL == {"KR": "1억 원", "US": "10만 달러"}
    funnel = (뿌리 / "docs" / "funnel.md").read_text(encoding="utf-8")
    assert "단위 금액(국내 1억 원·미국 10만 달러, 시장 통화)" in funnel
    web = (뿌리 / "web" / "lib" / "scoreboard.ts").read_text(encoding="utf-8")
    assert re.search(r'CAPACITY_UNIT_LABEL: Record<string, string> = \{ KR: "1억 원", US: "10만 달러" \}', web)
