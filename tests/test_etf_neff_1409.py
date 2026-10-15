"""ETF 실효 보유 종목 수 N_eff = (Σw)²/Σw² (docs/infra.md 25.1409, docs/factors.md 12.29 P5)."""

from __future__ import annotations

import pytest

from batch.services import etf_tilt as tilt


def test_같은_비중이면_종목_수와_같다() -> None:
    assert tilt.effective_count([(f"c{i}", 4.0) for i in range(25)]) == pytest.approx(25.0)


def test_쏠리면_줄고_같은_줄은_합친다() -> None:
    # 50% 한 줄 + 50% 를 50 종목에 1%씩 → (100)² / (2500 + 50) ≈ 3.92
    보유 = [("A", 50.0)] + [(f"c{i}", 1.0) for i in range(50)]
    assert tilt.effective_count(보유) == pytest.approx(10000 / 2550)
    # 같은 키가 두 줄이면 합친 뒤 센다(분할 기재)
    assert tilt.effective_count([("A", 25.0), ("A", 25.0), ("B", 50.0)]) == pytest.approx(2.0)


def test_잘린_목록과_비중_합_95퍼센트_미만은_모름() -> None:
    assert tilt.effective_count([(f"c{i}", 4.0) for i in range(25)], truncated=True) is None
    assert tilt.effective_count([(f"c{i}", 3.0) for i in range(30)]) is None  # 합 90%
    assert tilt.effective_count([("A", -5.0), ("B", 100.0)]) == pytest.approx(1.0)  # 음수 비중은 뺀다
