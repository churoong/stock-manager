"""25.1951 (전략 깔때기 3장 0 → 1 "코호트 BH q ≤ 0.10") — 판정판이 대조 분위 p 와 코호트 BH q 를 낸다.

- `t_sf` 는 scipy 없이 정규화 불완전 베타로 t 위쪽 꼬리를 낸다 — t 표·`pp.t_quantile`·코시(자유도 1)와 대조
- `control_p` 는 하한(`pp.block_bound`)과 같은 평균·표준오차·묶음 수로 H0: 평균 ≤ 50 의 한쪽 p — 하한 > 50 이면
  p < `pp.EVIDENCE_ALPHA` 이다(같은 검정의 두 얼굴)
- `bh_q` 는 벤야미니–호흐베르크 — m 은 같은 (시장, 코호트) 시도 줄 수(p 없는 줄도 가설로 센다)
- `rows` 가 줄에 `control_p`·`bh_q` 를 싣고 0 → 1 점검표가 BH 칸을 그것으로 본다. 코호트 밖 줄은 까닭과 함께 빈다
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from batch.services import paper as pp
from batch.services import scoreboard as scb


@pytest.mark.parametrize(("df", "t", "꼬리"), [
    (1, 12.706, 0.025), (2, 4.303, 0.025), (5, 4.032, 0.005), (10, 2.228, 0.025), (10, 1.812, 0.05),
    (30, 2.042, 0.025), (60, 1.671, 0.05), (120, 2.358, 0.01),
])  # fmt: skip
def test_t_표와_같다(df: int, t: float, 꼬리: float) -> None:
    """t 표(세 자리 반올림 값)라 1e-4 안에서"""
    assert scb.t_sf(t, df) == pytest.approx(꼬리, abs=1e-4)


def test_대칭·코시·끝() -> None:
    assert scb.t_sf(0.0, 7) == 0.5
    assert scb.t_sf(-1.5, 9) == pytest.approx(1 - scb.t_sf(1.5, 9), abs=1e-14)
    assert scb.t_sf(1.0, 1) == pytest.approx(0.25, abs=1e-12)  # 코시: P(T > 1) = 1/4
    assert scb.t_sf(math.inf, 5) == 0.0 and scb.t_sf(-math.inf, 5) == 1.0
    with pytest.raises(ValueError):
        scb.t_sf(1.0, 0)


@pytest.mark.parametrize("df", [3, 5, 10, 30, 100])
def test_하한의_분위와_같은_검정(df: int) -> None:
    """`pp.t_quantile`(Cornish–Fisher 근사)의 95% 분위에서 꼬리 0.05 — 근사 오차(자유도 3 에서 약 5e-5) 안"""
    assert scb.t_sf(pp.t_quantile(0.95, df), df) == pytest.approx(0.05, abs=1e-4)


def test_control_p() -> None:
    assert scb.control_p({"mean": 55.0, "se": 2.0, "n_blocks": 11}) == pytest.approx(scb.t_sf(2.5, 10))
    assert scb.control_p({"mean": 50.0, "se": 2.0, "n_blocks": 11}) == 0.5
    assert scb.control_p({"mean": 51.0, "se": 0.0, "n_blocks": 5}) == 0.0
    assert scb.control_p({"mean": 50.0, "se": 0.0, "n_blocks": 5}) == 1.0
    for ev in ({"mean": None, "se": 1.0, "n_blocks": 5}, {"mean": 55.0, "se": None, "n_blocks": 5},
               {"mean": 55.0, "se": 1.0, "n_blocks": 1}, {}):  # fmt: skip
        assert scb.control_p(ev) is None


def test_하한이_50_을_넘으면_p_는_알파_아래() -> None:
    """같은 묶음 값 → `pp.block_bound` 의 하한과 `control_p` 가 같은 판정(경계 양쪽)"""
    for 값 in ({k: 52.0 + (k % 3) for k in range(8)}, {k: 50.5 + (k % 5) * 0.3 for k in range(8)},
               {k: 49.0 + (k % 4) for k in range(8)}):  # fmt: skip
        b = pp.block_bound(값)
        p = scb.control_p(b)
        assert p is not None and (b["lower"] > 50) == (p < pp.EVIDENCE_ALPHA), (b, p)


def test_bh_q_손계산() -> None:
    # 정렬 p 0.01·0.03·0.04, m 4 → 4·0.04/3 = 0.0533·min(0.0533, 4·0.03/2 = 0.06)·min(0.0533, 4·0.01/1 = 0.04)
    q = scb.bh_q([0.01, 0.04, None, 0.03], m=4)
    assert q[2] is None and q[0] == pytest.approx(0.04) and q[1] == pytest.approx(0.16 / 3) == q[3]
    assert scb.bh_q([0.5, 0.9], m=2) == [0.9, 0.9]  # 1 을 넘지 않고 단조
    assert scb.bh_q([0.8], m=3) == [1.0]
    assert scb.bh_q([]) == []


def _실행(job: str, 증거: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    return {"job_name": job, "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "trial_floor": 21, "years": 5, "risk_every": "week"}, "group_id": "g"},
            "results": [{"strategy": n, "market": "KR", "group_id": "g", "warnings_json": "[]",
                         "metrics_json": json.dumps({"mdd": -0.2, **({"evidence": e} if e else {})})}
                        for n, e in 증거.items()]}  # fmt: skip


def test_rows_는_같은_코호트_줄들로_BH_를_내고_점검표가_쓴다() -> None:
    좋음 = {"mean": 58.0, "se": 2.0, "n_blocks": 13, "lower": 54.4}  # t = 4, 자유도 12
    애매 = {"mean": 52.0, "se": 1.5, "n_blocks": 13, "lower": 49.3}
    줄 = {r["strategy"]: r for r in scb.rows([_실행("backtest_explore",
                                                  {"core_d": 좋음, "core_e": 애매, "core_a": None})])}  # fmt: skip
    pd, pe = scb.t_sf(4.0, 12), scb.t_sf(2.0 / 1.5, 12)
    assert 줄["core_d"]["control_p"] == pytest.approx(pd) and 줄["core_e"]["control_p"] == pytest.approx(pe)
    # m = 등록 코호트 2 크기(25.1956 — 줄은 셋이지만 빠진 core_c·st_s·st_t 도 가설, 25.2050 부터 6). 상수를 따른다
    # (25.2051, 25.2050 교차검증 [필수]: 4 를 손으로 적어 상수를 바꾸자 깨졌다). 정렬 p 둘: d 1등, e 2등
    m = scb.COHORT_SIZE["2"]
    assert 줄["core_d"]["bh_q"] == pytest.approx(min(m * pd, m * pe / 2))
    assert 줄["core_e"]["bh_q"] == pytest.approx(m * pe / 2)
    assert 줄["core_a"]["bh_q"] is None and 줄["core_a"]["missing"]["bh_q"].startswith("대조 분위 p 없음")
    bh = {r["strategy"]: next(c for c in r["stage01"]["checks"] if c["key"] == "bh_q") for r in 줄.values()}
    assert bh["core_d"]["ok"] is True and bh["core_e"]["ok"] is False and bh["core_a"]["ok"] is None
    assert "코호트 BH q" in 줄["core_e"]["stage01"]["failed"]


def test_코호트_밖_줄과_시도_아닌_줄은_BH_묶음_밖() -> None:
    좋음 = {"mean": 58.0, "se": 2.0, "n_blocks": 13, "lower": 54.4}
    줄 = {r["strategy"]: r for r in scb.rows([_실행("backtest_limit", {"core_b": 좋음, "core_r1": 좋음})])}
    for s in ("core_b", "core_r1"):
        assert 줄[s]["bh_q"] is None and 줄[s]["missing"]["bh_q"] == scb.WHY_BH_NO_COHORT
    assert 줄["core_b"]["control_p"] is not None  # p 는 내고 BH 만 묶음이 없다
