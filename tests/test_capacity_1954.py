"""25.1954 (전략 깔때기 2.2·3장 수용량) — 실행이 리밸런스마다 수용량을 재어 결과 행에 싣고 판정판이 칸으로 낸다.

- `capacity_profile`: 리밸런스(그날 체결 뒤 비중)마다 단위 금액의 `capacity` 최대 비율. 20일 평균 거래대금은 그 종목의
  그날 **전** 20 거래일(그날 값은 쓰지 않는다 — point-in-time). 20일이 안 찬 종목은 재지 못한 종목(그날 최대는 하한)
- 판정판 칸 `capacity` = 단위당 최대. 0 → 1 점검의 수용량 칸은 실전 목표 금액(`STAGE01_TARGET`, 사용자 결정)이 있어야
  본다 — 없으면 모름, 재지 못한 날이 있어도 모름(하한이라)
- 실행 배선(핵심 H)과 계산이 깨져도 결과 행이 남는 것
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.jobs import backtest as jb
from batch.jobs import core_backtest as cj
from batch.services import scoreboard as scb
from tests.test_core_backtest_job_1534 import _db, 오늘

날 = [f"2024-01-{d:02d}" for d in range(1, 32)] + [f"2024-02-{d:02d}" for d in range(1, 10)]


def _거래대금(값: dict[int, float], 몇: int = 25) -> dict[int, dict[str, float]]:
    return {sid: {날[k]: v for k in range(몇)} for sid, v in 값.items()}


def test_손으로_센_비율() -> None:
    """1억 원 · 비중 0.5 · 20일 평균 100억 → 0.5억 ÷ 100억 = 0.005. 둘째 종목 0.3 · 10억 → 0.03 이 최대"""
    t = _거래대금({1: 1e10, 2: 1e9})
    p = scb.capacity_profile([(날[22], {1: 0.5, 2: 0.3})], t, 1e8)
    assert p["max"] == pytest.approx(0.03) and p["max_stock"] == 2 and p["max_day"] == 날[22]
    assert p["rebalances"] == 1 and p["complete"] is True and p["incomplete"] == 0 and p["unit"] == 1e8


def test_그날_거래대금은_쓰지_않는다() -> None:
    t = _거래대금({1: 1e10}, 몇=20)
    t[1][날[20]] = 1e15  # 그날 — 쓰면 비율이 거의 0
    p = scb.capacity_profile([(날[20], {1: 0.5})], t, 1e8)
    assert p["max"] == pytest.approx(0.005)
    # 그날 전이 19일뿐이면 재지 못한다
    p2 = scb.capacity_profile([(날[19], {1: 0.5})], t, 1e8)
    assert p2["max"] is None and p2["incomplete"] == 1 and p2["complete"] is False and p2["rebalances"] == 0


def test_재지_못한_종목이_있으면_하한이고_불완전() -> None:
    t = _거래대금({1: 1e10})
    p = scb.capacity_profile([(날[22], {1: 0.5, 9: 0.5})], t, 1e8)
    assert p["max"] == pytest.approx(0.005) and p["incomplete"] == 1 and p["complete"] is False


def test_여러_날의_최대·분위() -> None:
    t = _거래대금({1: 1e10}, 몇=40)
    재배 = [(날[20 + k], {1: w}) for k, w in enumerate([0.1, 0.2, 0.3, 0.4, 0.5])]
    p = scb.capacity_profile([*재배, (날[30], {})], t, 1e8)  # 빈 비중(현금만)은 세지 않는다
    assert p["rebalances"] == 5 and p["max"] == pytest.approx(0.005) and p["max_day"] == 날[24]
    assert p["median"] == pytest.approx(0.003) and p["p90"] == pytest.approx(0.005)


def _줄(cap: dict[str, Any] | None, market: str = "KR", mj: dict[str, Any] | None = None) -> dict[str, Any]:
    run = {"job_name": "backtest_explore", "started_at": "2026-11-04T00:00:00+00:00",
           "step_log": {"params": {"sha": "x", "trial_floor": 21}, "group_id": "g"},
           "results": [{"strategy": "core_d", "market": market, "group_id": "g", "warnings_json": "[]",
                        "metrics_json": json.dumps({"mdd": -0.2, **({"capacity": cap} if cap else {}),
                                                    **(mj or {})})}]}  # fmt: skip
    return scb.rows([run])[0]


def _수용량칸(row: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in row["stage01"]["checks"] if c["key"] == "capacity")


def test_판정판_칸과_점검은_실전_목표가_있어야(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = {"max": 0.002, "complete": True}
    r = _줄(cap)
    assert r["capacity"] == 0.002 and _수용량칸(r)["now"] == 0.002 and _수용량칸(r)["ok"] is None  # 목표 미정
    monkeypatch.setattr(scb, "STAGE01_TARGET", {"KR": 5e9})  # 50억 → 50 × 0.002 = 0.10 > 0.01
    assert _수용량칸(_줄(cap))["ok"] is False
    monkeypatch.setattr(scb, "STAGE01_TARGET", {"KR": 5e8})  # 5억 → 0.01 ≤ 0.01
    assert _수용량칸(_줄(cap))["ok"] is True
    assert _수용량칸(_줄({"max": 0.002, "complete": False}))["ok"] is None  # 하한이면 모름
    assert _수용량칸(_줄(cap, market="US"))["ok"] is None  # 그 시장 목표 없음


def test_옛_실행은_까닭과_함께_빈다() -> None:
    r = _줄(None)
    assert r["capacity"] is None and r["missing"]["capacity"] == scb.WHY_CAPACITY and "capacity_profile" not in r
    r2 = _줄({"max": None, "complete": False, "rebalances": 0, "incomplete": 3})
    assert r2["capacity"] is None and r2["missing"]["capacity"].startswith("재지 못함")
    # 25.1956 — 보유가 하루도 없던 실행은 "거래대금 없음" 이 아니라 그렇게
    r3 = _줄({"max": None, "complete": False, "rebalances": 0, "incomplete": 0})
    assert r3["missing"]["capacity"] == "보유 없는 실행 — 잴 날이 없다"


def test_핵심_실행이_수용량을_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db(monkeypatch)
    assert cj.run_core("KR", years=1, today=오늘, force=True, variants=("core_b",)) == 0
    run = mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ? ORDER BY id DESC LIMIT 1",
                      [cj.JOB_NAME]).dicts()[0]  # fmt: skip
    row = mem.execute("SELECT metrics_json FROM backtest_runs ORDER BY id DESC LIMIT 1").dicts()[0]
    cap = json.loads(row["metrics_json"])["capacity"]
    assert cap["unit"] == scb.CAPACITY_UNIT["KR"] and cap["rebalances"] > 0 and cap["max"] > 0
    assert json.loads(run["step_log"])["scoreboard"][0]["capacity"] == cap["max"]


def test_수용량이_깨져도_결과_행은_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db(monkeypatch)

    def 망(*_a: object, **_k: object) -> None:
        raise RuntimeError("수용량 망")

    monkeypatch.setattr(scb, "capacity_profile", 망)
    assert cj.run_core("KR", years=1, today=오늘, force=True, variants=("core_b",)) == 0
    row = mem.execute("SELECT metrics_json FROM backtest_runs ORDER BY id DESC LIMIT 1").dicts()[0]
    assert "capacity" not in json.loads(row["metrics_json"])
    assert json.loads(row["metrics_json"])["capacity_error"] == "RuntimeError: 수용량 망"  # 25.1956 실패 표지
    # 25.1958 (25.1956 교차검증 [권고]) — 판정판 줄이 실패 표지를 까닭으로 적는다("25.1954 전" 이 아니다)
    run = mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ? ORDER BY id DESC LIMIT 1",
                      [cj.JOB_NAME]).dicts()[0]  # fmt: skip
    줄 = json.loads(run["step_log"])["scoreboard"][0]
    assert 줄["capacity"] is None and 줄["missing"]["capacity"] == "수용량 계산 실패 — RuntimeError: 수용량 망"
    assert 줄["missing"]["capacity_complete"] == 줄["missing"]["capacity"]  # 같은 까닭(웹이 한 줄로 묶는다)
    # 재료 없음은 실패가 아니라 따로(25.1958)
    assert jb.capacity_extra(None, {}, "KR") == {"capacity_missing": "거래대금 자료 없음"}  # type: ignore[arg-type]
    assert jb.capacity_extra(None, {1: {}}, "JP") == {"capacity_missing": "단위 금액 없는 시장(JP)"}  # type: ignore[arg-type]
    assert _줄(None, mj={"capacity_missing": "거래대금 자료 없음"})["missing"]["capacity"] == "수용량 재지 못함 — 거래대금 자료 없음"


@pytest.mark.parametrize(("cap", "기대"), [
    ({"max": 0.004, "complete": False, "incomplete": 2, "rebalances": 5}, False),
    ({"max": 0.004, "complete": True, "incomplete": 0, "rebalances": 5}, True),
    (None, None),
])
def test_하한_여부_칸(cap: dict[str, Any] | None, 기대: bool | None) -> None:
    """25.1958 (25.1956 교차검증 [권고]) — `capacity_complete` 를 배치에서 잡는 시험이 없어 늘 True 로 박아도 지나갔다
    (화면의 "≥" 하한 표시가 조용히 사라진다)"""
    r = _줄(cap)
    assert r["capacity_complete"] is 기대
    if 기대 is None:
        assert r["missing"]["capacity_complete"] == r["missing"]["capacity"] == scb.WHY_CAPACITY
