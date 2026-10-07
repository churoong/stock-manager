"""A/B 판정 규칙의 빈칸 (docs/factors.md 12.13, 11회차) — 결과가 하나도 나오기 전에(운영 기록 0건, 2026-10-03) 고정한다."""

from __future__ import annotations

from batch.services import factor_ab as fab
from batch.services import factor_ic as fic
from tests.test_factor_ab_10 import _period


def _s(months: int, cov: float, mean: float = 0.02, t: float = 2.0) -> fic.IcSummary:
    return fic.IcSummary(months=months, mean=mean, std=0.1, t_stat=t, first_half_mean=0.01, second_half_mean=0.01,
                         coverage=cov)  # fmt: skip


def test_표본이_모자라면_탈락이_아니라_판정_불가() -> None:
    a, b, 차 = _s(40, 0.8, 0.01, 1.0), _s(40, 0.8), _s(40, 0.8)
    assert fab.ab_verdict(a, b, 차) == "통과"
    assert fab.ab_verdict(_s(35, 0.8, 0.01, 1.0), _s(35, 0.8), 차).startswith("판정 불가(IC 35개월")
    assert fab.ab_verdict(a, _s(40, 0.5), 차).startswith("판정 불가(표본 비율")
    assert fab.ab_verdict(_s(40, 0.8, 0.03, 3.0), b, 차) == "탈락"


def test_나라_계열과_공통_표본을_함께_쌓는다() -> None:
    acc = fab.AbAccumulator()
    for k in range(8):
        acc.add_period(*_period(k))
    log = acc.log()
    나라 = log["variants"]["growth_oi_assets"]["by_market"][fab.COUNTRY_KEY]
    assert 나라["a"]["months"] == 8 and "b_minus_a_common" in 나라
    assert 나라["verdict"].startswith("판정 불가")  # 8개월 < 36 — 예전 rule_met 는 True 였다
    assert 나라["rule_met"] is True
    assert ("oi_change_assets", "sue") in fab.OI_PAIRS
