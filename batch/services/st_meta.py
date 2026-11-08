"""43회차 e2 `st_s_meta` — S 리플레이 단기 신호를 로지스틱 메타 확률로 거르는 계산 (factors.md 12.46, infra 25.2192).

사전 등록(2026-11-08, 결과 보기 전 — 12.46 e2 문단이 이 코드보다 먼저 커밋됨). López de Prado 2018(3장 메타 라벨링·
4장 평균 고유도·7장 정화·엠바고). 1차 모델은 단기 신호 그대로, 2차 모델은 "이 신호가 비용 뒤 이길 확률" p 를 내고
p ≤ τ(학습 기준율)이면 거른다 — **크기 식 없음**(m = 1, 줄이기만). 계산만 한다 — DB·시각을 모른다. 리플레이
(`simulate_account` 고리)·`ST_VARIANTS` 에 잇는 일은 12.46 공통 일정(2026-12 판정 실행이 닫힌 뒤)이다.

정한 숫자(12.46 — 자유 숫자 0, 모두 빌린 값이거나 규칙에서 나온 값):
- L2 = 1(scikit-learn C = 1 꼴), 절편 벌점 없음. 표준화는 그 적합 학습 행의 평균·표준편차(가중 없음, ddof 0)
- 학습 자르기: 청산 체결 장 ≤ d0 − `PURGE_EMBARGO_DAYS`(정화 1 + 엠바고 11 = 기한 10 + 다음 장 시가 — 스윙 규칙에서)
- 첫 예측 달: 그 나라 첫 사건 달부터 13번째 달(학습 12개월)
- 구조 문: 사건 100·종목 50·28일 묶음 6(funnel.md 3장 1 → 2 표본 숫자 — 코드 상수가 없어 여기 둔다)
- 판정 α = 0.05/3(43회차 묶음 m = 3 본페로니), 묶음 하한은 `paper.block_bound`(뉴이–웨스트 시차 1)
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from batch.services import metrics, shadow, signals
from batch.services import paper as pp

NAME = "st_s_meta"
BASE = "st_s"

#: 특징 5개(12.46 — 결과 전 고정, 이름 목록을 지문에). ① 그 주 모멘텀 점수 ② 종가/MA20 − 1 ③ 20/60 거래대금 비
#: ④ 그 종목 시장 지수 추세 국면(약세 1, 강세·모름 0) ⑤ cutoff 까지 시세로 다시 낸 σ20
FEATURES = ("momentum", "close_ma20", "turnover_20_60", "regime_bear", "sigma20")
#: L2 세기 — ½‖β‖² 의 계수(scikit-learn C = 1 꼴, 12.46)
L2 = 1.0
#: 정화 1 장 — 라벨이 청산 체결 장에 끝난다(그 장 자체를 학습에 넣지 않는 몫)
PURGE_DAYS = 1
#: 엠바고 = 스윙 기한(`shadow.SWING_CLOSE` 10거래일) + 넘은 날 다음 장 시가 1 = 11
EMBARGO_DAYS = shadow.SWING_CLOSE.max_sessions + 1
#: 학습 행 = 청산 체결 장 ≤ d0 − 12 (12.46 "정화 1 + 엠바고 11")
PURGE_EMBARGO_DAYS = PURGE_DAYS + EMBARGO_DAYS
#: 첫 예측 달 = 첫 사건 달을 1번째로 셀 때 13번째 달(앞 12개월은 거름 없음·판정 구간 밖)
FIRST_PREDICT_MONTH_OFFSET = 13
#: 구조 문 하한 — funnel.md 3장 1 → 2 "끝난 거래 100건·28일 묶음 6개·서로 다른 종목 50개" 그대로
MIN_EVENTS = 100
MIN_STOCKS = 50
MIN_BLOCKS = 6
#: 묶음 길이(달력일, 신호 기준일로 묶음) — 결정 단위 증거 묶음과 같은 값
BLOCK_DAYS = pp.EVIDENCE_BLOCK_DAYS
#: 한쪽 유의수준 — 43회차 묶음 m = 3 본페로니(AUC·거래 단위에만)
ALPHA = 0.05 / 3
#: 뉴턴 풀이 — 판정 문턱이 아니라 계산 상수(결과 전 고정). 수렴 = max |기울기| ≤ GRAD_TOL × max(1, Σw)
GRAD_TOL = 1e-10
MAX_ITER = 100

SKIP, KEEP, UNKNOWN = "skip", "keep", "unknown"


@dataclass(frozen=True)
class MetaEvent:
    """사건 하나 = (나라, 종목, 신호 기준일) 단기 신호(나라는 부르는 쪽이 나눈다). `entry_idx`·`exit_idx` 는 그 나라
    거래소 달력의 거래일 번호(진입 체결 장·청산 체결 장 — 끊김 사건은 마지막 종가 장). `y` = 1[비용 뒤 순수익 > 0]"""

    stock_id: int
    signal_date: str
    entry_idx: int
    exit_idx: int
    y: int
    net_return: float
    features: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class LogitModel:
    beta: np.ndarray
    intercept: float
    mean: np.ndarray
    scale: np.ndarray
    converged: bool
    iterations: int


@dataclass(frozen=True)
class Fit:
    """d0 한 번의 적합 — `model`·`tau` 는 학습 행이 없으면 None"""

    model: LogitModel | None
    tau: float | None
    n_train: int
    n_missing: int  # 청산은 됐지만 특징이 빠져 학습에서 뺀 사건
    train: tuple[int, ...]  # 학습에 쓴 사건의 번호(입력 순서)


@dataclass(frozen=True)
class GateCount:
    events: int
    stocks: int
    blocks: int
    ok: bool


def label(net_return: float) -> int:
    """y = 1[비용 뒤 순수익 > 0] — 0 은 진 쪽"""
    return int(net_return > 0)


def uniqueness_weights(events: Sequence[MetaEvent]) -> np.ndarray:
    """평균 고유도(López de Prado 2018 4.4). 같은 종목 안에서 거래일 t 의 동시 활성 사건 수
    c_t = #{j : entry_j ≤ t ≤ exit_j}(진입 장~청산 장 양끝 포함), 사건 가중 = 수명 동안 1/c_t 의 평균,
    끝에 합 = 사건 수로 다시 맞춘다"""
    n = len(events)
    if n == 0:
        return np.zeros(0)
    by_stock: dict[int, list[int]] = {}
    for i, e in enumerate(events):
        if e.exit_idx < e.entry_idx:
            raise ValueError(f"청산 장이 진입 장보다 앞섬: {e}")
        by_stock.setdefault(e.stock_id, []).append(i)
    raw = np.zeros(n)
    for idx in by_stock.values():
        lo = min(events[i].entry_idx for i in idx)
        hi = max(events[i].exit_idx for i in idx)
        c = np.zeros(hi - lo + 1)
        for i in idx:
            c[events[i].entry_idx - lo : events[i].exit_idx - lo + 1] += 1
        for i in idx:
            raw[i] = float(np.mean(1.0 / c[events[i].entry_idx - lo : events[i].exit_idx - lo + 1]))
    return raw * (n / raw.sum())


def threshold(y: Sequence[float] | np.ndarray, w: Sequence[float] | np.ndarray) -> float:
    """τ = 가중 평균 y(ȳ) — Brier 기준선과 같은 값"""
    ya, wa = np.asarray(y, dtype=float), np.asarray(w, dtype=float)
    return float((wa * ya).sum() / wa.sum())


def _objective(z: np.ndarray, y: np.ndarray, w: np.ndarray, beta: np.ndarray, b: float) -> float:
    eta = z @ beta + b
    return float((w * (np.logaddexp(0.0, eta) - y * eta)).sum() + 0.5 * L2 * (beta @ beta))


def _sigmoid(eta: np.ndarray) -> np.ndarray:
    return np.exp(-np.logaddexp(0.0, -eta))


def fit_logistic(x: Sequence[Sequence[float]] | np.ndarray, y: Sequence[float] | np.ndarray,
                 w: Sequence[float] | np.ndarray) -> LogitModel:  # fmt: skip
    """Σ w_i·logloss_i + ½·L2·‖β‖² 최소(절편 벌점 없음) — 뉴턴(IRLS) + 되짚는 선 탐색. 표준화는 학습 행의 가중 없는
    평균·표준편차(ddof 0), 표준편차 0 인 열은 나누지 않는다(가운데 맞춘 뒤 0 그대로 — β 도 0). 시작점은 β = 0,
    절편 = logit(가중 ȳ)(0·1 끝은 잘라서). 같은 입력은 같은 결과(난수 없음)"""
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    wa = np.asarray(w, dtype=float)
    if xa.ndim != 2 or len(xa) != len(ya) or len(ya) != len(wa) or len(ya) == 0:
        raise ValueError("x 는 n×k, y·w 는 길이 n(n ≥ 1)")
    mean = xa.mean(axis=0)
    sd = xa.std(axis=0)
    scale = np.where(sd > 0, sd, 1.0)
    z = (xa - mean) / scale
    n, k = z.shape
    zt = np.hstack([z, np.ones((n, 1))])
    ybar = min(max(threshold(ya, wa), 1e-12), 1 - 1e-12)
    theta = np.zeros(k + 1)
    theta[k] = math.log(ybar / (1 - ybar))
    pen = np.full(k + 1, L2)
    pen[k] = 0.0  # 절편 벌점 없음
    tol = GRAD_TOL * max(1.0, float(wa.sum()))
    for it in range(1, MAX_ITER + 1):
        p = _sigmoid(zt @ theta)
        grad = zt.T @ (wa * (p - ya)) + pen * theta
        if float(np.max(np.abs(grad))) <= tol:
            return LogitModel(theta[:k].copy(), float(theta[k]), mean, scale, True, it - 1)
        h = (zt * (wa * p * (1 - p))[:, None]).T @ zt + np.diag(pen)
        try:
            step = np.linalg.solve(h, grad)
        except np.linalg.LinAlgError:
            break
        f0 = _objective(z, ya, wa, theta[:k], theta[k])
        t = 1.0
        for _ in range(60):
            cand = theta - t * step
            if _objective(z, ya, wa, cand[:k], cand[k]) <= f0 - 1e-4 * t * float(grad @ step):
                break
            t *= 0.5
        else:
            break
        theta = cand
    return LogitModel(theta[:k].copy(), float(theta[k]), mean, scale, False, MAX_ITER)


def predict_proba(model: LogitModel, x: Sequence[float] | Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    """p = σ(((x − 평균)/표준편차)·β + 절편) — 학습 행의 척도 그대로. 1차원이면 길이 1 배열"""
    xa = np.atleast_2d(np.asarray(x, dtype=float))
    return _sigmoid(((xa - model.mean) / model.scale) @ model.beta + model.intercept)


def feature_vector(features: Mapping[str, float | None]) -> np.ndarray | None:
    """`FEATURES` 순서의 값 — 하나라도 없거나(None) 유한하지 않으면 None('메타 확률 모름')"""
    out = []
    for name in FEATURES:
        v = features.get(name)
        if v is None or not math.isfinite(float(v)):
            return None
        out.append(float(v))
    return np.array(out)


def signal_features(closes: Sequence[float | None], turnovers: Sequence[float | None], momentum_score: float | None,
                    bear: bool) -> dict[str, float | None]:  # fmt: skip
    """신호 하나의 특징 다섯(12.46 보충 — 25.2192). `closes`·`turnovers` = 신호 기준일(cutoff)까지의 시점 창(오름차순).
    ②③ 은 단기 신호 판정(`signals.short_term`)과 **같은 함수**(`signals.moving_average` — 20·60 개 모두 있어야 값)로,
    ⑤ σ20 = 마지막 21 종가의 단순 일간 수익 20 개의 표본표준편차(`metrics.stdev`, ddof 1, 연환산 안 함, 하나라도 비면
    없음). 값이 없으면 None — `feature_vector` 가 '모름'(사지 않음)으로 돌린다"""
    ma20 = signals.moving_average(list(closes), signals.MA_SHORT_DAYS)
    close = closes[-1] if closes else None
    t20 = signals.moving_average(list(turnovers), signals.MA_SHORT_DAYS)
    t60 = signals.moving_average(list(turnovers), signals.MA_LONG_DAYS)
    last = list(closes[-(signals.MA_SHORT_DAYS + 1) :])
    sigma = None
    if len(last) == signals.MA_SHORT_DAYS + 1 and all(c is not None and c > 0 for c in last):
        sigma = metrics.stdev([b / a - 1 for a, b in zip(last, last[1:], strict=False)])  # type: ignore[operator]
    return {
        "momentum": momentum_score,
        "close_ma20": close / ma20 - 1 if close is not None and ma20 else None,
        "turnover_20_60": t20 / t60 if t20 is not None and t60 else None,
        "regime_bear": 1.0 if bear else 0.0,
        "sigma20": sigma,
    }


def event_proba(model: LogitModel, features: Mapping[str, float | None]) -> float | None:
    v = feature_vector(features)
    return None if v is None else float(predict_proba(model, v)[0])


def filter_decision(p: float | None, tau: float) -> str:
    """p ≤ τ → skip, p > τ → keep(그 행 비중 그대로, m = 1). p 가 없으면 unknown — 사지 않는다"""
    if p is None or not math.isfinite(p):
        return UNKNOWN
    return SKIP if p <= tau else KEEP


def train_mask(exit_days: Sequence[int] | np.ndarray, d0_index: int) -> np.ndarray:
    """학습에 들 사건 — **청산 체결 장**(신호일이 아니라) ≤ d0 − `PURGE_EMBARGO_DAYS`"""
    return np.asarray(exit_days, dtype=int) <= d0_index - PURGE_EMBARGO_DAYS


def fit_at(events: Sequence[MetaEvent], d0_index: int) -> Fit:
    """d0(그 달 첫 결정일의 거래일 번호)의 확장 창 적합. 학습 = 청산으로 자른 사건 가운데 특징이 다 있는 것,
    겹침 가중은 그 학습 사건끼리 낸 평균 고유도, τ = 같은 가중의 ȳ"""
    mask = train_mask([e.exit_idx for e in events], d0_index)
    rows, vecs, missing = [], [], 0
    for i, e in enumerate(events):
        if not mask[i]:
            continue
        v = feature_vector(e.features)
        if v is None:
            missing += 1
            continue
        rows.append(i)
        vecs.append(v)
    if not rows:
        return Fit(None, None, 0, missing, ())
    train = [events[i] for i in rows]
    w = uniqueness_weights(train)
    y = np.array([e.y for e in train], dtype=float)
    model = fit_logistic(np.vstack(vecs), y, w)
    return Fit(model, threshold(y, w), len(rows), missing, tuple(rows))


def first_predict_month(first_event_date: str) -> str:
    """첫 사건 달을 1번째로 셀 때 `FIRST_PREDICT_MONTH_OFFSET` 번째 달("YYYY-MM")"""
    y, m = int(first_event_date[:4]), int(first_event_date[5:7])
    k = y * 12 + (m - 1) + FIRST_PREDICT_MONTH_OFFSET - 1
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def block_key(signal_date: str) -> int:
    """28일 묶음 번호 — 신호 기준일의 서수 ÷ `BLOCK_DAYS`(결정 단위 증거 묶음과 같은 자름)"""
    return date.fromisoformat(signal_date[:10]).toordinal() // BLOCK_DAYS


def gates(events: Sequence[MetaEvent]) -> GateCount:
    """구조 문 셈 — 사건 수·서로 다른 종목 수·서로 다른 28일 묶음 수(신호 기준일), 셋 다 하한 이상이면 통과"""
    n = len(events)
    s = len({e.stock_id for e in events})
    b = len({block_key(e.signal_date) for e in events})
    return GateCount(n, s, b, n >= MIN_EVENTS and s >= MIN_STOCKS and b >= MIN_BLOCKS)


def gates_split(kept: Sequence[MetaEvent], skipped: Sequence[MetaEvent]) -> tuple[GateCount, GateCount, bool]:
    """구조 문 ② — 남긴 사건·거른 사건이 **각각** 하한 이상"""
    a, b = gates(kept), gates(skipped)
    return a, b, a.ok and b.ok


def brier(p: Sequence[float] | np.ndarray, y: Sequence[float] | np.ndarray) -> float:
    """가중 없는 평균 (p − y)² — 기준선은 같은 자리에 그 적합의 τ 를 넣어 같은 함수로"""
    pa, ya = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return float(np.mean((pa - ya) ** 2))


def auc(p: Sequence[float], y: Sequence[int]) -> float | None:
    """Mann–Whitney AUC — 양성·음성 짝마다 p_양 > p_음 이면 1, 같으면 0.5. 한 부류가 없으면 None"""
    pos = [float(a) for a, b in zip(p, y, strict=True) if b == 1]
    neg = [float(a) for a, b in zip(p, y, strict=True) if b != 1]
    if not pos or not neg:
        return None
    s = sum(1.0 if a > c else 0.5 if a == c else 0.0 for a in pos for c in neg)
    return s / (len(pos) * len(neg))


def block_aucs(signal_dates: Sequence[str], p: Sequence[float], y: Sequence[int]) -> tuple[dict[int, float], int]:
    """(묶음 → AUC, 뺀 묶음 수). 두 부류가 다 있지 않은 묶음은 빼고 센다"""
    groups: dict[int, tuple[list[float], list[int]]] = {}
    for d, a, b in zip(signal_dates, p, y, strict=True):
        g = groups.setdefault(block_key(d), ([], []))
        g[0].append(a)
        g[1].append(b)
    out, dropped = {}, 0
    for k in sorted(groups):
        v = auc(*groups[k])
        if v is None:
            dropped += 1
        else:
            out[k] = v
    return out, dropped


def trade_unit_blocks(signal_dates: Sequence[str], decisions: Sequence[str],
                      net_returns: Sequence[float]) -> tuple[dict[int, float], int]:  # fmt: skip
    """판정 (3) — 묶음마다 '남긴 사건 평균 순수익 − 거른 사건 평균 순수익'. unknown 은 어느 쪽에도 넣지 않는다.
    한쪽이 빈 묶음은 차이를 못 내 빼고 센다"""
    groups: dict[int, tuple[list[float], list[float]]] = {}
    for d, c, r in zip(signal_dates, decisions, net_returns, strict=True):
        if c not in (KEEP, SKIP):
            continue
        g = groups.setdefault(block_key(d), ([], []))
        (g[0] if c == KEEP else g[1]).append(float(r))
    out, dropped = {}, 0
    for k in sorted(groups):
        kept, skipped = groups[k]
        if kept and skipped:
            out[k] = sum(kept) / len(kept) - sum(skipped) / len(skipped)
        else:
            dropped += 1
    return out, dropped


def auc_lower_bound(blocks: dict[int, float]) -> dict:
    """묶음 AUC 의 한쪽 하한(α = 0.05/3) — 판정 (1) 은 lower > 0.5. 묶음 6 하한은 호출 밖(구조 문)"""
    return pp.block_bound(blocks, alpha=ALPHA)


def trade_unit_lower_bound(blocks: dict[int, float]) -> dict:
    """거래 단위 차이의 한쪽 하한(α = 0.05/3) — 판정 (3) 은 lower > 0"""
    return pp.block_bound(blocks, alpha=ALPHA)
