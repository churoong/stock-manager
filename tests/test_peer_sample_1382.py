"""비교 집단·지표별 실제 표본 — 기록만 (docs/infra.md 25.1382, 25회차 P1+P6)."""

from __future__ import annotations

from batch.services import scoring as sc


def _r(sid: int, group: str, size: int, raw: dict) -> sc.FactorResult:
    return sc.FactorResult(sid, "value", 0.0, 50.0, group, size, [], raw)


def test_작은_시장_집단과_지표별_표본을_센다() -> None:
    결과 = [_r(i, "market:BZX", 4, {"per": 10.0 + i, "pbr": None}) for i in range(4)]
    결과 += [_r(100 + i, "sector:NASDAQ:Tech", 40, {"per": 12.0, "dividend_yield": 0.01 if i < 9 else None})
           for i in range(40)]  # fmt: skip
    기록 = sc.peer_sample_report(결과)
    assert 기록["small_markets"] == {"market:BZX": {"n": 4, "stocks": [0, 1, 2, 3]}}
    작은 = {(x["group"], x["metric"]): x for x in 기록["small_metrics"]}
    assert 작은[("sector:NASDAQ:Tech", "dividend_yield")] == {
        "group": "sector:NASDAQ:Tech", "metric": "dividend_yield", "n": 9, "winsorized": False}
    assert 작은[("market:BZX", "per")]["n"] == 4 and 작은[("market:BZX", "pbr")]["n"] == 0
    assert ("sector:NASDAQ:Tech", "per") not in 작은  # 40 개 다 있음


def test_치환값은_표본에_넣지_않는다() -> None:
    결과 = [_r(i, "sector:KOSPI:X", 30, {"roe": 0.1, sc.SUBSTITUTED_KEY: {"roe": 0.0} if i < 25 else {}})
          for i in range(30)]  # fmt: skip
    작은 = {(x["group"], x["metric"]): x["n"] for x in sc.peer_sample_report(결과)["small_metrics"]}
    assert 작은[("sector:KOSPI:X", "roe")] == 5


def test_점수_작업이_기록한다() -> None:
    from pathlib import Path

    원문 = (Path(__file__).resolve().parents[1] / "batch/jobs/scores.py").read_text()
    assert '"peer_sample": sc.peer_sample_report(factor_results)' in 원문
