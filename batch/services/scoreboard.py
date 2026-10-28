"""전략 깔때기 판정판 — 실행 기록을 읽기만 하는 순수 모듈 (docs/funnel.md 2.2, 기록 25.1919).

입력은 **이미 읽어 온** 백테스트 실행 기록이다. 실행 하나 = 한 `batch_runs` 행과 그 실행의 `backtest_runs` 행들:

    {"job_name": "backtest" | "backtest_core" | "backtest_explore",
     "started_at": "2026-11-03T17:11:00+00:00",        # batch_runs.started_at (없으면 결과 행 created_at)
     "step_log": {... "params": {"sha", "default", "trial_floor", ...}},   # dict 또는 JSON 글
     "results": [backtest_runs 행 — `jobs/backtest.store_run` 이 쓰는 열 그대로
                 (market, strategy, start_date, end_date, rebalances, turnover_avg, excess_cagr,
                  metrics_json, warnings_json, group_id, calc_version, scoring_calc_version, created_at …)]}

`metrics_json`·`warnings_json`·`step_log` 는 JSON 글이어도 이미 푼 값이어도 된다.

출력은 (전략 이름, 시장, 코드 sha, 작업 이름, 코호트) 마다 한 줄(뒤의 둘 — 같은 sha 의 지정가·비기본 실행이 코호트
줄을 덮지 않게, 25.1922). 같은 열쇠의 실행이 여럿이면 가장 늦은 실행 하나를 쓰고 `runs` 에 그 수를
적는다. **저장된 값만 옮긴다**(절대 규칙 — 없는 숫자 생성 금지): 기록에 없는 칸은 None 이고 그 까닭을 `missing[칸]` 에
적는다(None 인 칸은 모두 `missing` 에 까닭이 있다 — 시험이 대조).

- `*_no_cost` 행(비용 0 사본)은 줄로 내지 않는다 — 같은 실행 같은 이름 짝의 비용 몫을 내는 데만 쓴다
- 올림·내림 문턱(funnel.md 3장)은 여기서 적용하지 않는다 — 칸만 낸다
- DB·네트워크 없음. 표준 라이브러리만 쓴다(시험이 import 를 대조)

새 순수 함수 둘(funnel.md 2.2 "새로"): `walk_windows`(걷는 창 — 12개월 창마다 초과 부호), `capacity`(수용량).
"""

from __future__ import annotations

import json
from typing import Any

#: 판정판의 단계 — 백테스트 실행 기록은 모두 0 리플레이(funnel.md 1장)
STAGE_REPLAY = 0

#: 깔때기 코호트(funnel.md 2.1) — 작업 이름 → 코호트
JOB_MONTHLY = "backtest"
JOB_CORE = "backtest_core"
JOB_EXPLORE = "backtest_explore"
COHORT_BY_JOB = {JOB_MONTHLY: "1", JOB_CORE: "1b", JOB_EXPLORE: "2"}
#: 코호트 1 = 2026-11-03 매월 기본 실행부터(사전 등록 — funnel.md 머리). 그 전 월 실행은 코호트 밖
COHORT1_FROM = "2026-11-03"

#: 시도가 아닌 줄 — 기준(벤치마크)·무작위 대조(R1·R2, `jobs/backtest.CORE_REGISTERED_TRIALS` 머리 "시도가 아니다").
#: 시험이 `core_strategy.VARIANTS` 와 대조한다
NOT_TRIALS = frozenset({"benchmark", "core_r1", "core_r2"})
#: 비용 0 사본 꼬리 — `jobs/backtest` 의 `composite_no_cost`
NO_COST_SUFFIX = "_no_cost"

#: 경고 글로 가르는 표시 — 정의처는 `services/backtest.WARN_SURVIVORSHIP`·`jobs/backtest.WARN_PIT_UNIVERSE`·
#: `WARN_NO_HISTORICAL_UNIVERSE`. jobs 를 import 하면 DB 모듈이 따라 들어와 머리글만 적는다(시험이 대조)
SURVIVORSHIP_PREFIX = "생존편향"
PIT_PREFIX = "시점 유니버스:"
TODAY_UNIVERSE_PREFIX = "과거 유니버스 스냅샷 없음"

#: 걷는 창 기본 길이(개월) — funnel.md 3장 "걷는 창 넷 가운데 셋" (약 4년 판정 구간 ÷ 12)
WALK_WINDOW_MONTHS = 12

#: 아직 저장되지 않아 늘 비는 칸의 까닭
WHY_WALK = "월 초과수익 계열 미저장 — 25.1947 전 실행(결과 행 metrics_json.monthly_excess 없음)"
WHY_CAPACITY = "목표 금액·보유 종목 20일 평균 거래대금 미저장 — 실행 기록에 보유 내역이 없다"
WHY_DISTINCT = "서로 다른 종목 수 미저장 — 증거의 buys 는 첫 매수 수(같은 종목 되풀이 포함)"
WHY_CLOSED = "끝난 거래 수 미저장 — 판 뒤(after_sell)의 sells 는 20 거래일이 찬 매도 수다"


def _parse(v: Any) -> Any:
    """JSON 글이면 풀고 아니면 그대로. 빈 값은 None"""
    if v is None or v == "":
        return None
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run_time(run: dict[str, Any]) -> str:
    """실행 시각 — batch_runs.started_at, 없으면 결과 행 created_at 가운데 가장 늦은 것"""
    t = run.get("started_at")
    if t:
        return str(t)
    return max((str(r.get("created_at") or "") for r in run.get("results") or []), default="")


def cohort(job_name: str | None, strategy: str, params: dict[str, Any], run_time: str) -> tuple[str | None, str | None]:
    """(코호트, None) 또는 (None, 까닭) — funnel.md 2.1.

    - `backtest` 기본 파라미터 실행(`params.default`)으로 2026-11-03 부터 → "1"
    - `backtest_core` → "1b"(핵심 H·G·B — 시도 수는 `CORE_REGISTERED_TRIALS` 로 이미 하한 안)
    - `backtest_explore` → "2"(탐색 변형, 시도 수 하한 `params.trial_floor`)
    - 기준·무작위 대조(`NOT_TRIALS`)는 시도가 아니라 코호트 없음
    """
    if strategy in NOT_TRIALS:
        return None, "기준·무작위 대조 — 시도 아님"
    c = COHORT_BY_JOB.get(job_name or "")
    if c is None:
        return None, f"깔때기 코호트 밖 작업({job_name})"
    if c == "1":
        if params.get("default") is not True:
            return None, "기본 파라미터 실행 아님 — 판정 코호트 밖(docs/factors.md 12장 머리)"
        if not run_time or run_time[:10] < COHORT1_FROM:
            return None, f"사전 등록({COHORT1_FROM}) 전 실행 — 코호트 밖"
    return c, None


def _row(run: dict[str, Any], r: dict[str, Any], twins: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """결과 행 하나 → 판정판 한 줄(칸 + `missing`)"""
    step = _parse(run.get("step_log")) or {}
    params = step.get("params") if isinstance(step.get("params"), dict) else {}
    mj = _parse(r.get("metrics_json")) or {}
    warns = _parse(r.get("warnings_json")) or []
    name = str(r.get("strategy"))
    job = run.get("job_name")
    luck = mj.get("luck") if isinstance(mj.get("luck"), dict) else None
    ev = mj.get("evidence") if isinstance(mj.get("evidence"), dict) else None
    gate = (ev or {}).get("gate") if isinstance((ev or {}).get("gate"), dict) else None
    sell = (ev or {}).get("after_sell") if isinstance((ev or {}).get("after_sell"), dict) else None
    half = mj.get("split_half") if isinstance(mj.get("split_half"), dict) else None
    missing: dict[str, str] = {}
    out: dict[str, Any] = {
        "strategy": name, "market": r.get("market"), "sha": params.get("sha"), "job_name": job,
        "group_id": r.get("group_id"), "start_date": r.get("start_date"), "end_date": r.get("end_date"),
        "created_at": r.get("created_at"), "calc_version": r.get("calc_version"),
        "scoring_calc_version": r.get("scoring_calc_version"), "stage": STAGE_REPLAY,
    }  # fmt: skip

    def put(col: str, value: Any, why: str) -> None:
        out[col] = value
        if value is None:
            missing[col] = why

    if out["sha"] is None:
        missing["sha"] = "실행 기록 params.sha 없음(로컬 실행이거나 25.1437 전 기록)"

    # 단계·코호트·누적 시도 번호 (funnel.md 2.1)
    c, why = cohort(job, name, params, _run_time(run))
    put("cohort", c, why or "")
    out["is_trial"] = name not in NOT_TRIALS
    dsr_n = (luck or {}).get("n_trials")
    floor = params.get("trial_floor")
    put("trials_cumulative", floor if floor is not None else dsr_n,
        "params.trial_floor·luck.n_trials 둘 다 없음 — 운 판정 없는 실행")  # fmt: skip

    # 비용 반영 CAGR·초과 CAGR·비용 몫 (`bt.summarize` 가 낸 값 — `metrics_json.cagr`·`excess_cagr` 열)
    cagr = _num(mj.get("cagr"))
    put("cagr", cagr, "metrics_json.cagr 없음(곡선이 짧아 지표 창 표본 미달)")
    put("excess_cagr", _num(r.get("excess_cagr")),
        "기준 전략 자체" if name == "benchmark" else "excess_cagr 열 없음(벤치마크 CAGR 이 비었다)")  # fmt: skip
    twin = twins.get(name)
    if twin is None:
        put("cost_drag", None, "비용 0 사본 없음 — 같은 실행의 `*_no_cost` 짝은 composite 만 돈다")
        put("cost_share", None, "비용 0 사본 없음 — 같은 실행의 `*_no_cost` 짝은 composite 만 돈다")
    else:
        free = _num((_parse(twin.get("metrics_json")) or {}).get("cagr"))
        if free is None or cagr is None:
            put("cost_drag", None, "비용 0 사본 또는 이 행의 CAGR 이 비었다")
            put("cost_share", None, "비용 0 사본 또는 이 행의 CAGR 이 비었다")
        else:
            # 비용 몫 = (비용 0 CAGR − 비용 반영 CAGR) ÷ 비용 0 CAGR — 비용 전 수익 가운데 비용이 가져간 몫
            put("cost_drag", free - cagr, "")
            put("cost_share", (free - cagr) / free if free > 0 else None, "비용 0 CAGR ≤ 0 — 몫 정의 안 됨")

    # MDD·투자 비중·평균 보유 수·회전율 (`bt.summarize`·`exposure_fields`)
    put("mdd", _num(mj.get("mdd")), "metrics_json.mdd 없음")
    put("sharpe", _num(mj.get("sharpe")), "metrics_json.sharpe 없음(무위험수익률 설정 없음 또는 표본 미달)")
    put("invested_share", _num(mj.get("invested_share")), "metrics_json.invested_share 없음(25.786 전 기록)")
    put("avg_holdings", _num(mj.get("avg_holdings")), "metrics_json.avg_holdings 없음(25.786 전 기록)")
    put("turnover", _num(r.get("turnover_avg")), "turnover_avg 열 없음(리밸런스 0회)")

    # 무작위 대조 분위 묶음 하한(`core_evidence.evidence`)·결정 단위 분위 하한(`gate_evidence`
    # = `pp.selection_evidence`)
    if ev is None:
        why_ev = ("월 백테스트는 결정 원장이 없어 결정 단위 판정을 내지 않는다" if job == JOB_MONTHLY
                  else "metrics_json.evidence 없음(판정 실패 또는 25.1544 전 기록)")  # fmt: skip
        put("control_lower", None, why_ev)
        put("gate_lower", None, why_ev)
    else:
        put("control_lower", _num(ev.get("lower")), f"하한 없음 — 묶음 {ev.get('n_blocks')}개(block_bound 최소 미달)")
        if gate is None:
            put("gate_lower", None, "evidence.gate 없음")
        else:
            put("gate_lower", _num(gate.get("lower")),
                f"하한 없음 — 묶음 {gate.get('n_blocks')}개(block_bound 최소 미달)")  # fmt: skip

    # DSR (N = 누적, `luck.judge`) — 판정 안 한 행은 그 문장이 까닭
    if luck is None:
        why_l = "기준 전략은 운 판정에 넣지 않는다" if name == "benchmark" else "metrics_json.luck 없음(25.997 전 기록)"
        put("dsr", None, why_l)
        put("luck_pct", None, why_l)
        put("dsr_n", None, why_l)
    else:
        why_l = str(luck.get("verdict") or "luck.dsr 없음")
        put("dsr", _num(luck.get("dsr")), why_l)
        put("luck_pct", _num(luck.get("luck_pct")), why_l)
        put("dsr_n", dsr_n, "luck.n_trials 없음")

    # 앞·뒤 반(`split_half`) / 걷는 창(아직 계열 미저장)
    why_h = ("월 백테스트는 반 나눈 기간을 싣지 않는다(핵심 H·탐색만)" if job == JOB_MONTHLY
             else "metrics_json.split_half 없음(곡선 3일 미만)")  # fmt: skip
    for part in ("first", "second"):
        p = (half or {}).get(part) if isinstance((half or {}).get(part), dict) else None
        put(f"half_{part}_cagr", _num((p or {}).get("cagr")), why_h if p is None else f"{part} 반 CAGR 표본 미달")
        put(f"half_{part}_mdd", _num((p or {}).get("mdd")), why_h if p is None else f"{part} 반 MDD 표본 미달")
    # 걷는 창(25.1947) — 결과 행에 실린 월 초과 계열(`metrics_json.monthly_excess`)을 `walk_windows` 로
    me = mj.get("monthly_excess")
    if name == "benchmark":
        why_w = "기준 전략 자체 — 초과가 0"
    elif not isinstance(me, list):
        why_w = WHY_WALK
    elif not all(isinstance(x, (list, tuple)) and len(x) == 2 and _num(x[1]) is not None for x in me):
        why_w, me = "metrics_json.monthly_excess 를 읽지 못함", None
    else:
        why_w = ""
    w = walk_windows([(str(k), float(v)) for k, v in me]) if why_w == "" and isinstance(me, list) else None
    put("walk", w, why_w)
    put("walk_positive", (w or {}).get("positive"), why_w)
    put("walk_total", (w or {}).get("total"), why_w)

    # 수용량 — 아직 재료 미저장
    put("capacity", None, WHY_CAPACITY)

    # 표본(개월·28일 묶음·서로 다른 종목·끝난 거래)
    put("months", (luck or {}).get("months"), "luck.months 없음 — 운 판정 없는 행")
    put("rebalances", r.get("rebalances"), "rebalances 열 없음")
    no_ev = "결정 단위 증거 없음(월 백테스트 또는 판정 실패)"
    put("buys", (ev or {}).get("buys"), no_ev)
    put("control_blocks", (ev or {}).get("n_blocks"), no_ev)
    put("gate_blocks", (gate or {}).get("n_blocks"), no_ev)
    put("sells_scored", (sell or {}).get("sells"), no_ev)
    put("distinct_stocks", None, WHY_DISTINCT)
    put("closed_trades", None, WHY_CLOSED)

    # 경고(생존편향·시점 유니버스 — `store_run` 의 warnings_json)
    ws = [str(w) for w in warns] if isinstance(warns, list) else []
    out["warnings"] = ws
    out["survivorship"] = any(w.startswith(SURVIVORSHIP_PREFIX) for w in ws)
    if any(w.startswith(PIT_PREFIX) for w in ws):
        put("universe", "pit", "")
    elif any(w.startswith(TODAY_UNIVERSE_PREFIX) for w in ws):
        put("universe", "today", "")
    else:
        put("universe", None, "경고에 유니버스 표시 없음")

    out["missing"] = missing
    return out


def rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """실행 기록들 → 판정판 줄들. (전략, 시장, sha, 작업, 코호트) 마다 가장 늦은 실행 하나, 정렬은 (시장, 코호트, 전략).

    같은 열쇠의 실행 수는 `runs` 칸 — 같은 커밋으로 다시 돌린 실행은 새 시도가 아니라 같은 줄이다.
    """
    best: dict[tuple[Any, ...], tuple[tuple[str, str], dict[str, Any]]] = {}
    seen: dict[tuple[Any, ...], int] = {}
    for run in runs:
        results = [r for r in run.get("results") or [] if isinstance(r, dict)]
        twins = {
            str(r["strategy"])[: -len(NO_COST_SUFFIX)]: r for r in results
            if str(r.get("strategy") or "").endswith(NO_COST_SUFFIX)
        }  # fmt: skip
        t = _run_time(run)
        for r in results:
            if str(r.get("strategy") or "").endswith(NO_COST_SUFFIX):
                continue
            row = _row(run, r, twins)
            # 작업 이름·코호트도 열쇠 — 같은 sha 로 돈 `backtest_limit`(코호트 밖)이 `backtest_core`(1b) 줄을, 비기본 월
            # 실행이 기본(코호트 1) 줄을 덮었다(25.1922, 25.1919 교차검증 [권고])
            key = (row["market"], row["strategy"], row["sha"], row["job_name"], row["cohort"])
            seen[key] = seen.get(key, 0) + 1
            order = (t, str(r.get("created_at") or ""))
            if key not in best or order > best[key][0]:
                best[key] = (order, row)
    out = []
    for key, (_order, row) in best.items():
        row["runs"] = seen[key]
        out.append(row)
    return sorted(out, key=lambda x: (str(x["market"]), str(x["cohort"] or "~"), x["strategy"]))


#: 실행 기록(`step_log["scoreboard"]`)에 싣는 칸 — 웹 /backtest "판정판" 이 보이는 칸만(25.1940). 실행 하나의 줄이라
#: sha·작업 이름·묶음은 같은 step_log 의 `params`·`group_id` 에 이미 있어 줄마다 되풀이하지 않는다.
#: `runs` 는 늘 1 이라 뺀다.
#: 걷는 창은 창 수·초과 > 0 창 수 두 칸으로 싣는다(25.1947 — 창마다의 기간·합은 결과 행의 계열로 다시 낼 수 있다).
#: 늘 비는 칸(수용량·서로 다른 종목·끝난 거래)은 뺀다 — 까닭은 이 모듈의 `WHY_*` 그대로이고 웹이 한 줄로 적는다.
#: 웹 `lib/scoreboard.ts` 의 `SCOREBOARD_KEYS` 와 같아야 한다(시험이 대조)
STEP_LOG_COLS = (
    "strategy", "market", "cohort", "trials_cumulative", "cagr", "excess_cagr", "mdd", "dsr", "dsr_n",
    "control_lower", "gate_lower", "half_first_cagr", "half_second_cagr", "walk_positive", "walk_total", "months",
    "rebalances", "buys", "control_blocks", "gate_blocks", "sells_scored",
)  # fmt: skip
#: 까닭 글 최대 길이 — step_log 크기를 묶는다(월 실행 줄 약 20개 × 빈 칸 약 10개). 넘는 까닭은 잘린다
WHY_MAX = 80


def step_log_rows(rows_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`rows` 의 줄을 실행 기록에 실을 꼴로 — `STEP_LOG_COLS` 칸 + 그 칸의 `missing` 까닭(`WHY_MAX` 자까지).
    값은 바꾸지 않는다(반올림도 없음)"""
    out = []
    for r in rows_:
        why = r.get("missing") or {}
        줄 = {c: r.get(c) for c in STEP_LOG_COLS}
        줄["missing"] = {c: str(why[c])[:WHY_MAX] for c in STEP_LOG_COLS if c in why}
        out.append(줄)
    return out


def walk_windows(monthly_excess: list[tuple[str, float]], window: int = WALK_WINDOW_MONTHS) -> dict[str, Any]:
    """걷는 창 — 월 초과수익 계열을 **겹치지 않는** `window` 개월 창으로 잘라 창마다 초과 부호 (funnel.md 2.2·3장).

    - `monthly_excess` = [(YYYY-MM, 그달 전략 수익 − 기준 수익)] (`luck.excess_series` 와 같은 뜻, 소수 — 0.01 = 1%p)
    - 달 순으로 정렬해 **가장 늦은 달부터 거꾸로** `window` 개씩 자른다 — 남는 가장 이른 달(< window 개)은 버리고
      `dropped` 에 수를 적는다(최근 구간이 늘 판정에 든다)
    - 창 초과 = 그 창 월 초과의 **합**(산술). 부호 = +1(> 0)·−1(< 0)·0
    - `positive` = 부호 +1 인 창 수, `total` = 창 수. 3장 기준은 "넷 가운데 셋 이상 > 0" `[확인필요]`
    - 달이 비어 있어도(계열에 빠진 달) 개수로 자른다 — 빠진 달 수는 `gaps`(같은 해·달 차로 센 수)에 적는다
    """
    if window < 1:
        raise ValueError("window 는 1 이상")
    xs = sorted((str(k)[:7], float(v)) for k, v in monthly_excess)
    n = len(xs) // window
    dropped = len(xs) - n * window
    창: list[dict[str, Any]] = []
    for i in range(n):
        조각 = xs[dropped + i * window : dropped + (i + 1) * window]
        합 = sum(v for _k, v in 조각)
        창.append({"start": 조각[0][0], "end": 조각[-1][0], "months": len(조각), "excess": 합,
                  "sign": (합 > 0) - (합 < 0)})  # fmt: skip

    def _idx(k: str) -> int:
        return int(k[:4]) * 12 + int(k[5:7]) - 1

    gaps = sum(max(0, _idx(b) - _idx(a) - 1) for (a, _x), (b, _y) in zip(xs, xs[1:], strict=False))
    return {"window": window, "windows": 창, "positive": sum(1 for w in 창 if w["sign"] > 0), "total": len(창),
            "dropped": dropped, "gaps": gaps}  # fmt: skip


def capacity(
    target_krw: float, holdings_avg_value_20d: dict[int, float], weights: dict[int, float]
) -> dict[str, Any]:
    """수용량 — 목표 금액이 보유 종목 20일 평균 거래대금의 몇 배(비율)인가 (funnel.md 2.2·3장).

    종목 i: 들 금액 = `target_krw` × w_i, 비율 r_i = 들 금액 ÷ 20일 평균 거래대금_i (소수 — 0.01 = 1%).
    - `max` = max r_i (가장 빠듯한 종목, `max_stock`)
    - `weighted` = Σ w_i·r_i ÷ Σ w_i (비율을 비중으로 평균 — 잰 종목의 비중 합으로 나눈다)
    - 비중 0 이하 종목은 보지 않는다. 거래대금이 없거나 0 이하인 종목은 `missing` 에 두고 계산에서 뺀다 — 그러면
      `max` 는 잰 종목 가운데 최대라 **하한**이다(`complete` False)
    - 3장 기준은 "실전 규모에서 거래대금의 1% 이하" `[확인필요]`
    """
    per: dict[int, float] = {}
    missing: list[int] = []
    for sid, w in weights.items():
        if w is None or w <= 0:
            continue
        adv = holdings_avg_value_20d.get(sid)
        if adv is None or adv <= 0:
            missing.append(sid)
            continue
        per[sid] = target_krw * w / adv
    if not per:
        return {"per_stock": {}, "max": None, "max_stock": None, "weighted": None, "missing": sorted(missing),
                "complete": False}  # fmt: skip
    top = max(per, key=lambda s: per[s])
    wsum = sum(weights[s] for s in per)
    return {"per_stock": per, "max": per[top], "max_stock": top,
            "weighted": sum(weights[s] * per[s] for s in per) / wsum, "missing": sorted(missing),
            "complete": not missing}  # fmt: skip
