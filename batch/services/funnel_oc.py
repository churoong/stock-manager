"""깔때기 작동특성 모의실험 — 실력을 아는 합성 전략을 **실제 판정 함수**에 넣어 통과율을 잰다.

docs/funnel.md 3.2, docs/infra.md 25.2276. 2026-11-24 사용자 지시("최고의 실전 자동매매 시스템 … 고도화 아이디어")의
docs/roadmap.md 1장 #1. 문턱이 진짜 실력을 거르는지, 실력 0 을 얼마나 통과시키는지를 숫자로 본다 — 문턱을 다시 등록할
때의 근거이고, 새 사전 등록의 "잡을 수 있는 최소 효과" 칸을 채우는 공통 함수(`min_effect`)다.
**합성 자료만 쓴다**(DB·운영 결과를 읽지 않는다 — 결과를 본 뒤 문턱을 맞추는 길이 아니다).
계산만 한다. 판정 함수·문턱은 그대로 가져다 쓴다(여기서 다시 적지 않는다):

- 0 → 1 의 DSR 칸: `luck.judge`(N 하한) + `scoreboard.STAGE01_MIN_DSR`
- 2 → 3 관문의 결정 단위 선정 칸: `paper.block_bound` + `desk.GATE_POCOCK_ALPHA`·`GATE_SEL_NULL`·`GATE_SEL_MIN_BLOCKS`
- 묶음 하한 판정 일반(사전 등록의 "하한 > 0" 꼴): `paper.block_bound` — 효과 ÷ 묶음 표준편차만으로 정해진다
"""

from __future__ import annotations

import math
import random
from typing import Any

from batch.services import desk, luck, scoreboard
from batch.services import paper as pp

#: 월 초과수익의 표준편차(합성) — DSR 은 샤프만 보므로 값은 결과를 바꾸지 않는다(시험이 확인)
MONTHLY_SD = 0.03
#: 판정 시점(평가 횟수) → 그때 쌓인 28 달력일 묶음 수 — 거래일 252 = 달력 365일로 환산해 내림
TRADING_DAYS, CALENDAR_DAYS = 252, 365


def blocks_at(evaluations: int) -> int:
    """평가 횟수(거래일) → 그때까지 찬 28 달력일 묶음 수(내림)"""
    return int(evaluations * CALENDAR_DAYS / TRADING_DAYS // pp.EVIDENCE_BLOCK_DAYS)


def dsr_pass_rate(ir_annual: float, months: int, n_trials: int, *, cohort: int = 11, rho: float = 0.0,
                  sims: int = 2000, seed: int = 1) -> dict[str, Any]:  # fmt: skip
    """연 정보비율 `ir_annual` 인 전략 하나 + 실력 0 인 전략 (cohort − 1)개를 `luck.judge` 에 넣어, 실력 있는 줄이
    DSR 문턱(`scoreboard.STAGE01_MIN_DSR`)을 넘는 비율과 실력 0 줄이 하나라도 넘는 비율을 낸다.

    `n_trials` 는 N 하한(등록 시도 수), `cohort` 는 한 실행의 전략 수(V 를 내는 묶음 — 월 예약 기본 실행 11),
    `rho` 는 전략들의 월 초과수익이 함께 움직이는 정도(공통 요인 하나 — 0 이면 서로 독립. 같은 유니버스·같은 기준을 쓰는
    전략들은 독립이 아니다: 함께 움직이면 샤프 추정들이 몰려 V 가 줄고 문턱이 낮아진다)
    """
    rng = random.Random(seed)
    mu = ir_annual / math.sqrt(12) * MONTHLY_SD
    공, 제 = math.sqrt(rho), math.sqrt(1 - rho)
    참, 거짓 = 0, 0
    for _ in range(sims):
        # rho = 0 이면 공통 요인을 뽑지 않는다(난수 흐름이 독립 꼴과 같다)
        c = [rng.gauss(0.0, 1.0) for _ in range(months)] if rho else [0.0] * months
        series = {"true": [mu + MONTHLY_SD * (공 * c[t] + 제 * rng.gauss(0.0, 1.0)) for t in range(months)]}
        for k in range(cohort - 1):
            series[f"null{k}"] = [MONTHLY_SD * (공 * c[t] + 제 * rng.gauss(0.0, 1.0)) for t in range(months)]
        j = luck.judge(series, n_floor=n_trials)
        넘음 = {k: (v.get("dsr") is not None and v["dsr"] >= scoreboard.STAGE01_MIN_DSR) for k, v in j.items()}
        참 += 넘음["true"]
        거짓 += any(v for k, v in 넘음.items() if k != "true")
    return {"ir_annual": ir_annual, "months": months, "n_trials": n_trials, "cohort": cohort, "rho": rho, "sims": sims,
            "pass_rate": 참 / sims, "any_null_pass_rate": 거짓 / sims}  # fmt: skip


def gate_pass_rate(mean_pct: float, block_sd: float, *, looks: tuple[int, ...] = desk.GATE_LOOKS, sims: int = 4000,
                   seed: int = 1) -> dict[str, Any]:  # fmt: skip
    """결정 단위 선정 분위의 묶음 평균이 N(`mean_pct`, `block_sd`) 로 쌓일 때, 판정 시점마다 `block_bound` 하한이
    무작위 기준(`desk.GATE_SEL_NULL`)을 넘는 **누적** 통과율. 시점의 묶음 수는 `blocks_at`, 묶음 하한
    `GATE_SEL_MIN_BLOCKS` 에 못 미치는 시점은 판정 불가(통과 아님). `mean_pct` = 50 이면 거짓 통과율이다
    """
    rng = random.Random(seed)
    수 = [blocks_at(n) for n in looks]
    누적 = [0] * len(looks)
    for _ in range(sims):
        값 = {b: rng.gauss(mean_pct, block_sd) for b in range(max(수))}
        넘었나 = False
        for i, n in enumerate(수):
            if not 넘었나 and n >= desk.GATE_SEL_MIN_BLOCKS:
                b = pp.block_bound({k: v for k, v in 값.items() if k < n}, desk.GATE_POCOCK_ALPHA)
                넘었나 = b["lower"] is not None and b["lower"] > desk.GATE_SEL_NULL
            누적[i] += 넘었나
    return {"mean_pct": mean_pct, "block_sd": block_sd, "looks": list(looks), "blocks": 수,
            "cumulative_pass": [x / sims for x in 누적], "sims": sims}  # fmt: skip


def bound_power(effect_sd: float, n_blocks: int, alpha: float, *, sims: int = 4000, seed: int = 1) -> float:
    """묶음 값 ~ N(`effect_sd`, 1) 이 `n_blocks` 개일 때 `block_bound` 하한 > 0 인 비율(검정력). 하한 > 0 은 효과와
    표준편차의 **비**로만 정해지므로 표준편차를 1 로 둔다. 묶음 키는 이웃(연속)이라 이웃 상관 보정도 그대로 탄다"""
    rng = random.Random(seed)
    넘음 = 0
    for _ in range(sims):
        b = pp.block_bound({k: effect_sd + rng.gauss(0.0, 1.0) for k in range(n_blocks)}, alpha)
        넘음 += b["lower"] is not None and b["lower"] > 0
    return 넘음 / sims


def min_effect(n_blocks: int, alpha: float, *, power: float = 0.8, sims: int = 4000, seed: int = 1) -> float | None:
    """검정력 `power` 를 내는 최소 효과(묶음 표준편차의 배수) — 새 사전 등록의 "잡을 수 있는 최소 효과" 칸.
    같은 씨앗의 난수를 쓰면 통과가 효과에 단조라 이분법이 선다. 묶음이 `block_bound` 최소에 못 미치면 None"""
    if bound_power(50.0, n_blocks, alpha, sims=20, seed=seed) < 1.0:
        return None
    낮, 높 = 0.0, 50.0
    for _ in range(24):
        중 = (낮 + 높) / 2
        if bound_power(중, n_blocks, alpha, sims=sims, seed=seed) >= power:
            높 = 중
        else:
            낮 = 중
    return round(높, 3)


#: 표에 싣는 격자 — 결과를 보고 고르지 않게 여기 고정한다(25.2276)
TABLE_IR = (0.0, 0.5, 0.8, 1.2, 1.6, 2.0, 2.5)
TABLE_MONTHS = (60, 120, 180)
TABLE_TRIALS = (18, 60)
#: 함께 움직이는 꼴 한 구획 — 상관 0.5, 가장 짧은 기간·지금 하한에서만(값은 어림 `[확인필요: 운영 전략들의 실제 상관]`)
TABLE_RHO = 0.5
TABLE_GATE_MEAN = (50.0, 53.0, 56.0, 60.0)
TABLE_GATE_SD = (6.0, 10.0)
TABLE_BLOCKS = (6, 9, 12, 13)
TABLE_ALPHA = (0.05, 0.0221, 0.0125)


def table(*, sims: int = 2000, seed: int = 1) -> list[str]:
    """사람이 읽을 표 — DSR 칸(실력 × 기간 × 시도 수), 관문 선정 칸(평균 분위 × 묶음 표준편차), 최소 효과(묶음 × α)"""
    out = [f"[깔때기 작동특성 — 합성 자료 {sims}회, 씨앗 {seed}. 문턱: DSR ≥ {scoreboard.STAGE01_MIN_DSR},"
           f" 관문 α {desk.GATE_POCOCK_ALPHA} × {len(desk.GATE_LOOKS)}시점]",
           "0→1 DSR 칸 — 연 정보비율 | 개월 | 시도 수 N | 통과율 | 실력 0 이 하나라도 통과"]  # fmt: skip
    for months in TABLE_MONTHS:
        for n in TABLE_TRIALS:
            for ir in TABLE_IR:
                r = dsr_pass_rate(ir, months, n, sims=sims, seed=seed)
                out.append(f"  {ir:.1f} | {months} | {n} | {r['pass_rate'] * 100:.1f}%"
                           f" | {r['any_null_pass_rate'] * 100:.1f}%")  # fmt: skip
    out.append(f"0→1 DSR 칸, 전략끼리 상관 {TABLE_RHO} — 칸은 위와 같다")
    for ir in TABLE_IR:
        r = dsr_pass_rate(ir, TABLE_MONTHS[0], TABLE_TRIALS[0], rho=TABLE_RHO, sims=sims, seed=seed)
        out.append(f"  {ir:.1f} | {TABLE_MONTHS[0]} | {TABLE_TRIALS[0]} | {r['pass_rate'] * 100:.1f}%"
                   f" | {r['any_null_pass_rate'] * 100:.1f}%")  # fmt: skip
    out.append("2→3 관문 선정 칸 — 평균 분위 | 묶음 표준편차 | 시점별 묶음 수 | 시점별 누적 통과율")
    for sd in TABLE_GATE_SD:
        for mu in TABLE_GATE_MEAN:
            r = gate_pass_rate(mu, sd, sims=sims * 2, seed=seed)
            누적 = " · ".join(f"{x * 100:.1f}%" for x in r["cumulative_pass"])
            out.append(f"  {mu:.0f} | {sd:.0f} | {r['blocks']} | {누적}")
    out.append("묶음 하한 > 0 판정이 검정력 80% 로 잡는 최소 효과(묶음 표준편차의 배수) — 묶음 수 | α | 최소 효과")
    for n in TABLE_BLOCKS:
        for a in TABLE_ALPHA:
            out.append(f"  {n} | {a} | {min_effect(n, a, sims=sims * 2, seed=seed)}")
    return out
