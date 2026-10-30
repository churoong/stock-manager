"""깔때기 ⑧-1 — 리플레이 실행기의 미국 시점 유니버스 (25.2055, docs/funnel.md 4장 7).

`core_backtest.pit_eligible(country="US")` 가 월 백테스트 `run` 의 미국 갈래와 같은 셋을 쓰는지:
하한 `UniverseFilters.usa()`, `listed_date` 는 관측 첫 거래일(`observed=True` — 이력 시작에 붙은 종목은 상장일 모름),
시총 하한은 가격 수준 `levels`(수정종가는 미래 배당만큼 낮다, 25.275).
"""

from __future__ import annotations

import pytest

from batch.core import calendar as cal
from batch.jobs import core_backtest as cj

_달력 = cal.exchange_calendar("US").sessions_in_range("2025-01-02", "2025-12-31")
날들 = [d.date().isoformat() for d in _달력][:160]
#: 이력 시작(날들[0])에 처음 보인 종목들 — 상장 1년이 안 됐지만 미국은 관측 첫 거래일이라 상장일을 모른다
유니버스 = [{"stock_id": sid, "listed_date": 날들[0], "listed_shares": 20_000_000} for sid in (1, 2)]
#: 1 — 가격 수준 100달러 × 2천만 주 = 20억 달러(하한 10억 위), 수정종가는 40달러(배당 많은 종목 — 8억, 하한 아래)
#: 2 — 가격 수준 40달러 = 8억 달러(하한 아래)
levels = {1: dict.fromkeys(날들, 100.0), 2: dict.fromkeys(날들, 40.0)}
prices = {1: dict.fromkeys(날들, 40.0), 2: dict.fromkeys(날들, 40.0)}
turnover = {sid: dict.fromkeys(날들, 50_000_000.0) for sid in (1, 2)}  # 하한 500만 달러 위
시장 = dict.fromkeys((1, 2), "US")


def _판정(cutoff: str) -> set[int]:
    eligible_at, _c, _r = cj.pit_eligible(유니버스, prices, turnover, 시장, country="US", levels=levels)
    return eligible_at(cutoff)


def test_미국은_가격_수준_시총과_미국_하한으로_고른다() -> None:
    # 날들[120] — 관측 첫 거래일 뒤 1년이 안 됐지만 이력 시작에 붙어 상장일 모름(빼지 않음)
    assert _판정(날들[120]) == {1}
    _f, _c, 까닭 = cj.pit_eligible(유니버스, prices, turnover, 시장, country="US", levels=levels)
    _f(날들[120])
    assert 까닭 == {"시총 하한 미만": 1}, 까닭  # 2 만 — 1 은 가격 수준으로 넘고, 상장 1년 미만으로도 빠지지 않는다


def test_미국은_가격_수준이_없으면_멈춘다() -> None:
    with pytest.raises(ValueError, match="levels"):
        cj.pit_eligible(유니버스, prices, turnover, 시장, country="US")
    with pytest.raises(ValueError, match="KR·US"):
        cj.pit_eligible(유니버스, prices, turnover, 시장, country="JP", levels=levels)


def test_국내_기본값은_그대로다() -> None:
    """국내(기본) — 같은 자료를 원화로 보면 1,000억 하한 아래라 아무도 들지 않는다(국내 갈래가 미국 하한을 쓰지 않는다)"""
    eligible_at, _c, _r = cj.pit_eligible(유니버스, prices, turnover, 시장)
    assert eligible_at(날들[120]) == set()
