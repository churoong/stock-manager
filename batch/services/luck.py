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


def judge(series: dict[str, list[float]], n_floor: int | None = None) -> dict[str, dict]:
    """전략 → 월별 초과수익 → 판정. 같은 실행의 전략들을 한꺼번에 넘긴다(V 를 그 묶음에서 낸다).

    `n_floor` — 시도 수 N 의 하한 (docs/backtest.md 9장, docs/infra.md 25.1358, 23회차 P4). 일부 전략만 돌린 실행
    (`--only-long` 은 N=3)이 같은 전략을 전체 실행보다 덜 "운" 으로 보았다. N = max(이 실행, **이 앱에 등록된
    전략 수**) — 판정은 더 보수적으로만 움직인다. 등록 수는 Bailey·López de Prado 의 누적 시도 수가 아니다(지난 회차에
    탈락한 기법·문턱 변형이 빠짐 — 여전히 적게 센다). V 는 그 실행의 전략들로만 낸다 — 전략 하나면 V=0 이라 하한도
    효과가 없다. 25.1542 에 핵심 H 등록 전략 3 을, 25.1646 에 `high52`·미리 센 `pead_event` 를 더해 하한이 17 이라
    월 예약 기본 실행(10 — 장기 문턱은 사람이 what=장기문턱으로 돌릴 때만, 25.1660)에도 하한이 걸린다
    ("이 실행 10개, 등록 전략 수 하한")"""
    m = {k: moments(v) for k, v in series.items()}
    srs = [x[0] for x in m.values() if x]
    n_run = len(srs)
    n = max(n_run, n_floor or 0)
    v = statistics.pvariance(srs) if n_run > 1 else 0.0
    bar = expected_max_sr(n, v)
    out = {}
    for k, xs in series.items():
        mm = m[k]
        if not mm or len(xs) < MIN_MONTHS:
            # 사유를 가른다 — 24개월이 넘는데 초과수익 변동이 0 이면(샤프 정의 안 됨) "월 30개 < 24" 라는
            # 거짓 문장이었다(25.1763)
            왜 = f"월 {len(xs)}개 < {MIN_MONTHS}" if len(xs) < MIN_MONTHS else "초과수익 변동 0 — 샤프 정의 안 됨"
            out[k] = {"months": len(xs), "n_trials": n, "n_trials_run": n_run, "verdict": f"판정 안 함({왜})"}
            continue
        sr, skew, kurt = mm
        p0, pd = psr(sr, 0.0, len(xs), skew, kurt), psr(sr, bar, len(xs), skew, kurt)
        out[k] = {
            "months": len(xs), "n_trials": n, "n_trials_run": n_run,
            "sr_monthly": round(sr, 4), "sr_star": round(bar, 4),
            "psr": None if math.isnan(p0) else round(p0, 4), "dsr": None if math.isnan(pd) else round(pd, 4),
            "luck_pct": None if math.isnan(pd) else round((1 - pd) * 100, 1),
        }  # fmt: skip
    return out


def line(j: dict | None) -> str:
    if not j or j.get("luck_pct") is None:
        return (j or {}).get("verdict") or "판정 없음"
    # 하한이 걸렸으면 이 실행에서 돈 수를 함께 (25.1358)
    run = j.get("n_trials_run")
    하한 = run is not None and run < j["n_trials"]
    시도 = f"{j['n_trials']}개" + (f"(이 실행 {run}개, 등록 전략 수 하한)" if 하한 else "")
    return (f"우연일 확률 {j['luck_pct']:.0f}% (DSR — 시도 {시도}, {j['months']}개월, "
            f"월 초과 샤프 {j['sr_monthly']:+.2f} · 문턱 {j['sr_star']:.2f})")  # fmt: skip
