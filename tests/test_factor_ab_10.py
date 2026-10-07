"""지표 교체 A/B — 종목선정 기법 발굴 10회차 (docs/factors.md 12.12, docs/infra.md 25.903).

운영 점수는 바뀌지 않고, 백테스트 한 실행 안에서 현행·교체 구성의 IC 를 함께 쌓아 기록만 한다.
"""

from __future__ import annotations

import pytest

from batch.services import factor_ab as fab
from batch.services import factor_ic as fic
from batch.services import scoring as sc


def test_영업이익_변화_나누기_전년_총자산() -> None:
    # 흑자전환 — 지금 성장률은 None 인데 새 지표는 값이 있다(12.11 진단의 22%)
    assert sc.oi_change_assets(30.0, -10.0, 200.0) == pytest.approx(0.2)
    assert sc.growth_rate(30.0, -10.0) is None
    assert sc.oi_change_assets(30.0, -10.0, 0.0) is None
    assert sc.oi_change_assets(30.0, None, 200.0) is None
    assert sc.growth_metrics(1, 1, 30.0, -10.0, None, 200.0)["oi_change_assets"] == pytest.approx(0.2)


def test_에코_모멘텀은_126_에서_273_거래일() -> None:
    closes = [100.0] * 274
    closes[-1 - 273] = 50.0
    closes[-1 - 126] = 80.0
    assert sc.momentum_metrics(closes)["momentum_echo"] == pytest.approx(0.6)
    assert sc.momentum_metrics(closes[1:])["momentum_echo"] is None  # 273 거래일 전 종가가 없으면 None


def test_운영_점수_구성은_그대로다() -> None:
    """A/B 지표는 점수에도 `raw_json` 에도 들어가지 않는다 — 백테스트가 효용을 보이기 전에는 운영을 바꾸지 않는다."""
    names = {m.name for ms in sc.FACTOR_METRICS.values() for m in ms}
    assert "momentum_echo" not in names and "oi_change_assets" not in names
    assert "momentum_vol_adjusted" in names and "operating_income_growth" in names
    구성 = {k: [m.name for m in v[1]] for k, v in sc.AB_VARIANTS.items()}
    assert 구성["momentum_echo"][-1] == "momentum_echo" and len(구성["momentum_echo"]) == 6
    assert "momentum_vol_adjusted" not in 구성["momentum_drop_vol_adj"] and len(구성["momentum_drop_vol_adj"]) == 5
    assert 구성["growth_oi_assets"] == ["revenue_growth", "oi_change_assets", "revenue_cagr_3y"]


def _period(k: int, n: int = 40) -> tuple[list[sc.StockInput], dict, dict, dict]:
    """B(ΔOI/총자산)는 다음 달 수익률을 대체로 맞히고, A 점수는 거꾸로 — 달마다 조금씩 다른 잡음."""
    inputs, a, ret, mk = [], {}, {}, {}
    for i in range(n):
        r = (i * 7 + k * 3) % n / n
        noise = ((i * 13 + k * 5) % 11) / 40
        inputs.append(sc.StockInput(stock_id=i, market="KOSPI", metrics={
            "revenue_growth": r + noise, "revenue_cagr_3y": r - noise, "oi_change_assets": r,
            "operating_income_growth": None, "piotroski_lite": float(i % 7),
        }))  # fmt: skip
        a[i] = {"growth": -r + noise}
        ret[i] = r
        mk[i] = "KOSPI"
    return inputs, a, ret, mk


def test_한_실행_안에서_A_와_B_를_나란히_쌓고_기준을_적는다() -> None:
    acc = fab.AbAccumulator()
    for k in range(8):
        acc.add_period(*_period(k))
    log = acc.log()
    칸 = log["variants"]["growth_oi_assets"]["by_market"]["KOSPI"]
    assert 칸["a"]["months"] == 8 and 칸["b"]["months"] == 8
    assert 칸["b"]["mean"] > 0 > 칸["a"]["mean"]
    assert 칸["b_minus_a"]["first_half"] > 0 and 칸["b_minus_a"]["second_half"] > 0
    assert 칸["rule_met"] is True
    assert 칸["b"]["coverage"] == 1.0
    assert log["variants"]["growth_oi_assets"]["factor"] == "growth"
    assert "KOSPI" in log["corr"]["oi_change_assets~piotroski_lite"]


def test_한쪽만_있는_달은_견주지_않는다() -> None:
    acc = fab.AbAccumulator()
    inputs, a, ret, mk = _period(0)
    for i in inputs:
        i.metrics["oi_change_assets"] = None  # B 가 절반 규칙에 걸려 그 달 B IC 가 없다
        i.metrics["revenue_cagr_3y"] = None
    acc.add_period(inputs, a, ret, mk)
    칸 = acc.ics["growth_oi_assets"]["KOSPI"]
    assert 칸["a"] == [None] and 칸["b"] == [None]


def test_기준은_평균_t_두_반을_모두_본다() -> None:
    def s(mean: float, t: float, h1: float = 0.1, h2: float = 0.1) -> fic.IcSummary:
        return fic.IcSummary(months=40, mean=mean, std=0.1, t_stat=t, first_half_mean=h1, second_half_mean=h2)

    assert fab.rule_met(s(0.01, 1.0), s(0.02, 2.0), s(0.01, 1.0, 0.01, 0.01)) is True
    assert fab.rule_met(s(0.01, 1.0), s(0.02, 0.5), s(0.01, 1.0, 0.01, 0.01)) is False  # t 가 낮다
    assert fab.rule_met(s(0.01, 1.0), s(0.02, 2.0), s(0.01, 1.0, 0.02, -0.01)) is False  # 뒤 반이 나빠졌다
    assert fab.rule_met(s(0.01, 1.0), s(0.02, 2.0), s(0.01, 1.0, None, 0.01)) is False  # type: ignore[arg-type]


def test_백테스트_IC_가_A_B_를_함께_쌓는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-03-30", "2026-03-31", "2026-04-29", "2026-04-30"]
    prices = {sid: {"2026-03-30": 100.0, "2026-03-31": 100.0, "2026-04-30": 100.0 + sid} for sid in ids}
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        sc.StockInput(stock_id=r["stock_id"], market="KOSPI", metrics={
            "revenue_growth": None, "revenue_cagr_3y": float(r["stock_id"] * 7 % 40),
            "operating_income_growth": -float(r["stock_id"]), "oi_change_assets": float(r["stock_id"])})
        for r in rows])  # fmt: skip
    acc = fab.AbAccumulator()
    got = job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-03-31", "2026-04-30"], {}, ab=acc)
    assert "ab" not in got and "growth_oi_assets" not in got  # 판정 키(IC_NAMES)에 섞이지 않는다
    칸 = acc.ics["growth_oi_assets"]["KOSPI"]
    assert 칸["b"][0] > 0 > 칸["a"][0]
