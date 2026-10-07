"""운인가 실력인가 — 백테스트 초과수익이 우연일 확률 (docs/backtest.md 9장, docs/infra.md 25.997).

전략의 **월별 초과수익**(전략 월수익 − 벤치마크 월수익)으로 샤프(정보비율)를 내고, 두 가지로 우연을 따진다.

1. **PSR** (Probabilistic Sharpe Ratio, Bailey·López de Prado 2012) — 표본 길이·왜도·첨도를 감안해
   "참 샤프가 0 보다 클 확률"
   PSR = Φ( (SR − SR*)·√(T−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²) ),  SR* = 0
2. **DSR** (Deflated Sharpe Ratio, 2014) — 같은 실행에서 전략을 N 개 시험했으면 그중 최고가 우연히 나올
   수준만큼 SR* 를 올린다
   SR* = √V · ((1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))),  V = 시험한 전략들의 SR 분산, γ = 오일러 상수 0.5772
   N = 1 이면 SR* = 0 (PSR 과 같다)

화면에는 **우연일 확률 = 1 − DSR** 을 적는다. SR·V 는 월 단위(연환산하지 않는다 — T 가 월 수라 단위를 맞춘다).
"""

from __future__ import annotations

import math
import statistics
from statistics import NormalDist

EULER_GAMMA = 0.5772156649
#: 이보다 짧으면 판정하지 않는다(월 수) — 왜도·첨도가 의미 없다
MIN_MONTHS = 24
_N = NormalDist()


def monthly_returns(curve: list[tuple[str, float]]) -> dict[str, float]:
    """일별 (날짜, 자산) → {YYYY-MM: 그달 수익}. 달의 마지막 값끼리 잇는다. 첫 달은 수익이 없다."""
    month_end: dict[str, float] = {}
    for d, eq in curve:
        month_end[str(d)[:7]] = float(eq)
    months = sorted(month_end)
    return {m: month_end[m] / month_end[p] - 1 for p, m in zip(months, months[1:], strict=False) if month_end[p] > 0}


def excess_series(strategy: dict[str, float], bench: dict[str, float]) -> list[float]:
    return [strategy[m] - bench[m] for m in sorted(strategy) if m in bench]


def moments(xs: list[float]) -> tuple[float, float, float] | None:
    """(SR, 왜도 γ₃, 첨도 γ₄ — 정규분포면 3). 표준편차가 0 이면 None."""
    if len(xs) < 3:
        return None
    mu, sd = statistics.fmean(xs), statistics.pstdev(xs)
    if sd <= 0:
        return None
    skew = sum((x - mu) ** 3 for x in xs) / len(xs) / sd**3
    kurt = sum((x - mu) ** 4 for x in xs) / len(xs) / sd**4
    return mu / sd, skew, kurt


def psr(sr: float, sr_star: float, t: int, skew: float, kurt: float) -> float:
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if t < 2 or denom <= 0:
        return float("nan")
    return _N.cdf((sr - sr_star) * math.sqrt(t - 1) / math.sqrt(denom))


def expected_max_sr(n_trials: int, var_sr: float) -> float:
    """N 개를 시험했을 때 우연히 나오는 최고 SR 의 기대값. N ≤ 1 이면 0."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * (
        (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials) + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def judge(series: dict[str, list[float]]) -> dict[str, dict]:
    """전략 → 월별 초과수익 → 판정. 같은 실행의 전략들을 한꺼번에 넘긴다(N·V 를 그 묶음에서 낸다)."""
    m = {k: moments(v) for k, v in series.items()}
    srs = [x[0] for x in m.values() if x]
    n = len(srs)
    v = statistics.pvariance(srs) if n > 1 else 0.0
    bar = expected_max_sr(n, v)
    out = {}
    for k, xs in series.items():
        mm = m[k]
        if not mm or len(xs) < MIN_MONTHS:
            out[k] = {"months": len(xs), "n_trials": n, "verdict": f"판정 안 함(월 {len(xs)}개 < {MIN_MONTHS})"}
            continue
        sr, skew, kurt = mm
        p0, pd = psr(sr, 0.0, len(xs), skew, kurt), psr(sr, bar, len(xs), skew, kurt)
        out[k] = {
            "months": len(xs), "n_trials": n, "sr_monthly": round(sr, 4), "sr_star": round(bar, 4),
            "psr": None if math.isnan(p0) else round(p0, 4), "dsr": None if math.isnan(pd) else round(pd, 4),
            "luck_pct": None if math.isnan(pd) else round((1 - pd) * 100, 1),
        }  # fmt: skip
    return out


def line(j: dict | None) -> str:
    if not j or j.get("luck_pct") is None:
        return (j or {}).get("verdict") or "판정 없음"
    return (f"우연일 확률 {j['luck_pct']:.0f}% (DSR — 시도 {j['n_trials']}개, {j['months']}개월, "
            f"월 초과 샤프 {j['sr_monthly']:+.2f} · 문턱 {j['sr_star']:.2f})")  # fmt: skip
