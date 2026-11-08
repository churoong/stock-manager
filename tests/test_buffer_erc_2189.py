"""43회차 e3 `buffer_erc` 계산 — 수축 공분산·ERC·상한·패널 규칙 (docs/factors.md 12.46, docs/infra.md 25.2189).

전략 줄(`STRATEGIES`)에 잇는 일은 2026-12 판정 실행이 두 나라 모두 닫힌 뒤다(12.46 일정) — 여기선 순수 계산만 묶는다.
"""

from __future__ import annotations

import numpy as np
import pytest

from batch.services import buffer_erc as be


def _lw_reference(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit·Wolf covCor.m 을 그대로 옮긴 대조(행렬 꼴) — 구현과 다른 길로 같은 값을 내는지 본다"""
    t, n = x.shape
    x = x - x.mean(axis=0)
    sample = x.T @ x / t
    var = np.diag(sample)
    sq = np.sqrt(var)
    rbar = ((sample / np.outer(sq, sq)).sum() - n) / (n * (n - 1))
    prior = rbar * np.outer(sq, sq)
    np.fill_diagonal(prior, var)
    phi_mat = (x**2).T @ (x**2) / t - sample**2
    theta = ((x**3).T @ x) / t - var[:, None] * sample
    np.fill_diagonal(theta, 0.0)
    rho = np.trace(phi_mat) + rbar * ((np.outer(1 / sq, sq)) * theta).sum()
    gamma = ((sample - prior) ** 2).sum()
    d = max(0.0, min(1.0, (phi_mat.sum() - rho) / gamma / t))
    return d * prior + (1 - d) * sample, d


def test_수축_공분산은_원논문_식과_같다() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(126, 8)) @ rng.normal(size=(8, 8)) * 0.01
    got, d = be.ledoit_wolf_cc(x)
    ref, dr = _lw_reference(x)
    assert d == pytest.approx(dr, abs=1e-12) and 0 < d < 1
    assert np.allclose(got, ref, atol=1e-14)


def test_ERC_는_위험_기여가_같다() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(size=(6, 6))
    cov = a @ a.T + np.eye(6) * 0.1
    w, ok, _ = be.erc_weights(cov)
    rc = w * (cov @ w)
    assert ok and w.sum() == pytest.approx(1.0) and (w > 0).all()
    assert np.max(np.abs(rc / rc.mean() - 1)) <= 1e-7


def test_상관이_같으면_ERC_는_역변동성이다() -> None:
    sd = np.array([0.1, 0.2, 0.4])
    cov = 0.3 * np.outer(sd, sd)
    np.fill_diagonal(cov, sd**2)
    w, ok, _ = be.erc_weights(cov)
    inv = (1 / sd) / (1 / sd).sum()
    assert ok and np.allclose(w, inv, atol=1e-7)


def test_상한을_넘는_몫은_안_걸린_종목에_비례로() -> None:
    w = be.cap_weights({1: 0.5, 2: 0.3, 3: 0.2}, 0.4)
    assert w[1] == pytest.approx(0.4) and sum(w.values()) == pytest.approx(1.0)
    assert w[2] / w[3] == pytest.approx(1.5)  # 남은 둘의 비는 그대로
    # 되풀이: 다시 나눈 뒤 넘는 종목도 자른다
    w2 = be.cap_weights({1: 0.6, 2: 0.35, 3: 0.05}, 0.4)
    assert max(w2.values()) <= 0.4 + 1e-12 and sum(w2.values()) == pytest.approx(1.0)


def _축(n: int = 130) -> list[str]:
    return [f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}" for i in range(n)]


def _시세(축: list[str], sd: float, seed: int, 빔: set[int] | None = None) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    p, out = 100.0, {}
    for i, d in enumerate(축):
        p *= 1 + rng.normal(0, sd)
        if not 빔 or i not in 빔:
            out[d] = p
    return out


def test_비중은_n_합_1_상한_안이고_저변동이_더_담긴다() -> None:
    축 = _축()
    고름 = list(range(1, 21))
    시세 = {s: _시세(축, 0.005 + 0.002 * s, s) for s in 고름}
    tr = be.ErcTrace()
    w = be.weights(고름, 축, 시세, kr=False, trace=tr)
    assert set(w) == set(고름) and sum(w.values()) == pytest.approx(1.0)
    assert max(w.values()) <= max(be.CAP, 1 / 20) + 1e-12
    assert w[1] > w[20] and tr.converged and tr.erc_n == 20 and tr.common_days == 126
    assert tr.delta is not None and tr.inv_vol_l1 is not None


def test_유효_수익_60_미만·국내_30퍼센트_날은_1_n_몫() -> None:
    축 = _축()
    시세 = {s: _시세(축, 0.01, s) for s in (1, 2, 3, 4)}
    시세[4] = _시세(축, 0.01, 4, 빔=set(range(0, 80)))  # 유효 수익 < 60
    큰날 = dict(시세[3])
    d = 축[100]
    큰날[d] = 큰날[d] * 1.5  # 하루 +50% 근처(수정 안 된 기업행위 흉내)
    시세[3] = 큰날
    tr = be.ErcTrace()
    w = be.weights([1, 2, 3, 4], 축, 시세, kr=True, trace=tr)
    assert w[3] == pytest.approx(0.25) and w[4] == pytest.approx(0.25) and set(tr.to_equal) == {3, 4}
    assert w[1] + w[2] == pytest.approx(0.5)
    # 미국은 30% 날 거름을 쓰지 않는다
    tr2 = be.ErcTrace()
    be.weights([1, 2, 3, 4], 축, 시세, kr=False, trace=tr2)
    assert 3 not in tr2.to_equal


def test_공통_날이_모자라면_유효_수익_적은_종목부터_뺀다() -> None:
    축 = _축()
    시세 = {1: _시세(축, 0.01, 1), 2: _시세(축, 0.01, 2, 빔=set(range(0, 50))),
            3: _시세(축, 0.01, 3, 빔=set(range(70, 125)))}  # fmt: skip
    # 2 는 앞 50 날, 3 은 뒤 55 날이 빔 — 셋 공통은 60 미만, 3 이 유효 수익이 더 적다
    tr = be.ErcTrace()
    w = be.weights([1, 2, 3], 축, 시세, kr=False, trace=tr)
    assert tr.to_equal == [3] and w[3] == pytest.approx(1 / 3) and tr.erc_n == 2


def test_유효_수익이_같으면_stock_id_큰_쪽부터_뺀다() -> None:
    축 = _축(127)  # 창 = 127 종가 날 그대로
    시세 = {1: _시세(축, 0.01, 1), 2: _시세(축, 0.01, 2, 빔=set(range(0, 50))),
            3: _시세(축, 0.01, 3, 빔=set(range(77, 127)))}  # fmt: skip
    # 2·3 모두 유효 수익 76 — 셋 공통은 26 < 60, 같으면 큰 쪽(3)부터(12.46 검증 B 방향)
    tr = be.ErcTrace()
    be.weights([1, 2, 3], 축, 시세, kr=False, trace=tr)
    assert tr.to_equal == [3] and tr.erc_n == 2 and tr.common_days == 76


def test_스무_종목에서_1_n_몫을_먼저_떼고_상한_넘는_몫은_1_n_종목도_받는다() -> None:
    """n > 10 이라 상한(10%)이 1/n(5%)보다 커서 비중 단언이 공허하지 않다(25.2190 교차검증)"""
    축 = _축()
    고름 = list(range(1, 21))
    시세 = {s: _시세(축, 0.005 + 0.002 * s, s) for s in 고름}
    시세[20] = _시세(축, 0.045, 20, 빔=set(range(0, 80)))  # 유효 수익 < 60 → 1/n 몫
    tr = be.ErcTrace()
    w = be.weights(고름, 축, 시세, kr=False, trace=tr)
    assert tr.to_equal == [20] and tr.erc_n == 19
    assert sum(w.values()) == pytest.approx(1.0)  # 1/n 몫을 떼지 않으면 1.05 (레버리지)
    assert max(w.values()) == pytest.approx(be.CAP)  # 저변동 종목이 상한에 걸린다
    assert w[20] > 1 / 20 + 1e-6  # 넘는 몫을 1/n 몫 종목도 비례로 받는다(12.46 '모든 종목')


def test_하한_60_은_포함이고_59_는_1_n() -> None:
    축 = _축(127)
    시세 = {1: _시세(축, 0.01, 1), 2: _시세(축, 0.01, 2, 빔=set(range(0, 66))),
            3: _시세(축, 0.01, 3, 빔=set(range(0, 67)))}  # fmt: skip
    # 2 는 유효 수익 60(공통 날도 60), 3 은 59
    tr = be.ErcTrace()
    be.weights([1, 2, 3], 축, 시세, kr=False, trace=tr)
    assert tr.to_equal == [3] and tr.erc_n == 2 and tr.common_days == 60


def test_수축_강도는_0_과_1_사이로_자른다() -> None:
    x = np.random.default_rng(0).normal(size=(6, 4))  # 날 6 — 자르기 전 κ̂/T ≈ 1.46
    _, d = be.ledoit_wolf_cc(x)
    assert d == 1.0


def test_공통_날_수익이_평평한_종목은_1_n_몫이고_나머지는_수렴한다() -> None:
    축 = _축()
    고름 = list(range(1, 14))
    시세 = {s: _시세(축, 0.005 + 0.002 * s, s) for s in 고름}
    시세[13] = {d: 50.0 for d in 축}  # 정지 중 같은 종가 되풀이
    tr = be.ErcTrace()
    with np.errstate(all="raise"):
        w = be.weights(고름, 축, 시세, kr=True, trace=tr)
    assert tr.to_equal == [13] and tr.converged is True and tr.erc_n == 12
    assert sum(w.values()) == pytest.approx(1.0) and w[13] >= 1 / 13 - 1e-12


def test_수렴_실패_기록은_ERC_를_한_것처럼_남기지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(be, "MAX_SWEEPS", 1)
    monkeypatch.setattr(be, "TOL", 0.0)
    축 = _축()
    고름 = list(range(1, 21))
    시세 = {s: _시세(축, 0.003 * s, s) for s in 고름}
    tr = be.ErcTrace()
    be.weights(고름, 축, 시세, kr=False, trace=tr)
    assert tr.converged is False and tr.erc_n == 0 and tr.to_equal == 고름


def test_ERC_대상이_둘_미만이면_모두_1_n() -> None:
    축 = _축()
    시세 = {1: _시세(축, 0.01, 1), 2: _시세(축, 0.01, 2, 빔=set(range(0, 100)))}
    assert be.weights([1, 2], 축, 시세, kr=False) == {1: 0.5, 2: 0.5}
    assert be.weights([], 축, 시세, kr=False) == {}


def test_수렴하지_못하면_리밸런스_전체를_1_n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(be, "MAX_SWEEPS", 1)
    monkeypatch.setattr(be, "TOL", 0.0)
    # 20종목 — 10 종목 이하면 상한 max(10%, 1/n) = 1/n 이 늘 동일가중으로 묶어 이 갈래를 가린다
    축 = _축()
    고름 = list(range(1, 21))
    시세 = {s: _시세(축, 0.003 * s, s) for s in 고름}
    tr = be.ErcTrace()
    w = be.weights(고름, 축, 시세, kr=False, trace=tr)
    assert tr.converged is False and all(x == pytest.approx(1 / 20) for x in w.values())


def test_고른_수가_10_이하면_상한이_1_n_이라_동일가중이다() -> None:
    """알고 두는 한계(12.46) — 상한 max(10%, 1/n) 은 n ≤ 10 이면 모든 비중을 1/n 으로 묶는다"""
    축 = _축()
    시세 = {s: _시세(축, 0.004 * s, s) for s in range(1, 6)}
    w = be.weights(list(range(1, 6)), 축, 시세, kr=False)
    assert all(x == pytest.approx(0.2) for x in w.values())


def test_사전_등록_값() -> None:
    assert (be.WINDOW_DAYS, be.MIN_POINTS, be.KR_DAILY_LIMIT, be.CAP, be.TOL, be.MAX_SWEEPS) == (
        126,
        60,
        0.30,
        0.10,
        1e-8,
        10_000,
    )
    from batch.services import high52, report_picks

    assert be.MIN_POINTS == report_picks.CORR_MIN_POINTS and be.KR_DAILY_LIMIT == high52.KR_DAILY_LIMIT
