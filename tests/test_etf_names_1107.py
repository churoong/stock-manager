"""ETF 이름 거르기 빈틈 (docs/infra.md 25.1107, ETF 감사 재현)."""

from __future__ import annotations

from dataclasses import replace

from batch.jobs import etf_tilt as job
from batch.services import etf as svc
from batch.services import etf_satellite as sat
from tests.test_etf import AS_OF, FETCHED, profile


def test_소수_배수·bull·short_는_레버리지로_본다() -> None:
    for 이름 in ("Direxion Daily TSLA Bull 1.5X Shares", "XX 1.25x Daily", "ProShares Short S&P500", "XX 5x",
                "Defiance WeeklyPay ETF"):  # fmt: skip
        assert svc.name_looks_leveraged(이름), 이름
    for 이름 in ("Vanguard Short-Term Bond ETF", "XX Short Duration Income", "Vanguard S&P 500 ETF", "BulletShares 2030"):
        assert not svc.name_looks_leveraged(이름), 이름


def test_분류가_Large_Blend_인_커버드콜은_핵심에서_뺀다() -> None:
    p = profile("VOO")  # 분류 Large Blend
    ev = svc.evaluate("XYLD", "Global X S&P 500 Covered Call ETF", p, AS_OF, FETCHED)
    assert not ev.passed and "옵션 전략" in (ev.excluded_reason or "")
    assert svc.evaluate("VOO", "Vanguard S&P 500 ETF", p, AS_OF, FETCHED).passed
    # 위성도 같은 정의처를 쓴다
    assert sat.US_OPTION_NAME_PATTERN is svc.US_OPTION_NAME_PATTERN


def test_우리_종목_집중_후보_풀도_옵션·소수_배수를_뺀다() -> None:
    기본 = {"country": "US", "passed": False, "category": "Large Blend", "stock_position": 0.99,
            "total_assets": 5e9}  # fmt: skip
    assert job.in_pool({**기본, "name": "Vanguard Total Stock Market ETF"})
    assert not job.in_pool({**기본, "name": "JPMorgan Equity Premium Income ETF"})
    assert not job.in_pool({**기본, "category": "", "name": "Direxion Daily TSLA Bull 1.5X Shares"})


def test_총보수가_NaN_이면_확인할_수_없는_값이다() -> None:
    p = replace(profile("VOO"), expense_ratio=float("nan"))
    ev = svc.evaluate("VOO", "Vanguard S&P 500 ETF", p, AS_OF, FETCHED)
    assert not ev.passed and "총보수" in (ev.excluded_reason or "")


def test_한글_배수·인버스_표기() -> None:
    for 이름 in ("TIGER 200 2배", "XX 200 숏", "XX 울트라 나스닥"):
        assert any(m in 이름 for m, _ in svc.KR_NAME_EXCLUSIONS), 이름
    assert sat.KR_OPTION_INDEX_PATTERN.search("Nasdaq-100 Weekly Target Income Index")
