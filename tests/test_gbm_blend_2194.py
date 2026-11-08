"""43회차 e1 `gbm_blend` 계산 — 백분위·학습 자르기·섞기·지문·실패 가두기 (docs/factors.md 12.46, docs/infra.md 25.2194).

전략 줄(`STRATEGIES`)에 잇는 일은 2026-12 판정 실행이 두 나라 모두 닫힌 뒤다(12.46 일정) — 여기선 순수 계산만 묶는다.
합성 고정 자료만 쓴다(오프라인).
"""

from __future__ import annotations

import hashlib
import importlib
import math
import sys

import numpy as np
import pytest

from batch.services import buffer2n as b2n
from batch.services import gbm_blend as gb

K = 5  # 합성 특징 수(적합 시험은 특징 이름과 무관)


def _section(i: int, rows: int = 120, seed: int | None = None) -> gb.Section:
    rng = np.random.default_rng(i if seed is None else seed)
    x = rng.uniform(size=(rows, K))
    y = 0.6 * x[:, 0] - 0.3 * x[:, 1] * x[:, 2] + 0.1 * rng.normal(size=rows)
    return gb.Section(date=f"2022-{i:03d}", x=x, y=y)


def _empty(i: int) -> gb.Section:
    return gb.Section(date=f"2022-{i:03d}", x=np.empty((0, K)), y=np.empty(0))


def _predict(r: int, sections: list[gb.Section]) -> tuple[np.ndarray, str]:
    ts = gb.stack_train(r, sections)
    return gb.fit_predict(ts.x, ts.y, sections[r].x)


# ---------------------------------------------------------------- 상수


def test_상수는_사전_등록과_같다() -> None:
    assert gb.PARAMS == {
        "objective": "regression", "num_leaves": 15, "max_depth": 4, "learning_rate": 0.05,
        "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "seed": 0,
        "deterministic": True, "force_row_wise": True, "num_threads": 1, "verbose": -1,
    }  # fmt: skip
    assert "min_data_in_leaf" not in gb.PARAMS and "num_boost_round" not in gb.PARAMS
    assert gb.NUM_BOOST_ROUND == 200
    assert gb.LIGHTGBM_VERSION == "4.6.0"
    assert gb.TIMEOUT_SECONDS == 1800
    assert gb.EMBARGO == 3 and gb.MIN_TRAIN_SECTIONS == 18
    assert gb.MIN_OOS_REBALANCES == b2n.JUDGE_MIN_REBALANCES == 36
    assert gb.TOP_N == 20 and gb.BLEND == 0.5
    assert gb.NAME == "gbm_blend" and gb.BASE == "composite"


def test_min_data_in_leaf_는_100_과_학습_행_1퍼센트_올림_중_큰_값() -> None:
    assert gb.params_for(0)["min_data_in_leaf"] == 100
    assert gb.params_for(10_000)["min_data_in_leaf"] == 100
    assert gb.params_for(10_001)["min_data_in_leaf"] == 101  # ⌈100.01⌉
    assert gb.params_for(200_000)["min_data_in_leaf"] == 2000
    assert gb.params_for(5)["bagging_freq"] == 1  # 나머지는 PARAMS 그대로


# ---------------------------------------------------------------- 특징 목록


def test_특징_목록은_등록_커밋에_고정된_튜플과_같다() -> None:
    """뒤에 생기는 IC 이름이 조용히 들어가면 여기서 깨진다(12.46 — 들이면 새 판·새 시도)"""
    assert gb.feature_names() == gb.FEATURES
    assert len(gb.FEATURES) == 43 and list(gb.FEATURES) == sorted(set(gb.FEATURES))


def test_특징에서_뺀_이름과_접은_이름() -> None:
    for name in ("credit_balance", "retail_flow", "tp_dispersion", "gbm_pred", "composite",
                 "flow_surge_x_rev", "pairs_gap_x_rev", "lt_reversal_x_value", "value", "momentum"):  # fmt: skip
        assert name not in gb.FEATURES
    for name in ("flow_surge", "pairs_gap", "lt_reversal", "sue", "ep", "mdd_recovery_days"):
        assert name in gb.FEATURES
    assert "mdd_unrecovered_rows" not in gb.FEATURES  # FACTOR_METRICS 에 없는 중간값(좁은 범위)
    assert "momentum_echo" not in gb.FEATURES  # A/B 변형 지표는 등록 지표가 아니다


# ---------------------------------------------------------------- 백분위


def test_구분_안_백분위는_평균_순위를_값이_있는_수로_나눈다() -> None:
    values = {1: 10.0, 2: 20.0, 3: 20.0, 4: None, 5: math.nan, 6: 5.0, 7: 1.0, 8: -3.0}
    group = {1: "KOSPI", 2: "KOSPI", 3: "KOSPI", 4: "KOSPI", 5: "KOSPI", 6: "KOSDAQ", 8: "KOSDAQ"}
    p = gb.percentile_by_group(values, group)
    assert p[1] == pytest.approx(1 / 3)
    assert p[2] == p[3] == pytest.approx(2.5 / 3)  # 동점 = 평균 순위
    assert math.isnan(p[4]) and math.isnan(p[5])  # 결측은 NaN 그대로
    assert p[8] == pytest.approx(1 / 2) and p[6] == pytest.approx(2 / 2)  # 구분마다 따로
    assert math.isnan(p[7])  # 구분 모름
    assert set(p) == set(values)


def test_구분_안_백분위_모두_동점() -> None:
    p = gb.percentile_by_group({1: 3.0, 2: 3.0, 3: 3.0}, {1: "NYSE", 2: "NYSE", 3: "NYSE"})
    assert p == {1: pytest.approx(2 / 3), 2: pytest.approx(2 / 3), 3: pytest.approx(2 / 3)}


# ---------------------------------------------------------------- 학습 자르기


def test_학습_단면은_r_빼기_3_까지() -> None:
    secs = [_section(i, rows=3) for i in range(10)]
    assert gb.train_sections(9, secs) == list(range(7))  # 0..6 — r−1(8)·r−2(7) 없음
    assert gb.train_sections(3, secs) == [0]
    assert gb.train_sections(2, secs) == []
    assert gb.train_sections(0, secs) == []


def test_빈_단면은_세지_않고_18_이_되는_첫_리밸런스부터_표본_밖() -> None:
    secs = [_section(i, rows=3) for i in range(25)]
    secs[0] = _empty(0)  # 빈 첫 창(25.1209)
    secs[5] = gb.Section(date="2022-005", x=np.zeros((4, K)), y=np.full(4, np.nan))  # 라벨 없는 행만
    # r = 21 → s ≤ 18 은 19개인데 둘이 비어 17
    assert len(gb.train_sections(21, secs)) == 17 and not gb.is_oos(21, secs)
    assert len(gb.train_sections(22, secs)) == 18 and gb.is_oos(22, secs)
    assert 0 not in gb.train_sections(22, secs) and 5 not in gb.train_sections(22, secs)


def test_쌓기는_라벨_없는_행을_빼고_센다() -> None:
    secs = [_section(i, rows=4) for i in range(6)]
    secs[1].y[[0, 2]] = np.nan
    ts = gb.stack_train(5, secs)  # 0·1·2
    assert ts.used == [0, 1, 2] and ts.rows == 10 and ts.dropped == 2
    assert ts.x.shape == (10, K) and np.isfinite(ts.y).all()
    assert (ts.first_date, ts.last_date) == ("2022-000", "2022-002")


# ---------------------------------------------------------------- 섞기


def test_섞은_점수는_두_백분위의_반반_평균() -> None:
    comp = {1: 1.0, 2: 2.0, 3: 3.0}
    pred = {1: 3.0, 2: 1.0, 3: 2.0}
    s = gb.blend_scores(comp, pred)
    assert s[1] == pytest.approx(0.5 * 1 / 3 + 0.5 * 1.0)
    assert s[2] == pytest.approx(0.5 * 2 / 3 + 0.5 * 1 / 3)
    assert s[3] == pytest.approx(0.5 * 1.0 + 0.5 * 2 / 3)
    assert gb.blend_pick(comp, pred) == [3, 1, 2]


def test_섞기_집합은_두_값이_모두_있는_종목만() -> None:
    comp = {1: 1.0, 2: 2.0, 3: None, 4: 4.0, 5: 5.0}
    pred = {1: 0.1, 2: 0.2, 3: 9.9, 4: None, 5: math.nan, 6: 9.9}
    s = gb.blend_scores(comp, pred)
    assert set(s) == {1, 2}  # n = 2 — 백분위 분모도 2
    assert s[2] == pytest.approx(1.0) and s[1] == pytest.approx(0.5)
    assert gb.blend_pick(comp, pred) == [2, 1]
    assert gb.blend_pick({}, {}) == []


def test_섞은_점수_동점은_composite_높은_순_다음_stock_id_작은_순() -> None:
    # 순위 합이 모두 5 — 섞은 점수 넷이 정확히 같다
    comp = {10: 4.0, 11: 1.0, 12: 2.0, 13: 3.0}
    pred = {10: 0.1, 11: 0.9, 12: 0.5, 13: 0.3}
    assert len(set(gb.blend_scores(comp, pred).values())) == 1
    assert gb.blend_pick(comp, pred) == [10, 13, 12, 11]
    # composite 까지 같으면 stock_id 작은 순
    assert gb.blend_pick({21: 1.0, 20: 1.0, 22: 0.0}, {21: 0.5, 20: 0.5, 22: 0.0}) == [20, 21, 22]


def test_상위_20_까지만() -> None:
    comp = {i: float(i) for i in range(50)}
    pred = {i: float(i) for i in range(50)}
    assert gb.blend_pick(comp, pred) == list(range(49, 29, -1))
    assert gb.blend_pick(comp, pred, top_n=3) == [49, 48, 47]


# ---------------------------------------------------------------- 지문


def test_지문은_구성마다_바뀌고_초모수_순서와_무관() -> None:
    p = gb.params_for(2000)
    base = gb.fingerprint(p, gb.FEATURES, "2021-01-04", "2022-06-01", 2000, "abc")
    assert base == gb.fingerprint(dict(reversed(list(p.items()))), gb.FEATURES, "2021-01-04", "2022-06-01", 2000,
                                  "abc")  # fmt: skip
    assert len(base) == 64
    다른 = [
        gb.fingerprint(p, gb.FEATURES, "2021-01-04", "2022-06-01", 2000, "abc", lightgbm_version="4.5.0"),
        gb.fingerprint({**p, "num_leaves": 31}, gb.FEATURES, "2021-01-04", "2022-06-01", 2000, "abc"),
        gb.fingerprint(p, gb.FEATURES[:-1], "2021-01-04", "2022-06-01", 2000, "abc"),
        gb.fingerprint(p, gb.FEATURES, "2021-01-05", "2022-06-01", 2000, "abc"),
        gb.fingerprint(p, gb.FEATURES, "2021-01-04", "2022-07-01", 2000, "abc"),
        gb.fingerprint(p, gb.FEATURES, "2021-01-04", "2022-06-01", 2001, "abc"),
        gb.fingerprint(p, gb.FEATURES, "2021-01-04", "2022-06-01", 2000, "abd"),
    ]
    assert base not in 다른 and len(set(다른)) == len(다른)


# ---------------------------------------------------------------- 적합


def test_같은_입력_두_번은_같은_모델_문자열() -> None:
    secs = [_section(i) for i in range(21)]
    p1, m1 = _predict(20, secs)
    p2, m2 = _predict(20, secs)
    assert hashlib.sha256(m1.encode()).hexdigest() == hashlib.sha256(m2.encode()).hexdigest()
    assert np.array_equal(p1, p2)
    assert np.std(p1) > 0  # 나무가 실제로 갈라졌다


def test_미래_미끼_r_1_r_2_를_바꿔도_예측이_같다() -> None:
    r = 20
    secs = [_section(i) for i in range(r + 1)]
    p0, m0 = _predict(r, secs)
    미끼 = list(secs)
    for s in (r - 1, r - 2):
        rng = np.random.default_rng(1000 + s)
        미끼[s] = gb.Section(date=secs[s].date, x=rng.uniform(size=(500, K)), y=rng.normal(size=500) * 100)
    p1, m1 = _predict(r, 미끼)
    assert np.array_equal(p0, p1) and m0 == m1
    # 선정도 한 칸도 안 바뀐다
    comp = {i: float(i % 7) for i in range(len(p0))}
    assert gb.blend_pick(comp, dict(enumerate(p0))) == gb.blend_pick(comp, dict(enumerate(p1)))
    # 시험이 둔하지 않은지: r−3 을 바꾸면 달라진다
    미끼[r - 3] = 미끼[r - 1]
    p2, _ = _predict(r, 미끼)
    assert not np.array_equal(p0, p2)


def test_TreeSHAP_기여_합은_예측과_같다() -> None:
    secs = [_section(i) for i in range(21)]
    pred, model = _predict(20, secs)
    c = gb.shap_contrib(model, secs[20].x)
    assert c.shape == (len(secs[20].y), K + 1)
    assert np.max(np.abs(c.sum(axis=1) - pred)) <= 1e-9


# ---------------------------------------------------------------- 실패 가두기


def test_적합_실패는_전용_예외로() -> None:
    x = np.random.default_rng(0).uniform(size=(300, K))
    y = x[:, 0].copy()
    with pytest.raises(gb.GbmFailure):
        gb.fit_predict(np.empty((0, K)), np.empty(0), x)  # 학습 행 없음
    bad = y.copy()
    bad[3] = np.nan
    with pytest.raises(gb.GbmFailure):
        gb.fit_predict(x, bad, x)  # 라벨 NaN
    with pytest.raises(gb.GbmFailure):
        gb.fit_predict(x, y, x[:, :2])  # 열 수 다름
    with pytest.raises(gb.GbmFailure):
        gb.shap_contrib("망가진 모델", x)


def test_lightgbm_내부_예외도_전용_예외로(monkeypatch: pytest.MonkeyPatch) -> None:
    import lightgbm

    def boom(*a: object, **k: object) -> None:
        raise lightgbm.basic.LightGBMError("합성 실패")

    monkeypatch.setattr(lightgbm, "train", boom)
    x = np.random.default_rng(0).uniform(size=(300, K))
    with pytest.raises(gb.GbmFailure):
        gb.fit_predict(x, x[:, 0], x)


def test_lightgbm_판이_다르면_실패(monkeypatch: pytest.MonkeyPatch) -> None:
    import lightgbm

    monkeypatch.setattr(lightgbm, "__version__", "4.5.0")
    x = np.random.default_rng(0).uniform(size=(300, K))
    with pytest.raises(gb.GbmFailure):
        gb.fit_predict(x, x[:, 0], x)


def test_걸린_시간_1800초를_넘으면_실패() -> None:
    gb.check_budget(0)
    gb.check_budget(1800)
    with pytest.raises(gb.GbmFailure):
        gb.check_budget(1800.5)


def test_lightgbm_없어도_import_된다() -> None:
    try:
        with pytest.MonkeyPatch.context() as m:
            m.setitem(sys.modules, "lightgbm", None)  # import lightgbm → ImportError
            mod = importlib.reload(gb)
            assert mod.FEATURES == gb.FEATURES
            assert mod.blend_pick({1: 1.0}, {1: 0.0}) == [1]  # GBM 밖 계산은 그대로
            with pytest.raises(mod.GbmFailure):
                mod.fit_predict(np.zeros((200, K)), np.zeros(200), np.zeros((1, K)))
    finally:
        importlib.reload(gb)
