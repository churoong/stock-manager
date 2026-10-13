"""칼라·덧씌움·선물 레버리지 미국 ETF 는 '우리 종목 집중' 풀에 들지 않는다 (docs/infra.md 25.1282, ETF 감사 재현)."""

from __future__ import annotations

import pytest

from batch.jobs import etf_tilt as job


def _pick(name: str, category: str = "Large Blend", stock: float = 0.99, bond: float | None = 0.0) -> dict:
    return {"country": "US", "symbol": "X", "name": name, "category": category, "passed": 0,
            "stock_position": stock, "bond_position": bond, "total_assets": 5e9}  # fmt: skip


@pytest.mark.parametrize("name", ["JPMorgan Hedged Equity Laddered Overlay ETF", "Global X S&P 500 Collar 95-110 ETF",
                                  "Cambria Tail Risk ETF"])  # fmt: skip
def test_옵션_덧씌움_이름은_뺀다(name: str) -> None:
    assert not job.in_pool(_pick(name))


def test_Equity_Hedged_분류는_뺀다() -> None:
    assert not job.in_pool(_pick("Some Fund", category="Equity Hedged"))


def test_비중_합이_105퍼센트를_넘으면_뺀다() -> None:
    assert not job.in_pool(_pick("WisdomTree U.S. Efficient Core Fund", stock=0.90, bond=0.60))
    assert job.in_pool(_pick("Vanguard Total Stock Market ETF", stock=0.99, bond=0.0))
    assert job.in_pool(_pick("Vanguard Total Stock Market ETF", stock=0.99, bond=None))  # 채권 비중 모름은 예전대로
