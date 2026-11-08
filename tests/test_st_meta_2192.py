"""43회차 e2 `st_s_meta` 계산 — 평균 고유도·로지스틱·문턱·학습 자르기·구조 문·AUC (docs/factors.md 12.46, docs/infra.md 25.2192).

리플레이 고리·`ST_VARIANTS` 에 잇는 일은 2026-12 판정 실행이 두 나라 모두 닫힌 뒤다(12.46 일정) — 여기선 순수 계산만 묶는다.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from batch.services import paper as pp
from batch.services import shadow
from batch.services import st_meta as sm


def _ev(sid: int, a: int, b: int, y: int = 1, day: str = "2024-01-02", r: float = 0.01, **f: float) -> sm.MetaEvent:
    feats = {name: f.get(name, 0.0) for name in sm.FEATURES}
    return sm.MetaEvent(sid, day, a, b, y, r, feats)


# ── 등록 상수 ─────────────────────────────────────────────────────────────────────────────────────


def test_등록_상수() -> None:
    assert sm.FEATURES == ("momentum", "close_ma20", "turnover_20_60", "regime_bear", "sigma20")
    assert sm.L2 == 1.0
    # 정화 1 + 엠바고 11 = 기한 10 + 다음 장 시가 — 스윙 규칙에서 나온 수
    assert shadow.SWING_CLOSE.max_sessions == 10
    assert sm.EMBARGO_DAYS == shadow.SWING_CLOSE.max_sessions + 1 == 11
    assert sm.PURGE_DAYS == 1 and sm.PURGE_EMBARGO_DAYS == 12
    assert sm.FIRST_PREDICT_MONTH_OFFSET == 13
    assert (sm.MIN_EVENTS, sm.MIN_STOCKS, sm.MIN_BLOCKS) == (100, 50, 6)
    assert sm.BLOCK_DAYS == pp.EVIDENCE_BLOCK_DAYS == 28
    assert pytest.approx(0.05 / 3) == sm.ALPHA
    assert sm.NAME == "st_s_meta" and sm.BASE == "st_s"


# ── 평균 고유도 ───────────────────────────────────────────────────────────────────────────────────


def test_평균_고유도_손계산() -> None:
    # 종목 1: e1 [0,4], e2 [2,6], e3 [4,5] → c = 1,1,2,2,3,2,1 (t=0..6). 종목 2 의 [0,4] 는 겹치지 않는다
    evs = [_ev(1, 0, 4), _ev(1, 2, 6), _ev(1, 4, 5), _ev(2, 0, 4)]
    raw = np.array(
        [(1 + 1 + 1 / 2 + 1 / 2 + 1 / 3) / 5, (1 / 2 + 1 / 2 + 1 / 3 + 1 / 2 + 1) / 5, (1 / 3 + 1 / 2) / 2, 1.0]
    )
    w = sm.uniqueness_weights(evs)
    assert w.sum() == pytest.approx(4.0)
    assert np.allclose(w, raw * 4 / raw.sum(), atol=1e-14)
    assert w[0] / w[3] == pytest.approx(2 / 3) and w[2] / w[3] == pytest.approx(5 / 12)


def test_겹치지_않으면_가중은_모두_1() -> None:
    w = sm.uniqueness_weights([_ev(1, 0, 3), _ev(1, 4, 6), _ev(2, 0, 9)])
    assert np.allclose(w, 1.0)
    assert sm.uniqueness_weights([]).size == 0
    with pytest.raises(ValueError):
        sm.uniqueness_weights([_ev(1, 5, 4)])


# ── 로지스틱 ─────────────────────────────────────────────────────────────────────────────────────


def _data(seed: int, n: int = 40, k: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, k)) * np.array([1.0, 5.0, 0.2])[:k] + 3.0
    eta = 0.8 * (x[:, 0] - 3) - 0.1 * (x[:, 1] - 3) + 0.3
    y = (rng.random(n) < 1 / (1 + np.exp(-eta))).astype(float)
    w = rng.uniform(0.3, 1.5, size=n)
    return x, y, w * n / w.sum()


def _std(x: np.ndarray) -> np.ndarray:
    """구현과 다른 길로 표준화 — 열마다 파이썬 합으로"""
    out = np.empty_like(x)
    for j in range(x.shape[1]):
        col = [float(v) for v in x[:, j]]
        m = sum(col) / len(col)
        s = math.sqrt(sum((v - m) ** 2 for v in col) / len(col))
        out[:, j] = [(v - m) / (s if s > 0 else 1.0) for v in col]
    return out


def test_최적점에서_기울기가_0() -> None:
    x, y, w = _data(0)
    m = sm.fit_logistic(x, y, w)
    assert m.converged
    z = _std(x)
    p = 1 / (1 + np.exp(-(z @ m.beta + m.intercept)))
    g_beta = z.T @ (w * (p - y)) + m.beta  # ½‖β‖² 의 기울기 = β
    g_b = float((w * (p - y)).sum())  # 절편 벌점 없음
    assert np.max(np.abs(g_beta)) < 1e-8 and abs(g_b) < 1e-8


def test_독립_경사하강과_같은_해() -> None:
    x, y, w = _data(1, n=12, k=2)
    z = _std(x)
    beta, b = np.zeros(2), 0.0
    for _ in range(20000):
        p = 1 / (1 + np.exp(-(z @ beta + b)))
        beta = beta - 0.02 * (z.T @ (w * (p - y)) + beta)
        b = b - 0.02 * float((w * (p - y)).sum())
    m = sm.fit_logistic(x, y, w)
    assert np.allclose(m.beta, beta, atol=1e-7) and m.intercept == pytest.approx(b, abs=1e-7)
    assert np.allclose(sm.predict_proba(m, x), 1 / (1 + np.exp(-(z @ beta + b))), atol=1e-7)


def test_절편은_벌점이_없다() -> None:
    # 상수 열은 표준화 뒤 0 → β = 0, 절편 = logit(가중 ȳ) 정확히. 절편에 벌점이 있으면 0 쪽으로 줄어든다
    x = np.full((20, 2), 7.0)
    y = np.array([1.0] * 19 + [0.0])
    w = np.ones(20)
    m = sm.fit_logistic(x, y, w)
    assert m.converged and np.allclose(m.beta, 0.0)
    assert m.intercept == pytest.approx(math.log(19), abs=1e-9)
    assert np.allclose(m.scale, 1.0) and np.allclose(m.mean, 7.0)
    # 잡음 특징이면 β 는 작고 절편은 크다
    rng = np.random.default_rng(3)
    y2 = (rng.random(400) < 0.95).astype(float)
    m2 = sm.fit_logistic(rng.normal(size=(400, 2)), y2, np.ones(400))
    assert m2.intercept > 2.5 and np.max(np.abs(m2.beta)) < 0.3


def test_가중이_적합에_든다() -> None:
    x = np.array([[0.0], [0.0], [1.0], [1.0]])
    y = np.array([1.0, 0.0, 1.0, 0.0])
    m1 = sm.fit_logistic(x, y, [1, 1, 1, 1])
    m2 = sm.fit_logistic(x, y, [3, 1, 1, 3])
    assert m1.beta[0] == pytest.approx(0.0, abs=1e-12)
    assert m2.beta[0] < -0.1  # x=0 에서 이긴 쪽·x=1 에서 진 쪽이 무거움


def test_같은_입력은_같은_모델() -> None:
    x, y, w = _data(5)
    a, b = sm.fit_logistic(x, y, w), sm.fit_logistic(x.copy(), y.copy(), w.copy())
    assert a.beta.tobytes() == b.beta.tobytes() and a.intercept == b.intercept


# ── 문턱·거름 ─────────────────────────────────────────────────────────────────────────────────────


def test_문턱은_가중_평균_y() -> None:
    assert sm.threshold([1, 0, 0, 1], [3, 1, 1, 1]) == pytest.approx(4 / 6)
    assert sm.threshold([1, 0], [1, 1]) == 0.5


def test_거름_판정() -> None:
    assert sm.filter_decision(0.40, 0.40) == sm.SKIP  # 같으면 거름
    assert sm.filter_decision(0.4000001, 0.40) == sm.KEEP
    assert sm.filter_decision(0.1, 0.40) == sm.SKIP
    assert sm.filter_decision(None, 0.40) == sm.UNKNOWN
    assert sm.filter_decision(float("nan"), 0.40) == sm.UNKNOWN
    f = {n: 1.0 for n in sm.FEATURES}
    assert sm.feature_vector(f) is not None
    assert sm.feature_vector({**f, "sigma20": None}) is None
    assert sm.feature_vector({k: v for k, v in f.items() if k != "momentum"}) is None
    m = sm.fit_logistic(np.zeros((2, 5)), [1, 0], [1, 1])
    assert sm.event_proba(m, {**f, "close_ma20": float("nan")}) is None


# ── 학습 자르기 ───────────────────────────────────────────────────────────────────────────────────


def test_학습_자르기_경계() -> None:
    d0 = 100
    got = sm.train_mask([87, 88, 89], d0)
    assert got.tolist() == [True, True, False]  # 88 = d0 − 12 는 들고 89 는 빠진다


def test_학습은_청산일로_자른다() -> None:
    # 신호(진입)는 이르지만 청산이 늦은 사건은 빠지고, 진입이 늦어도 청산이 이르면 든다
    evs = [_ev(1, 10, 95, y=1), _ev(2, 80, 88, y=0), _ev(3, 70, 86, y=1), _ev(4, 87, 89, y=0)]
    fit = sm.fit_at(evs, 100)
    assert fit.train == (1, 2) and fit.n_train == 2 and fit.n_missing == 0
    assert fit.tau == pytest.approx(0.5)


def test_적합의_문턱은_고유도_가중() -> None:
    # 종목 1 의 겹친 둘(y=1, [0,9]·[0,9] → 각 ½)과 종목 2 하나(y=0) → τ = (½+½)/(½+½+1) = 0.5, 가중 없으면 2/3
    evs = [_ev(1, 0, 9, y=1), _ev(1, 0, 9, y=1), _ev(2, 0, 9, y=0)]
    fit = sm.fit_at(evs, 30)
    assert fit.tau == pytest.approx(0.5)


def test_특징이_빠진_사건은_학습에서_빼고_센다() -> None:
    bad = sm.MetaEvent(9, "2024-01-02", 0, 3, 1, 0.02, {"momentum": 1.0})
    fit = sm.fit_at([_ev(1, 0, 3, y=1), _ev(2, 0, 3, y=0), bad], 50)
    assert fit.n_train == 2 and fit.n_missing == 1 and fit.train == (0, 1)
    empty = sm.fit_at([_ev(1, 0, 90)], 100)
    assert empty.model is None and empty.tau is None and empty.n_train == 0


def test_첫_예측_달() -> None:
    assert sm.first_predict_month("2021-09-15") == "2022-09"
    assert sm.first_predict_month("2021-01-04") == "2022-01"
    assert sm.first_predict_month("2021-12-01") == "2022-12"


# ── 구조 문 ──────────────────────────────────────────────────────────────────────────────────────


def _spread(n: int, stocks: int, blocks: int) -> list[sm.MetaEvent]:
    base = date(2024, 1, 1)
    start = (base.toordinal() // 28 + 1) * 28  # 묶음 첫날
    out = []
    for i in range(n):
        d = date.fromordinal(start) + timedelta(days=28 * (i % blocks))
        out.append(_ev(i % stocks, 0, 1, day=d.isoformat()))
    return out


def test_구조_문_경계() -> None:
    ok = sm.gates(_spread(100, 50, 6))
    assert (ok.events, ok.stocks, ok.blocks, ok.ok) == (100, 50, 6, True)
    assert not sm.gates(_spread(99, 50, 6)).ok
    assert not sm.gates(_spread(100, 49, 6)).ok
    assert not sm.gates(_spread(100, 50, 5)).ok
    a, b, both = sm.gates_split(_spread(100, 50, 6), _spread(99, 50, 6))
    assert a.ok and not b.ok and not both


def test_묶음은_신호일_28일() -> None:
    d = date.fromordinal((date(2024, 1, 1).toordinal() // 28 + 1) * 28)
    assert sm.block_key(d.isoformat()) == sm.block_key((d + timedelta(days=27)).isoformat())
    assert sm.block_key((d + timedelta(days=28)).isoformat()) == sm.block_key(d.isoformat()) + 1
    assert sm.block_key((d - timedelta(days=1)).isoformat()) == sm.block_key(d.isoformat()) - 1


# ── 평가 ──────────────────────────────────────────────────────────────────────────────────────────


def test_Brier_손계산() -> None:
    assert sm.brier([0.8, 0.3, 0.5], [1, 0, 1]) == pytest.approx((0.04 + 0.09 + 0.25) / 3)


def test_AUC_손계산_동점_포함() -> None:
    # 양성 0.9·0.5, 음성 0.5·0.2 → 짝 (0.9,0.5)=1 (0.9,0.2)=1 (0.5,0.5)=½ (0.5,0.2)=1 → 3.5/4
    assert sm.auc([0.9, 0.5, 0.5, 0.2], [1, 1, 0, 0]) == pytest.approx(0.875)
    assert sm.auc([0.3, 0.3], [1, 0]) == pytest.approx(0.5)
    assert sm.auc([0.1, 0.9], [1, 0]) == 0.0
    assert sm.auc([0.1, 0.9], [1, 1]) is None


def test_묶음_AUC_는_한_부류뿐인_묶음을_빼고_센다() -> None:
    d = date.fromordinal((date(2024, 1, 1).toordinal() // 28 + 1) * 28)
    d1, d2, d3 = d.isoformat(), (d + timedelta(days=28)).isoformat(), (d + timedelta(days=56)).isoformat()
    blocks, dropped = sm.block_aucs([d1, d1, d2, d2, d3], [0.7, 0.2, 0.4, 0.4, 0.9], [1, 0, 1, 0, 1])
    assert dropped == 1 and blocks == {sm.block_key(d1): 1.0, sm.block_key(d2): 0.5}


def test_하한은_등록_알파로_block_bound() -> None:
    vals = {1: 0.6, 2: 0.7, 3: 0.55, 4: 0.65, 5: 0.62, 6: 0.58}
    got = sm.auc_lower_bound(vals)
    assert got == pp.block_bound(vals, alpha=0.05 / 3)
    assert got["lower"] < pp.block_bound(vals)["lower"]  # α 가 작을수록 하한이 낮다
    assert sm.trade_unit_lower_bound(vals) == got


def test_거래_단위_묶음_차이() -> None:
    d = date.fromordinal((date(2024, 1, 1).toordinal() // 28 + 1) * 28)
    d1, d2 = d.isoformat(), (d + timedelta(days=28)).isoformat()
    blocks, dropped = sm.trade_unit_blocks(
        [d1, d1, d1, d1, d2, d2],
        [sm.KEEP, sm.KEEP, sm.SKIP, sm.UNKNOWN, sm.KEEP, sm.UNKNOWN],
        [0.04, 0.02, -0.01, 0.5, 0.03, -0.2],
    )
    assert dropped == 1 and blocks == {sm.block_key(d1): pytest.approx(0.03 + 0.01)}


def test_라벨은_비용_뒤_0_초과() -> None:
    assert sm.label(0.0001) == 1 and sm.label(0.0) == 0 and sm.label(-0.01) == 0


def test_특징_다섯은_단기_신호와_같은_식이다() -> None:
    """②③ = `signals.moving_average`(단기 신호와 같은 함수), ⑤ = 마지막 21 종가 단순 수익 20 개의 표본표준편차"""
    closes = [100.0 + i + (i % 3) for i in range(60)]
    turns = [1_000.0 + 10 * i for i in range(60)]
    f = sm.signal_features(closes, turns, 72.0, bear=True)
    ma20 = sum(closes[-20:]) / 20
    assert f["close_ma20"] == pytest.approx(closes[-1] / ma20 - 1)
    assert f["turnover_20_60"] == pytest.approx((sum(turns[-20:]) / 20) / (sum(turns) / 60))
    r = [b / a - 1 for a, b in zip(closes[-21:-1], closes[-20:], strict=True)]
    m = sum(r) / 20
    assert f["sigma20"] == pytest.approx(math.sqrt(sum((x - m) ** 2 for x in r) / 19))
    assert f["momentum"] == 72.0 and f["regime_bear"] == 1.0
    assert sm.feature_vector(f) is not None


def test_거래대금_60_행_미만이거나_종가가_비면_모름() -> None:
    closes = [100.0 + i for i in range(60)]
    f = sm.signal_features(closes, [1.0] * 59, 50.0, bear=False)
    assert f["turnover_20_60"] is None and sm.feature_vector(f) is None  # 12.46 'turnovers < 60행' → 사지 않음
    빈 = list(closes)
    빈[-5] = None  # type: ignore[call-overload]
    g = sm.signal_features(빈, [1.0] * 60, 50.0, bear=False)
    assert g["sigma20"] is None and g["close_ma20"] is None and g["regime_bear"] == 0.0
