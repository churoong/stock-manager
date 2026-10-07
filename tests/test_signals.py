"""매수 신호와 사이징 테스트.

docs/signals.md 7장의 표를 그대로 옮겼다. 기대값은 손으로 계산할 수 있는
자료로 만들고 근거를 함께 적는다.

자동 매매 경로가 없다는 것, 상한을 넘는 배분이 없다는 것, 근거 문장이
받은 수치만 인용한다는 것이 여기서 고정된다. 네트워크를 타지 않는다.
"""

from __future__ import annotations

import pytest

from batch.services import signals as sg

# ----------------------------------------------------------------------
# 기초 계산
# ----------------------------------------------------------------------


class Test이동평균:
    def test_최근_N개의_평균(self) -> None:
        assert sg.moving_average([1.0, 2.0, 3.0, 4.0], 2) == pytest.approx(3.5)

    def test_개수가_모자라면_없다(self) -> None:
        """100일치로 낸 값을 60일 이동평균이라고 부르지 않는다."""
        assert sg.moving_average([1.0, 2.0], 3) is None

    def test_0일은_거부한다(self) -> None:
        with pytest.raises(ValueError):
            sg.moving_average([1.0], 0)


class Test분위수:
    def test_손으로_계산한_값(self) -> None:
        # 1..11 에서 20% 지점은 0.2 × 10 = 2 번째 자리, 곧 값 3
        values = [float(v) for v in range(1, 12)]
        assert sg.percentile(values, 20) == pytest.approx(3.0)
        assert sg.percentile(values, 50) == pytest.approx(6.0)

    def test_값이_하나면_그_값(self) -> None:
        assert sg.percentile([7.0], 30) == pytest.approx(7.0)

    def test_빈_목록은_없다(self) -> None:
        assert sg.percentile([], 50) is None

    def test_범위_밖은_거부한다(self) -> None:
        with pytest.raises(ValueError):
            sg.percentile([1.0], 150)

    def test_분위_순위(self) -> None:
        # 10 은 [1,5,10,20] 에서 아래가 2개이므로 50%
        assert sg.percentile_rank([1.0, 5.0, 10.0, 20.0], 10.0) == pytest.approx(50.0)


# ----------------------------------------------------------------------
# 밸류에이션 밴드 — 미래를 보지 않는다
# ----------------------------------------------------------------------


class Test시점기준재무:
    EQUITIES = [("2025-03-10", 1000.0), ("2026-03-10", 1200.0)]

    def test_발표된_것만_쓴다(self) -> None:
        assert sg.known_equity_at(self.EQUITIES, "2026-03-10") == 1200.0

    def test_발표_하루_전에는_알_수_없다(self) -> None:
        """2026-03-10 접수 보고서를 2026-03-09 계산에 쓰면 미래를 보는 것이다."""
        assert sg.known_equity_at(self.EQUITIES, "2026-03-09") == 1000.0

    def test_아무것도_발표되지_않았으면_없다(self) -> None:
        assert sg.known_equity_at(self.EQUITIES, "2024-01-01") is None

    def test_옛_연도_정정이_최신_연도를_덮지_않는다(self) -> None:
        """2026-05 에 정정된 FY2023 이 FY2025(2026-03 접수)를 덮었다 (docs/infra.md 25.549, 감사 재현)."""
        e = [("2024-03-15", 100.0, 2023), ("2025-03-14", 120.0, 2024), ("2026-03-12", 150.0, 2025),
             ("2026-05-20", 100.5, 2023)]  # fmt: skip
        assert sg.known_equity_at(sorted(e), "2026-06-01") == 150.0
        # 같은 연도의 정정은 쓴다
        e2 = [("2026-03-12", 150.0, 2025), ("2026-05-20", 149.0, 2025)]
        assert sg.known_equity_at(e2, "2026-06-01") == 149.0
        assert sg.known_equity_at(e2, "2026-04-01") == 150.0


class TestPBR계열:
    def test_시점에_맞는_자본으로_계산한다(self) -> None:
        prices = [("2026-03-09", 100.0), ("2026-03-10", 100.0)]
        equities = [("2025-03-10", 1000.0), ("2026-03-10", 2000.0)]
        # 상장주식수 100주 → BPS 가 10 에서 20 으로 바뀐다
        series = sg.pbr_series(prices, equities, 100)
        assert series == [pytest.approx(10.0), pytest.approx(5.0)]

    def test_상장주식수가_없으면_계열이_없다(self) -> None:
        assert sg.pbr_series([("2026-01-01", 100.0)], [("2025-01-01", 10.0)], None) == []

    def test_자본잠식_구간은_건너뛴다(self) -> None:
        series = sg.pbr_series(
            [("2026-01-01", 100.0)], [("2025-01-01", -500.0)], 100
        )
        assert series == []

    def test_표본이_모자라면_밴드를_만들지_않는다(self) -> None:
        """1년도 안 되는 계열로 3년 밴드를 말할 수 없다."""
        assert sg.build_band([1.0] * 100) is None

    def test_밴드를_만든다(self) -> None:
        band = sg.build_band([float(v) for v in range(1, 301)])
        assert band is not None
        assert band.sample == 300
        assert band.p20 < band.p30 < band.p50 < band.p80


# ----------------------------------------------------------------------
# 기간별 판정
# ----------------------------------------------------------------------


def rising(days: int = 80, start: float = 100.0, step: float = 1.0) -> list[float]:
    """꾸준히 오르는 가격 계열. 정배열이 만들어진다."""
    return [start + step * i for i in range(days)]


def short_input(**kw) -> sg.SignalInput:
    base = {
        "stock_id": 1,
        "ticker": "005930",
        "name": "테스트전자",
        "market": "KOSPI",
        "closes": rising(),
        # 최근 20일 거래대금이 그 앞보다 크다
        "turnovers": [100.0] * 60 + [200.0] * 20,
        "factor_scores": {"momentum": 75.0},
    }
    base.update(kw)
    return sg.SignalInput(**base)


class Test단기신호:
    def test_조건을_모두_만족하면_신호가_난다(self) -> None:
        passed, data = sg.short_term(short_input())
        assert passed
        assert data["ma20"] > data["ma60"]

    def test_거래일이_모자라면_판단하지_않는다(self) -> None:
        passed, _ = sg.short_term(short_input(closes=rising(30)))
        assert not passed

    def test_모멘텀_점수가_낮으면_신호가_없다(self) -> None:
        """팩터와 어긋나는 신호를 내지 않는다."""
        passed, _ = sg.short_term(short_input(factor_scores={"momentum": 40.0}))
        assert not passed

    def test_수급이_늘지_않으면_신호가_없다(self) -> None:
        passed, _ = sg.short_term(short_input(turnovers=[100.0] * 80))
        assert not passed

    def test_과열이면_신호가_없다(self) -> None:
        """20일선에서 10% 넘게 뜨면 되돌림 위험이 크다."""
        closes = rising()
        closes[-1] = closes[-1] * 1.5
        passed, data = sg.short_term(short_input(closes=closes))
        assert not passed
        assert data["extension"] > sg.MAX_EXTENSION

    def test_역배열이면_신호가_없다(self) -> None:
        passed, _ = sg.short_term(short_input(closes=rising(step=-1.0)))
        assert not passed


def mid_input(**kw) -> sg.SignalInput:
    base = {
        "stock_id": 1,
        "ticker": "005930",
        "name": "테스트전자",
        "market": "KOSPI",
        "closes": [100.0],
        "factor_scores": {"growth": 81.0, "quality": 64.0},
        "revenue_growth": 0.182,
        "operating_income_growth": 0.241,
        "fiscal_year": 2025,
    }
    base.update(kw)
    return sg.SignalInput(**base)


class Test중기신호:
    def test_매출과_이익이_모두_늘면_신호가_난다(self) -> None:
        passed, _ = sg.mid_term(mid_input())
        assert passed

    def test_이익이_줄면_신호가_없다(self) -> None:
        """이익이 늘지 않는 매출 증가는 신호가 아니다."""
        passed, _ = sg.mid_term(mid_input(operating_income_growth=-0.05))
        assert not passed

    def test_성장_점수가_낮으면_신호가_없다(self) -> None:
        passed, _ = sg.mid_term(mid_input(factor_scores={"growth": 50.0, "quality": 64.0}))
        assert not passed

    def test_성장률이_없으면_신호가_없다(self) -> None:
        passed, _ = sg.mid_term(mid_input(revenue_growth=None))
        assert not passed


def long_input(**kw) -> sg.SignalInput:
    base = {
        "stock_id": 1,
        "ticker": "005930",
        "name": "테스트전자",
        "market": "KOSPI",
        "closes": [100.0],
        "factor_scores": {"quality": 71.0, "value": 83.0},
        "pbr_now": 0.74,
        "band": sg.Band(p20=0.68, p30=0.80, p50=1.10, p80=1.60, sample=750),
    }
    base.update(kw)
    return sg.SignalInput(**base)


class Test장기신호:
    def test_밴드_하단이면_신호가_난다(self) -> None:
        passed, data = sg.long_term(long_input())
        assert passed
        assert data["band_p30"] == 0.80

    def test_밴드_중간이면_신호가_없다(self) -> None:
        passed, _ = sg.long_term(long_input(pbr_now=1.20))
        assert not passed

    def test_밴드가_없으면_신호가_없다(self) -> None:
        """표본이 모자라 밴드를 못 만든 경우다."""
        passed, _ = sg.long_term(long_input(band=None))
        assert not passed

    def test_퀄리티가_낮으면_싸도_신호가_없다(self) -> None:
        passed, _ = sg.long_term(
            long_input(factor_scores={"quality": 40.0, "value": 83.0})
        )
        assert not passed


# ----------------------------------------------------------------------
# 매수 구간과 분할
# ----------------------------------------------------------------------


class Test매수구간:
    def test_중기는_현재가_아래_5퍼센트(self) -> None:
        inp = mid_input(closes=[100.0])
        low, high = sg.buy_zone("mid", inp, {})
        assert high == pytest.approx(100.0)
        assert low == pytest.approx(95.0)

    def test_단기는_20일선_부근(self) -> None:
        inp = short_input()
        _, data = sg.short_term(inp)
        low, high = sg.buy_zone("short", inp, data)
        assert low == pytest.approx(data["ma20"] * 0.98)
        assert high <= inp.close

    def test_장기는_밴드를_가격으로_되돌린다(self) -> None:
        inp = long_input(closes=[100.0])
        _, data = sg.long_term(inp)
        low, high = sg.buy_zone("long", inp, data)
        # BPS = 100 / 0.74. 밴드 20% 분위 0.68배 가격
        bps = 100.0 / 0.74
        assert low == pytest.approx(0.68 * bps)
        assert high == pytest.approx(100.0)  # 현재가가 밴드 30% 가격보다 싸다

    def test_장기는_PBR을_낸_종가로_되돌린다(self) -> None:
        """수정종가 107.84 로 되돌려 구간이 3% 낮았다 — PBR 은 111.18 로 냈다 (docs/infra.md 25.562, 25.521 의 값)."""
        band = sg.Band(p20=1.00, p30=1.12, p50=1.3, p80=1.6, sample=750)
        inp = long_input(closes=[107.84], pbr_now=1.1118, band=band, band_close=111.18)
        _, data = sg.long_term(inp)
        low, high = sg.buy_zone("long", inp, data)
        assert low == pytest.approx(100.0) and high == pytest.approx(111.18)
        import inspect

        from batch.jobs import signals as sg_job

        assert "band_close=band_close" in inspect.getsource(sg_job)  # 배치가 그 종가를 넘긴다

    def test_장기_기준_종가도_그_종가다(self) -> None:
        """구간은 원 종가 단위인데 ref_close 가 수정종가라 장중 감시·성적표가 비율을 한 번 더 곱했다 (25.565, 교차검증)."""
        band = sg.Band(p20=1.00, p30=1.12, p50=1.3, p80=1.6, sample=750)
        inp = long_input(closes=[107.84], pbr_now=1.1118, band=band, band_close=111.18)
        장기 = [x for x in sg.evaluate(inp, total_investable=10_000_000, min_order_amount=0) if x.horizon == "long"]
        assert 장기 and 장기[0].rationale_data["ref_close"] == pytest.approx(111.18)

    def test_뒤집힌_구간은_버린다(self) -> None:
        """상단이 하단보다 낮은 구간을 보여주지 않는다."""
        # 밴드 손절 분위 가격이 현재가보다 훨씬 높다 (하단 > 상단)
        inp = long_input(closes=[100.0], pbr_now=0.74,
                         band=sg.Band(p20=2.0, p30=3.0, p50=4.0, p80=5.0, sample=750))
        _, data = sg.long_term(inp)
        assert sg.buy_zone("long", inp, data) is None

    def test_진입_손절_분위를_상수에서_고른다(self) -> None:
        """**상수를 바꾸면 구간이 따라 움직여야 한다** (docs/infra.md 25.108).

        예전에는 `data["band_p30"]`·`data["band_p20"]` 을 글자로 집었다. 상수를 20 으로
        바꿔도 구간은 그대로 30% 분위였다 — 화면 글만 바뀌고 동작은 안 바뀐다.
        """
        band = sg.Band(p20=0.50, p30=0.80, p50=1.10, p80=1.60, sample=750)
        inp = long_input(closes=[100.0], pbr_now=0.74, band=band)
        _, data = sg.long_term(inp)
        bps = 100.0 / 0.74

        low, high = sg.buy_zone("long", inp, data)
        assert low == pytest.approx(band.at(sg.BAND_STOP_PERCENTILE) * bps)
        assert high == pytest.approx(min(100.0, band.at(sg.BAND_ENTRY_PERCENTILE) * bps))

    def test_없는_분위를_물으면_막는다(self) -> None:
        """지어내면 문턱을 바꾼 줄 알았는데 조용히 예전 값으로 돈다."""
        band = sg.Band(p20=0.5, p30=0.8, p50=1.1, p80=1.6, sample=750)
        assert band.at(30) == 0.8
        with pytest.raises(ValueError) as exc:
            band.at(25)
        assert "25" in str(exc.value)


class Test분할계획:
    def test_세_번에_나눈다(self) -> None:
        plan = sg.tranche_plan(90.0, 100.0)
        assert [t.step for t in plan] == [1, 2, 3]
        assert [t.price for t in plan] == [100.0, 95.0, 90.0]

    def test_비중의_합은_정확히_1이다(self) -> None:
        plan = sg.tranche_plan(90.0, 100.0)
        assert sum(t.ratio for t in plan) == pytest.approx(1.0)

    def test_첫_진입이_가장_크다(self) -> None:
        """안 떨어지면 못 사는 계획을 만들지 않는다."""
        plan = sg.tranche_plan(90.0, 100.0)
        assert plan[0].ratio >= plan[1].ratio
        assert plan[0].ratio >= plan[2].ratio


class Test목표와손절:
    def test_구간_중앙이_기준이다(self) -> None:
        """현재가로 잡으면 실제로 살 값과 어긋난다."""
        target, stop = sg.target_and_stop("short", 90.0, 100.0)
        # 중앙 95, 단기 +10% / -7%
        assert target == pytest.approx(104.5)
        assert stop == pytest.approx(88.35)

    def test_기간마다_다르다(self) -> None:
        long_target, long_stop = sg.target_and_stop("long", 90.0, 100.0)
        assert long_target == pytest.approx(142.5)  # 95 × 1.5
        assert long_stop == pytest.approx(71.25)  # 95 × 0.75

    def test_설정값이_우선한다(self) -> None:
        targets = {"short": {"target_pct": 20.0, "stop_pct": -10.0}}
        target, _ = sg.target_and_stop("short", 90.0, 100.0, targets)
        assert target == pytest.approx(114.0)


# ----------------------------------------------------------------------
# 사이징 — 상한을 넘지 않는다
# ----------------------------------------------------------------------


class Test비중축소:
    def test_변동성이_기준의_두_배면_절반으로_준다(self) -> None:
        # 기준 25%, 종목 50% → 배수 0.5
        reduction, data = sg.size_reduction(0.50, None)
        assert reduction == pytest.approx(0.5)
        assert data["volatility_factor"] == pytest.approx(0.5)

    def test_기준보다_안정적이어도_키우지_않는다(self) -> None:
        reduction, _ = sg.size_reduction(0.10, None)
        assert reduction == pytest.approx(1.0)

    def test_하한에서_멈춘다(self) -> None:
        """아무리 위험해도 0 으로 수렴시키지 않는다."""
        reduction, _ = sg.size_reduction(5.0, None)
        assert reduction == pytest.approx(sg.REDUCTION_FLOOR)

    def test_둘_중_더_줄이는_쪽을_쓴다(self) -> None:
        # 변동성 배수 1.0, MDD 배수 0.5 → 0.5
        reduction, _ = sg.size_reduction(0.20, -0.70)
        assert reduction == pytest.approx(0.5)

    def test_지표가_없으면_줄이지_않는다(self) -> None:
        """모르는 것을 위험하다고 단정하지 않는다."""
        reduction, data = sg.size_reduction(None, None)
        assert reduction == pytest.approx(1.0)
        assert "note" in data


class Test비중:
    def test_상한에서_시작해_줄어든다(self) -> None:
        weight, reduction, _ = sg.size_weight(10.0, 0.50, None)
        assert weight == pytest.approx(5.0)
        assert reduction == pytest.approx(0.5)

    def test_어떤_경우에도_상한을_넘지_않는다(self) -> None:
        for vol in (None, 0.01, 0.25, 3.0):
            for mdd in (None, -0.01, -0.9):
                weight, _, _ = sg.size_weight(10.0, vol, mdd)
                assert weight <= 10.0 + 1e-9

    def test_음수_상한은_거부한다(self) -> None:
        with pytest.raises(ValueError):
            sg.size_weight(-1.0, None, None)


# ----------------------------------------------------------------------
# 근거 문장 — 받은 수치만 인용한다
# ----------------------------------------------------------------------


class Test추세필터비중:
    """docs/signals.md 3.5. 국면 배수는 종목 위험 축소와 곱하고 근거표에 남는다."""

    BEAR = {
        "regime": {"index_code": "KOSPI", "state": "bear", "date": "2026-09-15", "close": 2500.0, "sma": 2600.0, "source": "yfinance ^KS11"},
        "regime_factor": 0.5,
        "bear_factor": 0.5,
    }

    def test_약세면_비중이_배수만큼_준다(self) -> None:
        weight, reduction, data = sg.size_weight(10.0, None, None, 0.5, self.BEAR)
        assert weight == pytest.approx(5.0) and reduction == pytest.approx(0.5)
        assert data["regime"]["state"] == "bear"

    def test_종목_위험_축소와_곱한다(self) -> None:
        # 변동성 50% → ×0.5, 약세 ×0.5 → 합쳐 ×0.25
        weight, reduction, _ = sg.size_weight(10.0, 0.5, None, 0.5, self.BEAR)
        assert weight == pytest.approx(2.5) and reduction == pytest.approx(0.25)

    def test_배수_0은_금액_없음이지만_신호는_남는다(self) -> None:
        weight, _, _ = sg.size_weight(10.0, None, None, 0.0, {**self.BEAR, "regime_factor": 0.0})
        assert weight == 0.0

    def test_배수는_0에서_1_사이다(self) -> None:
        with pytest.raises(ValueError):
            sg.size_weight(10.0, None, None, 1.5)

    def test_근거표에_지수와_200일선이_있다(self) -> None:
        rows = sg.regime_criteria(self.BEAR)
        assert len(rows) == 1 and rows[0]["label"] == "시장 국면"
        assert "KOSPI 2,500 < 200일선 2,600" in rows[0]["display"]
        assert "×0.50" in rows[0]["display"]
        assert rows[0]["source"] == "index_prices (yfinance ^KS11)"
        assert rows[0]["as_of"] == "2026-09-15"

    def test_모르면_미판정_행(self) -> None:
        rows = sg.regime_criteria({"regime": None, "regime_factor": 1.0, "regime_note": "국면을 몰라 줄이지 않았습니다"})
        assert rows[0]["label"] == "시장 국면 미판정"

    def test_꺼져_있으면_행이_없다(self) -> None:
        assert sg.regime_criteria({"volatility_factor": 0.8}) == []


class Test근거문장:
    def test_단기_문장에_실제_수치가_들어간다(self) -> None:
        inp = short_input()
        _, data = sg.short_term(inp)
        text = sg.rationale("short", inp, data)

        assert "20일선" in text
        assert "정배열" in text
        assert "모멘텀 75점" in text

    def test_중기_문장(self) -> None:
        inp = mid_input()
        _, data = sg.mid_term(inp)
        text = sg.rationale("mid", inp, data)

        assert "매출 18.2%" in text
        assert "영업이익 24.1%" in text
        assert "2025 사업보고서" in text

    def test_없는_수치는_문장에서_빠진다(self) -> None:
        """양호함 같은 말로 채우지 않는다."""
        inp = mid_input(revenue_growth=None, fiscal_year=None)
        _, data = sg.mid_term(inp)
        text = sg.rationale("mid", inp, data)

        assert "매출" not in text
        assert "사업보고서" not in text
        assert "영업이익" in text  # 있는 것은 그대로 들어간다

    def test_아무_수치도_없으면_빈_문장이다(self) -> None:
        inp = sg.SignalInput(stock_id=1, ticker="A", name="A", market="KOSPI")
        assert sg.rationale("mid", inp, {}) == ""


# ----------------------------------------------------------------------
# 전체 판정
# ----------------------------------------------------------------------


class Test전체:
    def test_조건에_맞는_기간만_돌려준다(self) -> None:
        signals = sg.evaluate(short_input(), total_investable=10_000_000)
        assert [s.horizon for s in signals] == ["short"]

    def test_금액과_분할이_함께_나온다(self) -> None:
        signals = sg.evaluate(short_input(), total_investable=10_000_000)
        signal = signals[0]

        assert signal.weight_pct == pytest.approx(10.0)  # 지표가 없어 축소 없음
        assert signal.suggested_amount == pytest.approx(1_000_000)
        assert sum(t.amount for t in signal.tranches) == pytest.approx(1_000_000)

    def test_변동성이_크면_금액이_준다(self) -> None:
        signals = sg.evaluate(
            short_input(volatility_ann=0.50), total_investable=10_000_000
        )
        assert signals[0].suggested_amount == pytest.approx(500_000)

    def test_투자_여력이_모자라면_금액을_비운다(self) -> None:
        signals = sg.evaluate(
            short_input(), total_investable=100_000, min_order_amount=100_000
        )
        # 10% 면 1만원이라 최소 단위에 못 미친다
        assert signals[0].suggested_amount is None
        # 근거표도 같은 말을 한다 (docs/infra.md 25.291)
        금액 = [r for r in signals[0].rationale_data["criteria"] if r["label"] == "권장 금액 (참고)"][0]
        assert "최소 주문 금액 미만" in 금액["display"]
        # 분할 칩에도 금액이 없어야 한다 — 카드가 "권장 금액 없음" 인데 칩이 "12,000원" 이었다 (docs/infra.md 25.380)
        assert all(t.amount is None for t in signals[0].tranches)

    def test_약세장_배수_0_에_최소_주문_0_이어도_0원을_금액으로_두지_않는다(self) -> None:
        """2부가 "0원 (0.0%)" 을 배분으로 싣던 것 (docs/infra.md 25.409)."""
        signals = sg.evaluate(short_input(), total_investable=10_000_000, min_order_amount=0, regime_factor=0.0)
        assert signals and signals[0].suggested_amount is None
        assert all(t.amount is None for t in signals[0].tranches)

    def test_업종이_없으면_섹터_상한_미적용을_밝힌다(self) -> None:
        """조용히 넘어가지 않는다."""
        signal = sg.evaluate(short_input())[0]
        assert not signal.sector_cap_applied
        assert signal.sector_cap_note == sg.SECTOR_CAP_UNAVAILABLE

    def test_업종이_있으면_적용_표시가_된다(self) -> None:
        signal = sg.evaluate(short_input(sector="반도체"))[0]
        assert signal.sector_cap_applied
        assert signal.sector_cap_note is None

    def test_근거_수치를_함께_저장한다(self) -> None:
        """나중에 문장을 검증할 수 있어야 한다."""
        signal = sg.evaluate(short_input())[0]
        assert signal.rationale_data["ma20"] is not None
        assert signal.rationale_data["momentum"] == 75.0

    def test_신호가_없으면_빈_목록이다(self) -> None:
        inp = sg.SignalInput(stock_id=1, ticker="A", name="A", market="KOSPI")
        assert sg.evaluate(inp) == []

    def test_자동_매도나_주문_경로가_없다(self) -> None:
        """자동 매매는 절대 없다. 여기서 나오는 것은 표시할 정보뿐이다."""
        signal = sg.evaluate(short_input())[0]
        for banned in ("order", "execute", "submit", "sell"):
            assert not hasattr(signal, banned)


# ----------------------------------------------------------------------
# 근거표 — 사용자가 확인할 수 있어야 한다 (2026-09-16 추가)
# ----------------------------------------------------------------------


class Test근거표:
    """모든 추천은 왜인지 분명해야 하고 사용자가 확인할 수 있어야 한다.

    근거표의 한 행은 "이 기준을 이 값이 이렇게 통과했다" 다. 값·문턱·출처·
    기준일이 전부 있어야 확인이라 부를 수 있다.
    """

    def test_단기_신호의_근거표에_다섯_기준이_있다(self) -> None:
        signal = sg.evaluate(
            short_input(price_date="2026-09-15", score_date="2026-09-16")
        )[0]
        rows = signal.rationale_data["criteria"]
        labels = [r["label"] for r in rows]

        for label in ("정배열", "추세 위", "과열 아님", "수급 유입", "모멘텀 점수"):
            assert label in labels, label
        # 카드의 목표가·손절가·권장 금액도 근거가 있다 (docs/infra.md 25.263)
        assert "목표·손절 (참고)" in labels and "권장 금액 (참고)" in labels

    def test_모든_행에_값_문턱_출처가_있다(self) -> None:
        """"-" 로 채운 표는 확인이 아니라 장식이다."""
        signal = sg.evaluate(short_input(price_date="2026-09-15"))[0]
        rows = signal.rationale_data["criteria"]

        # **빈 목록이면 아래 반복이 0번 돌고 공짜로 통과한다.** 근거표가 통째로 비는 것이
        # 바로 이 규칙이 막으려는 사고다 (25.34). 먼저 있는지부터 본다
        assert rows, "근거표가 비었다"
        for row in rows:
            assert row["display"], row
            assert row["threshold"], row
            assert row["source"], row
            assert "as_of" in row

    def test_기준일이_출처에_맞게_붙는다(self) -> None:
        signal = sg.evaluate(
            short_input(price_date="2026-09-15", score_date="2026-09-16")
        )[0]
        by_label = {r["label"]: r for r in signal.rationale_data["criteria"]}

        # 시세에서 온 값은 시세 날짜, 점수에서 온 값은 점수 계산일
        assert by_label["정배열"]["as_of"] == "2026-09-15"
        assert by_label["모멘텀 점수"]["as_of"] == "2026-09-16"

    def test_문턱값이_규칙_상수와_같다(self) -> None:
        """웹에 복사한 문턱이 아니라 이 파일의 상수에서 나와야 한다."""
        signal = sg.evaluate(short_input())[0]
        by_label = {r["label"]: r for r in signal.rationale_data["criteria"]}

        assert f"{sg.MIN_MOMENTUM_SCORE:.0f}" in by_label["모멘텀 점수"]["threshold"]
        assert f"{sg.TURNOVER_MULTIPLE}" in by_label["수급 유입"]["threshold"]

    def test_중기는_재무_기준일이_사업보고서다(self) -> None:
        signal = sg.evaluate(mid_input(score_date="2026-09-16"))[0]
        by_label = {r["label"]: r for r in signal.rationale_data["criteria"]}

        assert by_label["매출 증가"]["as_of"] == "2025 사업보고서"
        assert by_label["매출 증가"]["source"] == sg.SOURCE_FINANCIALS
        assert by_label["성장 점수"]["source"] == sg.SOURCE_SCORES

    def test_장기는_밴드_표본_수를_같이_보여준다(self) -> None:
        signal = sg.evaluate(long_input(price_date="2026-09-15"))[0]
        by_label = {r["label"]: r for r in signal.rationale_data["criteria"]}

        row = by_label["밸류에이션 밴드 하단"]
        assert "PBR 0.74배" in row["display"]
        assert "표본 750일" in row["display"]
        assert row["source"] == sg.SOURCE_DERIVED

    def test_비중을_줄였으면_얼마나_줄였는지_적는다(self) -> None:
        """왜 금액이 작은가에 답하지 않으면 추천 자체를 의심하게 된다."""
        signal = sg.evaluate(
            short_input(volatility_ann=0.50, metrics_date="2026-09-14")
        )[0]
        by_label = {r["label"]: r for r in signal.rationale_data["criteria"]}

        assert "×0.50" in by_label["변동성 축소"]["display"]
        assert by_label["변동성 축소"]["as_of"] == "2026-09-14"
        assert by_label["변동성 축소"]["source"] == sg.SOURCE_METRICS

    def test_줄이지_않았으면_그_사실을_적는다(self) -> None:
        """모르는 것을 위험하다고 단정하지 않았다는 것도 확인할 수 있어야 한다."""
        signal = sg.evaluate(short_input())[0]
        labels = [r["label"] for r in signal.rationale_data["criteria"]]
        assert "비중 축소 없음" in labels

    def test_근거표는_JSON으로_저장된다(self) -> None:
        import json

        from batch.jobs import signals as job

        row = job.to_row(sg.evaluate(short_input())[0], "2026-09-16", "지금")
        data = json.loads(row[16])
        assert isinstance(data["criteria"], list)
        assert data["criteria"][0]["label"]



def test_장기_문턱은_55점_경계() -> None:
    """2026-09-17 백테스트로 60 → 55 (docs/backtest.md 7.1)."""
    assert sg.long_term(long_input(factor_scores={"quality": 55.0, "value": 55.0}))[0]
    assert not sg.long_term(long_input(factor_scores={"quality": 54.9, "value": 83.0}))[0]
    assert not sg.long_term(long_input(factor_scores={"quality": 83.0, "value": 54.9}))[0]


def test_미국_근거는_10K_로_적는다() -> None:
    """미국 신호 문장에 국내 용어(사업보고서)가 나오던 것 (2026-09-17 첫 미국 신호)."""
    from batch.services import signals as sg_mod

    kr = sg_mod.SignalInput(stock_id=1, ticker="A", name="A", market="KOSPI", closes=[1.0], fiscal_year=2025)
    us = sg_mod.SignalInput(
        stock_id=2, ticker="B", name="B", market="NASDAQ", closes=[1.0], fiscal_year=2025, currency="USD"
    )
    assert sg_mod._annual_report(kr) == "사업보고서"
    assert sg_mod._annual_report(us) == "10-K"


# ----------------------------------------------------------------------
# 판정표 — "왜 이 종목은 없나" (docs/signals.md 9장)
# ----------------------------------------------------------------------


class Test판정표:
    def test_전부_통과이면_신호이고_그_역도_같다(self) -> None:
        for horizon, inp in (("short", short_input()), ("mid", mid_input()), ("long", long_input())):
            passed, data = sg.RULES[horizon](inp)
            table = sg.judgement(horizon, inp, data)
            assert passed and all(r["passed"] is True for r in table), horizon

    def test_어긋난_기준만_탈락으로_찍힌다(self) -> None:
        # 단기: 모멘텀 점수 부족
        inp = short_input(factor_scores={"momentum": 40.0})
        passed, data = sg.short_term(inp)
        table = sg.judgement("short", inp, data)
        assert not passed
        assert [r["label"] for r in table if r["passed"] is False] == ["모멘텀 점수"]
        # 중기: 영업이익 감소
        inp = mid_input(operating_income_growth=-0.1)
        passed, data = sg.mid_term(inp)
        assert [r["label"] for r in sg.judgement("mid", inp, data) if r["passed"] is False] == ["영업이익 증가"]
        # 장기: 밴드 위
        inp = long_input(pbr_now=1.5)
        passed, data = sg.long_term(inp)
        assert [r["label"] for r in sg.judgement("long", inp, data) if r["passed"] is False] == ["밸류에이션 밴드 하단"]

    def test_값이_없으면_값_없음으로_탈락(self) -> None:
        inp = mid_input(revenue_growth=None, factor_scores={"growth": 81.0})
        passed, data = sg.mid_term(inp)
        table = sg.judgement("mid", inp, data)
        assert not passed
        failed = {r["label"]: r["display"] for r in table if r["passed"] is False}
        assert failed == {"퀄리티 점수": "값 없음", "매출 증가": "값 없음"}

    def test_표본이_모자라면_한_줄로_말한다(self) -> None:
        inp = short_input(closes=[100.0] * 30, turnovers=[1.0] * 30)
        passed, data = sg.short_term(inp)
        table = sg.judgement("short", inp, data)
        assert not passed and len(table) == 1 and table[0]["label"] == "표본" and "30일" in table[0]["display"]

    def test_밴드가_없으면_그렇다고_적는다(self) -> None:
        inp = long_input(band=None)
        passed, data = sg.long_term(inp)
        table = sg.judgement("long", inp, data)
        assert table[0]["label"] == "밸류에이션 밴드" and table[0]["passed"] is False

    def test_세_기간을_한_번에(self) -> None:
        out = sg.judgements(short_input())
        assert set(out) == {"short", "mid", "long"}
        assert out["short"][0] is True and out["mid"][0] is False


def test_목표가_손절가_권장금액에_근거_행이_있다() -> None:
    """docs/infra.md 25.263 — 카드가 보여 주는 세 숫자의 출처가 근거표에 없었다."""
    from batch.services import signals as sg_

    inp = sg_.SignalInput(stock_id=1, ticker="A", name="가", market="KOSPI", currency="KRW", price_date="2026-09-25")
    행 = sg_.plan_criteria(inp, "short", 9000.0, 11000.0, 11000.0, 9300.0, None, 1_000_000.0, 10_000_000.0, 10.0)
    이름 = [r["label"] for r in 행]
    assert "목표·손절 (참고)" in 이름 and "권장 금액 (참고)" in 이름
    목표 = 행[0]
    assert "+10%" in 목표["display"] and "-7%" in 목표["display"] and "기본값" in 목표["source"]
    assert "총 투자가능금액 10,000,000 × 비중 10.00%" in 행[1]["threshold"]
    assert all(r["as_of"] == "2026-09-25" for r in 행)
    # 통화가 붙는다 — 국내는 원 단위 정수 (docs/infra.md 25.385)
    assert "목표 11,000원" in 목표["display"] and "중앙 10,000원" in 목표["threshold"]
    미국 = sg_.SignalInput(stock_id=2, ticker="B", name="나", market="NASDAQ", currency="USD", price_date="2026-09-25")
    assert "목표 110.00 USD" in sg_.plan_criteria(미국, "short", 90.0, 110.0, 110.0, 93.0, None, None, 0.0, 10.0)[0]["display"]


def test_매수_구간과_분할_가격에_근거_행이_있다() -> None:
    """돈에 닿는 구간·분할 가격의 식과 종가가 근거표에 없었다 (docs/infra.md 25.562, 감사 재현)."""
    from batch.services import signals as sg_

    inp = sg_.SignalInput(stock_id=1, ticker="A", name="가", market="KOSPI", currency="KRW",
                          closes=[70_543.0], price_date="2026-09-25")  # fmt: skip
    행 = sg_.plan_criteria(inp, "mid", 67_016.0, 70_543.0, 80_000.0, 60_000.0, None, 1_000_000.0, 10_000_000.0, 10.0)
    구간 = [r for r in 행 if r["label"] == "매수 구간·분할 (참고)"][0]
    assert "67,016원 ~ 70,543원" in 구간["display"] and "68,800원" in 구간["display"]  # 분할 가운데는 호가 (25.661)
    assert "종가 70,543원 × 0.95" in 구간["threshold"]
    # 미국 권장 금액은 통화와 환산을 밝힌다 — 설정은 원화다
    미국 = sg_.SignalInput(stock_id=2, ticker="B", name="나", market="NASDAQ", currency="USD", closes=[100.0])
    금액 = [r for r in sg_.plan_criteria(미국, "mid", 95.0, 100.0, 125.0, 85.0, None, 3_500.0, 35_000.0, 10.0)
          if r["label"] == "권장 금액 (참고)"][0]  # fmt: skip
    assert "35,000.00 USD (설정 원화 총액 ÷ USDKRW)" in 금액["threshold"]
    assert 금액["display"] == "3,500.00 USD"  # 카드($)와 같은 자릿수 (25.567)
    # 최소 주문 미만 갈래도 같은 문구 (25.565, 교차검증)
    작음 = [r for r in sg_.plan_criteria(미국, "mid", 95.0, 100.0, 125.0, 85.0, None, 50.0, 35_000.0, 0.1, 100.0)
          if r["label"] == "권장 금액 (참고)"][0]  # fmt: skip
    assert "USD (설정 원화 총액 ÷ USDKRW)" in 작음["threshold"]
    경계 = [r for r in sg_.plan_criteria(미국, "mid", 95.0, 100.0, 125.0, 85.0, None, 49.6, 35_000.0, 0.1, 50.0)
          if r["label"] == "권장 금액 (참고)"][0]  # fmt: skip
    assert "(49.60 < 50.00 USD)" in 경계["display"]  # "(50 < 50 USD)" 였다 (25.568)


def test_손절선이_3차_매수가_위면_말한다() -> None:
    """손절을 −2% 로 좁히면 손절선이 3차 가격보다 높다 (docs/infra.md 25.563, 감사 재현)."""
    from batch.services import signals as sg_

    inp = sg_.SignalInput(stock_id=1, ticker="A", name="가", market="KOSPI", currency="KRW", closes=[100.0])
    좁음 = sg_.plan_criteria(inp, "mid", 95.0, 100.0, 120.0, 95.55, None, 1.0, 10.0, 10.0)
    assert any(r["label"] == "손절선 위치 (주의)" for r in 좁음)
    보통 = sg_.plan_criteria(inp, "mid", 95.0, 100.0, 120.0, 83.0, None, 1.0, 10.0, 10.0)
    assert not any(r["label"] == "손절선 위치 (주의)" for r in 보통)


def test_최소_주문_미만이면_근거표도_금액을_내지_않았다고_적는다() -> None:
    """카드는 '권장 금액 없음' 인데 근거표가 걸러지기 전 30,000 을 적었다 (docs/infra.md 25.291)."""
    from batch.services import signals as sg_

    inp = sg_.SignalInput(stock_id=1, ticker="A", name="가", market="KOSPI", currency="KRW", price_date="2026-09-25")
    행 = sg_.plan_criteria(inp, "short", 9000.0, 11000.0, 11000.0, 9300.0, None, 30_000.0, 1_000_000.0, 3.0, 100_000.0)
    금액 = [r for r in 행 if r["label"] == "권장 금액 (참고)"][0]
    assert "최소 주문 금액 미만" in 금액["display"] and "30,000 < 100,000" in 금액["display"]


def test_비중_0이면_근거표에_0원을_적지_않는다() -> None:
    """약세 배수 0 + 최소 주문 0 에서 카드는 금액이 비었는데 근거표는 "0 KRW" 였다 (docs/infra.md 25.522, 감사)."""
    from batch.services import signals as sg_

    inp = sg_.SignalInput(stock_id=1, ticker="A", name="가", market="KOSPI", currency="KRW", price_date="2026-09-25")
    행 = sg_.plan_criteria(inp, "short", 9000.0, 11000.0, 11000.0, 9300.0, None, 0.0, 1_000_000.0, 0.0, 0.0)
    금액 = [r for r in 행 if r["label"] == "권장 금액 (참고)"][0]
    assert "0 KRW" not in 금액["display"] and "비중이 0" in 금액["display"]


def test_목표_출처는_그_기간에_설정을_썼을_때만_설정() -> None:
    """중기가 범위 밖이라 기본값으로 계산했는데 출처가 "설정" 이었다 (docs/infra.md 25.522, 감사)."""
    from batch.services import signals as sg_

    inp = sg_.SignalInput(stock_id=1, ticker="A", name="가", market="KOSPI", currency="KRW", price_date="2026-09-25")
    설정 = {"short": {"target_pct": 12.0, "stop_pct": -6.0}}
    중기 = sg_.plan_criteria(inp, "mid", 9000.0, 11000.0, 12500.0, 8500.0, 설정, 1.0, 10.0, 1.0)[0]
    단기 = sg_.plan_criteria(inp, "short", 9000.0, 11000.0, 11200.0, 9400.0, 설정, 1.0, 10.0, 1.0)[0]
    assert "기본값" in 중기["source"] and "horizon_targets" in 단기["source"]


def test_미국인데_환율이_없으면_근거표가_환율을_원인으로_댄다() -> None:
    """총액은 설정돼 있는데 환율이 없어 0 이 된 미국 — '설정에서 넣으라' 고 보내지 않는다 (docs/infra.md 25.295)."""
    없음 = sg.evaluate(short_input(currency="USD", market="NASDAQ"), total_investable=0.0, fx=None)[0]
    금액 = [r for r in 없음.rationale_data["criteria"] if r["label"] == "권장 금액 (참고)"][0]
    assert "환율이 없어" in 금액["display"]
    # 국내는 예전 그대로 '총 투자가능금액이 없어'
    국내 = sg.evaluate(short_input(), total_investable=0.0)[0]
    금액 = [r for r in 국내.rationale_data["criteria"] if r["label"] == "권장 금액 (참고)"][0]
    assert "총 투자가능금액이 없어" in 금액["display"]


def test_장기_근거는_분위_네_점으로_셀_수_없는_백분위를_말하지_않는다() -> None:
    """예전에는 20~30% 분위 사이를 '밴드의 25% 분위' 라 적었다 — 네 점 사이에서만 세서다 (docs/infra.md 25.302).

    20% 분위 아래는 구간이 뒤집혀(현재가 < 손절 가격) 신호 자체가 나오지 않는다.
    """
    글 = sg.evaluate(long_input())[0].rationale_text
    assert "20~30% 분위 사이" in 글 and "25% 분위" not in 글
    assert sg.evaluate(long_input(pbr_now=0.60)) == []


def test_장기_판정표는_손절_분위_아래를_탈락으로_적는다() -> None:
    """PBR 이 20% 분위 아래면 구간이 뒤집혀 신호가 없는데 판정표는 전부 통과였다 (docs/infra.md 25.522, 감사 재현)."""
    passed, 표 = sg.judgements(long_input(pbr_now=0.60))["long"]
    assert passed is False
    탈락 = [r for r in 표 if r["passed"] is False]
    assert [r["label"] for r in 탈락] == ["손절 분위 위"] and "손절선 아래" in 탈락[0]["display"]
    # 20~30% 분위 사이면 그대로 신호
    passed, 표 = sg.judgements(long_input())["long"]
    assert passed is True and all(r["passed"] is not False for r in 표)


def test_장기_경계값에서도_판정표와_신호가_같은_말을_한다() -> None:
    """PBR = 20% 분위에서 표는 통과인데 구간이 뒤집혀 신호가 없었다 (docs/infra.md 25.531, 교차검증 재현)."""
    띠 = sg.Band(p20=0.60, p30=0.80, p50=1.10, p80=1.60, sample=750)
    for close in [1276.0 + i * 0.37 for i in range(200)]:
        inp = long_input(pbr_now=0.60, closes=[close], band=띠)  # PBR = 20% 분위
        passed, 표 = sg.judgements(inp)["long"]
        실제 = sg.buy_zone("long", inp, sg.long_term(inp)[1]) is not None
        assert passed == (실제 and sg.long_term(inp)[0]), close


def test_미국_20일선은_센트까지_적는다() -> None:
    """9.60 을 "10달러" 로 적어 종가 $9.80 과 모순됐다 (docs/infra.md 25.600, 감사).
    근거표 행도 같다 — 25.600 은 문장만 고쳐 표는 "20일선 10달러 > 60일선 10달러" 였다 (25.620, 감사)."""
    import re

    from batch.services import signals as sg

    inp = short_input(ticker="AAPL", market="NASDAQ", currency="USD", closes=rising(start=9.0, step=0.01))
    _passed, data = sg.short_term(inp)
    표 = " ".join(str(r.get("display")) for r in sg.criteria("short", inp, data) + sg.judgement("short", inp, data))
    값들 = re.findall(r"(?:20일선|60일선|종가) ([0-9,]+(?:\.[0-9]+)?)달러", 표)
    assert 값들 and all(re.fullmatch(r"[0-9,]+\.[0-9]{2}", v) for v in 값들), 표



def test_장기_구간_재료는_읽지_않는_표를_가리키지_않는다() -> None:
    """신호는 밴드를 직접 만든다 — `valuation_bands` 를 적으면 날짜가 다른 분위와 맞춰 보게 된다 (docs/infra.md 25.620)."""
    import inspect

    from batch.services import signals as sg

    src = inspect.getsource(sg.plan_criteria)
    assert "·valuation_bands\"" not in src and "financials (자본총계)" in src


@pytest.mark.parametrize("만들기", [short_input, mid_input, long_input])
def test_정상_신호는_기간마다_확인_가능하다(만들기) -> None:
    """`verifiable` 이 통과 판정 행을 요구한 뒤(25.620) 정상 신호가 사라지지 않는다 — 기간마다 판정 행이 있어야 한다."""
    signals_ = sg.evaluate(만들기(), total_investable=10_000_000)
    assert signals_, "정상 입력에서 신호가 나야 한다"
    assert all(sg.verifiable(s) for s in signals_)


def test_축소_까닭은_실제로_적용된_요인만_적는다() -> None:
    """변동성 ×0.8, 낙폭 ×0.5, 국면 ×0.5 면 적용은 낙폭·국면(×0.25)이다 — 변동성은 min 에서 졌다 (docs/infra.md 25.624)."""
    _w, reduction, reasons = sg.size_weight(10.0, sg.TARGET_VOLATILITY / 0.8, sg.TARGET_MDD / 0.5, 0.5)
    말 = reasons["reduction_note"]
    assert reduction == pytest.approx(0.25)
    assert "낙폭" in 말 and "시장 약세" in 말 and "변동성" not in 말 and "75%" in 말


def test_줄이지_않았으면_까닭이_없다() -> None:
    _w, _r, reasons = sg.size_weight(10.0, None, None, 1.0)
    assert reasons["reduction_note"] is None


def test_문턱_근처는_자릿수를_늘려_판정과_맞춘다() -> None:
    """59.6 을 "60점 / ≥ 60점 / 탈락" 으로 적었다 (docs/infra.md 25.630, 감사)."""
    assert sg._문턱_자릿수(59.6, 60, 0) == 1  # 59.6점
    assert sg._문턱_자릿수(10.04, 10, 1) == 2  # 10.04%
    assert sg._문턱_자릿수(72.3, 60, 0) == 0  # 멀면 그대로
    assert sg._문턱_자릿수(60.0, 60, 0) == 0  # 같으면 그대로
    inp = short_input(factor_scores={"momentum": 59.6})
    _p, data = sg.short_term(inp)
    행 = next(r for r in sg.judgement("short", inp, data) if r["label"] == "모멘텀 점수")
    assert 행["display"] == "59.6점" and 행["passed"] is False


def test_근거표도_문턱과_같아_보이지_않게_적는다() -> None:
    """판정표에만 있던 자릿수 규칙을 추천 카드 근거표에도 (docs/infra.md 25.796, 감사 재현).

    거래대금 배수 1.2004 가 "1.20배 / > 1.2배 / 통과", 매출 +0.03% 가 "0.0% / > 0% / 통과" 로 보였다.
    """
    inp = short_input()
    _passed, data = sg.short_term(inp)
    data = {**data, "turnover_ratio": 1.2004, "extension": 0.10004 if data.get("extension") is not None else None}
    표 = {r["label"]: r["display"] for r in sg.criteria("short", inp, data)}
    assert 표["수급 유입"] == "20일 거래대금이 60일 평균의 1.2004배"
    mid = mid_input(revenue_growth=0.0003, operating_income_growth=0.00004)
    _p, mdata = sg.mid_term(mid)
    중 = {r["label"]: r["display"] for r in sg.criteria("mid", mid, mdata)}
    assert 중["매출 증가"] == "전년 대비 0.03%"
    assert 중["영업이익 증가"] == "전년 대비 0.004%"


def test_정수_점수는_5_를_올린다() -> None:
    """72.5 를 파이썬은 "72", 웹 카드(`toFixed(0)`)는 "73" 으로 적어 한 카드에서 1점 달랐다 (25.796, 감사)."""
    assert sg._점수(72.5) == "73점" and sg._점수(60.5) == "61점" and sg._점수(59.4) == "59점"
    assert sg._점수(59.96, 60.0) == "59.96점"  # 문턱과 같아 보이면 자릿수를 늘린다(25.630)


def test_홀수_문턱에서도_5_점수가_문턱과_같아_보이지_않는다() -> None:
    """54.5 가 문턱 55 에서 "55점 / ≥ 55점 / 탈락" 이었다 — 비교는 짝수 반올림, 표시는 올림이라 어긋났다 (25.799, 교차검증)."""
    assert sg._점수(54.5, 55.0) == "54.5점"
    assert sg._점수(55.0, 55.0) == "55점"


def test_판정표와_근거_문장의_거래대금_배수도_문턱과_같아_보이지_않는다() -> None:
    inp = short_input()
    _passed, data = sg.short_term(inp)
    data = {**data, "turnover_ratio": 1.2004}
    판정 = {r["label"]: r["display"] for r in sg.judgement("short", inp, data)}
    assert 판정["수급 유입"] == "20일 거래대금이 60일 평균의 1.2004배"


def test_찍힐_글자끼리_견준다() -> None:
    """25.799 는 비교만 .5 올림이라 1.25 가 "1.2배 / > 1.2배" 로 찍혔다 (25.801, 교차검증)."""
    assert sg._문턱_자릿수(1.25, 1.2, 1) == 2
    assert sg._문턱_자릿수(1.2005, 1.2, 2) >= 4
    assert sg._점수(54.5, 55.0) == "54.5점"


def test_근거_데이터에_과거_성과를_싣는다() -> None:
    """웹 1부가 텔레그램 1부처럼 CAGR·MDD·샤프를 보이도록 — 신호 때 읽은 행 그대로 (docs/infra.md 25.803)."""
    inp = short_input(cagr=0.12, mdd=-0.3, sharpe=0.8, metrics_window="3Y", metrics_date="2026-09-29")
    signals = sg.evaluate(inp, total_investable=10_000_000)
    assert signals, "짧은 신호가 나야 한다"
    assert signals[0].rationale_data["performance"] == {
        "window": "3Y", "as_of": "2026-09-29", "cagr": 0.12, "mdd": -0.3, "sharpe": 0.8, "source": sg.SOURCE_METRICS,
    }
    # 값이 없으면 싣지 않는다
    assert "performance" not in sg.evaluate(short_input(), total_investable=10_000_000)[0].rationale_data
