"""42회차 u1 `momentum_vs` — 계산 모듈(꺼진 채) 손 계산 시험 (docs/factors.md 12.45 "u1 `momentum_vs` 사전 등록").

e = min(1, σ_B ÷ σ_S), σ = 합집합 축 마지막 126일의 날마다 동일가중 평균 일간 수익 표본표준편차, 국내 |r| > 30% 종목-날·미국
분할 대기 종목 제외, 관측 < 60 이면 e = 1. 판정은 같은 실행 `momentum` 과 — 탈락 조항·두 계열 앞·뒤 반·스패닝 α.
"""

from __future__ import annotations

import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from batch.services import backtest as bt
from batch.services import momentum_vs as mvs

ROOT = Path(__file__).resolve().parents[1]


def _날(i: int) -> str:
    return (date(2024, 1, 1) + timedelta(days=i)).isoformat()


# ── 사전 등록 값 ───────────────────────────────────────────────────────────────
def test_사전_등록_값() -> None:
    from batch.jobs import metrics as jm

    assert (mvs.WINDOW_DAYS, mvs.JUDGE_MIN_REBALANCES, mvs.JUDGE_MDD_SLACK, mvs.KR_DAILY_LIMIT, mvs.BASE) == (
        126,
        36,
        0.02,
        0.30,
        "momentum",
    )
    assert jm.MIN_BENCHMARK_POINTS == 60  # jobs 층이 넘길 값(계산 모듈은 받기만)


def test_jobs_층을_import_하지_않는다() -> None:
    code = (
        "import sys; import batch.services.momentum_vs; "
        "bad = [m for m in sys.modules if m.startswith('batch.jobs')]; assert not bad, bad"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "batch.jobs" not in (ROOT / "batch/services/momentum_vs.py").read_text(encoding="utf-8")


# ── 날짜 축 ──────────────────────────────────────────────────────────────────
def test_날짜_축은_합집합의_cutoff_이하_마지막_126일() -> None:
    a = [(_날(i), 100.0) for i in range(0, 130)]  # 0..129
    b = [(_날(i), 50.0) for i in range(130, 141)]  # 130..140
    축 = mvs.date_axis({1: a, 2: b}, _날(135))
    assert 축 == [_날(i) for i in range(10, 136)]  # 합집합 0..135(136일) 의 마지막 126
    assert len(축) == 126
    # 한 종목 날짜만 보면 다르다(합집합이어야 함)
    assert mvs.date_axis({1: a}, _날(135)) != 축


def test_가격_창에서_합집합을_꺼낸다() -> None:
    view = bt.PriceView({1: {_날(0): 1.0, _날(2): 1.0}, 2: {_날(1): 1.0, _날(3): 1.0}}, _날(2))
    assert mvs.date_axis(mvs.series_of(view), _날(2)) == [_날(0), _날(1), _날(2)]


# ── σ 손 계산 ─────────────────────────────────────────────────────────────────
def test_σ_손_계산_날마다_동일가중_빈_날_빠짐() -> None:
    # 축 d0..d4. 종목 1: 100,110,99,_,99 / 종목 2: 50,_,55,55,44
    s = {
        1: [(_날(0), 100.0), (_날(1), 110.0), (_날(2), 99.0), (_날(4), 99.0)],
        2: [(_날(0), 50.0), (_날(2), 55.0), (_날(3), 55.0), (_날(4), 44.0)],
    }
    축 = mvs.date_axis(s, _날(4))
    rs = mvs.daily_mean_returns([1, 2], s, 축, kr=False)
    # d1: 종목1 +0.10(종목2 d1 없음) / d2: 종목1 −0.10, 종목2 d1 없어 빠짐 → −0.10
    # d3: 종목1 d3 없음, 종목2 0 → 0 / d4: 종목1 d3 없음, 종목2 −0.2 → −0.2
    assert rs == pytest.approx([0.10, -0.10, 0.0, -0.20])
    평균 = -0.05
    손 = math.sqrt(((0.15) ** 2 + (-0.05) ** 2 + (0.05) ** 2 + (-0.15) ** 2) / 3)  # ddof 1
    assert mvs.m.stdev(rs) == pytest.approx(손)
    assert sum(rs) / 4 == pytest.approx(평균)
    # 아무 종목도 없는 날은 관측이 아니다
    assert mvs.daily_mean_returns([1], {1: [(_날(0), 1.0), (_날(2), 1.1)]}, [_날(0), _날(1), _날(2)], kr=False) == []


def _흔들림(n: int, amp: float, sid_shift: int = 0) -> list[tuple[str, float]]:
    """날마다 ±amp 번갈아(평균 0 근처) 움직이는 가격"""
    p, out = 100.0, []
    for i in range(n):
        out.append((_날(i), p))
        p *= 1 + (amp if (i + sid_shift) % 2 == 0 else -amp)
    return out


def test_관측_59_면_e_1_이고_60_이면_스케일() -> None:
    벤치 = {10 + k: _흔들림(200, 0.005, k) for k in range(2)}  # 벤치 σ 작음, 관측 많음
    for 길이, 기대 in ((60, 1.0), (61, None)):  # 날 60 → 관측 59, 날 61 → 관측 60
        s = {1: [(d, c) for d, c in _흔들림(200, 0.03)][200 - 길이 :], **벤치}
        w, tr = mvs.scale({1: 1.0}, s, 벤치.keys(), _날(199), kr=False, min_points=60)
        assert tr.n_s == 길이 - 1
        if 기대 is not None:
            assert tr.e == 1.0 and "관측 부족" in tr.why_one
        else:
            assert tr.e < 1 and tr.why_one is None
            assert tr.e == pytest.approx(tr.sigma_b / tr.sigma_s)
        assert w == {1: pytest.approx(tr.e)}


def test_e_는_1_을_넘지_않는다_와_비중_e_나누기_P() -> None:
    s = {1: _흔들림(130, 0.001), 2: _흔들림(130, 0.001, 1), 9: _흔들림(130, 0.05)}
    w, tr = mvs.scale({1: 0.5, 2: 0.5}, s, [9], _날(129), kr=False, min_points=60)
    assert tr.sigma_b > tr.sigma_s and tr.e == 1.0 and tr.why_one is None
    assert mvs.exposure(0.01, 100, 0.03, 100, 60) == (1.0, None)
    assert mvs.exposure(0.0, 100, 0.03, 100, 60)[0] == 1.0
    assert mvs.exposure(None, 100, 0.03, 100, 60)[0] == 1.0
    assert mvs.exposure(0.04, 100, 0.01, 100, 60) == (0.25, None)
    w2, tr2 = mvs.scale({1: 0.5, 2: 0.5}, s, [1, 2], _날(129), kr=False, min_points=60)
    assert sum(w2.values()) == pytest.approx(tr2.e) and set(w2) == {1, 2}


def test_창은_마지막_126일만() -> None:
    # 앞쪽 큰 흔들림은 창 밖이면 σ 에 들지 않는다
    s = {1: [(d, c) for d, c in _흔들림(300, 0.2)][:150] + [(_날(i), 1.0) for i in range(150, 300)]}
    rs = mvs.daily_mean_returns([1], s, mvs.date_axis(s, _날(299)), kr=False)
    assert len(rs) == 125 and all(r == 0 for r in rs)


def test_국내_30퍼센트_넘는_종목_날은_뺀다_정확히_30은_남긴다() -> None:
    s = {
        1: [(_날(0), 100.0), (_날(1), 150.0), (_날(2), 150.0), (_날(3), 195.0)],
        2: [(_날(0), 10.0), (_날(1), 11.0), (_날(2), 11.0), (_날(3), 11.0)],
    }
    축 = mvs.date_axis(s, _날(3))
    kr = mvs.daily_mean_returns([1, 2], s, 축, kr=True)
    us = mvs.daily_mean_returns([1, 2], s, 축, kr=False)
    assert kr == pytest.approx([0.10, 0.0, 0.15])  # d1 종목1 +50% 빠짐, d3 +30% 남음
    assert us == pytest.approx([0.30, 0.0, 0.15])
    # 그 종목만 있는 날이면 그날 관측 자체가 빠진다
    assert mvs.daily_mean_returns([1], s, 축, kr=True) == pytest.approx([0.0, 0.30])


def test_미국_분할_대기_종목은_σ_에서_빼고_비중은_그대로() -> None:
    s = {1: _흔들림(130, 0.01), 2: _흔들림(130, 0.08), 3: _흔들림(130, 0.004)}
    w, tr = mvs.scale({1: 0.5, 2: 0.5}, s, [2, 3], _날(129), kr=False, min_points=60, split_pending={2})
    rs = mvs.daily_mean_returns([1], s, mvs.date_axis(s, _날(129)), kr=False)
    rb = mvs.daily_mean_returns([3], s, mvs.date_axis(s, _날(129)), kr=False)
    assert tr.sigma_s == pytest.approx(mvs.m.stdev(rs)) and tr.sigma_b == pytest.approx(mvs.m.stdev(rb))
    assert set(w) == {1, 2} and w[2] == pytest.approx(tr.e / 2)
    # 국내에는 분할 대기 제외를 쓰지 않는다
    _w, kr = mvs.scale({1: 0.5, 2: 0.5}, s, [2, 3], _날(129), kr=True, min_points=60, split_pending={2})
    assert kr.sigma_s != pytest.approx(tr.sigma_s)


# ── 판정 ─────────────────────────────────────────────────────────────────────
def _곡선(rets: list[float]) -> list[tuple[str, float]]:
    """2020-01 부터 달마다 한 점(15일). 첫 점 1.0 — luck.monthly_returns 가 달 수익을 그대로 돌려준다"""
    eq, out = 1.0, [("2020-01-15", 1.0)]
    for i, r in enumerate(rets, start=1):
        eq *= 1 + r
        y, mo = 2020 + i // 12, i % 12 + 1
        out.append((f"{y}-{mo:02d}-15", eq))
    return out


def _지표(vs: dict, mom: dict):
    calls = iter([vs, mom])

    def summarize(result, bench, risk_free_annual=None):
        x = next(calls)
        return SimpleNamespace(metrics=SimpleNamespace(**x))

    return summarize


좋음 = {"cagr": 0.12, "sharpe": 0.6, "mdd": -0.40}
짝 = {"cagr": 0.10, "sharpe": 0.5, "mdd": -0.45}
N = 40
X = [0.03 + (0.02 if i % 2 else -0.02) + 0.001 * (i % 5) for i in range(N)]
끝 = _곡선(X)[-1][0]  # 마지막 달 = 실행한 달


def _판정(vs_rets, mom_rets=X, *, vs=좋음, mom=짝, rebalances=40, rf=0.03, exposures=()):
    return mvs.judge(
        _곡선(vs_rets),
        _곡선(mom_rets),
        rebalances=rebalances,
        risk_free_annual=rf,
        trade_date=끝,
        exposures=exposures,
        summarize=_지표(vs, mom),
    )


def test_통과_와_기록() -> None:
    vs = [x + 0.003 for x in X]
    j = _판정(vs, exposures=[1.0, 0.5, 0.8])
    assert j["verdict"] == "통과", j
    sp = j["series"]["full"]["spanning"]
    assert sp["alpha"] == pytest.approx(0.003) and sp["beta"] == pytest.approx(1.0) and sp["n"] == N
    assert j["series"]["ex_partial"]["spanning"]["n"] == N - 1
    assert j["exposure"] == {"e_mean": pytest.approx(2.3 / 3), "e_min": 0.5, "e_below_1": 2}


def test_스패닝_t_는_보통_표준오차() -> None:
    x = [0.01, -0.02, 0.03, 0.0, 0.02]
    y = [0.012, -0.015, 0.031, 0.004, 0.019]
    sp = mvs.spanning(y, x)
    n, xm, ym = 5, sum(x) / 5, sum(y) / 5
    sxx = sum((a - xm) ** 2 for a in x)
    b = sum((a - xm) * (c - ym) for a, c in zip(x, y, strict=True)) / sxx
    a0 = ym - b * xm
    s2 = sum((c - a0 - b * a) ** 2 for a, c in zip(x, y, strict=True)) / (n - 2)
    assert sp["beta"] == pytest.approx(b) and sp["alpha"] == pytest.approx(a0)
    assert sp["t"] == pytest.approx(a0 / math.sqrt(s2 * (1 / n + xm**2 / sxx)))
    assert mvs.spanning([0.1, 0.2, 0.3], [0.01, 0.01, 0.01]) is None  # r_mom 분산 0
    assert mvs.spanning([0.1, 0.2], [0.0, 0.1]) is None  # 관측 < 3


def test_앞뒤_반_양이어도_α_가_0_이하면_탈락() -> None:
    vs = [-0.001 + 1.5 * x for x in X]  # 초과 = −0.001 + 0.5x > 0 — 반은 모두 양
    j = _판정(vs)
    for s in ("full", "ex_partial"):
        assert all(h > 0 for h in j["series"][s]["halves"])
        assert j["series"][s]["spanning"]["alpha"] == pytest.approx(-0.001)
    assert j["verdict"] == "탈락" and any("α" in w for w in j["why"])
    j2 = _판정([0.001 + 1.5 * x for x in X])
    assert j2["verdict"] == "통과"


def test_두_계열이_갈리면_모름() -> None:
    ex = [0.002] * 20 + [-0.001] * 19 + [0.2]  # 마지막(부분) 달 큰 초과
    mom = X[:-1] + [sum(X[:-1]) / (N - 1)]  # 끝 달 짝 수익을 평균에 — 큰 초과가 β 로 새지 않게
    j = _판정([x + e for x, e in zip(mom, ex, strict=True)], mom)
    full, cut = j["series"]["full"], j["series"]["ex_partial"]
    assert all(h > 0 for h in full["halves"]) and full["spanning"]["alpha"] > 0
    assert cut["halves"][1] < 0  # 끝 달을 빼면 뒤 반이 음
    assert j["series_verdict"] == {"full": "통과", "ex_partial": "탈락"}
    assert j["verdict"] == "모름"


def test_MDD_는_음수_그대로_더_나쁜_MDD_는_통과_못_한다() -> None:
    vs = [x + 0.003 for x in X]
    assert _판정(vs, vs={**좋음, "mdd": -0.48})["verdict"] == "탈락"  # −0.48 < −0.45 − 0.02
    assert _판정(vs, vs={**좋음, "mdd": -0.46})["verdict"] == "통과"  # 1%p 나쁨은 허용
    assert _판정(vs, vs={**좋음, "mdd": -0.20})["verdict"] == "통과"  # 더 좋은 MDD


def test_CAGR_샤프_같으면_탈락() -> None:
    vs = [x + 0.003 for x in X]
    assert _판정(vs, vs={**좋음, "cagr": 0.10})["verdict"] == "탈락"
    assert _판정(vs, vs={**좋음, "sharpe": 0.5})["verdict"] == "탈락"


def test_리밸런스_36_미만은_판정_불가() -> None:
    vs = [x + 0.003 for x in X]
    j = _판정(vs, rebalances=35)
    assert j["verdict"] == "판정 불가" and "35 < 36" in j["why"]
    assert _판정(vs, rebalances=36)["verdict"] == "통과"


def test_빈칸_무위험수익률_α_못냄_계산실패는_판정_불가() -> None:
    vs = [x + 0.003 for x in X]
    j = _판정(vs, rf=None)
    assert j["verdict"] == "판정 불가" and "무위험수익률" in j["why"]
    assert _판정(vs, vs={**좋음, "sharpe": None})["verdict"] == "판정 불가"
    flat = [0.01] * N
    j = _판정([0.012] * N, flat)
    assert j["verdict"] == "판정 불가" and "α" in j["why"]

    def boom(*_a, **_k):
        raise ZeroDivisionError("x")

    j = mvs.judge(_곡선(vs), _곡선(X), rebalances=40, risk_free_annual=0.03, trade_date=끝, summarize=boom)
    assert j["verdict"] == "판정 불가" and j["why"].startswith("계산 실패")


def test_실제_지표_함수로도_돈다() -> None:
    """기본 `bt.summarize`(metrics.compute) 경로 — 일간 곡선 900일"""

    def 일간(drift: float) -> list[tuple[str, float]]:
        eq, out = 1.0, []
        for i in range(900):
            out.append(((date(2020, 1, 1) + timedelta(days=i)).isoformat(), eq))
            eq *= 1 + drift + (0.01 if i % 2 else -0.01)
        return out

    a, b = 일간(0.0006), 일간(0.0004)
    j = mvs.judge(a, b, rebalances=40, risk_free_annual=0.03, trade_date=a[-1][0])
    assert j["metrics"]["momentum_vs"]["cagr"] > j["metrics"]["momentum"]["cagr"]
    assert j["verdict"] in {"통과", "탈락", "모름"}
    assert mvs.judge(a, b, rebalances=40, risk_free_annual=None, trade_date=a[-1][0])["verdict"] == "판정 불가"
