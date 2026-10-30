"""25.1950 (전략 깔때기 3장 0 → 1 점검표) — 판정판 줄마다 funnel.md 3장 "리플레이에서 그림자로" 첫 값에 대어 본다.

- 기준마다 True·False·None(모름). 하나라도 False 면 "탈락", 모두 True 면 "후보", 아니면 "모름"
- 코호트 BH q·수용량은 재료가 저장되지 않아 늘 모름 — 그래서 지금은 "후보" 가 나오지 않는다(올림은 사람이 정한다)
- MDD 는 같은 실행의 기준 B(`core_b`), 없으면(월 실행) `benchmark` 와 견준다. 시도 아닌 줄은 점검표 없음(까닭)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.services import scoreboard as scb

#: 아는 기준을 모두 넘는 줄 — 경계값 그대로
좋은 = {"strategy": "core_d", "cohort": "2", "months": 36, "invested_share": 0.5, "avg_holdings": 3.0, "excess_cagr": 0.001,
        "control_lower": 50.1, "dsr": 0.90, "half_first_cagr": 0.01, "half_second_cagr": 0.02, "walk_positive": 3,
        "walk_total": 4, "mdd": -0.25, "bh_q": 0.10}  # fmt: skip
기준 = {"strategy": "core_b", "mdd": -0.20}


def _ok(row: dict[str, Any], key: str, base: dict[str, Any] | None = None) -> bool | None:
    st = scb.stage01(row, 기준 if base is None else base)
    assert st is not None
    return next(c["ok"] for c in st["checks"] if c["key"] == key)


def test_아는_기준을_다_넘어도_재료_없는_수용량이_남아_모름() -> None:
    st = scb.stage01(좋은, 기준)
    assert st is not None and st["verdict"] == "모름" and st["failed"] == []
    assert st["unknown"] == ["수용량"] and st["base"] == "core_b"
    assert scb.stage01_note(st) == "재료 없음: 수용량"


@pytest.mark.parametrize(("key", "고침", "기대"), [
    ("months", {"months": 35}, False),
    ("invested_share", {"invested_share": 0.4999}, False),
    ("avg_holdings", {"avg_holdings": 2.9}, False),
    ("excess_cagr", {"excess_cagr": 0.0}, False),
    ("control_lower", {"control_lower": 50.0}, False),
    ("dsr", {"dsr": 0.8999}, False),
    ("half", {"half_second_cagr": 0.0}, False),
    ("half", {"half_first_cagr": None}, None),
    ("walk", {"walk_positive": 2}, False),
    ("walk", {"walk_positive": 3, "walk_total": 3}, False),  # 창이 넷 미만
    ("walk", {"walk_positive": 4, "walk_total": 5}, True),  # 4/5 ≥ 3/4
    ("walk", {"walk_positive": 3, "walk_total": 5}, False),  # 3/5 < 3/4
    ("walk", {"walk_total": None}, None),
    ("mdd_vs_base", {"mdd": -0.2501}, False),  # 기준 −0.20 보다 5%p 넘게 나쁨
    ("mdd_vs_base", {"mdd": None}, None),
    ("months", {"months": None}, None),
    # 25.1955 (25.1953 교차검증 [권고]) — BH q 경계(≤ 0.10, 등호가 빠져도 아무 시험도 깨지지 않았다)·NaN 은 모름
    ("bh_q", {"bh_q": 0.1001}, False),
    ("bh_q", {"bh_q": None}, None),
    ("months", {"months": float("nan")}, None),
    ("months", {"months": "nan"}, None),
])
def test_기준마다_경계(key: str, 고침: dict[str, Any], 기대: bool | None) -> None:
    assert _ok(좋은, key) is True
    assert _ok({**좋은, **고침}, key) is 기대


def test_하나라도_걸리면_탈락이고_글에_걸린_것과_모르는_것() -> None:
    st = scb.stage01({**좋은, "dsr": 0.5, "walk_positive": 1}, 기준)
    assert st is not None and st["verdict"] == "탈락" and st["failed"] == ["DSR", "걷는 창"]
    assert scb.stage01_note(st) == "걸림: DSR·걷는 창 · 재료 없음: 수용량"


def test_기준_줄이_없으면_MDD_는_모름() -> None:
    assert _ok(좋은, "mdd_vs_base", base={}) is None


def test_시도_아닌_줄은_점검표_없음() -> None:
    for s in scb.NOT_TRIALS:
        assert scb.stage01({**좋은, "strategy": s}, 기준) is None


def _실행(job: str, 이름들: list[str], mdd: dict[str, float]) -> dict[str, Any]:
    return {"job_name": job, "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "default": True, "years": 5, "risk_every": "week"}, "group_id": "g"},
            "results": [{"strategy": n, "market": "KR", "group_id": "g", "warnings_json": "[]",
                         "metrics_json": json.dumps({"mdd": mdd[n]})} for n in 이름들]}  # fmt: skip


def test_rows_는_같은_실행의_기준_B_와_없으면_benchmark_와_견준다() -> None:
    핵심 = {r["strategy"]: r for r in scb.rows([_실행("backtest_core", ["core_b", "core_h", "core_r1"],
                                                      {"core_b": -0.20, "core_h": -0.26, "core_r1": -0.1})])}  # fmt: skip
    assert 핵심["core_h"]["stage01"]["base"] == "core_b"
    assert "MDD(기준 대비)" in 핵심["core_h"]["stage01"]["failed"]  # −0.26 < −0.20 − 0.05
    assert 핵심["core_h"]["stage01_verdict"] == "탈락" and 핵심["core_h"]["stage01_note"].startswith("걸림:")
    assert 핵심["core_b"]["stage01"]["base"] == "core_b"  # 기준 B 는 자기와 견준다 — MDD 칸은 통과
    for c in ("stage01", "stage01_verdict", "stage01_note"):
        assert 핵심["core_r1"][c] is None and 핵심["core_r1"]["missing"][c] == "기준·무작위 대조 — 시도 아님"
    월 = {r["strategy"]: r for r in scb.rows([_실행("backtest", ["benchmark", "composite"],
                                                   {"benchmark": -0.30, "composite": -0.32})])}  # fmt: skip
    assert 월["composite"]["stage01"]["base"] == "benchmark"
    assert "MDD(기준 대비)" not in 월["composite"]["stage01"]["failed"]
    assert 월["benchmark"]["stage01_verdict"] is None


def test_모두_통과면_후보이고_글은_모든_기준_통과() -> None:
    """25.1953 (25.1950 교차검증 [권고]) — "후보" 갈래를 잡는 시험이 없어 지워도 지나갔다. 수용량이 늘 모름이라 줄로는
    닿지 않아 판정 함수를 직접 본다"""
    칸 = [{"label": "가", "ok": True}, {"label": "나", "ok": True}]
    v = scb.stage01_verdict(칸)
    assert v == {"verdict": "후보", "failed": [], "unknown": []}
    assert scb.stage01_note({**v, "checks": 칸}) == "모든 기준 통과"
    assert scb.stage01_verdict([*칸, {"label": "다", "ok": None}])["verdict"] == "모름"
    assert scb.stage01_verdict([*칸, {"label": "다", "ok": None}, {"label": "라", "ok": False}])["verdict"] == "탈락"


def test_코호트_밖_줄은_점검하지_않는다() -> None:
    """25.1953 (25.1950 교차검증 [권고]) — 지정가·비기본 월·등록 전 실행 줄에 "0→1 점검: 탈락" 이 떠 0→1 문 앞에 선
    것처럼 보였다. 코호트 밖은 None 과 까닭"""
    assert scb.stage01({**좋은, "cohort": None}, 기준) is None
    줄 = {r["strategy"]: r for r in scb.rows([_실행("backtest_limit", ["core_b", "core_h"],
                                                  {"core_b": -0.2, "core_h": -0.3})])}  # fmt: skip
    for c in ("stage01", "stage01_verdict", "stage01_note"):
        assert 줄["core_h"][c] is None and 줄["core_h"]["missing"][c] == "코호트 밖 — 0→1 점검 안 함"


def test_숫자가_아닌_칸은_모름() -> None:
    """25.1953 (25.1950 교차검증 note) — `months` 는 `_num` 을 거치지 않고 실려 문자열이면 비교가 TypeError 로 판정판
    전체를 `scoreboard_error` 로 바꿨다"""
    assert _ok({**좋은, "months": "x"}, "months") is None
    assert _ok({**좋은, "months": "40"}, "months") is True  # 숫자 글은 숫자로


def test_기준_줄은_core_b_가_먼저() -> None:
    """25.1953 (25.1950 교차검증 note) — 둘 다 있는 실행이면 core_b"""
    줄 = {r["strategy"]: r for r in scb.rows([_실행("backtest_core", ["benchmark", "core_b", "core_h"],
                                                  {"benchmark": -0.5, "core_b": -0.2, "core_h": -0.3})])}  # fmt: skip
    assert 줄["core_h"]["stage01"]["base"] == "core_b"

