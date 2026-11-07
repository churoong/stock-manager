"""업종 채움 경고를 모든 리밸런스 평균으로 (docs/infra.md 25.2183, factors.md 12.2·12장 머리 표본 규칙).

11-03 첫 판정 실행에서 두 시장 `momentum_sector` 결과 행에 "업종 평균이 잡힌 종목이 0%(2021-11-01)" 경고가 붙었다. 첫
리밸런스는 12-1 값이 아직 없어(워밍업) 업종 평균이 비고, 예전 규칙(리밸런스 **하나라도** 60% 미만)으로는 매달 판정 불가였다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from batch.services import factor_ic as fic
from batch.services import sector_momentum as sm


@dataclass
class 입력:
    stock_id: int
    market: str
    sector: str | None
    metrics: dict[str, float | None] = field(default_factory=dict)


def _줄(값: float | None) -> list[입력]:
    """한 업종 40종목 — 값이 있으면 업종 평균이 잡혀 채움 100%, 없으면(워밍업) 0%"""
    return [입력(k, "KOSPI", "반도체", {"momentum_12_1": 값}) for k in range(40)]


def _돌림(monkeypatch: pytest.MonkeyPatch, 달별: list[float | None], 경고: list[str]) -> None:
    from batch.jobs import backtest as job

    판 = {f"2026-{m:02d}-27": _줄(v) for m, v in enumerate(달별, 1)}
    monkeypatch.setattr(job, "build_pit_inputs", lambda _u, _s, view, *a, **k: 판[view.cutoff])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=1.0) for i in inputs])  # fmt: skip
    decide = job.make_strategy("momentum_sector", [{"stock_id": k} for k in range(40)], {}, 5, {}, 경고)
    for cutoff in 판:
        decide(cutoff, SimpleNamespace(cutoff=cutoff))


def _업종경고(경고: list[str]) -> list[str]:
    return [w for w in 경고 if w.startswith(sm.WARN_PREFIX)]


def test_워밍업_첫_달이_0퍼센트여도_평균이_넘으면_경고_없음(monkeypatch: pytest.MonkeyPatch) -> None:
    경고: list[str] = []
    _돌림(monkeypatch, [None, 0.1, 0.1, 0.1], 경고)  # 0·1·1·1 → 평균 0.75
    assert _업종경고(경고) == []


def test_평균이_낮으면_끝난_뒤_전_구간_평균의_경고_하나(monkeypatch: pytest.MonkeyPatch) -> None:
    경고 = ["다른 경고"]
    _돌림(monkeypatch, [0.1, 0.1, None, None, None], 경고)  # 1·1·0·0·0 → 평균 0.4
    업 = _업종경고(경고)
    assert len(업) == 1 and "5번 평균 40%" in 업[0] and "판정 불가" in 업[0]
    assert "다른 경고" in 경고  # 다른 경고는 그대로


def test_경고_문턱은_60퍼센트_미만() -> None:
    assert fic.MIN_COVERAGE == 0.6
    assert sm.coverage_warning([0.6], fic.MIN_COVERAGE) is None
    assert sm.coverage_warning([0.59], fic.MIN_COVERAGE) is not None
    assert sm.coverage_warning([0.0, 1.0, 0.8], fic.MIN_COVERAGE) is None  # 평균 0.6


def test_잰_리밸런스가_없으면_경고() -> None:
    말 = sm.coverage_warning([], fic.MIN_COVERAGE)
    assert 말 is not None and 말.startswith(sm.WARN_PREFIX) and "0번" in 말


def test_판정_질의가_찾는_경고_머리와_같다() -> None:
    sql = (Path(__file__).resolve().parent.parent / "scripts/diag/round42_judge_1103.sql").read_text(encoding="utf-8")
    assert f"LIKE '%{sm.WARN_PREFIX}%'" in sql
    assert sm.WARN_LOW_COVERAGE.startswith(sm.WARN_PREFIX)
