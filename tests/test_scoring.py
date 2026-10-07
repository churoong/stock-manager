"""팩터 계산과 정규화 테스트.

docs/factors.md 8장의 표를 그대로 옮긴 것이다. 기대값은 손으로 계산할 수
있는 자료로 만들고, 각 테스트에 그 근거를 적는다. 근거 없이 숫자만 적으면
코드가 틀렸을 때 테스트도 함께 틀린다.

네트워크를 타지 않는다.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from batch.services import scoring as sc

ROOT = Path(__file__).resolve().parent.parent

# ----------------------------------------------------------------------
# 지표: 성립하지 않는 값은 만들지 않는다
# ----------------------------------------------------------------------


class Test비율:
    def test_정상(self) -> None:
        assert sc.safe_ratio(10, 4) == 2.5

    def test_분모가_0이면_없다(self) -> None:
        assert sc.safe_ratio(10, 0) is None

    def test_분모가_음수면_없다(self) -> None:
        """자본잠식 기업의 ROE 는 나쁜 값이 아니라 성립하지 않는 값이다."""
        assert sc.safe_ratio(-10, -5) is None

    def test_음수_분모를_허용하면_계산한다(self) -> None:
        assert sc.safe_ratio(-10, -5, positive_denominator=False) == 2.0

    def test_입력이_없으면_없다(self) -> None:
        assert sc.safe_ratio(None, 5) is None
        assert sc.safe_ratio(5, None) is None


class Test밸류:
    def test_이익수익률은_PER의_역수다(self) -> None:
        # 시총 1,000억, 순이익 100억 → PER 10배, E/P 0.1
        m = sc.value_metrics(
            net_income=100, total_equity=500, revenue=800, market_cap=1000
        )
        assert m["ep"] == pytest.approx(0.1)
        assert m["bp"] == pytest.approx(0.5)
        assert m["sp"] == pytest.approx(0.8)

    def test_적자는_음수로_남는다(self) -> None:
        """PER 은 적자에서 음수가 되어 가장 싼 종목으로 올라온다. 역수는 그렇지 않다."""
        m = sc.value_metrics(
            net_income=-100, total_equity=500, revenue=800, market_cap=1000
        )
        assert m["ep"] == pytest.approx(-0.1)

    def test_시가총액이_없으면_전부_없다(self) -> None:
        m = sc.value_metrics(100, 500, 800, None)
        assert m == {"ep": None, "bp": None, "sp": None}

    def test_시가총액이_0이면_전부_없다(self) -> None:
        m = sc.value_metrics(100, 500, 800, 0)

        assert set(m) == {"ep", "bp", "sp"}, "빈 dict 면 아래 all() 이 공짜로 참이 된다"
        assert all(v is None for v in m.values())


class Test퀄리티:
    def test_기본_지표(self) -> None:
        # ROE = 100/500 = 0.2, ROA = 100/1000 = 0.1
        # 영업이익률 = 120/800 = 0.15, 부채비율 = 500/500 = 1.0
        m = sc.quality_metrics(
            net_income=100,
            total_assets=1000,
            total_equity=500,
            operating_income=120,
            revenue=800,
            total_liabilities=500,
        )
        assert m["roe"] == pytest.approx(0.2)
        assert m["roa"] == pytest.approx(0.1)
        assert m["operating_margin"] == pytest.approx(0.15)
        assert m["debt_ratio"] == pytest.approx(1.0)

    def test_부채총계가_없으면_자산에서_자본을_뺀다(self) -> None:
        m = sc.quality_metrics(100, 1000, 400, 120, 800)
        # 1000 - 400 = 600, 600/400 = 1.5
        assert m["debt_ratio"] == pytest.approx(1.5)

    def test_자본잠식이면_자본_기반_지표가_없다(self) -> None:
        m = sc.quality_metrics(
            net_income=-50,
            total_assets=1000,
            total_equity=-200,
            operating_income=-30,
            revenue=800,
            total_liabilities=1200,
        )
        assert m["roe"] is None
        assert m["debt_ratio"] is None
        # 자산 기반 지표는 성립한다
        assert m["roa"] == pytest.approx(-0.05)

    def test_이익_안정성(self) -> None:
        assert sc.profit_stability(4, 5) == pytest.approx(0.8)

    def test_관측_3년_미만이면_안정성을_내지_않는다(self) -> None:
        assert sc.profit_stability(2, 2) is None

    def test_흑자_연수가_관측_연수를_넘으면_거부한다(self) -> None:
        with pytest.raises(ValueError):
            sc.profit_stability(6, 5)


class Test성장:
    def test_전년_대비(self) -> None:
        assert sc.growth_rate(120, 100) == pytest.approx(0.2)

    def test_전년이_적자면_성장률이_없다(self) -> None:
        """-100억에서 -10억은 90% 성장이 아니다."""
        assert sc.growth_rate(-10, -100) is None

    def test_전년이_0이면_없다(self) -> None:
        assert sc.growth_rate(100, 0) is None

    def test_3년_CAGR(self) -> None:
        # 100 → 800 이면 3년에 2배씩. 8^(1/3) - 1 = 1.0
        assert sc.cagr(800, 100, 3) == pytest.approx(1.0)

    def test_과거_값이_없으면_CAGR이_없다(self) -> None:
        assert sc.cagr(800, None, 3) is None


class Test모멘텀:
    def test_3개월_수익률(self) -> None:
        # 64개 값. 첫 값 100, 마지막 값 110 → 10%
        closes = [100.0] + [105.0] * 62 + [110.0]
        m = sc.momentum_metrics(closes)
        assert m["momentum_3m"] == pytest.approx(0.1)

    def test_표본이_모자라면_없다(self) -> None:
        """없는 구간을 있는 데이터로 늘려 계산하지 않는다."""
        m = sc.momentum_metrics([100.0] * 63)
        assert m["momentum_3m"] is None
        assert m["momentum_6m"] is None
        assert m["momentum_12_1"] is None

    def test_12_1은_최근_한_달을_뺀다(self) -> None:
        # 274개가 필요하다: 기준(21일 전) 과 그보다 252일 앞선 값
        closes = [0.0] * 274
        closes[-1 - 21 - 252] = 100.0  # 시작점
        closes[-1 - 21] = 150.0  # 도착점
        closes[-1] = 999.0  # 최근 한 달의 급등. 여기에 영향받으면 안 된다
        m = sc.momentum_metrics(closes)
        assert m["momentum_12_1"] == pytest.approx(0.5)

    def test_시작_가격이_0이면_없다(self) -> None:
        closes = [0.0] + [10.0] * 63
        assert sc.momentum_metrics(closes)["momentum_3m"] is None


class Test리스크:
    def test_MDD와_베타는_절댓값으로_본다(self) -> None:
        """부호를 두면 많이 떨어진 종목과 역방향 종목의 순위가 뒤집힌다."""
        m = sc.risk_metrics(
            mdd=-0.35,
            volatility_ann=0.28,
            sharpe=1.2,
            sortino=1.5,
            beta=-0.4,
            mdd_recovery_days=120,
            cagr_value=0.18,
        )
        assert m["mdd_abs"] == pytest.approx(0.35)
        assert m["beta_abs"] == pytest.approx(0.4)
        assert m["mdd_recovery_days"] == pytest.approx(120.0)

    def test_미회복은_결측으로_넘긴다(self) -> None:
        """치환은 집단을 알아야 하므로 정규화 단계에서 한다."""
        m = sc.risk_metrics(-0.3, 0.2, 1.0, 1.0, 1.0, None, 0.1)
        assert m["mdd_recovery_days"] is None


# ----------------------------------------------------------------------
# 정규화
# ----------------------------------------------------------------------


class Test윈저라이즈:
    def test_극단값을_자른다(self) -> None:
        values = list(range(1, 101))  # 1..100
        cut = sc.winsorize([float(v) for v in values], pct=0.05)
        # 상·하위 5% 지점 값으로 잘린다. 최솟값과 최댓값이 바뀐다
        assert min(cut) > 1.0
        assert max(cut) < 100.0

    def test_순서를_유지한다(self) -> None:
        cut = sc.winsorize([5.0, 1.0, 3.0], pct=0.0)
        assert cut == [5.0, 1.0, 3.0]


class TestZ점수:
    def test_손으로_계산한_값과_같다(self) -> None:
        # 1,2,3,4,5 → 평균 3, 모분산 (4+1+0+1+4)/5 = 2, 표준편차 √2
        z = sc.zscores([1.0, 2.0, 3.0, 4.0, 5.0], pct=0.0)
        assert z[2] == pytest.approx(0.0)
        assert z[4] == pytest.approx(2 / math.sqrt(2))
        assert z[0] == pytest.approx(-2 / math.sqrt(2))

    def test_모두_같으면_전원_0이다(self) -> None:
        """우열이 없으면 점수 차이도 없어야 한다."""
        z = sc.zscores([7.0, 7.0, 7.0])
        assert z == [0.0, 0.0, 0.0]

    def test_방향이_반대면_부호를_뒤집는다(self) -> None:
        # 부채비율은 낮을수록 좋다
        z = sc.zscores([1.0, 2.0, 3.0, 4.0, 5.0], higher_is_better=False, pct=0.0)
        assert z[0] > 0
        assert z[4] < 0

    def test_결측은_결측으로_남는다(self) -> None:
        z = sc.zscores([1.0, None, 5.0], pct=0.0)
        assert z[1] is None

    def test_전부_결측이면_전부_결측이다(self) -> None:
        assert sc.zscores([None, None]) == [None, None]

    def test_미회복은_집단_최악값으로_친다(self) -> None:
        """MDD 회복 기간의 결측은 값이 없는 것이 아니라 가장 나쁜 상태다."""
        z = sc.zscores(
            [10.0, 20.0, None],
            higher_is_better=False,
            missing_is_worst=True,
            pct=0.0,
        )
        # 낮을수록 좋은 지표이므로 최악은 최댓값(20)이다. 20 과 같은 z 를 받는다
        assert z[2] == pytest.approx(z[1])
        assert z[2] < z[0]

    def test_모르는_회복기간은_치환하지_않는다(self) -> None:
        """성과지표 행이 없어 MDD 도 모르면 모름이다 — 최악값을 여러 번 더해 표본을 오염시켰다 (docs/infra.md 25.558)."""
        값 = [10.0, 20.0, 30.0, None, None, None]
        모름 = [False, False, False, True, True, True]
        z = sc.zscores(값, higher_is_better=False, missing_is_worst=True, pct=0.0, unknown=모름)
        assert z[3:] == [None, None, None]
        # 알던 셋의 z 는 셋만으로 낸 것과 같다
        assert z[:3] == pytest.approx(sc.zscores([10.0, 20.0, 30.0], higher_is_better=False, pct=0.0))
        # 회복 기간 지표는 MDD 를 알 때만 치환한다
        지표 = {m.name: m for m in sc.FACTOR_METRICS["risk"]}["mdd_recovery_days"]
        assert 지표.known_with == "mdd_abs"


    def test_미회복은_그만큼도_안_빠진_종목들의_최악값으로_친다(self) -> None:
        """신고가 다음 날 −0.75% 인 종목이 −40% 종목과 같은 최악이 됐다 (감사 재현, docs/infra.md 25.685)."""
        회복 = [100.0, 50.0, 5.0, None]
        깊이 = [0.40, 0.10, 0.005, 0.0075]
        대신 = sc.depth_substitutes(회복, 깊이, higher_is_better=False)
        assert 대신 == [None, None, None, 5.0]  # 0.75% 이하로 빠졌던 종목(0.5%, 5행)만큼
        z = sc.zscores(회복, higher_is_better=False, missing_is_worst=True, pct=0.0, depth=깊이)
        assert z[3] == pytest.approx(z[2]) and z[3] > z[0]
        # 깊게 빠진 미회복은 여전히 최악 쪽이다
        assert sc.depth_substitutes([100.0, 50.0, None], [0.4, 0.1, 0.5], higher_is_better=False)[2] == 100.0
        # 더 얕은 종목이 없으면 그보다 깊은 것 중 가장 얕은 종목 값, 깊이를 모르면 집단 최악
        assert sc.depth_substitutes([100.0, 50.0, None], [0.4, 0.1, 0.01], higher_is_better=False)[2] == 50.0
        assert sc.depth_substitutes([100.0, 50.0, None], [0.4, 0.1, None], higher_is_better=False)[2] == 100.0

    def test_팩터_점수가_치환값을_종목마다_남긴다(self) -> None:
        종목 = [
            sc.StockInput(stock_id=i, market="KOSPI", sector=None, metrics={"mdd_abs": d, "mdd_recovery_days": r})
            for i, (d, r) in enumerate([(0.4, 100.0), (0.1, 50.0), (0.005, 5.0), (0.0075, None), (0.5, None)], 1)
        ]
        위험 = {r.stock_id: r for r in sc.score_factors(종목) if r.factor == "risk"}
        assert 위험[4].raw[sc.SUBSTITUTED_KEY] == {"mdd_recovery_days": 5.0}
        assert 위험[5].raw[sc.SUBSTITUTED_KEY] == {"mdd_recovery_days": 100.0}

    def test_지난_행_수보다_좋게_채우지_않는다(self) -> None:
        """바닥 뒤 400행째 미회복 −10% 가 300행 만에 회복한 종목보다 좋게 나왔다 (교차검증 재현, docs/infra.md 25.691)."""
        회복, 깊이 = [100.0, 20.0, 300.0, None], [0.10, 0.05, 0.40, 0.10]
        assert sc.depth_substitutes(회복, 깊이, higher_is_better=False)[3] == 100.0
        assert sc.depth_substitutes(회복, 깊이, higher_is_better=False, floor=[None, None, None, 400.0])[3] == 400.0
        # 하한이 더 작으면 깊이 치환 그대로
        assert sc.depth_substitutes(회복, 깊이, higher_is_better=False, floor=[None, None, None, 3.0])[3] == 100.0
        z = sc.zscores(회복, higher_is_better=False, missing_is_worst=True, pct=0.0, depth=깊이, floor=[None] * 3 + [400.0])
        assert z[3] < z[2]

    def test_바닥_뒤_행_수(self) -> None:
        날 = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
        assert sc.elapsed_rows_since(날, "2026-09-02") == 2.0
        assert sc.elapsed_rows_since(날, "2026-08-01") == 4.0  # 계열보다 앞 — 적어도 4
        assert sc.elapsed_rows_since(날, None) is None
        # 회복했으면 하한을 넘기지 않는다
        assert sc.risk_metrics(-0.1, None, None, None, None, 5, None, unrecovered_rows=9.0).get(sc.UNRECOVERED_ROWS) is None
        assert sc.risk_metrics(-0.1, None, None, None, None, None, None, unrecovered_rows=9.0)[sc.UNRECOVERED_ROWS] == 9.0

    def test_팩터_점수가_하한을_치환값에_쓴다(self) -> None:
        종목 = [
            sc.StockInput(stock_id=i, market="KOSPI", sector=None, metrics=지표)
            for i, 지표 in enumerate([
                {"mdd_abs": 0.10, "mdd_recovery_days": 100.0},
                {"mdd_abs": 0.40, "mdd_recovery_days": 300.0},
                {"mdd_abs": 0.10, "mdd_recovery_days": None, sc.UNRECOVERED_ROWS: 400.0},
            ], 1)
        ]
        위험 = {r.stock_id: r for r in sc.score_factors(종목) if r.factor == "risk"}
        assert 위험[3].raw[sc.SUBSTITUTED_KEY] == {"mdd_recovery_days": 400.0}

class Test점수변환:
    def test_중앙은_50점(self) -> None:
        assert sc.to_score(0.0) == 50.0

    def test_양끝(self) -> None:
        assert sc.to_score(3.0) == 100.0
        assert sc.to_score(-3.0) == 0.0

    def test_절단_밖은_양끝에_붙는다(self) -> None:
        assert sc.to_score(9.9) == 100.0
        assert sc.to_score(-9.9) == 0.0

    def test_결측은_결측이다(self) -> None:
        assert sc.to_score(None) is None


# ----------------------------------------------------------------------
# 팩터 집계와 비교 집단
# ----------------------------------------------------------------------


class Test팩터집계:
    def test_절반_이상이면_계산한다(self) -> None:
        z, missing = sc.factor_from_metrics(
            {"ep": 1.0, "bp": 3.0, "sp": None}, "value"
        )
        assert z == pytest.approx(2.0)
        # 2026-09-21: 밸류가 3개에서 4개가 됐다(배당수익률). 이 시험 입력에는 배당이 없다
        assert missing == ["sp", "dividend_yield"]

    def test_절반_미만이면_내지_않는다(self) -> None:
        """지표 셋 중 하나로 낸 값을 그 팩터의 점수라고 부를 수 없다."""
        z, missing = sc.factor_from_metrics(
            {"ep": 1.0, "bp": None, "sp": None}, "value"
        )
        assert z is None
        assert sorted(missing) == ["bp", "dividend_yield", "sp"]

    def test_다섯_팩터가_모두_정의돼_있다(self) -> None:
        assert set(sc.FACTOR_METRICS) == set(sc.FACTORS)
        assert len(sc.FACTORS) == 5


def make_stock(stock_id: int, market: str, sector: str | None, base: float):
    """모멘텀 세 지표를 같은 크기로 채운 종목 하나."""
    return sc.StockInput(
        stock_id=stock_id,
        market=market,
        sector=sector,
        metrics={
            "momentum_12_1": base,
            "momentum_6m": base,
            "momentum_3m": base,
        },
    )


class Test비교집단:
    def test_업종_집단이_충분하면_업종으로_묶는다(self) -> None:
        stocks = [make_stock(i, "KOSPI", "반도체", float(i)) for i in range(40)]
        groups = sc.assign_peer_groups(stocks)
        assert groups[0] == "sector:KOSPI:반도체"

    def test_업종_집단이_작으면_시장으로_올린다(self) -> None:
        """표본 열둘로 낸 z-score 는 순위가 아니라 잡음이다."""
        stocks = [make_stock(i, "KOSPI", "반도체", float(i)) for i in range(12)]
        groups = sc.assign_peer_groups(stocks)
        assert groups[0] == "market:KOSPI"

    def test_업종이_없으면_시장으로_묶는다(self) -> None:
        stocks = [make_stock(i, "KOSPI", None, float(i)) for i in range(40)]
        groups = sc.assign_peer_groups(stocks)
        assert groups[0] == "market:KOSPI"

    def test_시장_위로는_올라가지_않는다(self) -> None:
        """한국과 미국을 같은 잣대로 비교하지 않는다."""
        stocks = [make_stock(1, "KOSPI", None, 1.0), make_stock(2, "NASDAQ", None, 2.0)]
        groups = sc.assign_peer_groups(stocks)
        assert groups[1] == "market:KOSPI"
        assert groups[2] == "market:NASDAQ"


class Test시장혼합금지:
    """국내와 미국을 직접 비교하지 않는다는 규칙을 코드로 확인한다."""

    def test_다른_시장을_더해도_점수가_변하지_않는다(self) -> None:
        kospi = [make_stock(i, "KOSPI", None, float(i)) for i in range(1, 6)]

        alone = {
            r.stock_id: r.score
            for r in sc.score_factors(kospi)
            if r.factor == "momentum"
        }

        mixed_input = kospi + [
            make_stock(100 + i, "NASDAQ", None, float(i) * 1000) for i in range(1, 6)
        ]
        mixed = {
            r.stock_id: r.score
            for r in sc.score_factors(mixed_input)
            if r.factor == "momentum"
        }

        for stock_id, score in alone.items():
            assert mixed[stock_id] == score

    def test_집단_이름과_크기를_남긴다(self) -> None:
        stocks = [make_stock(i, "KOSPI", None, float(i)) for i in range(1, 4)]
        results = [r for r in sc.score_factors(stocks) if r.factor == "momentum"]
        assert results[0].peer_group == "market:KOSPI"
        assert results[0].peer_size == 3

    def test_입력이_없는_팩터는_점수가_없다(self) -> None:
        stocks = [make_stock(i, "KOSPI", None, float(i)) for i in range(1, 4)]
        value = [r for r in sc.score_factors(stocks) if r.factor == "value"]
        assert all(r.score is None for r in value)
        assert sorted(value[0].missing_fields) == ["bp", "dividend_yield", "ep", "sp"]


# ----------------------------------------------------------------------
# 종합 점수
# ----------------------------------------------------------------------

WEIGHTS = {"value": 20.0, "quality": 20.0, "growth": 20.0, "momentum": 20.0, "risk": 20.0}


class Test종합점수:
    def test_동일_가중_평균(self) -> None:
        scores = {f: 60.0 for f in sc.FACTORS}
        result = sc.total_score(scores, WEIGHTS)
        assert result.total == pytest.approx(60.0)

    def test_가중치가_다르면_반영된다(self) -> None:
        scores = dict.fromkeys(sc.FACTORS, 0.0)
        scores["value"] = 100.0
        weights = {"value": 60.0, "quality": 10.0, "growth": 10.0, "momentum": 10.0, "risk": 10.0}
        result = sc.total_score(scores, weights)
        assert result.total == pytest.approx(60.0)

    def test_팩터_하나가_비면_나머지를_재정규화한다(self) -> None:
        scores = {f: 60.0 for f in sc.FACTORS}
        scores["risk"] = None
        result = sc.total_score(scores, WEIGHTS)

        assert result.total == pytest.approx(60.0)
        assert sum(result.weights_used.values()) == pytest.approx(100.0)
        assert "risk" not in result.weights_used

    def test_둘_이상_비면_점수를_내지_않는다(self) -> None:
        scores = {f: 60.0 for f in sc.FACTORS}
        scores["risk"] = None
        scores["growth"] = None
        result = sc.total_score(scores, WEIGHTS)

        assert result.total is None
        assert result.skip_reason == sc.SKIP_TOO_MANY_MISSING

    def test_센티먼트를_끄면_5팩터만_쓴다(self) -> None:
        """센티먼트 축은 꺼진 상태로도 동작해야 한다."""
        scores = {f: 60.0 for f in sc.FACTORS}
        result = sc.total_score(scores, WEIGHTS, sentiment=None, sentiment_weight=10.0)

        assert result.total == pytest.approx(60.0)
        assert result.sentiment_weight_used == 0.0

    def test_가중치가_0이어도_5팩터만_쓴다(self) -> None:
        scores = {f: 60.0 for f in sc.FACTORS}
        result = sc.total_score(scores, WEIGHTS, sentiment=80.0, sentiment_weight=0.0)
        assert result.total == pytest.approx(60.0)

    def test_센티먼트는_가중치만큼만_섞인다(self) -> None:
        # 팩터 60점, 센티먼트 +100 → 0~100 으로 100점. 가중치 10% 면 60 + (100−50)*0.1 = 65 (25.639)
        scores = {f: 60.0 for f in sc.FACTORS}
        result = sc.total_score(scores, WEIGHTS, sentiment=100.0, sentiment_weight=10.0)
        assert result.total == pytest.approx(65.0)

    def test_센티먼트_0은_중앙값_50으로_섞인다(self) -> None:
        scores = {f: 60.0 for f in sc.FACTORS}
        result = sc.total_score(scores, WEIGHTS, sentiment=0.0, sentiment_weight=50.0)
        # 중립은 움직이지 않는다 — 감성이 없는 종목(재정규화, 60)과 같다 (25.639. 예전 식은 55)
        assert result.total == pytest.approx(60.0)

    def test_감성이_있어도_중립이면_없는_종목과_같다(self) -> None:
        """팩터합 80 에 감성 0 이 77 로 깎여 감성 없는 종목(80) 아래로 갔다 (docs/infra.md 25.639, 감사)."""
        scores = {f: 80.0 for f in sc.FACTORS}
        없음 = sc.total_score(scores, WEIGHTS, sentiment=None, sentiment_weight=10.0).total
        중립 = sc.total_score(scores, WEIGHTS, sentiment=0.0, sentiment_weight=10.0).total
        좋음 = sc.total_score(scores, WEIGHTS, sentiment=40.0, sentiment_weight=10.0).total
        assert 없음 == 중립 == 80.0 and 좋음 == pytest.approx(82.0)

    def test_쓴_가중치를_돌려준다(self) -> None:
        """저장된 가중치로 같은 점수를 재현할 수 있어야 한다."""
        scores = {f: 60.0 for f in sc.FACTORS}
        result = sc.total_score(scores, WEIGHTS)
        assert result.weights_used == {f: pytest.approx(20.0) for f in sc.FACTORS}


class Test순위:
    def test_높은_점수가_1등이다(self) -> None:
        ranks = sc.rank_within([(1, 50.0), (2, 90.0), (3, 70.0)])
        assert ranks == {2: 1, 3: 2, 1: 3}

    def test_동점은_같은_순위다(self) -> None:
        ranks = sc.rank_within([(1, 90.0), (2, 90.0), (3, 70.0)])
        assert ranks[1] == 1
        assert ranks[2] == 1
        assert ranks[3] == 3

    def test_점수가_없으면_순위를_받지_않는다(self) -> None:
        ranks = sc.rank_within([(1, 90.0), (2, None)])
        assert 2 not in ranks


class Test가격지표:
    """2026-09-17 추가 (docs/factors.md 10.1). 손으로 검산할 수 있는 계열로 고정한다."""

    def test_52주_고점_근접도(self) -> None:
        closes = [100.0] * 251 + [80.0]  # 252개. 최고 100, 지금 80
        assert sc.high_52w_proximity(closes) == pytest.approx(0.8)
        # 지금이 고점이면 1
        assert sc.high_52w_proximity([90.0] * 251 + [120.0]) == pytest.approx(1.0)

    def test_52주_고점은_252일이_다_있어야_한다(self) -> None:
        assert sc.high_52w_proximity([100.0] * 251) is None
        # 창 밖의 더 높은 값은 보지 않는다
        closes = [500.0] + [100.0] * 251 + [100.0]
        assert sc.high_52w_proximity(closes) == pytest.approx(1.0)

    def test_꾸준함은_오른_날_비율에서_내린_날_비율을_뺀다(self) -> None:
        # 12-1 창(253개 종가)이 전부 조금씩 오르고, 최근 21일은 무엇이든 상관없다
        formation = [100.0 * (1.001**i) for i in range(253)]
        recent = [50.0] * 21  # 최근 한 달 급락. 여기에 영향받으면 안 된다
        assert sc.momentum_consistency(formation + recent) == pytest.approx(1.0)
        # 반은 오르고 반은 내리면 0 근처
        zigzag = [100.0 + (1.0 if i % 2 else 0.0) for i in range(253)]
        assert abs(sc.momentum_consistency(zigzag + recent)) < 0.01

    def test_꾸준함은_창이_모자라면_없다(self) -> None:
        assert sc.momentum_consistency([100.0] * 273) is None

    def test_변동성_조정_모멘텀(self) -> None:
        # 매일 0.1% 씩 오르면 변동성이 0 → None (수익률 / 0 은 없다)
        steady = [100.0 * (1.001**i) for i in range(253)] + [100.0] * 21
        assert sc.momentum_vol_adjusted(steady) is None
        # 오르내리면 변동성이 생기고, 같은 수익률이면 덜 흔들린 쪽이 높다
        wild = [100.0 + (5.0 if i % 2 else 0.0) for i in range(252)] + [110.0] + [100.0] * 21
        calm = [100.0 + (0.5 if i % 2 else 0.0) for i in range(252)] + [110.0] + [100.0] * 21
        w, c = sc.momentum_vol_adjusted(wild), sc.momentum_vol_adjusted(calm)
        assert w is not None and c is not None and c > w

    def test_아미후드_비유동성(self) -> None:
        # 매일 1% 오르고 거래대금이 10억이면 |r|/(v/10억) = 0.01
        closes = [100.0 * (1.01**i) for i in range(64)]
        values: list[float | None] = [1_000_000_000.0] * 64
        assert sc.amihud_illiquidity(closes, values) == pytest.approx(0.01)
        # 거래대금이 반이면 두 배 — 같은 움직임을 적은 돈이 만들었다
        assert sc.amihud_illiquidity(closes, [500_000_000.0] * 64) == pytest.approx(0.02)

    def test_아미후드는_거래대금이_없는_날을_빼고_30일_미만이면_없다(self) -> None:
        closes = [100.0 * (1.01**i) for i in range(64)]
        sparse: list[float | None] = [None] * 64
        for i in range(29):
            sparse[-1 - i] = 1_000_000_000.0
        assert sc.amihud_illiquidity(closes, sparse) is None
        assert sc.amihud_illiquidity(closes, None) is None

    def test_묶음_함수는_네_지표를_모두_돌려준다(self) -> None:
        m = sc.price_series_metrics([100.0] * 10)
        # 2026-09-21: MAX 효과가 더해져 다섯이다 (docs/factors.md 11.2).
        # 잔차 변동성은 지수 계열이 더 필요해 이 묶음에 없다 — 부르는 쪽이 따로 낸다
        assert set(m) == {
            "high_52w_proximity", "momentum_consistency", "momentum_vol_adjusted",
            "amihud_illiquidity", "max_daily_return",
        }
        assert all(v is None for v in m.values())  # 표본이 모자라면 전부 없다

    def test_모멘텀은_여섯_중_셋_리스크는_열_중_다섯이_살아야_한다(self) -> None:
        # 젊은 종목: 6m·3m 만 있다 → 6개 중 2개 → 모멘텀 없음
        z = {"momentum_6m": 1.0, "momentum_3m": 1.0}
        value, missing = sc.factor_from_metrics(z, "momentum")
        assert value is None and len(missing) == 4
        # 하나 더 살면 계산된다
        z["high_52w_proximity"] = 0.5
        value, _ = sc.factor_from_metrics(z, "momentum")
        assert value == pytest.approx((1.0 + 1.0 + 0.5) / 3)
        # 2026-09-21: 리스크가 8개에서 10개가 됐다 (MAX·잔차 변동성, docs/factors.md 11.2·11.3).
        # **문턱이 4개에서 5개로 올라갔다.** 지표를 더할 때 이 수를 먼저 본다 —
        # 성과지표가 통째로 비면(지금이 그렇다) 어차피 둘 다 못 넘으므로 실질 변화는 없다
        assert len(sc.FACTOR_METRICS["risk"]) == 10
        적게 = {"mdd_abs": 1.0, "volatility_ann": 1.0, "sharpe": 1.0, "cagr": 1.0}
        assert sc.factor_from_metrics(적게, "risk")[0] is None, "넷으로는 리스크를 내지 않는다"
        적게["max_daily_return"] = 1.0
        assert sc.factor_from_metrics(적게, "risk")[0] is not None, "다섯이면 낸다"


# ----------------------------------------------------------------------
# 재무제표 지표 (docs/factors.md 10.2). 2026-09-17 추가. 값은 손으로 검산했다
# ----------------------------------------------------------------------

# 모든 조건이 좋아진 회사. 전년 → 당해
GOOD_CUR = {
    "net_income": 100, "total_assets": 1000, "noncurrent_liabilities": 100, "total_liabilities": 400,
    "current_assets": 300, "current_liabilities": 150, "operating_income": 120, "revenue": 800,
}
GOOD_PREV = {
    "net_income": 50, "total_assets": 900, "noncurrent_liabilities": 150, "total_liabilities": 400,
    "current_assets": 250, "current_liabilities": 150, "operating_income": 60, "revenue": 600,
}


class Test재무제표지표:
    def test_자산_성장률(self) -> None:
        assert sc.asset_growth(1200, 1000) == pytest.approx(0.2)
        # 전년이 0 이하면 성립하지 않는다 (성장률과 같은 규칙)
        assert sc.asset_growth(1200, 0) is None
        assert sc.asset_growth(1200, -10) is None
        assert sc.asset_growth(None, 1000) is None

    def test_유동비율(self) -> None:
        assert sc.current_ratio(300, 200) == pytest.approx(1.5)
        assert sc.current_ratio(300, 0) is None  # 유동부채 0 → 성립 안 함
        assert sc.current_ratio(None, 200) is None  # 은행처럼 구분이 없는 경우

    def test_피오트로스키_전부_좋아지면_6(self) -> None:
        # 1 ROA 0.1>0 · 2 ROA 0.1>0.056 · 3 레버리지 0.1<0.167 · 4 유동비율 2.0>1.67
        # 5 이익률 0.15>0.10 · 6 회전율 0.8>0.67
        assert sc.piotroski_lite(GOOD_CUR, GOOD_PREV) == 6

    def test_피오트로스키_전부_나빠지면_흑자_하나만_남는다(self) -> None:
        # 두 해를 바꾸면 ROA 는 여전히 양수(조건 1)이고 나머지 다섯은 거짓
        assert sc.piotroski_lite(GOOD_PREV, GOOD_CUR) == 1

    def test_피오트로스키_적자면_조건1이_빠진다(self) -> None:
        cur = dict(GOOD_CUR, net_income=-10)
        # ROA 음수 → 1 거짓, 2(ROA 개선)도 거짓. 나머지 4개는 참
        assert sc.piotroski_lite(cur, GOOD_PREV) == 4

    def test_피오트로스키_하나라도_모르면_전체가_없다(self) -> None:
        # 부분 합은 낮은 쪽으로 치우친다. 5개가 참이어도 값을 내지 않는다
        cur = {k: v for k, v in GOOD_CUR.items() if k != "current_assets"}
        assert sc.piotroski_lite(cur, GOOD_PREV) is None
        assert sc.piotroski_lite(GOOD_CUR, None) is None
        assert sc.piotroski_lite(None, GOOD_PREV) is None

    def test_피오트로스키_레버리지는_두_해가_같은_잣대다(self) -> None:
        # 전년에 비유동부채가 없으면 두 해 모두 부채총계로 비교한다 (400/1000 < 400/900 → 감소 = 참)
        prev = {k: v for k, v in GOOD_PREV.items() if k != "noncurrent_liabilities"}
        assert sc.piotroski_lite(GOOD_CUR, prev) == 6
        # 부채총계를 같게 두면 감소가 아니다 → 5
        prev_same = dict(prev, total_liabilities=360)  # 360/900 = 0.4 = 400/1000
        assert sc.piotroski_lite(GOOD_CUR, prev_same) == 5
        # 두 잣대 다 한 해가 비면 판정 불가
        prev_none = {k: v for k, v in prev.items() if k != "total_liabilities"}
        assert sc.piotroski_lite(GOOD_CUR, prev_none) is None

    def test_묶음_함수는_세_지표를_같이_낸다(self) -> None:
        m = sc.statement_metrics(GOOD_CUR, GOOD_PREV)
        assert m["asset_growth"] == pytest.approx(1000 / 900 - 1)
        assert m["current_ratio"] == pytest.approx(2.0)
        assert m["piotroski_lite"] == 6.0
        # 전년이 없으면 유동비율만 남는다 (당해만으로 낼 수 있는 유일한 지표)
        m = sc.statement_metrics(GOOD_CUR, None)
        assert m["asset_growth"] is None and m["piotroski_lite"] is None
        assert m["current_ratio"] == pytest.approx(2.0)

    def test_퀄리티는_8개_중_4개가_살아야_한다(self) -> None:
        assert len(sc.FACTOR_METRICS["quality"]) == 8
        z = {"roe": 1.0, "roa": 1.0, "operating_margin": 1.0}
        assert sc.factor_from_metrics(z, "quality")[0] is None
        z["piotroski_lite"] = 1.0
        assert sc.factor_from_metrics(z, "quality")[0] == pytest.approx(1.0)


class Test치환한_사실을_남긴다:
    """**값이 비었는데 점수는 있는** 상황을 화면이 설명할 수 있어야 한다.

    `mdd_recovery_days` 가 없는 종목(아직 회복하지 못한 종목)은 집단의 최악값으로
    대신 점수를 받는다. 그런데 `raw` 에는 null 이 들어가고 `missing_fields` 에도
    안 들어간다 — 화면에는 아무 흔적이 없었다.

    `docs/factors.md` 3.5 는 2026-09-16 부터 "이 치환 사실을 `raw_json` 에 기록한다" 고
    요구하고 있었고, 코드는 그것을 하지 않았다 (docs/infra.md 25.93).
    """

    @staticmethod
    def _무리(회복: list[float | None]) -> list[sc.StockInput]:
        """리스크 지표를 다 채운 종목들. 회복 기간만 달리한다."""
        기본 = {
            "mdd_abs": 0.3, "volatility_ann": 0.25, "sharpe": 0.8, "sortino": 1.0,
            "beta_abs": 1.1, "cagr": 0.12, "amihud_illiquidity": 0.1,
            "max_daily_return": 0.05, "idio_volatility": 0.2,
        }
        return [
            sc.StockInput(stock_id=i + 1, market="KOSPI", metrics={**기본, "mdd_recovery_days": v})
            for i, v in enumerate(회복)
        ]

    def _리스크(self, 회복: list[float | None]) -> list[sc.FactorResult]:
        결과 = sc.score_factors(self._무리(회복), min_size=99)
        return [r for r in 결과 if r.factor == "risk"]

    def test_치환한_종목의_raw_에_사실이_남는다(self) -> None:
        리스크 = self._리스크([10.0, 20.0, None])

        섞인것 = 리스크[2].raw[sc.SUBSTITUTED_KEY]
        assert 섞인것 == {"mdd_recovery_days": 20.0}, "낮을수록 좋은 지표라 최악은 최댓값이다"

    def test_원래_값은_여전히_null_이다(self) -> None:
        """**치환값을 raw 에 덮어쓰지 않는다.** 그러면 그 종목이 아는 값처럼 보인다."""
        리스크 = self._리스크([10.0, 20.0, None])

        assert 리스크[2].raw["mdd_recovery_days"] is None

    def test_치환되지_않은_종목에는_없다(self) -> None:
        리스크 = self._리스크([10.0, 20.0, None])

        assert sc.SUBSTITUTED_KEY not in 리스크[0].raw
        assert sc.SUBSTITUTED_KEY not in 리스크[1].raw

    def test_다른_팩터에는_안_붙는다(self) -> None:
        """치환은 리스크의 한 지표에서만 일어난다. 밸류 근거표를 어지럽히지 않는다."""
        결과 = sc.score_factors(self._무리([10.0, 20.0, None]), min_size=99)

        다른것 = [r for r in 결과 if r.factor != "risk"]
        assert all(sc.SUBSTITUTED_KEY not in r.raw for r in 다른것)

    def test_집단이_통째로_비면_치환하지_않는다(self) -> None:
        """대신 쓸 값이 없다. **없는 값을 지어내지 않는다**."""
        리스크 = self._리스크([None, None, None])

        assert all(sc.SUBSTITUTED_KEY not in r.raw for r in 리스크)
        assert all("mdd_recovery_days" in r.missing_fields for r in 리스크)

    def test_치환하면_빠짐에는_안_들어간다(self) -> None:
        """이것이 문제의 뿌리다 — 그래서 치환 기록이 필요하다."""
        리스크 = self._리스크([10.0, 20.0, None])

        assert "mdd_recovery_days" not in 리스크[2].missing_fields
        assert 리스크[2].score is not None

    def test_최악값을_고르는_규칙이_한_곳에_있다(self) -> None:
        """`zscores` 와 `score_factors` 가 같은 함수를 부른다 — 각자 세면 갈라진다."""
        assert sc.worst_substitute([10.0, 20.0, None], higher_is_better=False) == 20.0
        assert sc.worst_substitute([10.0, 20.0, None], higher_is_better=True) == 10.0
        assert sc.worst_substitute([None, None], higher_is_better=True) is None

    def test_예약_키가_지표_이름과_안_겹친다(self) -> None:
        이름 = {m.name for ms in sc.FACTOR_METRICS.values() for m in ms}

        assert sc.SUBSTITUTED_KEY.startswith("_")
        assert sc.SUBSTITUTED_KEY not in 이름


class Test범위_밖_가중치로는_점수를_내지_않는다:
    """**문서가 0~100 을 약속한다** (docs/factors.md 5장, docs/infra.md 25.169).

    `web/lib/settings.ts` 머리말은 "설정은 웹앱만 쓴다. 배치는 읽기만 한다. 따라서
    검증은 여기 한 곳에 둔다" 고 적는다. 그 전제가 **복구·이주 경로에서 깨진다** —
    `restore_backup` 과 `move_user_data` 는 `settings` 행을 검증 없이 써 넣는다.

    그때 조용히 나오던 값이 이랬다.
      `sentiment_weight = 150`  →  종합 점수 **−40**
      `value` 가중치 −50        →  정규화 가중치 −45%, **좋은 값이 점수를 깎는다**
    """

    가중치 = dict.fromkeys(sc.FACTORS, 20.0)
    점수 = dict.fromkeys(sc.FACTORS, 80.0)

    def test_멀쩡한_값은_그대로_낸다(self) -> None:
        assert sc.total_score(self.점수, self.가중치).total == 80.0
        # 80 + (0−50)×1 = 30 (25.639 — 예전 식은 0)
        assert sc.total_score(self.점수, self.가중치, sentiment=-100.0, sentiment_weight=100).total == 30.0
        assert sc.total_score(self.점수, self.가중치, sentiment=100.0, sentiment_weight=100).total == 100.0  # 잘린다

    def test_센티먼트_가중치가_100을_넘으면_내지_않는다(self) -> None:
        t = sc.total_score(self.점수, self.가중치, sentiment=-100.0, sentiment_weight=150)

        assert t.total is None, "예전에는 −40 이 나왔다"
        assert sc.SKIP_BAD_WEIGHTS in (t.skip_reason or "")
        assert "sentiment" in (t.skip_reason or ""), "무엇이 잘못됐는지 말해야 한다"

    def test_음수_팩터_가중치면_내지_않는다(self) -> None:
        나쁜것 = {**self.가중치, "value": -50.0}
        t = sc.total_score(self.점수, 나쁜것)

        assert t.total is None
        assert "value" in (t.skip_reason or "")

    def test_경계는_통과한다(self) -> None:
        for w in (sc.WEIGHT_MIN, sc.WEIGHT_MAX):
            낱개 = {**self.가중치, "value": w}
            if w == sc.WEIGHT_MIN:
                낱개["quality"] = 100.0  # 합이 0 이 되지 않게
            assert sc.total_score(self.점수, 낱개).skip_reason is None, f"{w} 는 범위 안이다"

    def test_살아_있지_않은_팩터의_가중치는_보지_않는다(self) -> None:
        """결측 팩터는 어차피 안 쓰인다. 그 값 때문에 멀쩡한 점수를 버리지 않는다."""
        점수 = {**self.점수, "value": None}
        나쁜것 = {**self.가중치, "value": -999.0}

        assert sc.total_score(점수, 나쁜것).total is not None

    def test_문턱이_웹_스키마와_같은_값이다(self) -> None:
        """**설정의 단일 정의처는 `settings.ts` 다.** 두 수가 갈라지면 화면이 받는 값을
        배치가 거부하거나, 그 반대가 된다."""
        글 = (ROOT / "web" / "lib" / "settings.ts").read_text(encoding="utf-8")
        m = re.search(r"const percent = z\.number\(\)\.min\((\d+)\)\.max\((\d+)\)", 글)

        assert m, "web/lib/settings.ts 의 `percent` 정의를 못 읽었다"
        assert (float(m.group(1)), float(m.group(2))) == (sc.WEIGHT_MIN, sc.WEIGHT_MAX)


def test_작은_집단에서도_윈저라이즈가_한_종목은_자른다() -> None:
    """docs/infra.md 25.245 — 1% 는 100 종목 이하에서 아무것도 안 잘랐다(업종 집단은 30 부터 쓴다)."""
    from batch.services import scoring as sc

    값 = [float(i) for i in range(1, 60)] + [1000.0]
    잘린 = sc.winsorize(값)  # 운영값 WINSOR_PCT = 0.01
    assert max(잘린) == 59.0 and min(잘린) == 2.0
    assert sc.winsorize([1.0, 2.0]) == [1.0, 2.0]  # 세 종목 미만은 자를 것이 없다


def test_작은_업종에서_올라간_종목은_시장_전체와_비교된다() -> None:
    """업종 A 40·B 10·C 5 — B·C 15종목이 서로만 비교되던 것을 시장 55종목과 비교한다 (docs/infra.md 25.307)."""
    종목 = [make_stock(i, "KOSPI", "A", 100 + i) for i in range(40)]
    종목 += [make_stock(100 + i, "KOSPI", "B", i) for i in range(10)]
    종목 += [make_stock(200 + i, "KOSPI", "C", 10 + i) for i in range(5)]
    결과 = {(r.stock_id, r.factor): r for r in sc.score_factors(종목)}
    올라간_최고 = 결과[(204, "momentum")]  # B·C 중 가장 좋은 종목
    assert 올라간_최고.peer_group == "market:KOSPI" and 올라간_최고.peer_size == 55
    # 서로만 비교하면 15종목 중 1등이라 높은 점수였다. 시장 전체에선 A 40종목보다 아래다
    assert 올라간_최고.score is not None and 올라간_최고.score < 50
    # 업종 A 는 제 집단(40)에서 그대로
    assert 결과[(0, "momentum")].peer_group == "sector:KOSPI:A" and 결과[(0, "momentum")].peer_size == 40



def test_값이_적은_지표는_윈저라이즈가_순서를_지우지_않는다() -> None:
    """값 셋이면 셋 다 중앙값으로 잘려 z 가 전부 0 이었다 (docs/infra.md 25.309)."""
    z = sc.zscores([1.0, 2.0, 100.0], higher_is_better=True)
    assert z[0] < z[1] < z[2]
    # 10개 이상이면 예전대로 한쪽씩 자른다
    assert sc.winsorize([float(i) for i in range(9)] + [1000.0]) == [1.0] + [float(i) for i in range(1, 9)] + [8.0]


def test_리스크_팩터가_쓴_성과_지표의_창을_남긴다() -> None:
    """문서는 "어느 창을 썼는지 raw_json 에 남긴다" 인데 코드는 버렸다 (docs/infra.md 25.383)."""
    from pathlib import Path

    원본 = (Path(__file__).resolve().parents[1] / "batch" / "services" / "scoring.py").read_text(encoding="utf-8")
    assert 'if factor == "risk" and stock.risk_source:' in 원본
    잡 = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "scores.py").read_text(encoding="utf-8")
    assert '"window": metric_row.get("window")' in 잡
    assert sc.RISK_SOURCE_KEY == "_metrics_source"

    기본 = {"mdd_abs": 0.3, "volatility_ann": 0.25, "sharpe": 0.8}
    무리 = [
        sc.StockInput(stock_id=i + 1, market="KOSPI", metrics=기본, risk_source={"window": "1Y", "as_of_date": "2026-09-25"})
        for i in range(3)
    ]
    결과 = [r for r in sc.score_factors(무리, min_size=99) if r.factor == "risk"]
    assert 결과[0].raw[sc.RISK_SOURCE_KEY] == {"window": "1Y", "as_of_date": "2026-09-25"}
    assert all(sc.RISK_SOURCE_KEY not in r.raw for r in sc.score_factors(무리, min_size=99) if r.factor == "value")
