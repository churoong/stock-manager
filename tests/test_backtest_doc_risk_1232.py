"""docs/backtest.md 2.3절의 리스크 창 규칙이 코드와 같다 (docs/infra.md 25.1232, 백테스트 감사)."""

from __future__ import annotations

from pathlib import Path

from batch.jobs import backtest
from batch.services import metrics as m


def test_문서가_3Y_다음_1Y_로_내려간다고_말한다() -> None:
    글 = (Path(__file__).resolve().parents[1] / "docs/backtest.md").read_text(encoding="utf-8")
    assert m.RISK_WINDOWS == ("3Y", "1Y")
    assert f'1Y 표본({m.MIN_POINTS["1Y"]}일' in 글 and f'3Y 표본({m.MIN_POINTS["3Y"]}일' in 글
    assert "3Y·1Y 창 모두" in backtest.WARN_RISK_GAP
