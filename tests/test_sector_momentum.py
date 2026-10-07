"""업종 모멘텀 — 기법 발굴 루프 1회차 D (batch/services/sector_momentum.py, docs/infra.md 25.439)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from batch.services import sector_momentum as sm


@dataclass
class 입력:
    stock_id: int
    market: str
    sector: str | None
    metrics: dict[str, float | None] = field(default_factory=dict)


def 업종(시작: int, 업종명: str | None, 수익: float, 개수: int = 30, market: str = "KOSPI") -> list[입력]:
    return [입력(시작 + k, market, 업종명, {"momentum_12_1": 수익}) for k in range(개수)]


class Test업종_평균:
    def test_30종목_미만_업종은_빼고_업종_없는_종목은_어디에도_안_든다(self) -> None:
        rows = 업종(0, "반도체", 0.2) + 업종(100, "은행", 0.1, 개수=29) + 업종(200, None, 0.5)
        assert sm.sector_means(rows) == {("KOSPI", "반도체"): pytest.approx(0.2)}

    def test_값이_없는_종목은_평균에서_빠진다(self) -> None:
        rows = 업종(0, "반도체", 0.2) + [입력(99, "KOSPI", "반도체", {"momentum_12_1": None})]
        assert sm.sector_means(rows)[("KOSPI", "반도체")] == pytest.approx(0.2)


class Test상위_절반:
    def test_시장마다_따로_올림(self) -> None:
        means = {("KOSPI", "a"): 0.3, ("KOSPI", "b"): 0.2, ("KOSPI", "c"): 0.1, ("KOSDAQ", "x"): -0.1}
        assert sm.top_half_sectors(means) == {("KOSPI", "a"), ("KOSPI", "b"), ("KOSDAQ", "x")}

    def test_모르는_업종은_조건을_적용하지_않는다(self) -> None:
        means = {("KOSPI", "a"): 0.3, ("KOSPI", "b"): 0.1}
        top = sm.top_half_sectors(means)
        assert sm.passes(입력(1, "KOSPI", "a"), means, top)
        assert not sm.passes(입력(2, "KOSPI", "b"), means, top)
        assert sm.passes(입력(3, "KOSPI", "작은업종"), means, top)
        assert sm.passes(입력(4, "KOSPI", None), means, top)

    def test_종목별_값(self) -> None:
        rows = 업종(0, "반도체", 0.2) + [입력(500, "KOSPI", None)]
        vals = sm.stock_values(rows)
        assert vals[0] == pytest.approx(0.2) and vals[500] is None


def test_백테스트가_후보_전략과_IC_를_잰다() -> None:
    import inspect

    from batch.jobs import backtest as job

    assert "momentum_sector" in job.STRATEGIES and "momentum_sector" in job.CANDIDATE_STRATEGIES
    assert "sector_mom" in job.IC_NAMES
    원본 = inspect.getsource(job.make_strategy)
    assert "secmom.passes(i, means, top)" in 원본


def test_업종_평균_비율() -> None:
    rows = 업종(0, "반도체", 0.2) + [입력(500 + k, "KOSPI", None) for k in range(70)]
    means = sm.sector_means(rows)
    assert sm.coverage(rows, means) == pytest.approx(0.3)
    assert sm.coverage([], means) == 0.0


def test_표본이_성기면_결과에_판정_불가_경고(monkeypatch: pytest.MonkeyPatch) -> None:
    """업종 채움이 낮으면 조건이 거의 전부 통과해 momentum 과 같은 종목을 고른다 (docs/infra.md 25.443, 교차검증)."""
    from types import SimpleNamespace

    from batch.jobs import backtest as job

    rows = 업종(0, "반도체", 0.2) + [입력(500 + k, "KOSPI", None, {"momentum_12_1": 0.1}) for k in range(70)]
    monkeypatch.setattr(job, "build_pit_inputs", lambda *a, **k: rows)
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=1.0) for i in inputs])
    경고: list[str] = []
    decide = job.make_strategy("momentum_sector", [{"stock_id": r.stock_id} for r in rows], {}, 5, {}, 경고)
    decide("2026-02-02", SimpleNamespace(cutoff="2026-01-30"))
    decide("2026-03-02", SimpleNamespace(cutoff="2026-02-27"))
    업종경고 = [w for w in 경고 if "업종 모멘텀" in w]  # 리스크 결측 경고는 따로 붙는다
    assert len(업종경고) == 1 and "판정 불가" in 업종경고[0] and "30%" in 업종경고[0]


def test_웹의_후보_목록이_배치와_같다() -> None:
    import re
    from pathlib import Path

    from batch.jobs import backtest as job

    웹 = (Path(__file__).resolve().parents[1] / "web" / "lib" / "backtest.ts").read_text(encoding="utf-8")
    m = re.search(r"CANDIDATE_STRATEGIES = \[([^\]]*)\]", 웹)
    assert m is not None
    assert set(re.findall(r'"([^"]+)"', m.group(1))) == set(job.CANDIDATE_STRATEGIES)
