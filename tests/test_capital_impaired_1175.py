"""자본잠식 종목의 퀄리티 — 부채비율·ROE 가 빠져 오히려 높던 것 (docs/factors.md 3.2, docs/infra.md 25.1175)."""

from __future__ import annotations

import random

from batch.services import scoring as sc


def _집단(extra: list[sc.StockInput]) -> list[sc.StockInput]:
    rng = random.Random(1)
    out = []
    for i in range(60):
        ta = rng.uniform(500, 2000)
        eq = ta * rng.uniform(0.2, 0.7)
        ni = ta * rng.uniform(-0.03, 0.1)
        rev = ta * rng.uniform(0.5, 1.5)
        m = sc.quality_metrics(ni, ta, eq, rev * rng.uniform(-0.02, 0.15), rev, ta - eq, 4, 5)
        m.update({"asset_growth": rng.uniform(-0.05, 0.2), "current_ratio": rng.uniform(0.8, 2.5),
                  "piotroski_lite": float(rng.randint(1, 5))})  # fmt: skip
        out.append(sc.StockInput(i, "KOSDAQ", None, m))
    return out + extra


공통 = {"asset_growth": 0.07, "current_ratio": 1.6, "piotroski_lite": 3.0}


def _종목(sid: int, 순이익: float, 자본: float) -> sc.StockInput:
    m = sc.quality_metrics(순이익, 100, 자본, 6, 100, 100 - 자본, 4, 5)
    m.update(공통)
    return sc.StockInput(sid, "KOSDAQ", None, m)


def _퀄리티(stocks: list[sc.StockInput]) -> dict[int, float | None]:
    return {r.stock_id: r.score for r in sc.score_factors(stocks) if r.factor == "quality"}


def test_적자_잠식_종목이_자본_작은_양수_종목보다_높지_않다() -> None:
    점수 = _퀄리티(_집단([_종목(1001, -3, -10), _종목(1002, -3, 2)]))
    assert 점수[1001] is not None and 점수[1002] is not None
    assert 점수[1001] <= 점수[1002]  # 예전: 46.0 > 38.1


def test_사후_배정이라_다른_종목_점수는_그대로다() -> None:
    # 같은 잠식 종목을 표식 있음/없음(예전 동작)으로만 바꿔 견준다 — 그 종목 말고는 한 점도 달라지면 안 된다
    잠식 = _종목(1001, -3, -10)
    옛 = _종목(1001, -3, -10)
    옛.metrics[sc.CAPITAL_IMPAIRED] = None
    있음 = _퀄리티(_집단([잠식]))
    없음 = _퀄리티(_집단([옛]))
    assert all(있음[sid] == 없음[sid] for sid in 없음 if sid != 1001)
    assert 있음[1001] < 없음[1001]  # type: ignore[operator]


def test_흑자_잠식은_ROE_를_최악으로_두지_않고_표식을_남긴다() -> None:
    흑자 = _종목(1001, 5, -10)
    적자 = _종목(1002, -5, -10)
    결과 = {(r.stock_id, r.factor): r for r in sc.score_factors(_집단([흑자, 적자]))}
    assert 결과[(1001, "quality")].raw[sc.CAPITAL_IMPAIRED] == {"roe_worst": False}
    assert 결과[(1002, "quality")].raw[sc.CAPITAL_IMPAIRED] == {"roe_worst": True}
    # 흑자 잠식은 ROE 가 결측(빠짐), 적자 잠식은 결측이 아니다(최악으로 채움)
    assert "roe" in 결과[(1001, "quality")].missing_fields
    assert "roe" not in 결과[(1002, "quality")].missing_fields
    assert "debt_ratio" not in 결과[(1001, "quality")].missing_fields
    # 자본이 정확히 0 이면 자료 구멍일 수 있어 표식이 없다
    assert sc.quality_metrics(-3, 100, 0, 6, 100, 100)[sc.CAPITAL_IMPAIRED] is None


def test_저장된_원값으로_다시_내도_같다() -> None:
    stocks = _집단([_종목(1001, -3, -10)])
    처음 = sc.score_factors(stocks)
    rows = [{"stock_id": r.stock_id, "market": "KOSDAQ", "sector": None, "factor": r.factor,
             "raw": {**r.raw, sc.UNRECOVERED_KEY: None} if r.factor == "risk" else r.raw} for r in 처음]  # fmt: skip
    다시 = sc.score_factors(sc.inputs_from_stored(rows) or [])
    assert {(r.stock_id, r.factor): r.score for r in 처음} == {(r.stock_id, r.factor): r.score for r in 다시}
