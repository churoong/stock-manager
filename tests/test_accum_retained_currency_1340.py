"""적립 판정 이익잉여금 증가 구간이 보고 통화를 바꾼 해를 견주지 않는다 (docs/infra.md 25.1340, 21회차 곁가지 1)."""

from __future__ import annotations

from batch.services import accumulation as acc
from tests.test_accumulation import YEARS, good


def test_통화가_바뀐_구간은_모름() -> None:
    inp = good()
    for y in YEARS[:3]:
        inp.years[y].currency = "KRW"
    for y in YEARS[3:]:
        inp.years[y].currency = "USD"
        inp.years[y].retained_earnings = inp.years[y].retained_earnings / 1300  # 달러로 보고 — 숫자가 줄었다
    agg = acc.aggregate(inp, YEARS)
    assert agg.retained_pairs == len(YEARS) - 2  # 바뀐 한 구간을 뺐다 — 예전: 4 구간 중 하나를 "감소" 로 셌다
    assert agg.retained_up == len(YEARS) - 2


def test_통화를_모르면_예전처럼() -> None:
    agg = acc.aggregate(good(), YEARS)
    assert agg.retained_pairs == len(YEARS) - 1 and agg.retained_up == len(YEARS) - 1
