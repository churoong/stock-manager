"""감성 축의 시장 중심 — 기록만 (docs/infra.md 25.1370, 24회차 P5) + 백테스트 경고 "세전" (24회차 P4)."""

from __future__ import annotations

from pathlib import Path

from batch.services import backtest as bt
from batch.services import scoring as sc


def test_평균_이동은_평균의_절반_가중치_덮은_몫() -> None:
    # 감성 +20, +20, 없음 / 가중치 10% / 유니버스 3 → 평균 이동 = 20/2 × 0.1 × 2/3
    기록 = sc.sentiment_center({1: 20.0, 2: 20.0, 3: None}, 3, 10.0)
    assert 기록 == {"n": 2, "mean": 20.0, "median": 20.0, "coverage": 0.6667, "mean_shift": 0.667}
    # 이동은 실제 종합 점수 차이와 같다 — 덮인 종목 하나는 +1.0 점
    다섯 = {k: 50.0 for k in ("value", "quality", "growth", "momentum", "risk")}
    가중 = dict.fromkeys(다섯, 20.0)
    assert sc.total_score(다섯, 가중, sentiment=20.0, sentiment_weight=10.0).total == 51.0
    assert sc.sentiment_center({1: None}, 3, 10.0) is None


def test_점수_작업이_기록하고_백테스트_경고는_세전이라_말한다() -> None:
    root = Path(__file__).resolve().parents[1]
    assert "sc.sentiment_center(" in (root / "batch/jobs/scores.py").read_text()
    assert "세전" in bt.WARN_TOTAL_RETURN
    assert "세전 총수익" in (root / "web/lib/backtest.ts").read_text()
