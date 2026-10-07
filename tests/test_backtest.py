"""백테스트 엔진 테스트.

docs/backtest.md 6장의 표를 그대로 옮겼다. 기대값은 손으로 계산할 수 있는
자료로 만들고 근거를 적는다.

가장 중요한 두 테스트는 look-ahead 방지와 상장폐지 처리다. 이 둘이 깨지면
결과가 실제보다 좋게 나오고, 그것으로 규칙을 확정하면 돈을 잃는다.
"""

from __future__ import annotations

import pytest

from batch.services import backtest as bt

# ----------------------------------------------------------------------
# 표본 데이터. 손으로 맞출 수 있게 딱 떨어지는 값
# ----------------------------------------------------------------------

DATES = [
    "2026-01-02", "2026-01-05", "2026-01-06",
    "2026-02-02", "2026-02-03",
    "2026-03-02", "2026-03-03",
]


def two_stocks() -> dict[int, dict[str, float]]:
    """종목 1은 매달 10% 오르고, 종목 2는 매달 10% 내린다."""
    a = {"2026-01-02": 100, "2026-01-05": 100, "2026-01-06": 100,
         "2026-02-02": 110, "2026-02-03": 110,
         "2026-03-02": 121, "2026-03-03": 121}
    b = {"2026-01-02": 100, "2026-01-05": 100, "2026-01-06": 100,
         "2026-02-02": 90, "2026-02-03": 90,
         "2026-03-02": 81, "2026-03-03": 81}
    return {1: {k: float(v) for k, v in a.items()}, 2: {k: float(v) for k, v in b.items()}}


def hold_both(_t: str, _view: bt.PriceView) -> dict[int, float]:
    return {1: 0.5, 2: 0.5}


def hold_one(_t: str, _view: bt.PriceView) -> dict[int, float]:
    return {1: 1.0}


# ----------------------------------------------------------------------
# 달력
# ----------------------------------------------------------------------


class Test달력:
    def test_매월_첫_거래일(self) -> None:
        assert bt.month_starts(DATES) == ["2026-01-02", "2026-02-02", "2026-03-02"]

    def test_직전_거래일(self) -> None:
        assert bt.previous_trading_day(DATES, "2026-02-02") == "2026-01-06"

    def test_첫날은_직전이_없다(self) -> None:
        assert bt.previous_trading_day(DATES, "2026-01-02") is None


# ----------------------------------------------------------------------
# 비용
# ----------------------------------------------------------------------


class Test비용:
    def test_회전율에_비례한다(self) -> None:
        # 편도 수수료 0.015% + 슬리피지 0.10% = 0.115%, 양쪽이면 0.23%
        # 회전율 0.5 → 0.115%. 매도 0.5 × 거래세 0.18% = 0.09%. 합 0.205%
        costs = bt.Costs(tax_sell_pct=0.18)
        assert costs.rebalance_cost(0.5, 0.5) == pytest.approx(0.00205)

    def test_비용_0(self) -> None:
        assert bt.Costs.zero().rebalance_cost(1.0, 1.0) == 0.0

    def test_음수는_거부한다(self) -> None:
        with pytest.raises(ValueError):
            bt.Costs().rebalance_cost(-0.1, 0.0)


# ----------------------------------------------------------------------
# 판단 창 — 미래를 볼 수 없다
# ----------------------------------------------------------------------


class Test판단창:
    def test_cutoff_이후는_존재하지_않는다(self) -> None:
        view = bt.PriceView(two_stocks(), "2026-01-06")
        assert view.last_date(1) == "2026-01-06"
        assert all(d <= "2026-01-06" for d, _ in view.closes(1))

    def test_cutoff_전에_데이터가_없는_종목은_보이지_않는다(self) -> None:
        prices = {9: {"2026-03-02": 1.0}}
        view = bt.PriceView(prices, "2026-01-06")
        assert view.stocks() == []
        assert view.last_close(9) is None


class TestLookAhead방지:
    """미래를 훔쳐보려는 판단 함수를 넣으면 실패해야 한다."""

    def test_판단_함수는_리밸런스_당일_가격을_보지_못한다(self) -> None:
        seen: dict[str, str | None] = {}

        def peek(t: str, view: bt.PriceView) -> dict[int, float]:
            seen[t] = view.last_date(1)
            return {1: 1.0}

        bt.simulate(two_stocks(), DATES, ["2026-02-02"], peek, bt.Costs.zero())

        # 2월 2일에 판단할 때 볼 수 있는 마지막 날은 1월 6일이다
        assert seen["2026-02-02"] == "2026-01-06"

    def test_미래_가격으로_고르면_결과가_달라진다(self) -> None:
        """창 안에서는 '미래에 오르는 종목' 을 알 수 없다."""

        def cheat_if_possible(t: str, view: bt.PriceView) -> dict[int, float]:
            # 종목 1이 3월에 121 이 되는 것을 창에서 볼 수 있다면 그것만 산다.
            closes = dict(view.closes(1))
            if closes.get("2026-03-02") == 121.0:
                return {1: 1.0}
            return {1: 0.5, 2: 0.5}

        result = bt.simulate(
            two_stocks(), DATES, ["2026-02-02"], cheat_if_possible, bt.Costs.zero()
        )
        # 속임수가 통했다면 종목 1만 들고 3월에 1.1 이 됐을 것이다.
        # 통하지 않아 반반을 들었으므로 (1.1 + 0.9) / 2 = 1.0 이다
        assert result.curve[-1][1] == pytest.approx(1.0)


# ----------------------------------------------------------------------
# 손으로 맞출 수 있는 자본곡선
# ----------------------------------------------------------------------


class Test자본곡선:
    def test_반반_들고_두면_드리프트가_생긴다(self) -> None:
        # 1월 2일 체결(각 100). 2월 2일: 0.5×1.1 + 0.5×0.9 = 1.0
        # 3월 2일: 리밸런스가 없으므로 0.5×1.21 + 0.5×0.81 = 1.01
        # (처음에 1.0 이라고 손으로 잘못 계산했다가 엔진이 잡아냈다.
        #  복리는 상쇄되지 않는다. 리밸런스가 있어야 1.0 으로 돌아온다)
        result = bt.simulate(
            two_stocks(), DATES, ["2026-01-02"], hold_both, bt.Costs.zero()
        )
        by_date = dict(result.curve)
        assert by_date["2026-02-02"] == pytest.approx(1.0)
        assert by_date["2026-03-02"] == pytest.approx(1.01)

    def test_오르는_것만_들면_복리로_는다(self) -> None:
        result = bt.simulate(
            two_stocks(), DATES, ["2026-01-02"], hold_one, bt.Costs.zero()
        )
        by_date = dict(result.curve)
        assert by_date["2026-02-02"] == pytest.approx(1.1)
        assert by_date["2026-03-03"] == pytest.approx(1.21)

    def test_첫_리밸런스_전에는_현금이다(self) -> None:
        result = bt.simulate(
            two_stocks(), DATES, ["2026-02-02"], hold_one, bt.Costs.zero()
        )
        by_date = dict(result.curve)
        assert by_date["2026-01-06"] == pytest.approx(1.0)

    def test_리밸런스_시_드리프트를_반영한다(self) -> None:
        """2월에 다시 반반으로 맞추면 그 뒤 3월은 다시 상쇄된다."""
        result = bt.simulate(
            two_stocks(), DATES, ["2026-01-02", "2026-02-02"], hold_both, bt.Costs.zero()
        )
        by_date = dict(result.curve)
        # 2월 2일 자본 1.0 을 반반으로 다시 맞춘다 → 3월 2일 (1.1+0.9)/2 = 1.0
        assert by_date["2026-03-02"] == pytest.approx(1.0)
        # 2월 리밸런스에서 종목 1 은 0.55 → 0.5, 종목 2 는 0.45 → 0.5 로 조정됐다
        assert result.rebalances[1].turnover == pytest.approx(0.05)


class Test현금비중:
    """비중 합이 1 미만이면 나머지는 현금(수익 0). docs/backtest.md 2.4 (추세 오버레이용)."""

    def test_반만_들면_수익도_반이다(self) -> None:
        def half(_t: str, _view: bt.PriceView) -> dict[int, float]:
            return {1: 0.5}

        result = bt.simulate(two_stocks(), DATES, ["2026-01-02"], half, bt.Costs.zero())
        by_date = dict(result.curve)
        assert by_date["2026-02-02"] == pytest.approx(1.05)  # 0.5×1.1 + 0.5 현금
        assert by_date["2026-03-02"] == pytest.approx(1.105)  # 0.5×1.21 + 0.5

    def test_합이_1을_넘으면_1로_줄인다(self) -> None:
        def double(_t: str, _view: bt.PriceView) -> dict[int, float]:
            return {1: 2.0}

        result = bt.simulate(two_stocks(), DATES, ["2026-01-02"], double, bt.Costs.zero())
        assert dict(result.curve)["2026-02-02"] == pytest.approx(1.1)

    def test_살_수_없는_종목의_몫은_남은_종목에_간다(self) -> None:
        # 종목 9 는 가격이 없다. 투자 비율(1.0)은 지키고 종목 1 에 전부 → 이전 동작과 같다
        def with_ghost(_t: str, _view: bt.PriceView) -> dict[int, float]:
            return {1: 0.5, 9: 0.5}

        result = bt.simulate(two_stocks(), DATES, ["2026-01-02"], with_ghost, bt.Costs.zero())
        assert dict(result.curve)["2026-02-02"] == pytest.approx(1.1)

    def test_현금으로_옮기는_거래도_비용을_낸다(self) -> None:
        calls = iter([{1: 1.0}, {1: 0.5}])

        def shrink(_t: str, _view: bt.PriceView) -> dict[int, float]:
            return next(calls)

        costs = bt.Costs(commission_pct=1.0, tax_sell_pct=0.0, slippage_pct=0.0)
        result = bt.simulate(two_stocks(), DATES, ["2026-01-02", "2026-02-02"], shrink, costs)
        # 2월: 100% → 50% 로 판 비중 0.5. turnover = (0 + 0.5)/2 = 0.25, 비용 = 0.25 × 2% = 0.5%
        assert result.rebalances[1].turnover == pytest.approx(0.25)
        assert dict(result.curve)["2026-02-02"] == pytest.approx(1.1 * 0.99 * (1 - 0.005))


class Test비용반영:
    def test_비용을_내면_결과가_작다(self) -> None:
        free = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_one, bt.Costs.zero())
        paid = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_one, bt.Costs())

        assert paid.curve[-1][1] < free.curve[-1][1]

    def test_첫_매수_비용이_정확하다(self) -> None:
        # 회전율 0.5(현금→전량), 매도 0 → 0.5 × 0.23% = 0.115%
        paid = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_one, bt.Costs())
        assert paid.rebalances[0].cost == pytest.approx(0.00115)
        assert dict(paid.curve)["2026-01-02"] == pytest.approx(1 - 0.00115)

    def test_마지막_날은_평가액이고_청산_비용을_빼지_않는다(self) -> None:
        """docs/backtest.md 3장 (docs/infra.md 25.284). 끝 날짜는 관찰을 멈춘 날이지 판 날이 아니다."""
        paid = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_one, bt.Costs())
        assert paid.curve[-1][1] == pytest.approx((1 - 0.00115) * 1.21)


class Test재현성:
    def test_같은_입력이면_같은_곡선(self) -> None:
        a = bt.simulate(two_stocks(), DATES, ["2026-01-02", "2026-02-02"], hold_both, bt.Costs())
        b = bt.simulate(two_stocks(), DATES, ["2026-01-02", "2026-02-02"], hold_both, bt.Costs())
        assert a.curve == b.curve


# ----------------------------------------------------------------------
# 상장폐지 — 살아남은 종목만 보지 않는다
# ----------------------------------------------------------------------


class Test상장폐지:
    def test_가격이_끊긴_종목은_마지막_종가에_묶인다(self) -> None:
        prices = two_stocks()
        # 종목 2 가 2월 3일 이후 사라진다 (상장폐지)
        for d in ("2026-03-02", "2026-03-03"):
            del prices[2][d]

        result = bt.simulate(prices, DATES, ["2026-01-02"], hold_both, bt.Costs.zero())
        by_date = dict(result.curve)
        # 3월: 종목 1 은 1.21, 종목 2 는 마지막 종가 90 에 묶임 → 0.5×1.21 + 0.5×0.9
        assert by_date["2026-03-02"] == pytest.approx(1.055)

    def test_끊긴_종목은_다음_리밸런스에서_다시_사지_않는다(self) -> None:
        prices = two_stocks()
        for d in ("2026-03-02", "2026-03-03"):
            del prices[2][d]

        result = bt.simulate(
            prices, DATES, ["2026-01-02", "2026-03-02"], hold_both, bt.Costs.zero()
        )
        march = result.rebalances[1]
        assert 2 in march.dropped
        assert set(march.weights) == {1}
        assert march.weights[1] == pytest.approx(1.0)

    def test_상장폐지가_없으면_생존편향_경고가_붙는다(self) -> None:
        result = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_both, bt.Costs.zero())
        assert bt.WARN_SURVIVORSHIP in result.warnings

    def test_상장폐지가_있다고_알리면_경고가_빠진다(self) -> None:
        result = bt.simulate(
            two_stocks(), DATES, ["2026-01-02"], hold_both, bt.Costs.zero(), has_delisted=True
        )
        assert bt.WARN_SURVIVORSHIP not in result.warnings


# ----------------------------------------------------------------------
# 입력 검증과 선정
# ----------------------------------------------------------------------


class Test입력:
    def test_리밸런스가_거래일에_없으면_거부(self) -> None:
        with pytest.raises(ValueError):
            bt.simulate(two_stocks(), DATES, ["2026-01-03"], hold_both, bt.Costs.zero())

    def test_거래일이_없으면_거부(self) -> None:
        with pytest.raises(ValueError):
            bt.simulate(two_stocks(), [], [], hold_both, bt.Costs.zero())

    def test_표본이_적으면_경고(self) -> None:
        result = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_both, bt.Costs.zero())
        assert bt.WARN_FEW_REBALANCES in result.warnings


class Test상위N:
    def test_점수_순으로_동일가중(self) -> None:
        w = bt.top_n_equal_weight({1: 90.0, 2: 80.0, 3: 70.0}, n=2)
        assert w == {1: 0.5, 2: 0.5}

    def test_점수_없는_종목은_뽑지_않는다(self) -> None:
        w = bt.top_n_equal_weight({1: 90.0, 2: None}, n=2)
        assert w == {1: 1.0}

    def test_동점은_종목_번호로_가른다(self) -> None:
        """재현성. 같은 입력이면 같은 선택이어야 한다."""
        w = bt.top_n_equal_weight({5: 90.0, 3: 90.0}, n=1)
        assert w == {3: 1.0}

    def test_아무것도_없으면_비어_있다(self) -> None:
        assert bt.top_n_equal_weight({}, n=5) == {}


# ----------------------------------------------------------------------
# 요약
# ----------------------------------------------------------------------


class Test요약:
    def test_지표는_metrics_compute_와_같다(self) -> None:
        """새 식을 만들지 않는다."""
        from batch.services import metrics as m

        result = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_one, bt.Costs.zero())
        summary = bt.summarize(result)
        direct = m.compute("1Y", result.points())

        assert summary.metrics.cagr == direct.cagr
        assert summary.metrics.mdd == direct.mdd
        assert summary.final_equity == pytest.approx(1.21)

    def test_벤치마크_대비_승률(self) -> None:
        mine = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_one, bt.Costs.zero())
        bench = bt.simulate(two_stocks(), DATES, ["2026-01-02"], hold_both, bt.Costs.zero())
        summary = bt.summarize(mine, bench)
        # 2월·3월 두 달 모두 종목 1 만 든 쪽이 반반보다 낫다
        assert summary.win_rate == pytest.approx(1.0)


class Test낙폭곡선:
    def test_고점_대비_비율이고_최솟값이_MDD(self) -> None:
        curve = [("d1", 1.0), ("d2", 1.2), ("d3", 0.9), ("d4", 1.2), ("d5", 1.5)]
        dd = bt.drawdown_curve(curve)
        assert dd == pytest.approx([0.0, 0.0, 0.9 / 1.2 - 1, 0.0, 0.0])
        assert min(dd) == pytest.approx(-0.25)

    def test_빈_곡선은_빈_목록(self) -> None:
        assert bt.drawdown_curve([]) == []


class Test나라마다_다른_거래비용:
    """**미국에는 매매마다 붙는 세금이 없다** (docs/infra.md 25.126).

    기본값(`Costs()`)은 국내 기준이다. `jobs/backtest` 가 나라를 안 보고 그것을 썼다 —
    미국 백테스트가 매도마다 **국내 증권거래세 0.18%** 를 물었다. 월 1회 리밸런스에
    매도 비중 0.25 면 **연 0.5%** 다. 전략 비교가 그 폭에서 뒤집힌다.

    같은 말이 `services/portfolio.CostRates` 에는 이미 적혀 있었다("미국은 매매마다 붙는
    세금이 없다")고, `jobs/portfolio.cost_rates` 는 USD 에 `sell_tax_pct=None` 을 준다.
    **옆집에 있는 규칙을 백테스트만 몰랐다.**
    """

    def test_미국은_거래세가_0_이다(self) -> None:
        assert bt.Costs.for_country("US").tax_sell_pct == bt.US_SELL_TAX_PCT == 0.0

    def test_국내는_기본_거래세를_쓴다(self) -> None:
        assert bt.Costs.for_country("KR").tax_sell_pct == bt.Costs().tax_sell_pct

    def test_설정이_있으면_설정을_쓴다(self) -> None:
        """`docs/backtest.md` 가 "settings.taxes 가 있으면 그 값" 이라고 적어 두었는데
        코드는 한 번도 읽지 않았다. 문서가 약속하고 코드가 안 하던 자리다(25.0)."""
        costs = bt.Costs.for_country(
            "KR",
            {"kr_buy_pct": 0.01, "kr_sell_pct": 0.03},
            {"kr_transaction_pct": 0.15},
        )
        assert costs.tax_sell_pct == 0.15
        # 편도 수수료다. 매수·매도의 평균이 곧 편도 — 둘을 더하면 왕복이 된다
        assert costs.commission_pct == pytest.approx(0.02)

    def test_설정의_0_은_비어_있음과_다르다(self) -> None:
        """수수료 0 인 증권사도 있다. 0 을 "안 넣었다" 로 읽어 기본값으로 되돌리면 안 된다."""
        assert bt.Costs.for_country("KR", {"kr_buy_pct": 0.0, "kr_sell_pct": 0.0}).commission_pct == 0.0

    def test_기본값을_쓴_항목만_적는다(self) -> None:
        """화면이 늘 "기본값" 이라 붙이던 것을 항목별로 (docs/infra.md 25.358)."""
        from dataclasses import asdict

        설정 = bt.Costs.for_country("KR", {"kr_buy_pct": 0.005, "kr_sell_pct": 0.005}, {"kr_transaction_pct": 0.15})
        assert 설정.defaults == ("slippage",)
        assert bt.Costs.for_country("KR").defaults == ("commission", "tax", "slippage")
        assert bt.Costs.for_country("US").defaults == ("commission", "slippage")  # 미국 세금 0 은 규칙이지 기본값이 아니다
        assert asdict(설정)["defaults"] == ("slippage",)
        assert bt.Costs.for_country("KR", {}, {"kr_transaction_pct": 0.0}).tax_sell_pct == 0.0

    def test_한쪽만_채워도_그_값을_쓴다(self) -> None:
        assert bt.Costs.for_country("US", {"us_sell_pct": 0.05}).commission_pct == pytest.approx(0.05)

    def test_국내_거래세는_체결일의_법정_세율이다(self) -> None:
        """전 구간 0.18% 이던 것을 시행일별로 (docs/infra.md 25.783, 기법 발굴 5회차 C①)."""
        kr = bt.Costs.for_country("KR")
        기대 = {"2019-01-02": 0.30, "2019-06-03": 0.25, "2022-12-30": 0.23, "2023-01-02": 0.20,
               "2024-06-03": 0.18, "2025-03-04": 0.15, "2026-09-01": 0.20}  # fmt: skip
        for 날, 세율 in 기대.items():
            assert kr.tax_at(날) == 세율, 날
        # 매도 비중 1 → 비용에 그날 세율이 들어간다 (수수료·슬리피지 몫은 같으므로 차이만 본다)
        assert kr.rebalance_cost(0.0, 1.0, "2025-03-04") == pytest.approx(0.0015)
        assert kr.rebalance_cost(0.0, 1.0, "2021-03-02") == pytest.approx(0.0023)
        assert kr.tax_sell_pct == 0.20  # 지금 시행 중인 값

    def test_설정_세율은_지금_시행_구간만_바꾼다(self) -> None:
        """설정 화면의 세율은 사용자가 지금 치르는 값이다 — 과거 매도에 물리지 않는다 (25.783)."""
        kr = bt.Costs.for_country("KR", {}, {"kr_transaction_pct": 0.25})
        assert kr.tax_at("2026-03-03") == 0.25
        assert kr.tax_at("2025-03-04") == 0.15
        assert kr.tax_sell_pct == 0.25 and "tax" not in kr.defaults

    def test_미국은_시점별_세율이_없다(self) -> None:
        us = bt.Costs.for_country("US")
        assert us.tax_schedule == () and us.tax_at("2021-03-02") == 0.0

    def test_슬리피지는_나라를_가리지_않는다(self) -> None:
        # 설정에 항목이 없다. 둘 다 같은 [확인필요] 값이어야 한다
        assert bt.Costs.for_country("US").slippage_pct == bt.Costs.for_country("KR").slippage_pct

    def test_미국_매도_비용이_국내보다_싸다(self) -> None:
        """**값이 아니라 결과를 본다.** 상수만 맞춰 두고 계산에 안 쓰면 소용없다 (25.108)."""
        국내 = bt.Costs.for_country("KR").rebalance_cost(turnover=0.25, sold=0.25)
        미국 = bt.Costs.for_country("US").rebalance_cost(turnover=0.25, sold=0.25)
        assert 미국 < 국내
        assert 국내 - 미국 == pytest.approx(0.25 * bt.Costs().tax_sell_pct / 100)  # 날짜 없이 부르면 지금 세율


def test_백테스트_작업이_나라를_넘긴다() -> None:
    """**부르는 곳이 없으면 분류 메서드를 하나 더 만든 것일 뿐이다** (25.123 에서 배운 것).

    `ast` 로 호출 노드를 센다 — 주석에 적어 둔 것은 안 센다.
    """
    import ast
    from pathlib import Path

    본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "backtest.py").read_text("utf-8")
    나무 = ast.parse(본문)
    부름 = [
        n
        for n in ast.walk(나무)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "for_country"
    ]
    assert 부름, "jobs/backtest 가 Costs.for_country 를 안 부른다 — 나라를 안 보고 비용을 매긴다"

    민낯 = [
        n.lineno
        for n in ast.walk(나무)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "Costs"
        and not n.args
    ]
    assert not 민낯, f"기본 Costs() 를 그대로 쓴다(국내 기준이다): {민낯}"


def test_배당_경고는_나라마다_다르다() -> None:
    """docs/infra.md 25.244 — 미국 수정종가는 배당 포함 총수익인데 '가격 수익만' 이라고 적었다."""
    from batch.services import backtest as bt_

    가격 = {1: {"2024-01-02": 100.0, "2024-02-01": 110.0}}
    날 = ["2024-01-02", "2024-02-01"]
    def 전부(_d: str, _view: object) -> dict[int, float]:
        return {1: 1.0}

    국내 = bt_.simulate(가격, 날, ["2024-01-02"], 전부, bt_.Costs.zero())
    미국 = bt_.simulate(가격, 날, ["2024-01-02"], 전부, bt_.Costs.zero(), dividends_included=True)
    assert bt_.WARN_NO_DIVIDENDS in 국내.warnings and bt_.WARN_TOTAL_RETURN not in 국내.warnings
    assert bt_.WARN_TOTAL_RETURN in 미국.warnings and bt_.WARN_NO_DIVIDENDS not in 미국.warnings


def test_가격_수준을_따로_주면_시총은_그것으로_잰다() -> None:
    """docs/infra.md 25.275 — 미국 수정종가(배당 포함)로 시총을 재면 미래 배당만큼 작게 나왔다."""
    from batch.services import backtest as bt_

    수정 = {1: {"2024-01-02": 82.0, "2024-01-03": 83.0}}
    원 = {1: {"2024-01-02": 100.0, "2024-01-03": 101.0}}
    view = bt_.PriceView(수정, "2024-01-03", None, 원)
    assert view.level_closes(1)[-1] == ("2024-01-03", 101.0)
    assert view.closes(1)[-1] == ("2024-01-03", 83.0)  # 수익률은 그대로 수정종가
    # 수준을 안 주면(국내) 같은 값
    assert bt_.PriceView(수정, "2024-01-03").level_closes(1) == bt_.PriceView(수정, "2024-01-03").closes(1)


def test_백테스트_입력의_시총은_가격_수준으로_잰다() -> None:
    """시총을 내는 자리가 `level_closes` 를 쓰는지 (25.275). 수익률 자리는 그대로 `closes`."""
    import inspect

    from batch.jobs import backtest as job

    원본 = inspect.getsource(job)
    assert "수준 = view.level_closes(sid)" in 원본
    assert "view.level_closes(sid),  # PBR 밴드는 가격 수준" in 원본


def test_첫_리밸런스도_워밍업_가격으로_판단한다() -> None:
    """날짜 축이 시작일 이후만 담아 첫 리밸런스는 늘 빈 창이었다 (docs/infra.md 25.623, 감사)."""
    prices = {1: {"2025-12-30": 100.0, "2025-12-31": 101.0, "2026-01-02": 102.0, "2026-01-05": 103.0}}
    본_창: list[str | None] = []

    def 판단(t: str, view: bt.PriceView) -> dict[int, float]:
        본_창.append(view.cutoff)
        return {1: 1.0} if view.values(1) else {}

    r = bt.simulate(prices, ["2026-01-02", "2026-01-05"], ["2026-01-02"], 판단, bt.Costs(0, 0, 0))
    assert 본_창 == ["2025-12-31"], "t-1 은 워밍업의 마지막 날이다 — t 당일은 보지 않는다"
    assert r.rebalances[0].weights == {1: 1.0}


def test_판단_기준일은_전략과_IC_가_같은_규칙이다() -> None:
    """IC 만 첫 달을 건너뛰어 전략과 한 달 어긋났다 (docs/infra.md 25.627, 교차검증)."""
    import inspect

    from batch.jobs import backtest as job

    prices = {1: {"2025-12-31": 1.0, "2026-01-02": 1.0}}
    assert bt.decision_cutoff(["2026-01-02"], prices, "2026-01-02") == "2025-12-31"
    assert bt.decision_cutoff(["2026-01-02", "2026-01-05"], prices, "2026-01-05") == "2026-01-02"
    assert bt.decision_cutoff([], {}, "2026-01-02") is None
    assert "bt.decision_cutoff(dates, prices, t)" in inspect.getsource(job.factor_ics)


def test_리밸런스는_그날의_거래세를_문다() -> None:
    """2025 년 매도는 0.15%, 2026 년 매도는 0.20% (docs/infra.md 25.783). 날짜를 넘기지 않으면 지금 세율 하나다."""
    날들 = ["2024-12-30", "2025-01-02", "2025-02-03", "2026-01-02", "2026-02-02", "2026-02-03"]  # 마지막 날은 평가만(25.792)
    가격 = {1: {d: 100.0 for d in 날들}, 2: {d: 100.0 for d in 날들}}
    차례 = {"2025-01-02": {1: 1.0}, "2025-02-03": {2: 1.0}, "2026-01-02": {1: 1.0}, "2026-02-02": {2: 1.0}}

    def 판단(d, view):  # noqa: ANN001, ANN202
        return 차례[d]

    세금만 = bt.Costs(commission_pct=0.0, tax_sell_pct=0.20, slippage_pct=0.0, tax_schedule=bt.KR_SELL_TAX_SCHEDULE)
    r = bt.simulate(가격, 날들, list(차례), 판단, 세금만)
    비용 = {rb.date: rb.cost for rb in r.rebalances}
    assert 비용["2025-02-03"] == pytest.approx(0.0015)  # 전량 교체, 2025 년 세율
    assert 비용["2026-02-02"] == pytest.approx(0.0020)


def test_마지막_날이_월초여도_일부만_들어온_시세로_나머지를_팔지_않는다() -> None:
    """끝 날에 다섯 종목 중 하나만 시세가 있으면 나머지를 폐지로 보고 팔았다 (docs/infra.md 25.792, 백테스트 감사 #4)."""
    날들 = ["2026-08-31", "2026-09-01", "2026-09-30", "2026-10-01"]
    가격 = {sid: {d: 100.0 for d in 날들[:3]} for sid in range(1, 6)}
    가격[1]["2026-10-01"] = 100.0  # 끝 날엔 한 종목만 들어왔다

    def 판단(d, view):  # noqa: ANN001, ANN202
        return {sid: 0.2 for sid in range(1, 6)}

    r = bt.simulate(가격, 날들, ["2026-09-01", "2026-10-01"], 판단, bt.Costs(0.015, 0.2, 0.1))
    assert [rb.date for rb in r.rebalances] == ["2026-09-01"]  # 끝 날은 평가만
    # 첫 매수 비용만 빠지고, 끝에서 회전·비용이 붙지 않는다
    assert r.curve[-1][1] == pytest.approx(r.curve[1][1])
