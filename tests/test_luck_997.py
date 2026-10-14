"""운인가 실력인가 — PSR·DSR (docs/backtest.md 9장, docs/infra.md 25.997). 손으로 계산한 값과 대조한다."""

from __future__ import annotations

import math

from batch.services import luck


def test_월말_자산끼리_잇는다() -> None:
    curve = [("2026-01-05", 1.0), ("2026-01-30", 1.1), ("2026-02-02", 1.05), ("2026-02-27", 1.21), ("2026-03-31", 1.089)]
    r = luck.monthly_returns(curve)
    assert list(r) == ["2026-02", "2026-03"]
    assert math.isclose(r["2026-02"], 1.21 / 1.1 - 1) and math.isclose(r["2026-03"], 1.089 / 1.21 - 1)


def test_PSR_손계산() -> None:
    # SR 0.2, 60개월, 정규(왜도 0·첨도 3): 분모 √(1 + 0.5·0.04) = √1.02, z = 0.2·√59/√1.02 = 1.5211 → Φ = 0.9359
    assert math.isclose(luck.psr(0.2, 0.0, 60, 0.0, 3.0), 0.9359, abs_tol=5e-4)


def test_시도가_많을수록_문턱이_오른다() -> None:
    # N 10, V 0.01: 0.1·(0.4228·Φ⁻¹(0.9) + 0.5772·Φ⁻¹(1 − 1/(10e))) = 0.1·(0.4228·1.2816 + 0.5772·1.7897) ≈ 0.1575
    assert math.isclose(luck.expected_max_sr(10, 0.01), 0.1575, abs_tol=5e-4)
    assert luck.expected_max_sr(1, 0.01) == 0.0
    assert luck.expected_max_sr(50, 0.01) > luck.expected_max_sr(10, 0.01)


def test_판정_한_전략이면_DSR_은_PSR_과_같고_짧으면_판정_안_함() -> None:
    xs = [0.01 if i % 3 else -0.005 for i in range(36)]
    [one] = luck.judge({"a": xs}).values()
    assert one["n_trials"] == 1 and one["sr_star"] == 0.0 and one["dsr"] == one["psr"]
    assert math.isclose(one["luck_pct"], round((1 - one["dsr"]) * 100, 1))
    short = luck.judge({"b": xs[: luck.MIN_MONTHS - 1]})["b"]
    assert "luck_pct" not in short
    assert "판정 안 함" in short["verdict"]


def _잡음(seed: int, n: int, mean: float) -> list[float]:
    """재현되는 의사난수(선형 합동) — 평균 mean, 대략 ±3%."""
    out, x = [], seed
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2**31
        out.append(mean + (x / 2**31 - 0.5) * 0.1)
    return out


def test_같은_수익도_시도가_많으면_우연일_확률이_오른다() -> None:
    좋음 = _잡음(7, 48, 0.006)
    혼자 = luck.judge({"좋음": 좋음})["좋음"]
    여럿 = luck.judge({"좋음": 좋음, **{f"n{k}": _잡음(100 + k, 48, 0.0) for k in range(19)}})["좋음"]
    assert 여럿["n_trials"] == 20 and 여럿["sr_star"] > 0
    assert 여럿["luck_pct"] > 혼자["luck_pct"]


def test_한_줄() -> None:
    j = {"luck_pct": 12.3, "n_trials": 8, "months": 60, "sr_monthly": 0.21, "sr_star": 0.15}
    assert luck.line(j) == "우연일 확률 12% (DSR — 시도 8개, 60개월, 월 초과 샤프 +0.21 · 문턱 0.15)"


def test_시도_수_하한은_더_보수적으로만() -> None:
    """일부만 돌린 실행(N=3)이 같은 전략을 전체 실행(N=12)보다 덜 운으로 보지 않는다 (25.1358, 23회차 P4)."""
    import random

    from batch.jobs import backtest as bt_job

    rnd = random.Random(7)
    묶음 = {f"s{i}": [rnd.gauss(0.004 * (i + 1), 0.03) for _ in range(48)] for i in range(3)}
    셋 = luck.judge(묶음)
    하한 = luck.judge(묶음, n_floor=bt_job.REGISTERED_TRIALS)
    for k in 묶음:
        assert 하한[k]["n_trials"] == 12 and 하한[k]["n_trials_run"] == 3
        assert 하한[k]["sr_star"] >= 셋[k]["sr_star"]
        assert 하한[k]["luck_pct"] >= 셋[k]["luck_pct"]
    assert "이 실행 3개" in luck.line(하한["s0"])
    # 기본 실행은 이미 등록 수 — 바뀌지 않는다
    열둘 = {f"s{i}": [rnd.gauss(0.002 * i, 0.03) for _ in range(48)] for i in range(12)}
    assert luck.judge(열둘) == luck.judge(열둘, n_floor=12)
    assert "이 실행" not in luck.line(luck.judge(열둘, n_floor=12)["s1"])
    등록 = len(bt_job.STRATEGIES) - 1 + len(bt_job.DEFAULT_LONG_THRESHOLDS)
    assert 등록 == bt_job.REGISTERED_TRIALS
