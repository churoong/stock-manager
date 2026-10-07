"""미국 단위형 신탁(SPY·MDY)은 같은 지수 ETF 보유로 본다 (docs/infra.md 25.982)."""

from __future__ import annotations

from batch.jobs import etf_tilt as job
from batch.services import etf_tilt as tilt


def _pick(sym: str, **kw) -> dict:
    return {"pick_id": 1, "country": "US", "symbol": sym, "passed": 1, "category": "Large Blend", "name": sym} | kw


def test_SPY_는_IVV_보유로_MDY_는_IJH_보유로() -> None:
    assert job.source_of(_pick("SPY")) == ("us", "IVV")
    assert job.source_of(_pick("mdy", category="Mid-Cap Blend")) == ("us", "IJH")


def test_다른_미국_ETF_는_제_보유() -> None:
    assert job.source_of(_pick("VOO")) == ("us", "VOO")


def test_대리는_같은_지수를_완전_복제하는_ETF_다() -> None:
    """대리 자신이 단위형 신탁이면 또 못 받는다. 대리끼리 꼬리를 물지 않는다."""
    assert set(tilt.US_UIT_PROXY.values()).isdisjoint(tilt.US_UIT_PROXY)
