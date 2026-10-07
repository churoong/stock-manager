"""성과 지표 테스트.

CLAUDE.md 요구사항: CAGR·MDD·샤프는 **손으로 계산 가능한 고정 데이터**로
검증한다. 각 테스트에 기대값의 근거를 적어 둔다. 근거 없이 숫자만 적으면
코드가 틀렸을 때 테스트도 함께 틀린다.

네트워크도 데이터베이스도 타지 않는다.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from batch.services import metrics as m


def series(values: list[float], start: date = date(2020, 1, 1), step_days: int = 1):
    """값 목록을 날짜가 붙은 계열로 바꾼다."""
    return [
        m.PricePoint(date=start + timedelta(days=i * step_days), close=v)
        for i, v in enumerate(values)
    ]


class Test일간수익률:
    def test_기본(self) -> None:
        # 100 → 110 은 +10%, 110 → 99 는 -10%
        returns = m.daily_returns(series([100, 110, 99]))

        assert returns[0] == pytest.approx(0.1)
        assert returns[1] == pytest.approx(-0.1)

    def test_점이_하나면_수익률이_없다(self) -> None:
        assert m.daily_returns(series([100])) == []

    def test_0_이하_가격은_건너뛴다(self) -> None:
        # 거래정지 종목에서 0 이 들어오면 나누기가 무한대가 된다
        returns = m.daily_returns(series([100, 0, 110]))
        assert returns == []

    def test_음수_가격도_건너뛴다(self) -> None:
        assert m.daily_returns(series([100, -5, 110])) == []


class Test표본표준편차:
    def test_손으로_계산한_값과_맞는다(self) -> None:
        # [1, 2, 3, 4] 의 평균은 2.5
        # 편차 제곱합 = 2.25 + 0.25 + 0.25 + 2.25 = 5
        # 표본분산 = 5 / 3 = 1.6667, 표준편차 = 1.29099
        assert m.stdev([1, 2, 3, 4]) == pytest.approx(1.2909944, rel=1e-6)

    def test_모집단이_아니라_표본이다(self) -> None:
        # 모집단 표준편차라면 sqrt(5/4) = 1.118 이 나온다
        assert m.stdev([1, 2, 3, 4]) != pytest.approx(1.1180339, rel=1e-6)

    def test_관측치가_둘_미만이면_None(self) -> None:
        assert m.stdev([]) is None
        assert m.stdev([1.0]) is None

    def test_모두_같으면_0(self) -> None:
        assert m.stdev([5, 5, 5]) == 0


class TestCAGR:
    def test_1년에_두_배면_100퍼센트(self) -> None:
        # 365.25일이 정확히 1년이다
        points = [
            m.PricePoint(date(2020, 1, 1), 100),
            m.PricePoint(date(2020, 12, 31) + timedelta(days=1), 200),
        ]
        # 2020-01-01 에서 2021-01-01 은 366일 (윤년)
        # 연수 = 366 / 365.25 = 1.0020534
        # CAGR = 2^(1/1.0020534) - 1 = 0.99716
        assert m.cagr(points) == pytest.approx(0.99716, rel=1e-4)

    def test_2년에_1점21배면_10퍼센트(self) -> None:
        # 1.21 = 1.1^2 이므로 연 10%
        points = [
            m.PricePoint(date(2020, 1, 1), 100),
            m.PricePoint(date(2022, 1, 1), 121),
        ]
        # 2020-01-01 에서 2022-01-01 은 731일, 연수 = 2.00137
        # 1.21^(1/2.00137) - 1 = 0.09992
        assert m.cagr(points) == pytest.approx(0.09992, rel=1e-3)

    def test_제자리면_0(self) -> None:
        points = [
            m.PricePoint(date(2020, 1, 1), 100),
            m.PricePoint(date(2023, 1, 1), 100),
        ]
        assert m.cagr(points) == pytest.approx(0.0, abs=1e-12)

    def test_반토막이면_음수(self) -> None:
        points = [
            m.PricePoint(date(2020, 1, 1), 100),
            m.PricePoint(date(2021, 1, 1), 50),
        ]
        result = m.cagr(points)
        assert result is not None and result < -0.49

    def test_시작_가격이_0이면_None(self) -> None:
        points = [m.PricePoint(date(2020, 1, 1), 0), m.PricePoint(date(2021, 1, 1), 100)]
        assert m.cagr(points) is None

    def test_같은_날짜면_None(self) -> None:
        # 연수가 0 이면 나눌 수 없다
        points = [m.PricePoint(date(2020, 1, 1), 100), m.PricePoint(date(2020, 1, 1), 200)]
        assert m.cagr(points) is None

    def test_점이_하나면_None(self) -> None:
        assert m.cagr(series([100])) is None

    def test_연수는_거래일이_아니라_달력_기준이다(self) -> None:
        # 같은 가격 변화라도 기간이 길면 CAGR 이 낮다
        short = [m.PricePoint(date(2020, 1, 1), 100), m.PricePoint(date(2021, 1, 1), 200)]
        long = [m.PricePoint(date(2020, 1, 1), 100), m.PricePoint(date(2022, 1, 1), 200)]

        assert m.cagr(short) > m.cagr(long)


class TestMDD:
    def test_손으로_계산한_값과_맞는다(self) -> None:
        # 고점 120 에서 바닥 60 → 60/120 - 1 = -0.5
        # 그 뒤 150 으로 올라가지만 최대 낙폭은 바뀌지 않는다
        result = m.max_drawdown(series([100, 120, 60, 80, 150]))
        assert result.mdd == pytest.approx(-0.5)

    def test_고점과_바닥_날짜를_알려준다(self) -> None:
        result = m.max_drawdown(series([100, 120, 60, 80, 150]))

        assert result.peak_date == date(2020, 1, 2)  # 120 인 날
        assert result.trough_date == date(2020, 1, 3)  # 60 인 날

    def test_회복까지_걸린_거래일을_센다(self) -> None:
        # 바닥(60)에서 고점(120)을 다시 넘는 데 두 걸음 걸렸다
        result = m.max_drawdown(series([100, 120, 60, 80, 150]))
        assert result.recovery_days == 2

    def test_아직_회복_못_했으면_None(self) -> None:
        # None 과 0 은 다르다. None 이 더 나쁜 신호다
        result = m.max_drawdown(series([100, 120, 60, 80, 90]))

        assert result.mdd == pytest.approx(-0.5)
        assert result.recovery_days is None

    def test_계속_오르기만_하면_낙폭이_0(self) -> None:
        result = m.max_drawdown(series([100, 110, 120, 130]))
        assert result.mdd == 0.0
        assert result.peak_date is None

    def test_계속_내려가면_전체가_낙폭이다(self) -> None:
        # 100 에서 25 로 → -0.75
        result = m.max_drawdown(series([100, 75, 50, 25]))
        assert result.mdd == pytest.approx(-0.75)
        assert result.recovery_days is None

    def test_더_깊은_낙폭이_나중에_오면_그것을_쓴다(self) -> None:
        # 앞: 100 → 80 (-20%), 뒤: 120 → 60 (-50%)
        result = m.max_drawdown(series([100, 80, 100, 120, 60]))
        assert result.mdd == pytest.approx(-0.5)

    def test_점이_하나면_모두_None(self) -> None:
        result = m.max_drawdown(series([100]))
        assert result.mdd is None


class Test변동성:
    def test_손으로_계산한_값과_맞는다(self) -> None:
        # 수익률이 +10%, -10% 를 반복하면
        #   평균 0, 편차 제곱합 = 0.01 × 4 = 0.04
        #   표본분산 = 0.04 / 3 = 0.0133333
        #   일간 표준편차 = 0.1154701
        #   연환산 = 0.1154701 × √252 = 1.83313
        prices = [100.0]
        for i in range(4):
            prices.append(prices[-1] * (1.1 if i % 2 == 0 else 0.9))
        # 위 계열의 수익률은 정확히 [0.1, -0.1, 0.1, -0.1] 이다

        result = m.volatility(series(prices))
        assert result == pytest.approx(0.1154701 * math.sqrt(252), rel=1e-5)

    def test_움직이지_않으면_0(self) -> None:
        assert m.volatility(series([100, 100, 100, 100])) == 0

    def test_점이_둘이면_수익률이_하나라_None(self) -> None:
        assert m.volatility(series([100, 110])) is None

    def test_연환산_계수는_252다(self) -> None:
        assert m.TRADING_DAYS_PER_YEAR == 252
        assert m.annualize_volatility(1.0) == pytest.approx(math.sqrt(252))


class Test하방편차:
    def test_상승만_있으면_0(self) -> None:
        assert m.downside_deviation([0.1, 0.2, 0.3]) == 0

    def test_손으로_계산한_값과_맞는다(self) -> None:
        # [0.1, -0.2, 0.1, -0.2] 에서 음수만 제곱해 더하면
        #   0.04 + 0.04 = 0.08
        # 전체 관측치 수로 나눈다: 0.08 / 3 = 0.0266667
        # 제곱근 = 0.1632993
        assert m.downside_deviation([0.1, -0.2, 0.1, -0.2]) == pytest.approx(
            0.1632993, rel=1e-5
        )

    def test_하락한_날_수로_나누지_않는다(self) -> None:
        # 하락한 날이 2일이라고 0.08/1 로 나누면 0.283 이 나온다.
        # 그러면 하락이 드문 종목의 하방편차가 과대평가된다
        assert m.downside_deviation([0.1, -0.2, 0.1, -0.2]) != pytest.approx(
            0.2828427, rel=1e-5
        )

    def test_관측치가_둘_미만이면_None(self) -> None:
        assert m.downside_deviation([-0.1]) is None


class Test샤프:
    def test_손으로_계산한_값과_맞는다(self) -> None:
        # 1년 동안 100 → 120, 무위험 2%, 변동성을 알고 있는 계열을 만든다
        # 계산: (CAGR - 0.02) / 연환산변동성
        points = [
            m.PricePoint(date(2020, 1, 1) + timedelta(days=i), v)
            for i, v in enumerate([100, 110, 99, 108.9])
        ]
        growth = m.cagr(points)
        sigma = m.volatility(points)
        expected = (growth - 0.02) / sigma

        assert m.sharpe(points, 0.02) == pytest.approx(expected)

    def test_무위험수익률이_없으면_None(self) -> None:
        # 0 으로 두면 무위험수익률이 0인 세상의 값이 나오는데
        # 그게 맞는 값처럼 보인다
        assert m.sharpe(series([100, 110, 99, 108]), None) is None

    def test_움직이지_않으면_None(self) -> None:
        # 변동성 0 인 종목의 샤프는 뜻이 없다
        assert m.sharpe(series([100, 100, 100, 100]), 0.02) is None

    def test_무위험수익률이_높을수록_낮아진다(self) -> None:
        points = series([100, 110, 99, 108.9])
        assert m.sharpe(points, 0.05) < m.sharpe(points, 0.01)


class Test소르티노:
    def test_하락이_없으면_None(self) -> None:
        # 하방편차가 0 이면 나눌 수 없다
        assert m.sortino(series([100, 110, 120, 130]), 0.02) is None

    def test_무위험수익률이_없으면_None(self) -> None:
        assert m.sortino(series([100, 90, 110, 95]), None) is None

    def test_샤프보다_크다(self) -> None:
        # 하방편차는 전체 변동성보다 작거나 같으므로
        # 같은 분자에 더 작은 분모를 쓰면 값이 커진다
        points = series([100, 110, 99, 120, 108])
        sharpe = m.sharpe(points, 0.02)
        sortino = m.sortino(points, 0.02)

        assert sharpe is not None and sortino is not None
        assert sortino > sharpe


class Test베타:
    def test_시장과_똑같이_움직이면_1(self) -> None:
        prices = [100, 110, 99, 108.9, 120]
        result, n = m.beta(series(prices), series(prices))

        assert result == pytest.approx(1.0)
        assert n == 4

    def test_시장의_두_배로_움직이면_2(self) -> None:
        # 시장 수익률이 [+10%, -10%] 일 때 종목이 [+20%, -20%] 면 베타 2
        market_prices = [100.0]
        stock_prices = [100.0]
        for i in range(6):
            up = i % 2 == 0
            market_prices.append(market_prices[-1] * (1.1 if up else 0.9))
            stock_prices.append(stock_prices[-1] * (1.2 if up else 0.8))

        result, _ = m.beta(series(stock_prices), series(market_prices))
        assert result == pytest.approx(2.0)

    def test_반대로_움직이면_음수(self) -> None:
        market_prices = [100.0]
        stock_prices = [100.0]
        for i in range(6):
            up = i % 2 == 0
            market_prices.append(market_prices[-1] * (1.1 if up else 0.9))
            stock_prices.append(stock_prices[-1] * (0.9 if up else 1.1))

        result, _ = m.beta(series(stock_prices), series(market_prices))
        assert result is not None and result < 0

    def test_시장이_움직이지_않으면_None(self) -> None:
        # 분모가 0 이다
        result, _ = m.beta(series([100, 110, 99, 120]), series([100, 100, 100, 100]))
        assert result is None

    def test_겹치는_날이_모자라면_None(self) -> None:
        stock = series([100, 110, 120], start=date(2020, 1, 1))
        market = series([100, 110, 120], start=date(2021, 1, 1))

        result, n = m.beta(stock, market)
        assert result is None
        assert n == 0


class Test베타는_수익률을_짝으로_낸다:
    """**한쪽에만 있는 빈 날이 뒤의 짝을 전부 어긋나게 했다** (docs/infra.md 25.202)."""

    @staticmethod
    def _두_계열(배: float = 2.0) -> tuple[list[m.PricePoint], list[m.PricePoint]]:
        """시장 수익률의 정확히 `배` 배로 움직이는 종목. 베타가 손으로 `배` 다."""

        시작 = date(2025, 1, 2)
        시장 = [m.PricePoint(시작, 100.0)]
        종목 = [m.PricePoint(시작, 50.0)]
        for i in range(1, 301):
            r = 0.01 * math.sin(i * 1.7) + 0.003 * ((i * 7) % 5 - 2)
            d = 시작 + timedelta(days=i)
            시장.append(m.PricePoint(d, 시장[-1].close * (1 + r)))
            종목.append(m.PricePoint(d, 종목[-1].close * (1 + 배 * r)))
        return 종목, 시장

    def test_표본의_베타는_손으로_2다(self) -> None:
        종목, 시장 = self._두_계열()
        result, n = m.beta(종목, 시장)
        assert result == pytest.approx(2.0) and n == 300

    def test_종가_0_하루가_베타를_바꾸지_않는다(self) -> None:
        """거래정지 종목에서 0 이 들어온다(docs/metrics.md 0장). 고치기 전에는 0.25 였다."""
        종목, 시장 = self._두_계열()
        종목[150] = m.PricePoint(종목[150].date, 0.0)

        result, n = m.beta(종목, 시장)

        assert result == pytest.approx(2.0)
        assert n == 298  # 그날 앞뒤 두 쌍을 양쪽에서 함께 버렸다

    def test_짝을_버릴_때_양쪽을_같이_버린다(self) -> None:
        종목, 시장 = self._두_계열()
        시장[10] = m.PricePoint(시장[10].date, -1.0)

        s_ret, m_ret = m.paired_returns(종목, 시장)

        assert len(s_ret) == len(m_ret) == 298


class Test종가_0은_값이_없는_날이다:
    """**꾸준히 오른 종목이 0 하루로 MDD −100% 가 됐다** (docs/infra.md 25.203)."""

    @staticmethod
    def _오르는_계열() -> list[m.PricePoint]:
        시작 = date(2025, 1, 2)
        return [m.PricePoint(시작 + timedelta(days=i), 100 + i * 0.1) for i in range(300)]

    def test_미끼__0_이_없으면_낙폭이_없다(self) -> None:
        결과 = m.compute("1Y", self._오르는_계열())
        assert 결과.mdd == pytest.approx(0.0)

    def test_0_하루가_MDD_를_만들지_않는다(self) -> None:
        계열 = self._오르는_계열()
        계열[150] = m.PricePoint(계열[150].date, 0.0)

        결과 = m.compute("1Y", 계열)

        assert 결과.mdd == pytest.approx(0.0)
        assert 결과.data_points == 299  # 뺀 날은 세지 않는다

    def test_마지막_날이_0_이어도_CAGR_을_낸다(self) -> None:
        """끝점이 0 이면 예전에는 CAGR 이 통째로 None 이었다. 앞의 값으로 낸다."""
        계열 = self._오르는_계열()
        계열[-1] = m.PricePoint(계열[-1].date, 0.0)

        assert m.compute("1Y", 계열).cagr is not None


class Test날짜맞추기:
    def test_겹치는_날만_남긴다(self) -> None:
        left = [
            m.PricePoint(date(2020, 1, 1), 100),
            m.PricePoint(date(2020, 1, 2), 110),
            m.PricePoint(date(2020, 1, 3), 120),
        ]
        right = [
            m.PricePoint(date(2020, 1, 2), 200),
            m.PricePoint(date(2020, 1, 3), 210),
            m.PricePoint(date(2020, 1, 4), 220),
        ]

        a, b = m.align(left, right)

        assert len(a) == 2
        assert [p.date for p in a] == [date(2020, 1, 2), date(2020, 1, 3)]
        assert [p.date for p in b] == [date(2020, 1, 2), date(2020, 1, 3)]

    def test_날짜순으로_정렬한다(self) -> None:
        left = [
            m.PricePoint(date(2020, 1, 3), 120),
            m.PricePoint(date(2020, 1, 1), 100),
        ]
        right = [
            m.PricePoint(date(2020, 1, 1), 200),
            m.PricePoint(date(2020, 1, 3), 210),
        ]

        a, _ = m.align(left, right)
        assert [p.date for p in a] == [date(2020, 1, 1), date(2020, 1, 3)]

    def test_겹치지_않으면_빈_목록(self) -> None:
        a, b = m.align(series([100, 110]), series([100, 110], start=date(2021, 1, 1)))
        assert a == [] and b == []


class Test표본하한:
    def test_1년은_200거래일이_필요하다(self) -> None:
        assert m.MIN_POINTS["1Y"] == 200

    def test_모자라면_값을_채우지_않는다(self) -> None:
        # 100일치로 계산한 값을 "1년 변동성" 이라고 부르면 안 된다
        result = m.compute("1Y", series([100.0 + i for i in range(100)]))

        assert result.data_points == 100
        assert result.cagr is None
        assert result.volatility_ann is None
        assert result.mdd is None

    def test_충분하면_계산한다(self) -> None:
        points = series([100.0 + i for i in range(250)])
        result = m.compute("1Y", points, risk_free_annual=0.02)

        assert result.data_points == 250
        assert result.cagr is not None
        assert result.volatility_ann is not None
        assert result.mdd is not None

    def test_표본이_모자라도_개수는_남긴다(self) -> None:
        # 왜 비었는지 나중에 알 수 있어야 한다
        result = m.compute("3Y", series([100.0] * 10))
        assert result.data_points == 10


class Test한벌계산:
    def make(self, n: int = 300):
        """오르내리며 우상향하는 계열."""
        prices = [100.0]
        for i in range(n - 1):
            prices.append(prices[-1] * (1.01 if i % 3 else 0.995))
        return series(prices)

    def test_무위험수익률을_함께_기록한다(self) -> None:
        result = m.compute("1Y", self.make(), risk_free_annual=0.032)

        # 나중에 값이 바뀌어도 그때 쓴 값으로 재현할 수 있어야 한다
        assert result.risk_free_rate_used == 0.032

    def test_계산식_판을_기록한다(self) -> None:
        result = m.compute("1Y", self.make())
        assert result.calc_version == m.CALC_VERSION

    def test_벤치마크가_없으면_베타도_없다(self) -> None:
        result = m.compute("1Y", self.make())

        assert result.beta is None
        assert result.benchmark is None

    def test_벤치마크를_주면_베타를_계산한다(self) -> None:
        points = self.make()
        result = m.compute("1Y", points, market=points, benchmark="KOSPI")

        assert result.beta == pytest.approx(1.0)
        assert result.benchmark == "KOSPI"

    def test_순서가_뒤섞여_들어와도_정렬한다(self) -> None:
        points = self.make()
        shuffled = list(reversed(points))

        ordered = m.compute("1Y", points)
        result = m.compute("1Y", shuffled)

        assert result.cagr == pytest.approx(ordered.cagr)


class Test구멍을_사이에_둔_수익률:
    """**2주 쉬었다 재개한 첫날 움직임은 하루치가 아니다** (docs/metrics.md 0장, infra 25.102).

    `P_t / P_{t-1} - 1` 이 하루치라는 것은 두 행이 **잇닿은 거래일**일 때만 참이다.
    그것을 일간 수익률로 세면 변동성·샤프·소르티노가 그 한 점에 끌려가고,
    **정지 이력이 있는 종목이 일제히 "위험한 종목"** 으로 보인다.
    """

    @staticmethod
    def _점(날: str, 값: float) -> m.PricePoint:
        from datetime import date

        return m.PricePoint(date=date.fromisoformat(날), close=값)

    def test_잇닿은_날은_그대로_센다(self) -> None:
        점들 = [self._점("2026-09-14", 100.0), self._점("2026-09-15", 110.0)]

        assert m.daily_returns(점들) == [pytest.approx(0.1)]

    def test_주말은_걸리지_않는다(self) -> None:
        """금요일 → 월요일은 3일이다. **정상 휴장은 절대 안 걸린다.**"""
        점들 = [self._점("2026-09-18", 100.0), self._점("2026-09-21", 110.0)]

        assert m.daily_returns(점들) == [pytest.approx(0.1)]

    def test_최장_연휴도_안_걸린다(self) -> None:
        """실측한 최악(2017 추석 11일)이 문턱과 **같다**. 같으면 통과다."""
        점들 = [self._점("2017-09-29", 100.0), self._점("2017-10-10", 130.0)]

        assert (점들[1].date - 점들[0].date).days == m.MAX_SESSION_GAP_DAYS
        assert m.daily_returns(점들) == [pytest.approx(0.3)]

    def test_그보다_길면_버린다(self) -> None:
        점들 = [self._점("2026-09-01", 100.0), self._점("2026-09-13", 130.0)]

        assert (점들[1].date - 점들[0].date).days == m.MAX_SESSION_GAP_DAYS + 1
        assert m.daily_returns(점들) == []

    def test_구멍_앞뒤는_살린다(self) -> None:
        """**버리는 것은 그 한 쌍뿐이다.** 계열 전체를 버리지 않는다."""
        점들 = [
            self._점("2026-08-31", 100.0),
            self._점("2026-09-01", 101.0),   # 정상
            self._점("2026-09-20", 150.0),   # 19일 구멍 → 버린다
            self._점("2026-09-21", 151.0),   # 정상
        ]

        수익률 = m.daily_returns(점들)

        assert len(수익률) == 2
        assert 수익률[0] == pytest.approx(0.01)
        assert 수익률[1] == pytest.approx(151 / 150 - 1)

    def test_변동성이_그_한_점에_안_끌려간다(self) -> None:
        """**이것이 고치려는 것이다.** 잔잔한 종목이 정지 한 번으로 위험해 보이면 안 된다."""
        from datetime import date, timedelta

        시작 = date(2026, 1, 1)
        잔잔 = [m.PricePoint(시작 + timedelta(days=i), 100.0 + (i % 2)) for i in range(60)]
        # 60일째 뒤에 20일 쉬고 40% 뛴 채로 재개
        튄것 = [*잔잔, m.PricePoint(시작 + timedelta(days=80), 140.0)]

        assert m.volatility(튄것) == pytest.approx(m.volatility(잔잔))

    def test_CAGR_과_MDD_는_이_규칙과_무관하다(self) -> None:
        """둘은 수익률이 아니라 **가격 경로**에서 나온다.

        쉬는 동안 값이 떨어졌다면 그것은 **실제 낙폭**이므로 MDD 에 들어가는 것이 맞다.
        """
        점들 = [
            self._점("2026-01-02", 100.0),
            self._점("2026-02-20", 40.0),    # 49일 구멍 뒤 폭락 → 수익률로는 안 센다
            self._점("2026-03-02", 45.0),    # 10일 뒤 → 이 쌍은 잇닿은 것으로 본다
        ]

        # 폭락한 그 한 쌍만 빠진다. 뒤의 회복분(45/40−1)은 그대로 센다
        assert m.daily_returns(점들) == [pytest.approx(45 / 40 - 1)]
        assert -0.6 not in [pytest.approx(r) for r in m.daily_returns(점들)]

        # **낙폭으로는 센다.** 쉬는 동안 값이 떨어진 것은 실제 낙폭이다
        assert m.max_drawdown(점들).mdd == pytest.approx(-0.6)
        assert m.cagr(점들) is not None

    def test_문턱이_실측과_같다(self) -> None:
        """어림이 아니다. 달력에서 **다시 재어** 대조한다."""
        xcals = pytest.importorskip("exchange_calendars")

        cal = xcals.get_calendar("XKRX", start="2016-01-01", end="2026-12-31")
        날 = [d.date() for d in cal.sessions]
        최장 = max((뒤 - 앞).days for 앞, 뒤 in zip(날, 날[1:], strict=False))

        assert 최장 == m.MAX_SESSION_GAP_DAYS, (
            f"국내 최장 휴장이 {최장}일인데 문턱이 {m.MAX_SESSION_GAP_DAYS}일이다 —"
            " 작으면 정상 휴장을 버리고, 크면 진짜 구멍을 놓친다"
        )

    def test_계산_판을_올렸다(self) -> None:
        """계산식이 바뀌었으니 과거 값과 섞이면 안 된다 (docs/infra.md 25.91)."""
        assert m.CALC_VERSION >= 2  # 25.701 에서 3


class Test연수는_한_식으로만_낸다:
    """`/ 365.25` 가 세 곳에 따로 적혀 있었다 (2026-09-22, docs/infra.md 25.109).

    `services/metrics` · `services/etf` · `services/accumulation` 이 각자 나눴다.
    "달력 연수냐 거래일이냐" 는 **판단**이고(거래일로 나누면 휴장이 많은 해에 부풀려진다),
    판단이 세 벌이면 한 곳만 바뀔 때 같은 종목의 상장 연수가 화면마다 달라진다.
    """

    def test_같은_답을_준다(self) -> None:
        from datetime import date

        from batch.services import etf as etf_svc
        from batch.services import metrics as m

        시작, 기준 = date(2020, 3, 1), date(2026, 9, 22)
        assert etf_svc.years_between("2020-03-01", 기준) == pytest.approx(m.years_between(시작, 기준))

    def test_식을_다시_적지_않는다(self) -> None:
        """**값이 같은지가 아니라 다시 적었는지를 본다.**

        글자가 아니라 **코드에 든 숫자**를 본다. 주석에 "`/ 365.25` 가 세 곳에 있었다" 고
        적어 둔 설명까지 물면 거짓 양성이고, 그러면 사람이 그물을 끈다(25.98).
        """
        import ast
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        다시적음 = []
        for 이름 in ("batch/services/etf.py", "batch/services/accumulation.py", "batch/services/etf_satellite.py"):
            나무 = ast.parse((뿌리 / 이름).read_text(encoding="utf-8"))
            if any(isinstance(n, ast.Constant) and n.value == 365.25 for n in ast.walk(나무)):
                다시적음.append(이름)
        assert not 다시적음, f"연수 나누는 식을 다시 적었다. 정의처는 services/metrics 다: {다시적음}"

    def test_읽을_수_없는_날짜는_None(self) -> None:
        """글자를 날짜로 바꾸는 일은 etf 쪽이 한다 — 그 몫은 남겨 둔다."""
        from datetime import date

        from batch.services import etf as etf_svc

        assert etf_svc.years_between("없는날짜", date(2026, 9, 22)) is None


class Test베타도_겹치는_날이_하한을_넘어야_한다:
    """종목 행 수만 하한을 봐서 짧은 지수로 낸 베타가 '1년 베타' 로 저장됐다 (docs/infra.md 25.227)."""

    @staticmethod
    def _계열(날수: int, 시작: date) -> list[m.PricePoint]:
        return [m.PricePoint(시작 + timedelta(days=i), 100 * (1 + 0.01 * math.sin(i))) for i in range(날수)]

    def test_미끼__겹침이_충분하면_베타가_나온다(self) -> None:
        종목 = self._계열(300, date(2025, 1, 1))
        assert m.compute("1Y", 종목, market=종목).beta == pytest.approx(1.0)

    def test_겹침이_하한_아래면_None(self) -> None:
        종목 = self._계열(300, date(2025, 1, 1))
        지수 = 종목[-70:]  # 지수는 70일치뿐
        결과 = m.compute("1Y", 종목, market=지수)
        assert 결과.beta is None
        assert 결과.cagr is not None  # 다른 지표는 종목 표본으로 그대로 낸다


def test_딱_하한만큼_겹치면_베타도_낸다() -> None:
    """200일 계열: CAGR·변동성은 나오는데 베타만 199 쌍이라 빠졌다 (docs/infra.md 25.303)."""
    s = series([100 * (1 + 0.01 * math.sin(i)) for i in range(200)])
    결과 = m.compute("1Y", s, market=s)
    assert 결과.data_points == 200 and 결과.volatility_ann is not None
    assert 결과.beta == pytest.approx(1.0)


def test_한_번도_안_빠졌으면_회복_기간은_0_이다() -> None:
    """None 은 '미회복' 이라 점수가 최악값으로 바꿔 넣는다 (docs/infra.md 25.310)."""
    결과 = m.max_drawdown(series([100, 101, 102, 103]))
    assert 결과.mdd == 0.0 and 결과.recovery_days == 0


class Test창을_채웠나:
    """행 수만 봐서 상장 2.3년 종목의 "3년" 값, 끝 6개월이 빈 계열의 "현재" 값이 났다 (docs/infra.md 25.703, 감사 재현)."""

    @staticmethod
    def _계열(시작: date, 개수: int) -> list[m.PricePoint]:
        return [m.PricePoint(date=시작 + timedelta(days=i), close=100.0 + (i % 7)) for i in range(개수)]

    def test_시작이_비면_값을_내지_않는다(self) -> None:
        끝 = date(2026, 9, 18)
        창시작 = 끝 - timedelta(days=1095)
        점 = self._계열(끝 - timedelta(days=700), 701)  # 행은 600 이상이지만 창 앞 1년여가 비었다
        r = m.compute("3Y", 점, span=(창시작, 끝))
        assert r.data_points == 701 and r.cagr is None and r.mdd is None
        assert m.compute("3Y", 점).cagr is not None  # span 없이(백테스트)는 예전 그대로

    def test_끝이_비면_값을_내지_않는다(self) -> None:
        끝 = date(2026, 9, 18)
        창시작 = 끝 - timedelta(days=1095)
        점 = self._계열(창시작, 900)  # 마지막 6개월 가까이 비었다
        assert m.compute("3Y", 점, span=(창시작, 끝)).cagr is None

    def test_다_채웠으면_낸다(self) -> None:
        끝 = date(2026, 9, 18)
        창시작 = 끝 - timedelta(days=1095)
        점 = self._계열(창시작 + timedelta(days=3), 1093)
        assert m.compute("3Y", 점, span=(창시작, 끝)).cagr is not None
