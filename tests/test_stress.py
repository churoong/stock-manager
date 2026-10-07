"""스트레스 테스트의 테스트.

docs/stress.md 5장의 표를 옮겼다. 손으로 맞출 수 있는 자료로 기대값을 낸다.
"""

from __future__ import annotations

import pytest

from batch.services import stress as st

DATES = [f"2026-01-{d:02d}" for d in range(1, 11)]  # 10 거래일


def flat_then_crash() -> dict[int, dict[str, float]]:
    """종목 1은 5일째부터 반토막, 종목 2는 계속 100."""
    a = {d: 100.0 for d in DATES}
    for d in DATES[4:]:
        a[d] = 50.0
    b = {d: 100.0 for d in DATES}
    return {1: a, 2: b}


class Test바스켓가치:
    def test_손으로_맞춘_값(self) -> None:
        curve, used, cash, excluded = st.basket_curve(
            {1: 0.5, 2: 0.5}, flat_then_crash(), DATES
        )
        by_date = dict(curve)
        assert by_date[DATES[0]] == pytest.approx(1.0)
        # 5일째: 0.5×0.5 + 0.5×1.0 = 0.75
        assert by_date[DATES[4]] == pytest.approx(0.75)
        assert cash == pytest.approx(0.0)
        assert excluded == []

    def test_현금은_수익_0이다(self) -> None:
        """비중 합 60% 면 낙폭도 60% 만큼만. 100% 로 다시 나누면 과장된다."""
        curve, _used, cash, _ = st.basket_curve({1: 0.6}, flat_then_crash(), DATES)
        assert cash == pytest.approx(0.4)
        # 종목 1 이 반토막 → 0.6×0.5 + 0.4 = 0.7
        assert dict(curve)[DATES[-1]] == pytest.approx(0.7)

    def test_시작일_가격이_없는_종목은_뺀다(self) -> None:
        prices = flat_then_crash()
        prices[3] = {DATES[5]: 100.0}  # 6일째부터만 있다
        _curve, used, cash, excluded = st.basket_curve(
            {1: 0.5, 3: 0.5}, prices, DATES
        )
        assert excluded == [3]
        assert 3 not in used
        assert cash == pytest.approx(0.5)  # 뺀 만큼 현금이다

    def test_도중에_끊기면_마지막_종가에_묶인다(self) -> None:
        prices = flat_then_crash()
        for d in DATES[7:]:
            del prices[1][d]
        curve, *_ = st.basket_curve({1: 1.0}, prices, DATES)
        assert dict(curve)[DATES[-1]] == pytest.approx(0.5)

    def test_종가_0은_값이_없는_날로_잇는다(self) -> None:
        """거래정지의 0 을 그대로 쓰면 바스켓이 그날 가짜로 폭락한다 (docs/infra.md 25.203)."""
        prices = flat_then_crash()
        prices[2][DATES[2]] = 0.0

        curve, *_ = st.basket_curve({2: 1.0}, prices, DATES)

        assert dict(curve)[DATES[2]] == pytest.approx(1.0)

    def test_비중_합이_1을_넘으면_거부(self) -> None:
        with pytest.raises(ValueError):
            st.basket_curve({1: 0.7, 2: 0.7}, flat_then_crash(), DATES)

    def test_음수_비중은_거부(self) -> None:
        with pytest.raises(ValueError):
            st.basket_curve({1: -0.1}, flat_then_crash(), DATES)


class Test최악창:
    def test_가장_나쁜_창을_찾는다(self) -> None:
        curve, *_ = st.basket_curve({1: 1.0}, flat_then_crash(), DATES)
        w = st.worst_window(curve, 2)
        assert w is not None
        # 3일째→5일째 (100→50) 가 -50% 로 가장 나쁘다
        assert w.start == DATES[2]
        assert w.end == DATES[4]
        assert w.return_pct == pytest.approx(-0.5)
        assert w.max_drawdown == pytest.approx(-0.5)

    def test_회복하지_못하면_None(self) -> None:
        curve, *_ = st.basket_curve({1: 1.0}, flat_then_crash(), DATES)
        w = st.worst_window(curve, 2)
        assert w is not None and w.recovery_days is None

    def test_회복하면_거래일_수(self) -> None:
        prices = {1: {d: 100.0 for d in DATES}}
        prices[1][DATES[3]] = 80.0  # 하루 급락 뒤 다음날 회복
        curve, *_ = st.basket_curve({1: 1.0}, prices, DATES)
        w = st.worst_window(curve, 1)
        assert w is not None
        assert w.recovery_days == 1

    def test_표본이_모자라면_None(self) -> None:
        """창 하나뿐이면 최악이 아니라 유일이다."""
        curve, *_ = st.basket_curve({1: 1.0}, flat_then_crash(), DATES)
        assert st.worst_window(curve, 6) is None  # 10 < 6×2

    def test_창_길이_0은_거부(self) -> None:
        with pytest.raises(ValueError):
            st.worst_window([("d", 1.0)], 0)


class Test전체:
    def test_경고가_항상_붙는다(self) -> None:
        result = st.run({1: 0.5, 2: 0.5}, flat_then_crash(), DATES, windows=(2,))
        assert st.WARN_SURVIVORSHIP in result.warnings
        assert st.WARN_NOT_FORECAST in result.warnings

    def test_표본_부족_창은_따로_적는다(self) -> None:
        result = st.run({1: 1.0}, flat_then_crash(), DATES, windows=(2, 20))
        assert [w.length for w in result.windows] == [2]
        assert result.skipped_windows == [20]

    def test_어느_비중을_썼는지_남긴다(self) -> None:
        result = st.run({1: 1.0}, flat_then_crash(), DATES, weighting="equal", windows=(2,))
        assert result.weighting == "equal"


class Test짧은_이력_종목:
    """docs/infra.md 25.823 — 시작일에 가격이 없는 종목의 비중이 조용히 현금이 되어 낙폭이 작게 나왔다."""

    def _가격(self, n: int, 늦은_시작: int) -> tuple[dict[int, dict[str, float]], list[str]]:
        from datetime import date, timedelta

        days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]
        prices = {1: {d: 100.0 - i * 0.01 for i, d in enumerate(days)},
                  2: {d: 100.0 - i * 0.05 for i, d in enumerate(days) if i >= 늦은_시작}}  # fmt: skip
        return prices, days

    def test_모든_종목에_가격이_있는_첫_날부터_잰다(self) -> None:
        prices, days = self._가격(300, 100)
        r = st.run({1: 0.5, 2: 0.5}, prices, days)
        assert r.excluded == [] and r.cash_weight == pytest.approx(0.0)
        assert r.curve[0][0] == days[100]  # 짧은 종목의 첫 날
        assert 250 in r.skipped_windows  # 줄어든 표본으로는 긴 창을 정직하게 못 본다

    def test_줄인_표본이_모자라면_예전처럼_재되_뺀_비중을_경고한다(self) -> None:
        prices, days = self._가격(300, 290)
        r = st.run({1: 0.5, 2: 0.5}, prices, days)
        assert r.excluded == [2] and r.cash_weight == pytest.approx(0.0)  # 현금은 상한에 남은 몫만
        assert any("시작일까지 가격이 없어 뺀 1종목의 비중 50%" in w for w in r.warnings)


    def test_긴_이력_종목이_공통_시작일_하루만_비어도_빼지_않는다(self) -> None:
        """그날 행 하나 없다고 통째로 빼면 비중이 수익 0 이 됐다 (docs/infra.md 25.827, 교차검증)."""
        prices, days = self._가격(300, 100)
        del prices[1][days[100]]
        r = st.run({1: 0.5, 2: 0.5}, prices, days)
        assert r.excluded == [] and r.curve[0][0] == days[100]
