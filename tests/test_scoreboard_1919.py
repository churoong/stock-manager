"""전략 깔때기 판정판 — `services/scoreboard` (docs/funnel.md 2.2, 기록 25.1919). 오프라인, 손으로 센 고정 자료."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from batch.services import scoreboard as sb

뿌리 = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 고정 자료 — `store_run` 이 쓰는 열 모양 그대로(JSON 글)
# ---------------------------------------------------------------------------
_LUCK_COMPOSITE = {"months": 48, "n_trials": 17, "n_trials_run": 10, "sr_monthly": 0.2, "sr_star": 0.25,
                   "psr": 0.9, "dsr": 0.62, "luck_pct": 38.0}  # fmt: skip
_WARN_MONTHLY = ["생존편향: 상장폐지 종목 미포함. 실제보다 좋게 나온다", "시점 유니버스: 리밸런스마다 …"]


def _행(strategy: str, metrics: dict, *, market: str = "KR", excess: float | None = None, turnover: float | None = 0.4,
        warnings: list[str] | None = None, created: str = "2026-11-03T18:00:00+00:00", rebalances: int = 50) -> dict:
    return {"market": market, "strategy": strategy, "start_date": "2022-09-01", "end_date": "2026-10-30", "top_n": 20,
            "rebalance": "monthly", "costs_json": "{}", "rebalances": rebalances, "final_equity": 1.5,
            "turnover_avg": turnover, "excess_cagr": excess, "win_rate": 0.55, "metrics_json": json.dumps(metrics),
            "warnings_json": json.dumps(_WARN_MONTHLY if warnings is None else warnings, ensure_ascii=False),
            "group_id": "g1", "calc_version": 6, "scoring_calc_version": 3, "created_at": created}  # fmt: skip


def _월실행(started: str = "2026-11-03T17:11:00+00:00", default: bool = True, sha: str | None = "abc123def456") -> dict:
    return {
        "job_name": "backtest", "started_at": started,
        "step_log": json.dumps({"group_id": "g1", "params": {"default": default, "sha": sha}}),
        "results": [
            _행("composite", {"cagr": 0.12, "mdd": -0.25, "sharpe": 0.8, "invested_share": 0.9, "avg_holdings": 18.5,
                              "luck": _LUCK_COMPOSITE}, excess=0.03),
            _행("composite_no_cost", {"cagr": 0.15, "mdd": -0.24}, excess=0.06),
            _행("benchmark", {"cagr": 0.09, "mdd": -0.3, "invested_share": 1.0, "avg_holdings": 300.0}),
            _행("value", {"cagr": 0.05, "mdd": -0.4,
                          "luck": {"months": 20, "n_trials": 17, "n_trials_run": 10, "verdict": "판정 안 함(월 20개 < 24)"}},
                excess=-0.04, warnings=["과거 유니버스 스냅샷 없음. 현재 유니버스를 전 기간에 썼다"]),
        ],
    }  # fmt: skip


_EV_H = {"buys": 40, "scored": 38, "no_control": 2, "days": 35, "n_blocks": 9, "mean": 55.0, "se": 1.5, "lower": 52.5,
         "gate": {"days": 30, "n_blocks": 7, "mean": 51.0, "se": 2.0, "lower": 48.0},
         "after_sell": {"sells": 30, "n_blocks": 8, "mean": -0.5, "se": 0.4, "lower": -1.2}}  # fmt: skip


def _핵심실행(job: str = "backtest_core", floor: int = 17, sha: str = "fff000111222") -> dict:
    half = {"first": {"start": "2022-09-01", "end": "2024-09-30", "days": 500, "cagr": 0.10, "mdd": -0.20},
            "second": {"start": "2024-09-30", "end": "2026-10-30", "days": 500, "cagr": -0.05, "mdd": -0.30}}
    luck_h = {"months": 50, "n_trials": floor, "n_trials_run": 5, "dsr": 0.91, "luck_pct": 9.0}
    이름 = "core_h" if job == "backtest_core" else "core_d"
    return {
        "job_name": job, "started_at": "2026-11-10T01:00:00+00:00",
        "step_log": {"params": {"trial_floor": floor, "sha": sha, "explore": job != "backtest_core"}},
        "results": [
            _행(이름, {"cagr": 0.08, "mdd": -0.22, "invested_share": 0.7, "avg_holdings": 4.2, "luck": luck_h,
                       "split_half": half, "evidence": _EV_H}, excess=0.01, turnover=0.05),
            _행("core_r1", {"cagr": 0.02, "mdd": -0.35, "luck": {"months": 50, "n_trials": floor, "dsr": 0.1,
                                                                 "luck_pct": 90.0},
                            "evidence": {"buys": 10, "n_blocks": 3, "lower": None,
                                         "gate": {"n_blocks": 2, "lower": None}}}, excess=-0.05),
        ],
    }  # fmt: skip


def _by(rows: list[dict], name: str) -> dict:
    (r,) = [x for x in rows if x["strategy"] == name]
    return r


# ---------------------------------------------------------------------------
# 칸마다
# ---------------------------------------------------------------------------
def test_월_실행_칸() -> None:
    rows = sb.rows([_월실행()])
    assert [r["strategy"] for r in rows] == ["composite", "value", "benchmark"]  # 코호트 1 먼저, 코호트 없음 뒤
    c = _by(rows, "composite")
    assert (c["market"], c["sha"], c["job_name"], c["stage"], c["runs"]) == ("KR", "abc123def456", "backtest", 0, 1)
    assert c["cagr"] == 0.12 and c["excess_cagr"] == 0.03
    # 비용 몫 손 계산: 비용 0 CAGR 0.15 − 비용 반영 0.12 = 0.03, 0.03 ÷ 0.15 = 0.2
    assert c["cost_drag"] == pytest.approx(0.03) and c["cost_share"] == pytest.approx(0.2)
    assert (c["mdd"], c["sharpe"], c["invested_share"], c["avg_holdings"], c["turnover"]) == (-0.25, 0.8, 0.9, 18.5, 0.4)
    assert (c["dsr"], c["luck_pct"], c["dsr_n"], c["months"], c["rebalances"]) == (0.62, 38.0, 17, 48, 50)
    assert c["cohort"] == "1" and c["is_trial"] and c["trials_cumulative"] == 17  # 월 실행은 luck.n_trials
    assert c["survivorship"] is True and c["universe"] == "pit" and len(c["warnings"]) == 2
    assert c["start_date"] == "2022-09-01" and c["calc_version"] == 6


def test_월_실행에_없는_칸은_None_과_까닭() -> None:
    c = _by(sb.rows([_월실행()]), "composite")
    for col in ("control_lower", "gate_lower", "half_first_cagr", "half_second_mdd", "walk", "capacity", "buys",
                "control_blocks", "gate_blocks", "sells_scored", "distinct_stocks", "closed_trades"):
        assert c[col] is None and c["missing"][col], col
    assert "결정 원장" in c["missing"]["control_lower"]
    assert "반 나눈 기간" in c["missing"]["half_first_cagr"]
    assert c["missing"]["walk"] == sb.WHY_WALK and c["missing"]["capacity"] == sb.WHY_CAPACITY


def test_None_인_칸은_모두_까닭이_있고_값이_있는_칸은_까닭이_없다() -> None:
    rows = sb.rows([_월실행(), _핵심실행(), _핵심실행("backtest_explore", 19)])
    for r in rows:
        for k, v in r.items():
            if k in ("missing", "warnings", "is_trial", "survivorship", "runs"):
                continue
            if v is None:
                assert r["missing"].get(k), (r["strategy"], k)
            else:
                assert k not in r["missing"], (r["strategy"], k)


def test_비용_사본_없는_행과_기준_행() -> None:
    rows = sb.rows([_월실행()])
    assert "composite_no_cost" not in {r["strategy"] for r in rows}  # 사본은 줄로 내지 않는다
    v = _by(rows, "value")
    assert v["cost_share"] is None and "비용 0 사본 없음" in v["missing"]["cost_share"]
    assert v["dsr"] is None and v["missing"]["dsr"] == "판정 안 함(월 20개 < 24)"  # 판정 안 한 문장이 까닭
    assert v["months"] == 20 and v["universe"] == "today" and v["survivorship"] is False
    b = _by(rows, "benchmark")
    assert b["excess_cagr"] is None and b["missing"]["excess_cagr"] == "기준 전략 자체"
    assert b["cohort"] is None and not b["is_trial"] and "시도 아님" in b["missing"]["cohort"]
    assert b["dsr"] is None and "운 판정에 넣지 않는다" in b["missing"]["dsr"]


def test_비용_0_CAGR_이_0_이하면_몫_없음() -> None:
    run = _월실행()
    run["results"][1] = _행("composite_no_cost", {"cagr": -0.01})
    c = _by(sb.rows([run]), "composite")
    assert c["cost_drag"] == pytest.approx(-0.13) and c["cost_share"] is None
    assert "몫 정의 안 됨" in c["missing"]["cost_share"]


def test_핵심_실행_칸() -> None:
    rows = sb.rows([_핵심실행()])
    h = _by(rows, "core_h")
    assert (h["cohort"], h["trials_cumulative"], h["dsr_n"], h["dsr"]) == ("1b", 17, 17, 0.91)
    assert h["control_lower"] == 52.5 and h["gate_lower"] == 48.0
    assert (h["buys"], h["control_blocks"], h["gate_blocks"], h["sells_scored"]) == (40, 9, 7, 30)
    assert (h["half_first_cagr"], h["half_first_mdd"], h["half_second_cagr"], h["half_second_mdd"]) == (
        0.10, -0.20, -0.05, -0.30)  # fmt: skip
    assert h["turnover"] == 0.05 and h["avg_holdings"] == 4.2
    r1 = _by(rows, "core_r1")
    assert r1["cohort"] is None and not r1["is_trial"]
    assert r1["control_lower"] is None and "묶음 3개" in r1["missing"]["control_lower"]
    assert r1["gate_lower"] is None and "묶음 2개" in r1["missing"]["gate_lower"]
    assert r1["half_first_cagr"] is None and "split_half 없음" in r1["missing"]["half_first_cagr"]


# ---------------------------------------------------------------------------
# 코호트 (funnel.md 2.1)
# ---------------------------------------------------------------------------
def test_코호트_세_작업() -> None:
    assert _by(sb.rows([_월실행()]), "composite")["cohort"] == "1"
    assert _by(sb.rows([_핵심실행()]), "core_h")["cohort"] == "1b"
    d = _by(sb.rows([_핵심실행("backtest_explore", 19)]), "core_d")
    assert d["cohort"] == "2" and d["trials_cumulative"] == 19  # params.trial_floor


def test_코호트_밖() -> None:
    assert sb.cohort("backtest", "composite", {"default": True}, "2026-11-02T23:59:00") == (
        None, f"사전 등록({sb.COHORT1_FROM}) 전 실행 — 코호트 밖")  # fmt: skip
    c, why = sb.cohort("backtest", "composite", {"default": False}, "2026-11-03T17:11:00")
    assert c is None and "기본 파라미터" in str(why)
    c, why = sb.cohort("backtest", "composite", {}, "2026-11-03T17:11:00")  # default 칸 없는 옛 기록
    assert c is None and "기본 파라미터" in str(why)
    c, why = sb.cohort("paper", "composite", {}, "2026-11-03")
    assert c is None and "코호트 밖 작업" in str(why)
    assert sb.cohort("backtest_core", "core_r2", {}, "2026-11-10")[0] is None


def test_코호트_실행_시각은_started_at_없으면_결과_행() -> None:
    run = _월실행()
    del run["started_at"]  # 결과 행 created_at(11-03 18:00) 로
    assert _by(sb.rows([run]), "composite")["cohort"] == "1"
    run2 = _월실행(started="2026-10-01T17:11:00+00:00")
    c = _by(sb.rows([run2]), "composite")
    assert c["cohort"] is None and "사전 등록" in c["missing"]["cohort"]


def test_작업_이름과_시도_목록이_정의처와_같다() -> None:
    from batch.jobs import backtest as jb
    from batch.jobs import core_backtest as cj
    from batch.services import backtest as bt
    from batch.services import core_strategy as cs

    assert (jb.JOB_NAME, cj.JOB_NAME, cj.EXPLORE_JOB_NAME) == (sb.JOB_MONTHLY, sb.JOB_CORE, sb.JOB_EXPLORE)
    assert len(set(cs.VARIANTS) - sb.NOT_TRIALS) == jb.CORE_REGISTERED_TRIALS
    assert not set(cs.EXPLORE_VARIANTS) & sb.NOT_TRIALS
    assert bt.WARN_SURVIVORSHIP.startswith(sb.SURVIVORSHIP_PREFIX)
    assert jb.WARN_PIT_UNIVERSE.startswith(sb.PIT_PREFIX)
    assert jb.WARN_NO_HISTORICAL_UNIVERSE.startswith(sb.TODAY_UNIVERSE_PREFIX)


# ---------------------------------------------------------------------------
# 열쇠 (전략, 시장, sha) — 가장 늦은 실행 하나
# ---------------------------------------------------------------------------
def test_같은_sha_는_가장_늦은_실행_하나() -> None:
    늦은 = _월실행(started="2026-12-01T17:11:00+00:00")
    늦은["results"][0] = _행("composite", {"cagr": 0.2, "mdd": -0.1}, excess=0.05)
    rows = sb.rows([늦은, _월실행()])  # 순서와 무관
    c = _by(rows, "composite")
    assert c["cagr"] == 0.2 and c["runs"] == 2
    다른 = sb.rows([_월실행(), _월실행(sha="999999999999")])
    assert len([r for r in 다른 if r["strategy"] == "composite"]) == 2


def test_sha_없으면_까닭() -> None:
    c = _by(sb.rows([_월실행(sha=None)]), "composite")
    assert c["sha"] is None and "params.sha 없음" in c["missing"]["sha"]


def test_store_run_이_쓴_행을_그대로_읽는다() -> None:
    """실제 `store_run` 이 쓴 행(JSON 글 열)으로 — 열 이름이 어긋나면 깨진다"""
    from datetime import date, timedelta

    from batch.core import db
    from batch.jobs import backtest as jb
    from batch.services import backtest as bt
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    d0 = date(2024, 1, 1)
    곡선 = [((d0 + timedelta(days=i)).isoformat(), 1 + i / 1000) for i in range(260)]
    r = bt.BacktestResult(curve=곡선, rebalances=[bt.Rebalance(곡선[0][0], {1: 0.5, 2: 0.5}, 1.0, 0.0)],
                          costs=bt.Costs())  # fmt: skip
    s = bt.summarize(r)
    luck = {"months": 30, "n_trials": 17, "n_trials_run": 5, "dsr": 0.5, "luck_pct": 50.0}
    jb.store_run(mem, "KR", "core_b", r, s, 5, "g9", [], "2026-11-10T00:00:00", luck, rebalance="daily",  # type: ignore[arg-type]
                 extra={"split_half": {"first": {"cagr": 0.01, "mdd": -0.02}, "second": {"cagr": 0.03, "mdd": -0.04}},
                        "evidence": {"buys": 3, "n_blocks": 2, "lower": None, "gate": {"n_blocks": 1, "lower": None}}})  # fmt: skip
    행들 = mem.execute("SELECT * FROM backtest_runs").dicts()
    (b,) = sb.rows([{"job_name": "backtest_core", "started_at": "2026-11-10T00:00:00",
                     "step_log": json.dumps({"params": {"trial_floor": 17, "sha": "s"}}), "results": 행들}])  # fmt: skip
    assert b["cohort"] == "1b" and b["cagr"] == s.metrics.cagr and b["mdd"] == s.metrics.mdd
    assert b["invested_share"] == jb.exposure_fields(r)["invested_share"]
    assert b["avg_holdings"] == 2.0 and b["dsr"] == 0.5 and b["half_second_cagr"] == 0.03
    assert b["control_blocks"] == 2 and b["control_lower"] is None
    # 생존편향·유니버스 경고 없이 저장한 행 — store_run 이 붙인 노출 경고(평균 보유 2 < 3)만 그대로 옮긴다
    assert b["survivorship"] is False and b["universe"] is None
    assert b["warnings"] == [jb.exposure_warning("core_b", jb.exposure_fields(r))]


# ---------------------------------------------------------------------------
# 걷는 창
# ---------------------------------------------------------------------------
def test_걷는_창_손_계산() -> None:
    # 2022-11·12 는 남는 가장 이른 달 → 버림. 2023: 0.01×12 = 0.12(+), 2024: 0.02×6 − 0.03×6 = −0.06(−)
    xs = [("2022-11", 0.5), ("2022-12", -0.5)]
    xs += [(f"2023-{m:02d}", 0.01) for m in range(1, 13)]
    xs += [(f"2024-{m:02d}", 0.02 if m <= 6 else -0.03) for m in range(1, 13)]
    w = sb.walk_windows(list(reversed(xs)))  # 순서와 무관
    assert (w["total"], w["positive"], w["dropped"], w["gaps"], w["window"]) == (2, 1, 2, 0, 12)
    a, b = w["windows"]
    assert (a["start"], a["end"], a["months"], a["sign"]) == ("2023-01", "2023-12", 12, 1)
    assert a["excess"] == pytest.approx(0.12)
    assert (b["start"], b["end"], b["sign"]) == ("2024-01", "2024-12", -1)
    assert b["excess"] == pytest.approx(-0.06)


def test_걷는_창_영_부호_빈_달_짧은_계열() -> None:
    w = sb.walk_windows([("2024-01", 0.1), ("2024-03", -0.1), ("2024-04", 0.0)], window=1)
    assert [x["sign"] for x in w["windows"]] == [1, -1, 0] and w["gaps"] == 1 and w["positive"] == 1
    w2 = sb.walk_windows([(f"2024-{m:02d}", 0.01) for m in range(1, 12)])
    assert (w2["total"], w2["dropped"], w2["windows"]) == (0, 11, [])
    with pytest.raises(ValueError):
        sb.walk_windows([], window=0)


# ---------------------------------------------------------------------------
# 수용량
# ---------------------------------------------------------------------------
def test_수용량_손_계산() -> None:
    # 목표 10억: 종목1 비중 0.5 → 5억 ÷ 50억 = 0.1, 종목2 0.3 → 3억 ÷ 10억 = 0.3, 종목3 거래대금 없음
    c = sb.capacity(1e9, {1: 5e9, 2: 1e9}, {1: 0.5, 2: 0.3, 3: 0.2})
    assert c["per_stock"] == {1: pytest.approx(0.1), 2: pytest.approx(0.3)}
    assert c["max"] == pytest.approx(0.3) and c["max_stock"] == 2
    # 가중 = (0.5×0.1 + 0.3×0.3) ÷ (0.5 + 0.3) = 0.14 ÷ 0.8 = 0.175
    assert c["weighted"] == pytest.approx(0.175)
    assert c["missing"] == [3] and c["complete"] is False


def test_수용량_다_찬_경우와_빈_경우() -> None:
    c = sb.capacity(2e8, {1: 1e10, 2: 4e9}, {1: 0.5, 2: 0.5, 3: 0.0})  # 비중 0 종목은 보지 않는다
    # 1억 ÷ 100억 = 0.01, 1억 ÷ 40억 = 0.025 → 가중 0.0175
    assert c["max"] == pytest.approx(0.025) and c["weighted"] == pytest.approx(0.0175) and c["complete"] is True
    e = sb.capacity(1e9, {1: 0.0}, {1: 1.0})
    assert e["max"] is None and e["weighted"] is None and e["missing"] == [1]


# ---------------------------------------------------------------------------
# 순수 모듈 — DB·네트워크 없음
# ---------------------------------------------------------------------------
def test_입출력_import_없음() -> None:
    src = (뿌리 / "batch/services/scoreboard.py").read_text(encoding="utf-8")
    mods: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "json", "typing"}, mods
    # 불러온 뒤에도 DB·HTTP 모듈이 따라 들어오지 않는다(새 프로세스)
    code = ("import sys, batch.services.scoreboard; "
            "print(sorted(m for m in ('batch.core.db', 'batch.core.client', 'requests', 'httpx') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=뿌리, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
