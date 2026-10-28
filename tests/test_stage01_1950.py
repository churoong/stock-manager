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
좋은 = {"strategy": "core_d", "months": 36, "invested_share": 0.5, "avg_holdings": 3.0, "excess_cagr": 0.001,
        "control_lower": 50.1, "dsr": 0.90, "half_first_cagr": 0.01, "half_second_cagr": 0.02, "walk_positive": 3,
        "walk_total": 4, "mdd": -0.25}  # fmt: skip
기준 = {"strategy": "core_b", "mdd": -0.20}


def _ok(row: dict[str, Any], key: str, base: dict[str, Any] | None = None) -> bool | None:
    st = scb.stage01(row, 기준 if base is None else base)
    assert st is not None
    return next(c["ok"] for c in st["checks"] if c["key"] == key)


def test_아는_기준을_다_넘어도_재료_없는_둘이_남아_모름() -> None:
    st = scb.stage01(좋은, 기준)
    assert st is not None and st["verdict"] == "모름" and st["failed"] == []
    assert st["unknown"] == ["코호트 BH q", "수용량"] and st["base"] == "core_b"
    assert scb.stage01_note(st) == "모름: 코호트 BH q·수용량"


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
])
def test_기준마다_경계(key: str, 고침: dict[str, Any], 기대: bool | None) -> None:
    assert _ok(좋은, key) is True
    assert _ok({**좋은, **고침}, key) is 기대


def test_하나라도_걸리면_탈락이고_글에_걸린_것과_모르는_것() -> None:
    st = scb.stage01({**좋은, "dsr": 0.5, "walk_positive": 1}, 기준)
    assert st is not None and st["verdict"] == "탈락" and st["failed"] == ["DSR", "걷는 창"]
    assert scb.stage01_note(st) == "걸림: DSR·걷는 창 · 모름: 코호트 BH q·수용량"


def test_기준_줄이_없으면_MDD_는_모름() -> None:
    assert _ok(좋은, "mdd_vs_base", base={}) is None


def test_시도_아닌_줄은_점검표_없음() -> None:
    for s in scb.NOT_TRIALS:
        assert scb.stage01({**좋은, "strategy": s}, 기준) is None


def _실행(job: str, 이름들: list[str], mdd: dict[str, float]) -> dict[str, Any]:
    return {"job_name": job, "started_at": "2026-11-04T00:00:00+00:00",
            "step_log": {"params": {"sha": "x", "default": True}, "group_id": "g"},
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
