"""43회차 e1 `gbm_blend` — 시장 구분 안 백분위 특징의 LightGBM 회귀 예측과 composite 를 0.5/0.5 로 섞은 상위 20
(docs/factors.md 12.46, docs/infra.md 25.2194).

사전 등록(2026-11-08, 결과 보기 전 — 12.46 문단이 이 코드보다 먼저 커밋됨). 초모수·특징 목록·변환·섞기 가중을 결과 뒤
바꾸면 새 판·새 시도다. 근거: Gu·Kelly·Xiu 2020(RFS 33(5) — 나무 모형의 단면 수익 예측). 계산만 한다 — DB·시각을
모른다(걸린 시간은 부르는 쪽이 재서 `check_budget` 에 넘긴다). `STRATEGIES` 에 잇는 일은 2026-12 판정 실행이 두 나라
모두 닫힌 뒤다(12.46 일정) — 이 모듈은 그 전에 **꺼진 채** 들어간다.

정한 숫자(12.46 — 자유 숫자 0, 모두 등록 문단의 값이거나 빌린 값):
- 초모수 `PARAMS`·`NUM_BOOST_ROUND` — 문헌·관행의 근거 없는 첫 값 한 벌(탐색 없음). `bagging_freq` 1 이 없으면
  `bagging_fraction` 이 듣지 않는다(두 검증 같음). `force_row_wise` 는 B 의 초안대로(A 의 col_wise 와 같은 입력 두 번이
  같은 모델 문자열 — 2026-11-08 로컬 실측이라 보수성 차이 없음)
- 학습 자르기: 리밸런스 r 의 학습 단면 = s ≤ r − `EMBARGO`(3). r−1 은 라벨이 t_r 종가에 끝나 시점상 못 쓰고, r−2 는
  엠바고. **비지 않은 학습 단면 ≥ `MIN_TRAIN_SECTIONS`(18)** 인 첫 리밸런스부터 표본 밖
- 실패 가두기: lightgbm 은 쓰는 함수 안에서만 import, 적합 예외·설치 실패·판 어긋남·`TIMEOUT_SECONDS` 초과는
  `GbmFailure` — 부르는 쪽은 그 실행의 `gbm_blend` 만 '판정 불가(실행 실패)' 로 적는다. **composite 로 물러나지 않는다**
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from batch.services import buffer2n as b2n

NAME = "gbm_blend"
#: 판정 대조 — 같은 실행의 composite (12.46 판정 구간·견주기)
BASE = "composite"
#: 고정 lightgbm 판 — 바뀌면 새 판(지문에 들어간다). requirements-backtest.txt 의 고정과 같아야 한다
LIGHTGBM_VERSION = "4.6.0"
#: 나무 수 — 200회 고정, 조기 멈춤 없음(12.46). `lgb.train` 인자라 PARAMS 와 따로 둔다(지문에는 함께 넣는다)
NUM_BOOST_ROUND = 200
#: `min_data_in_leaf` = max(이 하한, ⌈이 몫 × 학습 행⌉) — 12.46 등록 식(학습 행 수에 따라 계산하는 값이라 PARAMS 밖)
MIN_DATA_IN_LEAF_FLOOR = 100
MIN_DATA_IN_LEAF_SHARE = 0.01
#: 초모수(12.46 등록 — 한 벌뿐, 탐색 없음). `min_data_in_leaf` 는 `params_for(rows)` 가 채운다
PARAMS: dict[str, Any] = {
    "objective": "regression",  # L2
    "num_leaves": 15,
    "max_depth": 4,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,  # 0 이면 배깅이 꺼진다 — 두 검증 같음, SPEC 의 뜻(배깅)을 살림
    "lambda_l2": 1.0,
    "seed": 0,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "verbose": -1,
}
#: GBM 단계 걸린 시간 상한(초, 나라 실행당) — 계산 어림(나라당 수 분)의 6배 넘는 여유, 120분 timeout 안에 다른 단계 몫을
#: 남김(12.46 계산 상수). **넘으면** 실패
TIMEOUT_SECONDS = 1800
#: 학습 자르기 — s ≤ r − EMBARGO (r−1 시점상 불가 · r−2 엠바고)
EMBARGO = 3
#: 표본 밖이 되는 비지 않은 학습 단면 하한(12.46)
MIN_TRAIN_SECTIONS = 18
#: 판정 표본 하한 — 표본 밖 리밸런스 수(`buffer2n.JUDGE_MIN_REBALANCES` 를 빌림, 36)
MIN_OOS_REBALANCES = b2n.JUDGE_MIN_REBALANCES
#: 상위 몇 종목(동일가중) — composite 와 같은 20
TOP_N = 20
#: composite 백분위의 몫 — 고정 0.5/0.5 (학습한 쌓기 가중 없음)
BLEND = 0.5

#: 특징에서 빼는 이름 ① — 12.46 이 이름으로 적은 셋(국내 전용 수급·의견 재료)
EXCLUDED_NAMED = ("credit_balance", "retail_flow", "tp_dispersion")
#: 특징에서 빼는 이름 ② — **감성·증권사 의견을 재료로 쓰는 IC 이름**. 등록 커밋 때 factor_ics 본문(jobs/backtest.py)을
#: 읽어 가른 결과: 증권사 의견(`kr_opinions` → `KrExtra.opinions`)을 쓰는 것은 `tp_dispersion` 하나, 뉴스 감성
#: (`news_sentiment`)을 쓰는 IC 이름은 없다. 그래서 ①과 겹치는 하나뿐이지만, 규칙의 두 번째 갈래를 따로 남긴다
EXCLUDED_SENTIMENT_OPINION = ("tp_dispersion",)
#: 자기 자신 — 기록용 GBM 단독 IC(`DIAG_IC_NAMES` 에 나중에 들어간다)
SELF_NAME = "gbm_pred"
#: 통제형 → 원값(통제는 GBM 이 상호작용으로 낼 수 있어 원값 하나로 접는다)
FOLD = {"flow_surge_x_rev": "flow_surge", "pairs_gap_x_rev": "pairs_gap", "lt_reversal_x_value": "lt_reversal"}

#: **등록 커밋에 고정한 특징 목록**(12.46 "뒤에 생기는 IC 이름은 들어가지 않는다" — 들이면 새 판·새 시도).
#: 학습은 이 튜플만 쓴다. `feature_names()`(기계적 규칙)와 같아야 하고, 어긋나면 시험이 깨진다
FEATURES: tuple[str, ...] = (
    "accrual", "amihud_illiquidity", "asset_growth", "beta_abs", "bp", "buyback", "cagr", "current_ratio",
    "debt_ratio", "dilution", "div_omission", "dividend_yield", "ep", "flow_surge", "gross_profitability",
    "high_52w_proximity", "idio_volatility", "lt_reversal", "max_daily_return", "mdd_abs", "mdd_recovery_days",
    "momentum_12_1", "momentum_3m", "momentum_6m", "momentum_consistency", "momentum_vol_adjusted",
    "net_issuance", "operating_income_growth", "operating_margin", "pairs_gap", "piotroski_lite",
    "profit_stability", "revenue_cagr_3y", "revenue_growth", "reversal_1m", "roa", "roe", "sector_mom", "sharpe",
    "sortino", "sp", "sue", "volatility_ann",
)  # fmt: skip


class GbmFailure(RuntimeError):
    """GBM 단계 실패 — 그 실행의 `gbm_blend` 만 '판정 불가(실행 실패)'. composite 로 물러나지 않는다(12.46)"""


def feature_names() -> tuple[str, ...]:
    """12.46 의 기계적 규칙: ① `scoring.FACTOR_METRICS` 하위 지표 ∪ ② (LOOP ∪ USER ∪ DIAG IC 이름) − 이름으로 뺀 셋 −
    감성·의견 재료 − `gbm_pred`, 통제형은 원값으로 접음. 정렬·중복 제거.

    jobs/backtest 는 나중에 이 모듈을 부를 쪽이라 순환을 피해 함수 안에서 읽는다. 런타임은 `FEATURES` 를 쓴다"""
    from batch.jobs import backtest as job
    from batch.services import scoring as sc

    지표 = {m.name for ms in sc.FACTOR_METRICS.values() for m in ms}
    뺌 = set(EXCLUDED_NAMED) | set(EXCLUDED_SENTIMENT_OPINION) | {SELF_NAME}
    ic = {FOLD.get(n, n) for n in (*job.LOOP_IC_NAMES, *job.USER_IC_NAMES, *job.DIAG_IC_NAMES)} - 뺌
    return tuple(sorted(지표 | ic))


def _missing(x: float | None) -> bool:
    return x is None or not math.isfinite(x)


def _avg_ranks2(values: Mapping[Any, float]) -> dict[Any, int]:
    """평균 순위의 **두 배**(정수 — 동점 평균이 반 정수라도 정확). 작은 값이 순위 1"""
    order = sorted(values, key=lambda k: values[k])
    out: dict[Any, int] = {}
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in order[i : j + 1]:
            out[k] = (i + 1) + (j + 1)  # 2 × 평균 순위
        i = j + 1
    return out


def percentile_by_group(values: Mapping[Any, float | None], group: Mapping[Any, str | None]) -> dict[Any, float]:
    """12.46 변환: 구분(시장 KOSPI·KOSDAQ / NYSE·NASDAQ) 안 백분위 = 평균 순위 ÷ 그 구분의 **값이 있는** 종목 수.
    결측(None·NaN)은 NaN 그대로. 구분을 모르는 종목도 NaN(어느 단면 안에서 셀지 모른다 — 보수적)"""
    out: dict[Any, float] = {k: math.nan for k in values}
    by: dict[str, dict[Any, float]] = {}
    for k, v in values.items():
        g = group.get(k)
        if g is None or _missing(v):
            continue
        by.setdefault(g, {})[k] = float(v)  # type: ignore[arg-type]
    for xs in by.values():
        n = len(xs)
        for k, r2 in _avg_ranks2(xs).items():
            out[k] = r2 / (2 * n)
    return out


@dataclass
class Section:
    """학습 단면 하나 — 리밸런스 s 의 cutoff 에서 낸 특징(행 × `FEATURES`, 이미 백분위)과 라벨(백분위, 없으면 NaN).
    과거 단면은 그 cutoff 에서 낸 입력 그대로 둔다(나중 창으로 다시 계산 금지 — 캐시 공유)"""

    date: str
    x: np.ndarray
    y: np.ndarray
    stock_ids: list[int] = field(default_factory=list)

    def n_labeled(self) -> int:
        return int(np.isfinite(self.y).sum()) if len(self.y) else 0


def train_sections(r: int, sections: Sequence[Section]) -> list[int]:
    """리밸런스 r 의 학습 단면 번호 — s ≤ r − `EMBARGO` 이고 **비지 않은**(라벨 있는 행이 하나라도 있는) 것.
    빈 단면은 세지 않는다(25.1209). 라벨 없는 행만 있는 단면은 학습에 한 행도 못 보태 빈 것으로 본다(보수적 —
    표본 밖 시작이 늦어진다)"""
    return [s for s in range(min(r - EMBARGO, len(sections) - 1) + 1) if sections[s].n_labeled() > 0]


def is_oos(r: int, sections: Sequence[Section]) -> bool:
    """표본 밖인가 — 비지 않은 학습 단면 ≥ `MIN_TRAIN_SECTIONS`. 아니면 `gbm_blend` = composite 이고 판정 구간 밖"""
    return len(train_sections(r, sections)) >= MIN_TRAIN_SECTIONS


@dataclass
class TrainSet:
    x: np.ndarray
    y: np.ndarray
    used: list[int]
    rows: int
    dropped: int  # 라벨이 없어 뺀 행 수(step_log 기록용)
    first_date: str | None
    last_date: str | None


def stack_train(r: int, sections: Sequence[Section]) -> TrainSet:
    """`train_sections` 의 단면만 쌓는다(확장 창). 라벨 없는 행은 빼고 그 수를 센다. r−1·r−2·r 은 보지 않는다"""
    used = train_sections(r, sections)
    xs, ys, dropped = [], [], 0
    for s in used:
        sec = sections[s]
        keep = np.isfinite(sec.y)
        dropped += int((~keep).sum())
        xs.append(np.asarray(sec.x, dtype=float)[keep])
        ys.append(np.asarray(sec.y, dtype=float)[keep])
    x = np.vstack(xs) if xs else np.empty((0, len(FEATURES)))
    y = np.concatenate(ys) if ys else np.empty(0)
    return TrainSet(x, y, used, len(y), dropped, sections[used[0]].date if used else None,
                    sections[used[-1]].date if used else None)  # fmt: skip


def params_for(rows: int) -> dict[str, Any]:
    """PARAMS + `min_data_in_leaf` = max(100, ⌈0.01 × 학습 행⌉)"""
    out = dict(PARAMS)
    out["min_data_in_leaf"] = max(MIN_DATA_IN_LEAF_FLOOR, math.ceil(MIN_DATA_IN_LEAF_SHARE * rows))
    return out


def fingerprint(params: Mapping[str, Any], features: Sequence[str], first_date: str | None, last_date: str | None,
                rows: int, code_sha: str, lightgbm_version: str = LIGHTGBM_VERSION) -> str:  # fmt: skip
    """지문 = sha256(lightgbm 판 · 초모수 정렬 JSON · 특징 이름 목록 · 학습 구간 첫·끝 단면 날짜 · 학습 행 수 ·
    코드 sha). 나무 수도 초모수로 넣는다(바뀌면 지문이 바뀌는 쪽이 보수적)"""
    body = {
        "lightgbm": lightgbm_version,
        "params": {**params, "num_boost_round": NUM_BOOST_ROUND},
        "features": list(features),
        "first": first_date,
        "last": last_date,
        "rows": int(rows),
        "code_sha": code_sha,
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_budget(elapsed_seconds: float) -> None:
    """GBM 단계 걸린 시간(부르는 쪽이 잰 값)이 `TIMEOUT_SECONDS` 를 넘으면 실패"""
    if elapsed_seconds > TIMEOUT_SECONDS:
        raise GbmFailure(f"GBM 단계 {elapsed_seconds:.0f}초 > {TIMEOUT_SECONDS}초")


def _lightgbm() -> Any:
    try:
        import lightgbm as lgb
    except Exception as exc:  # 설치 실패·libgomp 없음 등
        raise GbmFailure(f"lightgbm import 실패: {exc}") from exc
    if getattr(lgb, "__version__", None) != LIGHTGBM_VERSION:
        # 판이 바뀌면 새 판 — 조용히 다른 판으로 돌리지 않는다(보수적)
        raise GbmFailure(f"lightgbm 판 {getattr(lgb, '__version__', None)} ≠ 고정 {LIGHTGBM_VERSION}")
    return lgb


def fit_predict(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray) -> tuple[np.ndarray, str]:
    """(test 예측, 모델 문자열). 한 나라·한 리밸런스 적합. 어떤 실패든 `GbmFailure` — 물러나는 길 없음"""
    lgb = _lightgbm()
    try:
        x = np.asarray(train_x, dtype=float)
        y = np.asarray(train_y, dtype=float)
        t = np.asarray(test_x, dtype=float)
        if x.ndim != 2 or len(x) == 0 or len(x) != len(y) or not np.isfinite(y).all():
            raise ValueError(f"학습 자료 모양·라벨이 맞지 않음: x{x.shape} y{y.shape}")
        if t.ndim != 2 or t.shape[1] != x.shape[1]:
            raise ValueError(f"예측 자료 열 수가 다름: {t.shape} 대 {x.shape}")
        params = params_for(len(y))
        booster = lgb.train(params, lgb.Dataset(x, label=y, params=params), num_boost_round=NUM_BOOST_ROUND)
        pred = np.asarray(booster.predict(t), dtype=float)
        return pred, booster.model_to_string()
    except GbmFailure:
        raise
    except Exception as exc:
        raise GbmFailure(f"GBM 적합 실패: {exc}") from exc


def shap_contrib(model_str: str, x: np.ndarray) -> np.ndarray:
    """TreeSHAP 기여(행 × (특징 + 기댓값 열)). 행 합 = 예측(12.46 — 시험은 1e-9 로 묶음)"""
    lgb = _lightgbm()
    try:
        return np.asarray(lgb.Booster(model_str=model_str).predict(np.asarray(x, dtype=float), pred_contrib=True))
    except Exception as exc:
        raise GbmFailure(f"TreeSHAP 실패: {exc}") from exc


def blend_scores(composite_scores: Mapping[int, float | None],
                 gbm_preds: Mapping[int, float | None]) -> dict[int, float]:  # fmt: skip
    """섞기 집합(composite 점수와 GBM 예측이 **모두** 있는 종목) 안 두 백분위(평균 순위 ÷ n)의 BLEND/(1−BLEND) 평균"""
    집합 = [s for s in composite_scores if not _missing(composite_scores[s]) and not _missing(gbm_preds.get(s))]
    n = len(집합)
    if n == 0:
        return {}
    rc = _avg_ranks2({s: float(composite_scores[s]) for s in 집합})  # type: ignore[arg-type]
    rg = _avg_ranks2({s: float(gbm_preds[s]) for s in 집합})  # type: ignore[arg-type]
    return {s: (BLEND * rc[s] + (1 - BLEND) * rg[s]) / (2 * n) for s in 집합}


def blend_pick(composite_scores: Mapping[int, float | None], gbm_preds: Mapping[int, float | None],
               top_n: int = TOP_N) -> list[int]:  # fmt: skip
    """섞은 점수 상위 `top_n`(높은 순). 동점은 composite 점수 높은 순 → stock_id 작은 순. composite 점수가 없는
    종목은 고르지 않는다. 동일가중 비중은 부르는 쪽이 1/len 으로 준다"""
    sc = blend_scores(composite_scores, gbm_preds)
    order = sorted(sc, key=lambda s: (-sc[s], -float(composite_scores[s]), s))  # type: ignore[arg-type]
    return order[:top_n]
