"""43회차 e3 `buffer_erc` — buffer_2n 과 같은 종목에 위험 기여 균등(ERC) 비중 (factors.md 12.46, infra 25.2189).

사전 등록(2026-11-08, 결과 보기 전 — 12.46 문단이 이 코드보다 먼저 커밋됨). 종목은 `buffer2n.pick` 그대로(같은 순위·같은
엔진 보유), **비중만** 1/n 대신 Ledoit·Wolf(2004, JPM 30(4)) 상수 상관 수축 공분산 위의 롱온리 ERC(Maillard·Roncalli·
Teïletche 2010). 계산만 한다 — DB·시각을 모른다.

정한 숫자(12.46 — 자유 숫자 0, 모두 빌린 값이거나 계산 상수):
- 창: 그 나라 엔진 거래일 축에서 cutoff 이하 마지막 `WINDOW_DAYS + 1` 날(수익 126 — signals.md 3.6 상관 창과 같은 수)
- ERC 대상 하한: 유효 수익 `MIN_POINTS`(60 — `report_picks.CORR_MIN_POINTS` 와 같은 값), 공통 날도 같은 하한
- 국내: 창 안에 |일간 수익| > `KR_DAILY_LIMIT`(가격제한 30%) 인 날이 있으면 1/n 몫(high52 선례 — 수정 안 된
  기업행위 하루)
- 풀이: 순환 좌표 하강(Griveau-Billion·Richard·Roncalli 2013), 수렴 = max_i |n·w_i(Σw)_i / wᵀΣw − 1| ≤ `TOL`,
  최대 `MAX_SWEEPS` 바퀴. 못 맞추면 그 리밸런스 전체를 1/n 으로(기록에 센다 — 보수적, 결과를 보고 고치지 않음)
- 상한: max(`CAP`, 1/n). 넘는 몫은 상한에 걸리지 않은 모든 종목에 비중 비례로 다시 나눈다(넘는 종목이 없을 때까지)
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from batch.core import markets

#: 공분산 창 — 일간 수익 수 (signals.md 3.6 의 상관 창과 같은 126)
WINDOW_DAYS = 126
#: ERC 대상·공통 날 하한 (`report_picks.CORR_MIN_POINTS` 와 같은 60)
MIN_POINTS = 60
#: 국내 가격제한폭 — 이보다 큰 하루 수익은 수정 안 된 기업행위로 본다(정의처 core/markets, high52 와 같다)
KR_DAILY_LIMIT = markets.KR_DAILY_LIMIT
#: 종목 상한 — 종목 비중 상한 기본값(CLAUDE.md 매매 규칙 10%)
CAP = 0.10
#: ERC 수렴 허용치와 최대 바퀴 — 판정 문턱이 아니라 계산 상수(12.46 검증 B 제안 값, 결과 전 고정)
TOL = 1e-8
MAX_SWEEPS = 10_000
NAME = "buffer_erc"
BASE = "buffer_2n"


@dataclass
class ErcTrace:
    """리밸런스 한 번의 기록 — 판정에 쓰지 않는 진단(12.46 기록 칸)"""

    n: int = 0
    erc_n: int = 0
    common_days: int = 0
    delta: float | None = None
    converged: bool | None = None
    sweeps: int = 0
    to_equal: list[int] = field(default_factory=list)  # 1/n 몫으로 보낸 종목
    inv_vol_l1: float | None = None


def ledoit_wolf_cc(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """(수축 공분산, δ*). `returns` = T×k(공통 날의 일간 수익). Ledoit·Wolf 2004 상수 상관 목표, 표본 공분산은 1/T,
    강도 δ* = max(0, min(1, κ̂/T)), κ̂ = (π̂ − ρ̂)/γ̂ (원논문 부록 식). γ̂ = 0(표본이 이미 목표)이면 δ* = 0"""
    t, k = returns.shape
    y = returns - returns.mean(axis=0)
    s = y.T @ y / t
    sd = np.sqrt(np.diag(s))
    corr = s / np.outer(sd, sd)
    rbar = (corr.sum() - k) / (k * (k - 1)) if k > 1 else 0.0
    f = rbar * np.outer(sd, sd)
    np.fill_diagonal(f, np.diag(s))
    # π̂: 표본 공분산 원소의 점근 분산 합
    y2 = y**2
    pi_mat = (y2.T @ y2) / t - s**2
    pi_hat = pi_mat.sum()
    # ρ̂: 대각은 π_ii, 비대각은 θ 항
    theta_ii = ((y**3).T @ y) / t - np.diag(s)[:, None] * s  # θ_ii,ij = E[(y_i² − s_ii)(y_i y_j − s_ij)]
    rho_hat = np.trace(pi_mat)
    for i in range(k):
        for j in range(k):
            if i != j:
                rho_hat += rbar / 2 * (math.sqrt(s[j, j] / s[i, i]) * theta_ii[i, j]
                                       + math.sqrt(s[i, i] / s[j, j]) * theta_ii[j, i])  # fmt: skip
    gamma_hat = ((f - s) ** 2).sum()
    if gamma_hat <= 0:
        return s, 0.0
    delta = max(0.0, min(1.0, (pi_hat - rho_hat) / gamma_hat / t))
    return delta * f + (1 - delta) * s, delta


def erc_weights(cov: np.ndarray) -> tuple[np.ndarray, bool, int]:
    """(비중, 수렴, 바퀴). 롱온리 ERC — ½wᵀΣw − Σ(1/k)ln w 의 순환 좌표 하강(해가 유일, 끝에 합 1 로)"""
    k = cov.shape[0]
    b = 1.0 / k
    w = 1.0 / np.sqrt(np.diag(cov))
    w = w / w.sum()
    for sweep in range(1, MAX_SWEEPS + 1):
        for i in range(k):
            c = cov[i] @ w - cov[i, i] * w[i]
            w[i] = (-c + math.sqrt(c * c + 4 * cov[i, i] * b)) / (2 * cov[i, i])
        v = w / w.sum()
        sv = cov @ v
        total = float(v @ sv)
        if total > 0 and float(np.max(np.abs(k * v * sv / total - 1))) <= TOL:
            return v, True, sweep
    return w / w.sum(), False, MAX_SWEEPS


def cap_weights(weights: Mapping[int, float], cap: float) -> dict[int, float]:
    """상한 넘는 몫을 상한에 안 걸린 모든 종목에 비중 비례로 다시 나눈다 — 넘는 종목이 없을 때까지"""
    w = dict(weights)
    for _ in range(len(w) + 1):
        over = {s for s, x in w.items() if x > cap + 1e-15}
        if not over:
            break
        excess = sum(w[s] - cap for s in over)
        for s in over:
            w[s] = cap
        free = [s for s in w if w[s] < cap - 1e-15]
        tot = sum(w[s] for s in free)
        if not free or tot <= 0:
            break
        for s in free:
            w[s] += excess * w[s] / tot
    return w


def _returns(axis: Sequence[str], closes: Mapping[str, float]) -> list[float | None]:
    """축의 이웃한 두 날 모두 종가가 있는 날만 수익(정지로 건너뛴 다일 수익은 쓰지 않는다)"""
    out: list[float | None] = []
    for a, b in zip(axis, axis[1:], strict=False):
        p0, p1 = closes.get(a), closes.get(b)
        out.append(p1 / p0 - 1 if p0 and p1 and p0 > 0 else None)
    return out


def weights(picked: Sequence[int], axis: Sequence[str], closes: Mapping[int, Mapping[str, float]], kr: bool,
            trace: ErcTrace | None = None) -> dict[int, float]:  # fmt: skip
    """12.46 `buffer_erc` 비중. `picked` = buffer_2n 이 고른 종목, `axis` = 그 나라 엔진 거래일(cutoff 이하, 오름차순),
    `closes` = 종목 → {날짜: 종가}(cutoff 이하만). 미래 정보 없음 — 넘겨받은 것만 본다"""
    tr = trace if trace is not None else ErcTrace()
    n = len(picked)
    tr.n = n
    if n == 0:
        return {}
    eq = 1.0 / n
    창 = list(axis)[-(WINDOW_DAYS + 1) :]
    수익 = {s: _returns(창, closes.get(s, {})) for s in picked}
    대상 = []
    for s in sorted(picked):
        r = [x for x in 수익[s] if x is not None]
        if len(r) < MIN_POINTS or (kr and any(abs(x) > KR_DAILY_LIMIT for x in r)):
            tr.to_equal.append(s)
            continue
        대상.append(s)
    while len(대상) >= 2:
        공통 = [i for i in range(len(창) - 1) if all(수익[s][i] is not None for s in 대상)]
        if len(공통) >= MIN_POINTS:
            # 공통 날 수익이 모두 같은 종목(분산 0 — 정지 중 같은 종가가 되풀이되는 시세)은 상관이 정의되지 않아
            # 수축 강도·ERC 가 NaN 으로 무너진다 → '60 미만' 과 같은 갈래로 1/n 몫(12.46 보충, 25.2190 교차검증)
            평평 = [s for s in 대상 if np.ptp([수익[s][i] for i in 공통]) == 0]
            if not 평평:
                break
            for s in 평평:
                대상.remove(s)
                tr.to_equal.append(s)
            continue
        # 유효 수익이 가장 적은 종목부터(같으면 stock_id 큰 쪽부터) 1/n 몫으로
        뺌 = min(대상, key=lambda s: (sum(x is not None for x in 수익[s]), -s))
        대상.remove(뺌)
        tr.to_equal.append(뺌)
    if len(대상) < 2:
        tr.to_equal = sorted(set(tr.to_equal) | set(대상))
        return {s: eq for s in picked}
    tr.to_equal = sorted(set(tr.to_equal))  # 기록 순서를 stock_id 순으로(평평 갈래가 섞여도, 25.2196)
    tr.erc_n = len(대상)
    tr.common_days = len(공통)
    r = np.array([[수익[s][i] for s in 대상] for i in 공통], dtype=float)
    cov, tr.delta = ledoit_wolf_cc(r)
    w, ok, tr.sweeps = erc_weights(cov)
    tr.converged = ok
    if not ok:
        tr.to_equal = sorted(picked)  # 기록: 리밸런스 전체가 1/n — ERC 를 한 것처럼 남기지 않는다
        tr.erc_n = 0
        return {s: eq for s in picked}
    몫 = eq * len(대상)
    out = {s: eq for s in picked if s not in 대상}
    out.update({s: 몫 * float(x) for s, x in zip(대상, w, strict=True)})
    cap = max(CAP, eq)
    capped = cap_weights(out, cap)
    # 기록만: 같은 창·같은 상한의 역변동성 비중과의 L1 거리(상관이 같으면 ERC = 역변동성 — 검증 B)
    inv = 1.0 / np.sqrt(np.diag(cov))
    역 = {s: eq for s in picked if s not in 대상}
    역.update({s: 몫 * float(x) for s, x in zip(대상, inv / inv.sum(), strict=True)})
    역 = cap_weights(역, cap)
    tr.inv_vol_l1 = sum(abs(capped[s] - 역[s]) for s in picked)
    return capped
