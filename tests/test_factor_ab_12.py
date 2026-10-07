"""지표 교체 A/B — 종목선정 기법 발굴 12회차 (docs/factors.md 12.14, docs/infra.md 25.929).

운영 점수는 바뀌지 않고, 백테스트 한 실행 안에서 현행·교체 구성의 IC 를 함께 쌓아 기록만 한다.
"""

from __future__ import annotations

import pytest

from batch.services import factor_ab as fab
from batch.services import scoring as sc


def test_영업이익_나누기_총자산() -> None:
    q = sc.quality_metrics(10.0, 200.0, 100.0, 30.0, 300.0)
    assert q["op_assets"] == pytest.approx(0.15)
    assert q["roa"] == pytest.approx(0.05)
    assert sc.quality_metrics(10.0, 0.0, 100.0, 30.0, 300.0)["op_assets"] is None  # 총자산 0 이하
    assert sc.quality_metrics(10.0, 200.0, 100.0, None, 300.0)["op_assets"] is None


def test_운영_점수_구성은_그대로다() -> None:
    """A/B 지표는 점수에도 `raw_json` 에도 들어가지 않는다 — 효용을 보이기 전에는 운영을 바꾸지 않는다."""
    names = {m.name for ms in sc.FACTOR_METRICS.values() for m in ms}
    assert "op_assets" not in names
    assert {"roa", "idio_volatility", "revenue_cagr_3y"} <= names  # 3년 CAGR 빼기는 탈락(12.14) — 변형도 없다
    구성 = {k: [m.name for m in v[1]] for k, v in sc.AB_VARIANTS.items()}
    퀄 = [m.name for m in sc.FACTOR_METRICS["quality"]]
    assert 구성["quality_op_assets"] == [("op_assets" if n == "roa" else n) for n in 퀄]
    리 = [m.name for m in sc.FACTOR_METRICS["risk"]]
    assert 구성["risk_drop_idio"] == [n for n in 리 if n != "idio_volatility"] and len(리) - 1 == 9
    assert "growth_drop_cagr3y" not in sc.AB_VARIANTS


def test_리스크_9개_중_5개면_점수_4개면_없음() -> None:
    """문턱(절반 이상)은 지표 수에서 정해진다 — 10→9 에서 문턱 5 그대로라 표본 효과가 거의 없다."""
    구성 = sc.AB_VARIANTS["risk_drop_idio"][1]
    다섯 = {m.name: 0.1 for m in 구성[:5]}
    넷 = {m.name: 0.1 for m in 구성[:4]}
    inputs = [sc.StockInput(stock_id=i, market="KOSPI", metrics={k: v * (i + 1) for k, v in 다섯.items()}) for i in range(12)]
    inputs += [sc.StockInput(stock_id=100 + i, market="KOSPI", metrics={k: v * (i + 1) for k, v in 넷.items()}) for i in range(12)]
    점수 = {r.stock_id: r.score for r in sc.score_factors(inputs, factor_metrics={**sc.FACTOR_METRICS, "risk": 구성})
           if r.factor == "risk"}  # fmt: skip
    assert all(점수.get(i) is not None for i in range(12))
    assert all(점수.get(100 + i) is None for i in range(12))


def test_겹침_짝을_기록한다() -> None:
    assert ("op_assets", "roa") in fab.QUALITY_PAIRS
    assert ("idio_volatility", "volatility_ann") in fab.RISK_PAIRS
    acc = fab.AbAccumulator()
    inputs = [
        sc.StockInput(stock_id=i, market="KOSPI", metrics={
            "op_assets": i * 0.01, "roa": i * 0.005 + (i % 3) * 0.001, "operating_margin": 0.1,
            "idio_volatility": 0.2 + i * 0.01, "volatility_ann": 0.25 + i * 0.01, "max_daily_return": 0.05,
        })  # fmt: skip
        for i in range(fab.MIN_PAIR_STOCKS + 5)
    ]
    acc.add_period(inputs, {i.stock_id: {} for i in inputs}, {i.stock_id: 0.0 for i in inputs},
                   {i.stock_id: "KOSPI" for i in inputs})  # fmt: skip
    log = acc.log()
    assert log["corr"]["op_assets~roa"]["KOSPI"]["mean"] > 0.9
    assert log["corr"]["idio_volatility~volatility_ann"]["KOSPI"]["months"] == 1
    assert {"quality_op_assets", "risk_drop_idio"} <= set(log["variants"])
    # 겹침 문 (12.14) — op_assets~roa 가 0.9 이상이면 나라 계열 판정을 하지 않는다
    assert log["variants"]["quality_op_assets"]["by_market"][fab.COUNTRY_KEY]["verdict"].startswith("판정 불가(겹침")
    assert not log["variants"]["risk_drop_idio"]["by_market"][fab.COUNTRY_KEY]["verdict"].startswith("판정 불가(겹침")
