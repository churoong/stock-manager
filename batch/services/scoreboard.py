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
- 올림·내림 문턱(funnel.md 3장)은 0 → 1 첫 값만 `stage01` 이 대어 본다(25.1950 — 코호트 줄만). 1 → 2 이후 문턱은
  적용하지 않는다
- DB·네트워크 없음. 표준 라이브러리만 쓴다(시험이 import 를 대조)

새 순수 함수 둘(funnel.md 2.2 "새로"): `walk_windows`(걷는 창 — 12개월 창마다 초과 부호), `capacity`(수용량 —
25.1954 부터 `capacity_profile` 이 리밸런스마다 내어 결과 행에 싣는다).
"""

from __future__ import annotations

import bisect
import json
import math
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
WHY_CAPACITY = "수용량 미저장 — 25.1954 전 실행(결과 행 metrics_json.capacity 없음)"
#: 수용량 단위 금액(25.1954) — 시장 통화로. 비율은 금액에 비례하므로 실전 목표 T 의 비율 = T ÷ 단위 × 실은 값.
#: 미국은 10만 달러(약 1.4억 원) — 시장 통화 거래대금과 같은 통화라 환율이 끼지 않는다
CAPACITY_UNIT = {"KR": 1e8, "US": 1e5}
CAPACITY_UNIT_LABEL = {"KR": "1억 원", "US": "10만 달러"}
#: 20일 평균 거래대금 — 그날 **전** 20 거래일(판단이 본 창, point-in-time). 20일이 안 찬 종목은 재지 못한 종목
CAPACITY_ADV_DAYS = 20
#: 0 → 1 수용량 기준 — 실전 규모에서 거래대금의 1% 이하(funnel.md 3장 `[확인필요]`). 실전 목표 금액(시장 → 금액)은
#: 사용자만 아는 값이라 정해지기 전까지 비어 있다(점검 칸 모름, docs/handoff.md 사용자 결정)
STAGE01_MAX_CAPACITY = 0.01
STAGE01_TARGET: dict[str, float] = {}
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
    """숫자로 — 숫자가 아니거나 **유한하지 않으면**(NaN·무한) None(25.1956). NaN 이 칸에 실리면 step_log 의 JSON
    리터럴 NaN 을 웹이 읽지 못해 판정판 전체가 사라지고, 비교는 늘 거짓이라 점검표가 "모름" 대신 "걸림" 을 냈다"""
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _month_key(v: Any) -> bool:
    """YYYY-MM(뒤에 더 붙어도 앞 일곱 자) — 걷는 창 계열의 달 이름"""
    k = str(v)[:7] if isinstance(v, str) else ""
    return len(k) == 7 and k[4] == "-" and k[:4].isdigit() and k[5:].isdigit() and 1 <= int(k[5:]) <= 12


def _finite(v: Any) -> bool:
    x = _num(v)
    return x is not None and math.isfinite(x)


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
        put("control_p", None, why_ev)
        put("gate_lower", None, why_ev)
    else:
        put("control_lower", _num(ev.get("lower")), f"하한 없음 — 묶음 {ev.get('n_blocks')}개(block_bound 최소 미달)")
        # 한쪽 p 값(25.1951) — 하한과 같은 평균·표준오차·묶음 수로 H0: 평균 ≤ 50. 하한이 없으면(묶음 미달) 없음
        put("control_p", control_p(ev) if _num(ev.get("lower")) is not None else None,
            f"묶음 {ev.get('n_blocks')}개(block_bound 최소 미달) 또는 평균·표준오차 없음")
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

    # 앞·뒤 반(`split_half`) / 걷는 창(25.1947 — 결과 행 `monthly_excess`)
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
    elif mj.get("monthly_excess_error"):
        # 25.1956 — 25.1947 뒤 실행에서 계산이 깨졌다(실패 표지). "25.1947 전 실행" 이 아니다
        why_w, me = f"월 초과 계열 계산 실패 — {mj['monthly_excess_error']}", None
    elif not isinstance(me, list):
        why_w = WHY_WALK
    elif not all(isinstance(x, (list, tuple)) and len(x) == 2 and _month_key(x[0]) and _finite(x[1]) for x in me):
        # 달 이름이 YYYY-MM 이 아니면 `walk_windows` 가 깨져 판정판 전체가 `scoreboard_error` 로 갔다 — 칸 하나만 빠지게
        # (25.1952, 25.1947 교차검증 note). NaN·무한 초과는 창 합을 NaN 으로 만들어 "> 0 아님" 으로 조용히 셌다
        why_w, me = "metrics_json.monthly_excess 를 읽지 못함", None
    else:
        why_w = ""
    w = walk_windows([(str(k), float(v)) for k, v in me]) if why_w == "" and isinstance(me, list) else None
    put("walk", w, why_w)
    put("walk_positive", (w or {}).get("positive"), why_w)
    put("walk_total", (w or {}).get("total"), why_w)

    # 수용량(25.1954) — 리밸런스마다 단위 금액의 가장 빠듯한 종목 비율(`capacity_profile`) 가운데 최대
    cap = mj.get("capacity") if isinstance(mj.get("capacity"), dict) else None
    if name == "benchmark":
        why_c = "기준 전략 — 싣지 않음"
    elif mj.get("capacity_error"):
        why_c = f"수용량 계산 실패 — {mj['capacity_error']}"  # 25.1956 실패 표지 — "25.1954 전" 이 아니다
    elif mj.get("capacity_missing"):
        why_c = f"수용량 재지 못함 — {mj['capacity_missing']}"  # 25.1958 — 단위 없는 시장·거래대금 없음(실패가 아니다)
    elif cap is None:
        why_c = WHY_CAPACITY
    elif not cap.get("rebalances") and not cap.get("incomplete"):
        why_c = "보유 없는 실행 — 잴 날이 없다"
    else:
        why_c = "재지 못함 — 보유 종목의 그날 전 20일 거래대금 없음"
    put("capacity", _num((cap or {}).get("max")), why_c)
    # 하한인가(25.1956) — 재지 못한 종목이 있는 날이 있으면 max 는 하한이다. 화면이 "≥" 를 붙인다
    # 수용량이 비면 같은 까닭(25.1958 — 웹이 "수용량·수용량 하한 여부 — 까닭" 한 줄로 묶는다)
    put("capacity_complete", None if out["capacity"] is None else bool(cap.get("complete")), why_c)
    if cap is not None:
        out["capacity_profile"] = cap  # 점검표(`_capacity_ok`)가 complete 를 본다

    # 표본(개월·28일 묶음·서로 다른 종목·끝난 거래)
    # 숫자로 고정해 싣는다 — 점검표(`_num`)와 화면(z.number)이 같은 값을 본다(25.1955)
    put("months", _num((luck or {}).get("months")), "luck.months 없음 — 운 판정 없는 행")
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


def _betacf(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타의 연분수(수정 렌츠법, Numerical Recipes 6.4 `betacf`)"""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-14:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타 I_x(a, b)"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(ln) * _betacf(a, b, x) / a
    return 1.0 - math.exp(ln) * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: int) -> float:
    """t 분포 위쪽 꼬리 P(T > t) — P(|T| > t) = I_{df/(df+t²)}(df/2, 1/2) (25.1951, scipy 없이 — 시험이 t 표와 대조)"""
    if df < 1:
        raise ValueError("자유도는 1 이상")
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    two = _betai(df / 2.0, 0.5, df / (df + t * t))
    return two / 2.0 if t >= 0 else 1.0 - two / 2.0


def control_p(ev: dict[str, Any]) -> float | None:
    """무작위 대조 분위의 한쪽 p — H0: 평균 ≤ 50, (평균 − 50) ÷ 표준오차 를 자유도 묶음 − 1 의 t 로 (하한
    `pp.block_bound` 와 같은 재료). 표준오차 0 이면 평균이 50 을 넘으면 0, 아니면 1. 재료가 없으면 None"""
    m, se, n = _num(ev.get("mean")), _num(ev.get("se")), _num(ev.get("n_blocks"))
    if m is None or se is None or n is None or n < 2:
        return None
    if se <= 0:
        return 0.0 if m > 50 else 1.0
    return t_sf((m - 50.0) / se, int(n) - 1)


def bh_q(ps: list[float | None], m: int | None = None) -> list[float | None]:
    """벤야미니–호흐베르크 q — p 들(None 은 건너뜀)을 m 가설(기본 = p 수)로. q_(i) = min_{j≥i} m·p_(j)/j, 1 로 자름"""
    idx = sorted((p, i) for i, p in enumerate(ps) if p is not None)
    m = len(idx) if m is None else m
    out: list[float | None] = [None] * len(ps)
    run = 1.0
    for rank in range(len(idx), 0, -1):
        p, i = idx[rank - 1]
        run = min(run, m * p / rank)
        out[i] = run
    return out


#: 0 → 1 (리플레이에서 그림자로) 기준 — funnel.md 3장 "첫 값 — 보수적". 모두 `[확인필요]`(첫 값, 결과 전에 고정 —
#: 바꾸면 새 판 등록). 판정판은 칸만 대어 본다 — 올림은 사람이(그림자 개설) 정한다
STAGE01_MIN_MONTHS = 36  # 판정 구간 36개월 이상(`luck.MIN_MONTHS` 24 는 수백 후보에 짧다)
STAGE01_MIN_INVESTED = 0.5  # 투자 비중
STAGE01_MIN_HOLDINGS = 3.0  # 평균 보유 종목 수
STAGE01_MIN_CONTROL_LOWER = 50.0  # 무작위 대조 분위 묶음 하한
STAGE01_MIN_DSR = 0.90  # DSR(N = 누적)
STAGE01_WALK = (3, 4)  # 걷는 창 "넷 가운데 셋" — 창이 넷 이상이고 > 0 인 창의 몫이 3/4 이상
STAGE01_MDD_SLACK = 0.05  # MDD 가 기준 B 보다 5%p 넘게 나쁘지 않음
STAGE01_MAX_BH_Q = 0.10  # 대조 분위 p 의 코호트 BH q(거르는 단계라 FDR)
#: 0 → 1 을 대어 볼 기준 줄 — 같은 실행의 기준 B(`core_b`), 없으면(월 실행) 동일가중 기준(`benchmark`) `[확인필요]`
STAGE01_BASE = ("core_b", "benchmark")
#: 코호트 → 등록 시도 수(BH 의 m, 25.1956) — 1b = 핵심 시도(H·G·B — 무작위 대조 R1·R2 는 시도 아님), 2 = 탐색 변형
#: (`core_strategy.EXPLORE_VARIANTS`). 코호트 1(월 실행)은 결정 단위 증거가 없어 p 가 늘 없다.
#: 시험이 jobs 상수와 대조한다
COHORT_SIZE = {"1b": 3, "2": 4}
#: BH 묶음 밖 — 코호트 없는 줄(25.1951). m 은 위 `COHORT_SIZE`(25.1956 — 예전엔 판정판에 든 줄 수라 일부 변형만 돈
#: 실행이 m 을 줄였다). 실행 여럿을 모은 판정판(sha·판 단위 묶기)은 그 판정판이 생길 때 다시 정한다
WHY_BH_NO_COHORT = "코호트 밖 — BH 묶음 없음"


def stage01(row: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any] | None:
    """판정판 한 줄 → 0 → 1 점검표 {verdict, checks: [{key, ok, now, target}], failed, unknown} (funnel.md 3장).

    `ok` = True·False·None(모름 — 칸이 비었거나 재료 미저장). 하나라도 False 면 "탈락", 모두 True 면 "후보",
    아니면 "모름". 시도 아닌 줄(`NOT_TRIALS`)·코호트 밖 줄(cohort None, 25.1953)은 None. 앞·뒤 반은 반 기간
    CAGR > 0 으로 본다 `[확인필요]`(초과가 아니라 절대 — `split_half` 가 싣는 값). 한 코호트 10개 상한은 여러 줄을
    보는 일이라 여기서 세지 않는다"""
    if row.get("strategy") in NOT_TRIALS or row.get("cohort") is None:
        return None

    def ok(v: Any, f: Any) -> bool | None:
        # 숫자가 아니면(옛·손상 기록) 모름 — 비교가 TypeError 로 판정판 전체를 깨지 않게(25.1953)
        x = _num(v)
        return None if x is None or not math.isfinite(x) else bool(f(x))  # NaN 은 어떤 비교든 거짓 — 걸림이 아니라 모름

    wp, wt = row.get("walk_positive"), row.get("walk_total")
    hf, hs = row.get("half_first_cagr"), row.get("half_second_cagr")
    bm = (base or {}).get("mdd")
    mdd = row.get("mdd")
    checks = [
        ("months", "판정 구간", row.get("months"), f">= {STAGE01_MIN_MONTHS}개월",
         ok(row.get("months"), lambda v: v >= STAGE01_MIN_MONTHS)),
        ("invested_share", "투자 비중", row.get("invested_share"), f">= {STAGE01_MIN_INVESTED}",
         ok(row.get("invested_share"), lambda v: v >= STAGE01_MIN_INVESTED)),
        ("avg_holdings", "평균 보유", row.get("avg_holdings"), f">= {STAGE01_MIN_HOLDINGS:g}종목",
         ok(row.get("avg_holdings"), lambda v: v >= STAGE01_MIN_HOLDINGS)),
        ("excess_cagr", "초과 CAGR", row.get("excess_cagr"), "> 0", ok(row.get("excess_cagr"), lambda v: v > 0)),
        ("control_lower", "대조 하한", row.get("control_lower"), f"> {STAGE01_MIN_CONTROL_LOWER:g}",
         ok(row.get("control_lower"), lambda v: v > STAGE01_MIN_CONTROL_LOWER)),
        ("bh_q", "코호트 BH q", row.get("bh_q"), f"<= {STAGE01_MAX_BH_Q}",
         ok(row.get("bh_q"), lambda v: v <= STAGE01_MAX_BH_Q)),
        ("dsr", "DSR", row.get("dsr"), f">= {STAGE01_MIN_DSR}", ok(row.get("dsr"), lambda v: v >= STAGE01_MIN_DSR)),
        ("half", "앞뒤 반", None if hf is None or hs is None else [hf, hs], "둘 다 > 0",
         None if hf is None or hs is None else hf > 0 and hs > 0),
        ("walk", "걷는 창", None if wp is None or wt is None else [wp, wt],
         f"창 {STAGE01_WALK[1]}개 이상, > 0 몫 {STAGE01_WALK[0]}/{STAGE01_WALK[1]} 이상",
         None if wp is None or wt is None
         else wt >= STAGE01_WALK[1] and wp * STAGE01_WALK[1] >= STAGE01_WALK[0] * wt),
        ("mdd_vs_base", "MDD(기준 대비)", mdd, f">= 기준 MDD − {STAGE01_MDD_SLACK:g}",
         None if mdd is None or bm is None else mdd >= bm - STAGE01_MDD_SLACK),
        ("capacity", "수용량", row.get("capacity"), f"실전 목표에서 <= 거래대금의 {STAGE01_MAX_CAPACITY:.0%}",
         _capacity_ok(row)),
    ]  # fmt: skip
    out = [{"key": k, "label": lab, "now": now, "target": tgt, "ok": o} for k, lab, now, tgt, o in checks]
    return {**stage01_verdict(out), "checks": out, "base": (base or {}).get("strategy")}


def _capacity_ok(row: dict[str, Any]) -> bool | None:
    """실전 목표(`STAGE01_TARGET[시장]`)의 최대 비율 ≤ 1% — 목표가 정해지기 전(사용자 결정)이나 재지 못했으면 모름"""
    target, unit, cap = STAGE01_TARGET.get(str(row.get("market"))), CAPACITY_UNIT.get(str(row.get("market"))), _num(
        row.get("capacity"))
    if target is None or unit is None or cap is None or not (row.get("capacity_profile") or {}).get("complete", False):
        return None
    return target / unit * cap <= STAGE01_MAX_CAPACITY


def capacity_profile(rebalances: list[tuple[str, dict[int, float]]], turnover: dict[int, dict[str, float]],
                     unit: float, days: int = CAPACITY_ADV_DAYS) -> dict[str, Any]:  # fmt: skip
    """리밸런스마다(그날 체결 뒤 비중) 단위 금액 `unit` 의 `capacity` 최대 비율 → {unit, days, rebalances, max,
    max_day, max_stock, p90, median, incomplete, complete} (25.1954, funnel.md 2.2·3장).

    20일 평균 거래대금 = 그 종목의 그날 **전** `days` 개 거래대금 평균(판단이 본 창 — 그날 값은 쓰지 않는다). 그만큼 안
    찬 종목은 재지 못한 종목(`capacity.missing`) — 그날 최대는 하한이 되고 `incomplete` 에 센다. `complete` = 재지 못한
    날이 없다. 비율은 금액에 비례하므로 다른 금액은 곱해서 낸다"""
    키: dict[int, list[str]] = {}
    누적: dict[int, list[float]] = {}
    # 리밸런스에 든 종목만 정렬·누적합을 만든다(25.1956 — 유니버스 전체를 전략마다 다시 정렬해 호출마다 몇 초)
    든 = {sid for _d, w in rebalances for sid, x in w.items() if x and x > 0}
    for sid, by in ((s, turnover[s]) for s in 든 if s in turnover):
        xs = sorted((d, float(v)) for d, v in by.items() if v is not None)
        키[sid] = [d for d, _v in xs]
        acc = [0.0]
        for _d, v in xs:
            acc.append(acc[-1] + v)
        누적[sid] = acc

    def adv(sid: int, d: str) -> float | None:
        if sid not in 키:
            return None
        k = bisect.bisect_left(키[sid], d)
        return (누적[sid][k] - 누적[sid][k - days]) / days if k >= days else None

    날: list[tuple[float, str, int]] = []
    모자람 = 0
    for d, w in rebalances:
        held = {sid: x for sid, x in w.items() if x and x > 0}
        if not held:
            continue
        c = capacity(unit, {sid: a for sid in held if (a := adv(sid, d)) is not None}, held)
        모자람 += bool(c["missing"])
        if c["max"] is not None:
            날.append((c["max"], d, c["max_stock"]))
    if not 날:
        return {"unit": unit, "days": days, "rebalances": 0, "max": None, "max_day": None, "max_stock": None,
                "p90": None, "median": None, "incomplete": 모자람, "complete": False}  # fmt: skip
    xs = sorted(x for x, _d, _s in 날)
    top = max(날)

    def 분위(q: float) -> float:
        return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]

    return {"unit": unit, "days": days, "rebalances": len(날), "max": top[0], "max_day": top[1], "max_stock": top[2],
            "p90": 분위(0.9), "median": 분위(0.5), "incomplete": 모자람, "complete": 모자람 == 0}  # fmt: skip


def stage01_verdict(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """점검 칸들 → {verdict, failed, unknown} — 하나라도 False 면 "탈락", 모두 True 면 "후보", 아니면 "모름"."""
    failed = [c["label"] for c in checks if c["ok"] is False]
    unknown = [c["label"] for c in checks if c["ok"] is None]
    return {"verdict": "탈락" if failed else ("후보" if not unknown else "모름"), "failed": failed, "unknown": unknown}


def stage01_note(st: dict[str, Any]) -> str:
    """점검표 → 한 줄 글 — "걸림: DSR·걷는 창 · 재료 없음: 수용량" (판정판 실행 기록 칸). 판정 "모름" 과 겹쳐 읽히지
    않게 모르는 칸은 "재료 없음" 으로 적는다(25.1953)"""
    parts = []
    if st["failed"]:
        parts.append(f"걸림: {'·'.join(st['failed'])}")
    if st["unknown"]:
        parts.append(f"재료 없음: {'·'.join(st['unknown'])}")
    return " · ".join(parts) or "모든 기준 통과"


def rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """실행 기록들 → 판정판 줄들. (전략, 시장, sha, 작업, 코호트) 마다 가장 늦은 실행 하나, 정렬은 (시장, 코호트, 전략).

    같은 열쇠의 실행 수는 `runs` 칸 — 같은 커밋으로 다시 돌린 실행은 새 시도가 아니라 같은 줄이다.
    """
    best: dict[tuple[Any, ...], tuple[tuple[str, str], dict[str, Any]]] = {}
    seen: dict[tuple[Any, ...], int] = {}
    기준들: dict[int, dict[str, Any] | None] = {}
    for run in runs:
        results = [r for r in run.get("results") or [] if isinstance(r, dict)]
        twins = {
            str(r["strategy"])[: -len(NO_COST_SUFFIX)]: r for r in results
            if str(r.get("strategy") or "").endswith(NO_COST_SUFFIX)
        }  # fmt: skip
        t = _run_time(run)
        줄들 = [_row(run, r, twins) for r in results if not str(r.get("strategy") or "").endswith(NO_COST_SUFFIX)]
        # 0 → 1 점검표(25.1950)의 MDD 기준 — 같은 실행의 기준 줄(`STAGE01_BASE` 앞쪽부터)
        by = {x["strategy"]: x for x in 줄들}
        base = next((by[b] for b in STAGE01_BASE if b in by), None)
        for row in 줄들:
            기준들[id(row)] = base
            # 작업 이름·코호트도 열쇠 — 같은 sha 로 돈 `backtest_limit`(코호트 밖)이 `backtest_core`(1b) 줄을, 비기본 월
            # 실행이 기본(코호트 1) 줄을 덮었다(25.1922, 25.1919 교차검증 [권고])
            key = (row["market"], row["strategy"], row["sha"], row["job_name"], row["cohort"])
            seen[key] = seen.get(key, 0) + 1
            order = (t, str(row.get("created_at") or ""))
            if key not in best or order > best[key][0]:
                best[key] = (order, row)
    out = []
    for key, (_order, row) in best.items():
        row["runs"] = seen[key]
        out.append(row)
    # 코호트 BH q(25.1951) — 같은 (시장, 코호트) 시도 줄들의 대조 분위 p. m 은 등록 코호트 크기(`COHORT_SIZE`,
    # 25.1956)와 그 묶음의 시도 줄 수 가운데 큰 쪽(p 없는 줄·빠진 변형도 가설로 센다)
    묶음: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in out:
        if row["is_trial"] and row["cohort"] is not None:
            묶음.setdefault((row["market"], row["cohort"]), []).append(row)
    for row in out:
        row["bh_q"] = None
    for 줄들 in 묶음.values():
        # m = 등록 코호트 크기(funnel.md 2.1 "m 은 등록할 때 고정 — 그날 줄 수로 움직이지 않게", 25.1915) — 일부 변형만
        # 돈 실행이 m 을 줄여 덜 보수적으로 거르던 것(25.1956, 25.1951 교차검증 [권고]). 빠진 변형은 p = 1 과 같다.
        # 등록 크기를 모르는 코호트는 그 줄 수
        m = max(COHORT_SIZE.get(str(줄들[0]["cohort"]), 0), len(줄들))
        for row, q in zip(줄들, bh_q([x.get("control_p") for x in 줄들], m=m), strict=True):
            row["bh_q"] = q
    for row in out:
        if row["bh_q"] is None:
            밖 = row["cohort"] is None or not row["is_trial"]
            p_why = row["missing"].get("control_p", "")
            row["missing"]["bh_q"] = WHY_BH_NO_COHORT if 밖 else f"대조 분위 p 없음 — {p_why}"  # p_why: 묶음 미달 등
        st = stage01(row, 기준들.get(id(row)))
        row["stage01"] = st
        if st is None:
            row["stage01_verdict"] = row["stage01_note"] = None
            why = "기준·무작위 대조 — 시도 아님" if not row["is_trial"] else "코호트 밖 — 0→1 점검 안 함"
            for c in ("stage01", "stage01_verdict", "stage01_note"):
                row["missing"][c] = why
        else:
            row["stage01_verdict"], row["stage01_note"] = st["verdict"], stage01_note(st)
    return sorted(out, key=lambda x: (str(x["market"]), str(x["cohort"] or "~"), x["strategy"]))


#: 실행 기록(`step_log["scoreboard"]`)에 싣는 칸 — 웹 /backtest "판정판" 이 보이는 칸만(25.1940). 실행 하나의 줄이라
#: sha·작업 이름·묶음은 같은 step_log 의 `params`·`group_id` 에 이미 있어 줄마다 되풀이하지 않는다.
#: `runs` 는 늘 1 이라 뺀다.
#: 걷는 창은 창 수·초과 > 0 창 수 두 칸으로 싣는다(25.1947 — 창마다의 기간·합은 결과 행의 계열로 다시 낼 수 있다).
#: 수용량은 단위 금액당 최대 비율 하나만(25.1954 — 분위·그날·종목은 결과 행 `metrics_json.capacity`).
#: 늘 비는 칸(서로 다른 종목·끝난 거래)은 뺀다 — 까닭은 이 모듈의 `WHY_*` 그대로이고 웹이 한 줄로 적는다.
#: 웹 `lib/scoreboard.ts` 의 `SCOREBOARD_KEYS` 와 같아야 한다(시험이 대조)
STEP_LOG_COLS = (
    "strategy", "market", "cohort", "trials_cumulative", "cagr", "excess_cagr", "mdd", "dsr", "dsr_n",
    "control_lower", "gate_lower", "half_first_cagr", "half_second_cagr", "walk_positive", "walk_total", "months",
    "rebalances", "buys", "control_blocks", "gate_blocks", "sells_scored", "bh_q", "capacity", "capacity_complete",
    "stage01_verdict", "stage01_note",
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
