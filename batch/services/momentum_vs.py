"""42회차 u1 `momentum_vs` — `momentum` 과 같은 종목, 투자 비중 e = min(1, σ_B ÷ σ_S), 나머지 현금
(docs/factors.md 12.45 "u1 `momentum_vs` 사전 등록", 결과 보기 전 — 그 문단이 이 코드보다 먼저).

근거: Barroso·Santa-Clara 2015 JFE · Moreira·Muir 2017 JF · Cederburg 외 2020 JFE(포트폴리오 수준 변동성 스케일링).
**계산만 한다** — DB·시각·jobs 층을 모른다(jobs 를 import 하지 않는다 — 60 관측 하한은 jobs 가 넘긴다).
지금은 꺼진 채다: `STRATEGIES`·판정 기록 잇기는 12월 판정 실행이 닫힌 뒤(12.45 일정).

정한 숫자(12.45 — 자유 숫자 0):
- 창: 가격 창 종목 날짜 **합집합** 가운데 cutoff 이하 마지막 `WINDOW_DAYS`(126) 날. 수익은 그 창 안 이웃한 축 날짜끼리
  (창 첫날은 그 전 축 날짜가 창 밖이라 관측이 없다 — 관측 최대 125, 보수적·글자 그대로)
- σ = 날마다 동일가중 평균 일간 수익의 표본표준편차(`metrics.stdev`, ddof 1). ×√252 는 비율에서 지워져 쓰지 않는다
- 관측에서 빼는 종목-날: 국내 |r| > `KR_DAILY_LIMIT`(0.30), 미국 분할 대기 종목 전부(σ 에서만 — 비중은 P 전체)
- e = 1: σ_S·σ_B 관측 가운데 하나라도 `min_points` 미만, σ_S 가 0·None
- 판정(`judge`): 같은 실행 `momentum` 과 — 탈락 조항(CAGR·샤프·MDD 2%p) · 두 계열(전 구간·실행한 달 뺌) 앞·뒤 반 ·
  스패닝 α. 두 계열·α 를 **스스로** 계산한다(`buffer2n.judge` 를 베끼지 않는다)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from batch.core import markets
from batch.services import backtest as bt
from batch.services import luck
from batch.services import metrics as m

NAME = "momentum_vs"
#: 짝 — 같은 판정 실행의 `momentum`(12.45)
BASE = "momentum"
#: σ 창 — 합집합 축 날짜 수(12.45 "마지막 126일")
WINDOW_DAYS = 126
#: 국내 가격제한폭 — 이보다 큰 하루 수익은 조정 안 된 병합·감자 흔적(high52·signals 와 같은 규칙, 정의처 core/markets)
KR_DAILY_LIMIT = markets.KR_DAILY_LIMIT
#: 판정 표본 하한 — 리밸런스 수(`high52`·`buffer_2n` 관례 36)
JUDGE_MIN_REBALANCES = 36
#: MDD 악화 허용 — 음수 소수 그대로 mdd_vs < mdd_mom − 0.02 이면 탈락(12.45)
JUDGE_MDD_SLACK = 0.02
#: 부동소수 비교 여유 — high52.proximity 와 같은 1e-9(정확히 30% 인 날을 빼지 않는다)
_EPS = 1e-9


# ----------------------------------------------------------------------
# σ 와 비중
# ----------------------------------------------------------------------
def series_of(view: bt.PriceView) -> dict[int, list[tuple[str, float]]]:
    """가격 창(`PriceView`)의 종목별 (날짜, 수정종가) — 날짜 축을 내놓지 않는 창에서 합집합 재료를 꺼낸다"""
    return {sid: view.closes(sid) for sid in view.stocks()}


def date_axis(series: Mapping[int, Iterable[tuple[str, float]]], cutoff: str, days: int = WINDOW_DAYS) -> list[str]:
    """종목 날짜 **합집합** 가운데 cutoff 이하 마지막 `days` 날(오름차순)"""
    if days <= 0:
        raise ValueError("days 는 1 이상이어야 합니다")
    합 = {d for rows in series.values() for d, _c in rows if d <= cutoff}
    return sorted(합)[-days:]


def daily_mean_returns(
    stocks: Iterable[int],
    series: Mapping[int, Iterable[tuple[str, float]]],
    axis: Sequence[str],
    *,
    kr: bool,
    split_pending: Iterable[int] = (),
) -> list[float]:
    """축의 이웃한 날 (전, 그날) 마다 두 날 수정종가가 모두 있는(> 0) 종목들의 일간 수익 산술평균.
    그런 종목이 없는 날은 관측에서 뺀다. 국내는 |r| > `KR_DAILY_LIMIT` 인 그 종목 그날을, 미국은 `split_pending`
    종목 전부를 뺀다(국내에는 분할 대기 제외를 쓰지 않는다 — 12.45 는 미국 규칙)"""
    빼기 = set() if kr else set(split_pending)
    값: dict[int, dict[str, float]] = {}
    for sid in set(stocks):
        if sid in 빼기 or sid not in series:
            continue
        값[sid] = {d: float(c) for d, c in series[sid]}
    out: list[float] = []
    for 전, 날 in zip(axis, axis[1:], strict=False):
        rs: list[float] = []
        for by in 값.values():
            a, b = by.get(전), by.get(날)
            if a is None or b is None or a <= 0 or b <= 0:
                continue
            r = b / a - 1
            if kr and abs(r) > KR_DAILY_LIMIT + _EPS:
                continue
            rs.append(r)
        if rs:
            out.append(sum(rs) / len(rs))
    return out


def exposure(
    sigma_s: float | None, n_s: int, sigma_b: float | None, n_b: int, min_points: int
) -> tuple[float, str | None]:
    """(e, e = 1 로 둔 까닭 또는 None). e = min(1, σ_B ÷ σ_S) — 레버리지 없음"""
    if n_s < min_points or n_b < min_points:
        return 1.0, f"관측 부족(S {n_s}·B {n_b} < {min_points})"
    if sigma_s is None or sigma_s == 0:
        return 1.0, "σ_S 가 0·None"
    if sigma_b is None:
        return 1.0, "σ_B 없음"
    return min(1.0, sigma_b / sigma_s), None


@dataclass
class ScaleTrace:
    """리밸런스 한 번의 기록 — e 와 그 재료"""

    e: float
    sigma_s: float | None
    sigma_b: float | None
    n_s: int
    n_b: int
    axis_days: int
    why_one: str | None = None


def scale(
    picked: Mapping[int, float],
    series: Mapping[int, Iterable[tuple[str, float]]],
    benchmark_ids: Iterable[int],
    cutoff: str,
    *,
    kr: bool,
    min_points: int,
    split_pending: Iterable[int] = (),
    days: int = WINDOW_DAYS,
) -> tuple[dict[int, float], ScaleTrace]:
    """`momentum` 이 고른 P(`picked` — `bt.top_n_equal_weight` 결과)를 종목마다 e/|P| 로. 나머지 1 − e 는 현금.
    `benchmark_ids` = 같은 cutoff 의 `benchmark` 구성(시점 유니버스 안 last_close 있는 종목 전부 — 실현 곡선 아님).
    `min_points` = jobs 층이 넘기는 `MIN_BENCHMARK_POINTS`(60)"""
    series = {sid: list(rows) for sid, rows in series.items()}
    split = set(split_pending)
    axis = date_axis(series, cutoff, days)
    rs = daily_mean_returns(picked, series, axis, kr=kr, split_pending=split)
    rb = daily_mean_returns(benchmark_ids, series, axis, kr=kr, split_pending=split)
    ss, sb = m.stdev(rs), m.stdev(rb)
    e, why = exposure(ss, len(rs), sb, len(rb), min_points)
    weights = {sid: e / len(picked) for sid in picked} if picked else {}
    return weights, ScaleTrace(e=e, sigma_s=ss, sigma_b=sb, n_s=len(rs), n_b=len(rb), axis_days=len(axis), why_one=why)


# ----------------------------------------------------------------------
# 판정
# ----------------------------------------------------------------------
def spanning(r_vs: Sequence[float], r_mom: Sequence[float]) -> dict[str, float | int | None] | None:
    """r_vs = α + β·r_mom 보통 최소제곱. {α, t(보통 표준오차), β, n}. 관측 < 3 이거나 r_mom 분산 0 이면 None(α 못 냄).
    잔차가 0 이면 t 는 None(α 는 낸다)"""
    n = len(r_vs)
    if n != len(r_mom) or n < 3:
        return None
    xm, ym = sum(r_mom) / n, sum(r_vs) / n
    sxx = sum((x - xm) ** 2 for x in r_mom)
    if sxx <= 0:
        return None
    beta = sum((x - xm) * (y - ym) for x, y in zip(r_mom, r_vs, strict=True)) / sxx
    alpha = ym - beta * xm
    ssr = sum((y - alpha - beta * x) ** 2 for x, y in zip(r_mom, r_vs, strict=True))
    se = math.sqrt(ssr / (n - 2) * (1 / n + xm**2 / sxx))
    return {"alpha": alpha, "t": alpha / se if se > 0 else None, "beta": beta, "n": n}


def _한_계열(mv: dict[str, float], mm: dict[str, float]) -> dict[str, Any]:
    """한 계열의 앞·뒤 반(앞 ⌊n/2⌋ 달·뒤 나머지)과 스패닝 α"""
    달 = [k for k in sorted(mv) if k in mm]
    초과 = [mv[k] - mm[k] for k in 달]
    반 = len(초과) // 2
    앞, 뒤 = 초과[:반], 초과[반:]
    return {
        "months": len(달),
        "halves": [sum(앞) / len(앞) if 앞 else None, sum(뒤) / len(뒤) if 뒤 else None],
        "spanning": spanning([mv[k] for k in 달], [mm[k] for k in 달]),
    }


def _exposure_record(exposures: Sequence[float]) -> dict[str, Any]:
    es = list(exposures)
    return {
        "e_mean": sum(es) / len(es) if es else None,
        "e_min": min(es) if es else None,
        "e_below_1": sum(1 for x in es if x < 1),
    }


def judge(
    curve_vs: list[tuple[str, float]],
    curve_mom: list[tuple[str, float]],
    *,
    rebalances: int,
    risk_free_annual: float | None,
    trade_date: str,
    exposures: Sequence[float] = (),
    summarize: Callable[..., bt.Summary] = bt.summarize,
) -> dict[str, Any]:
    """12.45 판정 — 같은 판정 실행의 비용 반영 곡선 둘(`momentum_vs`·`momentum`).

    판정 불가: 리밸런스 < 36 · 지표 빈칸(무위험수익률 없음 포함) · α 못 냄 · 계산 실패.
    탈락 조항: CAGR ≤ · 샤프 ≤ · mdd_vs < mdd_mom − 0.02(음수 그대로).
    계열 = 전 구간, 그리고 `trade_date` 의 달(부분 끝 달)을 뺀 것. 계열마다 결론 = 탈락 조항 없음·앞·뒤 반 양·α > 0
    이면 통과, 아니면 탈락. 두 계열 결론이 같으면 그것, 갈리면 모름. 기록: ē·e 최솟값·e < 1 리밸런스 수"""
    out: dict[str, Any] = {"verdict": "판정 불가", "rebalances": rebalances, "exposure": _exposure_record(exposures)}
    try:
        return _judge(out, curve_vs, curve_mom, rebalances, risk_free_annual, trade_date, summarize)
    except Exception as exc:  # 계산 실패 — 실행은 성공으로 닫고 판정 기회를 쓰지 않는다(12.45)
        out["verdict"] = "판정 불가"
        out["why"] = f"계산 실패: {type(exc).__name__}: {exc}"
        return out


def _judge(
    out: dict[str, Any],
    curve_vs: list[tuple[str, float]],
    curve_mom: list[tuple[str, float]],
    rebalances: int,
    risk_free_annual: float | None,
    trade_date: str,
    summarize: Callable[..., bt.Summary],
) -> dict[str, Any]:
    지표: dict[str, dict[str, float | None]] = {}
    for 이름, curve in ((NAME, curve_vs), (BASE, curve_mom)):
        mt = summarize(bt.BacktestResult(curve=list(curve), rebalances=[], costs=bt.Costs()), None,
                       risk_free_annual=risk_free_annual).metrics  # fmt: skip
        지표[이름] = {"cagr": mt.cagr, "sharpe": mt.sharpe, "mdd": mt.mdd}
    out["metrics"] = 지표
    mv, mm = luck.monthly_returns(curve_vs), luck.monthly_returns(curve_mom)
    끝달 = str(trade_date)[:7]
    계열 = {
        "full": _한_계열(mv, mm),
        "ex_partial": _한_계열({k: v for k, v in mv.items() if k != 끝달}, {k: v for k, v in mm.items() if k != 끝달}),
    }
    out["series"] = 계열
    out["partial_month"] = 끝달
    if rebalances < JUDGE_MIN_REBALANCES:
        out["why"] = f"리밸런스 {rebalances} < {JUDGE_MIN_REBALANCES}"
        return out
    빈칸 = [f"{이름}.{k}" for 이름, x in 지표.items() for k, v in x.items() if v is None]
    빈칸 += [f"{s}.halves" for s, x in 계열.items() if None in x["halves"]]
    if 빈칸 or risk_free_annual is None:
        out["why"] = "지표 빈칸: " + ", ".join(빈칸 or ["무위험수익률"])
        if risk_free_annual is None:
            out["why"] += " — 무위험수익률 설정 없음"
        return out
    못냄 = [s for s, x in 계열.items() if x["spanning"] is None]
    if 못냄:
        out["why"] = "α 를 못 냄: " + ", ".join(못냄)
        return out
    v, o = 지표[NAME], 지표[BASE]
    실패: list[str] = []
    if not v["cagr"] > o["cagr"]:  # type: ignore[operator]
        실패.append(f"CAGR ≤ {BASE}")
    if not v["sharpe"] > o["sharpe"]:  # type: ignore[operator]
        실패.append(f"샤프 ≤ {BASE}")
    if v["mdd"] < o["mdd"] - JUDGE_MDD_SLACK:  # type: ignore[operator]
        실패.append(f"MDD 가 {BASE} 보다 2%p 넘게 나쁨")
    결론: dict[str, str] = {}
    까닭: list[str] = list(실패)
    for s, x in 계열.items():
        앞, 뒤 = x["halves"]
        반_양 = 앞 > 0 and 뒤 > 0
        알파_양 = x["spanning"]["alpha"] > 0
        if not 반_양:
            까닭.append(f"{s}: 앞·뒤 반 월 초과가 모두 양이 아님")
        if not 알파_양:
            까닭.append(f"{s}: 스패닝 α ≤ 0")
        결론[s] = "통과" if not 실패 and 반_양 and 알파_양 else "탈락"
    out["series_verdict"] = 결론
    if 결론["full"] == 결론["ex_partial"]:
        out["verdict"] = 결론["full"]
    else:
        out["verdict"] = "모름"
        까닭.append("두 계열 결론이 갈림")
    if 까닭:
        out["why"] = 까닭
    return out
