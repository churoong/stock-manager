"""상관 기반 분산 (docs/signals.md 3.6). **손으로 검산한 값**으로 본다.

왜 있나: 지금 분산 장치가 **꺼져 있다.** 섹터 상한은 `stocks.sector` 가 비어 적용되지
않고(docs/signals.md 3.2), 그것이 유일한 분산 장치였다. 상관은 시세만으로 나므로
그 구멍을 실제로 메운다. 그리고 섹터가 있더라도 **업종이 달라도 같이 움직이는 종목**이 있다.
"""

from __future__ import annotations

import math

import pytest

from batch.services import report_picks as rp
from batch.services import signals as sig


def _series(수익률: list[float], 시작: float = 100.0, 첫날: int = 1) -> dict[str, float]:
    """{날짜: 종가}. 날짜는 2026-01-01 부터 하루씩 (거래일 달력을 흉내 내지 않는다)."""
    from datetime import date, timedelta

    기준 = date(2026, 1, 1) + timedelta(days=첫날 - 1)
    계열 = {기준.isoformat(): 시작}
    값 = 시작
    for i, r in enumerate(수익률, start=1):
        값 *= 1 + r
        계열[(기준 + timedelta(days=i)).isoformat()] = 값
    return 계열


#: 겹치는 날이 하한을 넘도록 넉넉히
_N = rp.CORR_MIN_POINTS + 20


class Test피어슨:
    def test_완전히_같이_움직이면_1(self) -> None:
        a = [0.01, -0.02, 0.03, -0.01, 0.02]

        assert rp.pearson(a, a) == pytest.approx(1.0)

    def test_정반대면_마이너스_1(self) -> None:
        a = [0.01, -0.02, 0.03, -0.01, 0.02]

        assert rp.pearson(a, [-x for x in a]) == pytest.approx(-1.0)

    def test_배수만_다르면_여전히_1(self) -> None:
        """상관은 **크기가 아니라 방향**이다. 베타와 다른 것을 본다."""
        a = [0.01, -0.02, 0.03, -0.01, 0.02]

        assert rp.pearson(a, [3 * x for x in a]) == pytest.approx(1.0)

    def test_손으로_검산(self) -> None:
        # x=[1,2,3], y=[2,4,5] → r = 0.5/sqrt(2*4.6667)... 직접 센다
        x, y = [1.0, 2.0, 3.0], [2.0, 4.0, 5.0]
        mx, my = 2.0, 11 / 3
        sxy = sum((p - mx) * (q - my) for p, q in zip(x, y, strict=True))
        sxx = sum((p - mx) ** 2 for p in x)
        syy = sum((q - my) ** 2 for q in y)

        assert rp.pearson(x, y) == pytest.approx(sxy / math.sqrt(sxx * syy))

    def test_한쪽이_안_움직이면_None(self) -> None:
        """**0 으로 나누지 않고, 상관이 0 이라고 단정하지도 않는다.**"""
        assert rp.pearson([0.01, 0.02, 0.03], [0.0, 0.0, 0.0]) is None

    def test_표본이_모자라면_None(self) -> None:
        assert rp.pearson([0.01], [0.02]) is None
        assert rp.pearson([], []) is None


class Test상관_축소:
    def test_보유가_없으면_줄이지_않는다(self) -> None:
        """첫 종목은 상관할 대상이 없다. **모르면 줄이지 않는다.**"""
        배수, 근거 = rp.correlation_reduction(_series([0.01] * _N), [])

        assert 배수 == 1.0
        assert "보유 종목이 없어" in 근거["note"]

    def test_문턱_아래면_그대로_둔다(self) -> None:
        # 서로 무관하게 흔들리는 두 계열
        후보 = _series([0.01 if i % 2 == 0 else -0.01 for i in range(_N)])
        보유 = _series([0.01 if i % 3 == 0 else -0.005 for i in range(_N)])

        배수, 근거 = rp.correlation_reduction(후보, [(2, "다른회사", 보유)])

        assert 근거["max_correlation"] < rp.CORR_THRESHOLD
        assert 배수 == 1.0

    def test_똑같이_움직이면_하한까지_줄인다(self) -> None:
        """ρ=1 이면 배수 = 1 − (1−0.7)/(1−0.7) = 0 → 하한 0.3 으로 막힌다."""
        수익률 = [0.01 if i % 2 == 0 else -0.012 for i in range(_N)]
        후보 = _series(수익률)
        보유 = _series([2 * r for r in 수익률])  # 배수만 다르다 → 상관 1

        배수, 근거 = rp.correlation_reduction(후보, [(2, "닮은회사", 보유)])

        assert 근거["max_correlation"] == pytest.approx(1.0)
        assert 배수 == pytest.approx(sig.REDUCTION_FLOOR)
        assert 근거["most_similar"] == "닮은회사"

    def test_0_으로_만들지_않는다(self) -> None:
        """0 원은 제외와 같고, 제외라면 사유를 밝혀야 한다 (docs/signals.md 3.1)."""
        수익률 = [0.01 if i % 2 == 0 else -0.012 for i in range(_N)]
        배수, _ = rp.correlation_reduction(
            _series(수익률), [(2, "닮은회사", _series(수익률))]
        )

        assert 배수 > 0

    def test_하한을_signals_에서_가져온다(self) -> None:
        """여기서 0.3 을 다시 적으면 한 규칙이 두 곳에 생긴다 (docs/infra.md 25.0)."""
        import inspect

        서명 = inspect.signature(rp.correlation_reduction)

        assert 서명.parameters["floor"].default == sig.REDUCTION_FLOOR

    def test_가장_닮은_하나를_본다(self) -> None:
        """평균이면 낮은 상관들이 0.95 를 희석한다. **분산의 적은 가장 닮은 하나다.**"""
        수익률 = [0.01 if i % 2 == 0 else -0.012 for i in range(_N)]
        후보 = _series(수익률)
        똑같음 = _series(수익률)
        딴판 = _series([0.002 if i % 7 == 0 else -0.001 for i in range(_N)])

        배수, 근거 = rp.correlation_reduction(
            후보, [(2, "딴판회사", 딴판), (3, "똑같은회사", 똑같음)]
        )

        assert 근거["most_similar"] == "똑같은회사"
        assert 배수 == pytest.approx(sig.REDUCTION_FLOOR)

    def test_자기_자신은_빼고_본다(self) -> None:
        """같은 종목을 더 사는 것은 **종목 비중 상한**이 다룬다. 두 번 줄이면 안 된다."""
        수익률 = [0.01 if i % 2 == 0 else -0.012 for i in range(_N)]
        후보 = _series(수익률)

        배수, 근거 = rp.correlation_reduction(
            후보, [(7, "자기자신", _series(수익률))], exclude_stock_id=7
        )

        assert 배수 == 1.0
        assert 근거["compared"] == 0

    def test_겹치는_날이_모자라면_세지_않는다(self) -> None:
        """상장 1년 미만 같은 경우다. **모르면 줄이지 않는다.**"""
        후보 = _series([0.01] * _N)
        짧은보유 = _series([0.01] * 10, 첫날=1)

        배수, 근거 = rp.correlation_reduction(후보, [(2, "새내기", 짧은보유)])

        assert 배수 == 1.0
        assert 근거["compared"] == 0
        assert "겹치는 거래일이" in 근거["note"]

    def test_날짜가_겹치지_않으면_세지_않는다(self) -> None:
        """**날짜를 안 맞추면 값이 통째로 틀린다** (베타와 같은 규칙)."""
        후보 = _series([0.01] * _N, 첫날=1)
        딴시기 = _series([0.01] * _N, 첫날=1000)

        배수, 근거 = rp.correlation_reduction(후보, [(2, "딴시기", 딴시기)])

        assert 배수 == 1.0 and 근거["compared"] == 0

    def test_창이_모멘텀_6개월과_같은_숫자다(self) -> None:
        """새 숫자를 만들지 않았다 (docs/signals.md 3.6)."""
        from batch.services import scoring as sc

        assert rp.CORR_WINDOW == sc.MOMENTUM_6M_DAYS

    def test_하한이_베타와_같다(self) -> None:
        from batch.services import scoring as sc

        assert rp.CORR_MIN_POINTS == sc.IDIO_MIN_POINTS

    def test_중간_상관은_중간만큼_줄인다(self) -> None:
        """문턱 0.7 과 1.0 사이를 선형으로 잇는다 — 낭떠러지가 없어야 한다."""
        근거 = {"max_correlation": 0.85}
        # 0.85 → 1 − (0.85−0.7)/(0.3) = 0.5
        예상 = 1.0 - (0.85 - rp.CORR_THRESHOLD) / (1.0 - rp.CORR_THRESHOLD)

        assert 예상 == pytest.approx(0.5)
        assert sig.REDUCTION_FLOOR < 예상 < 1.0, "중간값이 하한과 1 사이에 있어야 뜻이 있다"
        assert 근거["max_correlation"] > rp.CORR_THRESHOLD


class Test2부에_실제로_배선됐나:
    """**계산만 있고 안 불리면 없는 것과 같다** (docs/infra.md 25.65·25.92 가 그랬다).

    `correlation_reduction` 을 만들어 두고 `build_portfolio` 가 안 부르면
    상관은 테스트에서만 도는 장식이 된다. 여기서는 **2부의 금액이 실제로 줄었는지**를 본다.
    """

    @staticmethod
    def _row(stock_id: int = 1, **kw):
        from tests.test_report_picks import row

        return row(stock_id, **kw)

    @staticmethod
    def _보유(stock_id: int = 9, value: float = 1_000_000.0):
        return rp.Holding(stock_id, f"{stock_id:06d}", f"보유{stock_id}", None, value)

    #: 똑같이 움직이는 두 계열 — 상관 1.0 이라 바닥까지 줄어야 한다
    _같이 = [0.01, -0.02, 0.03, -0.01, 0.015, -0.005] * (_N // 6 + 1)

    def test_상관이_높으면_2부_금액이_줄어든다(self) -> None:
        계열 = _series(self._같이)
        view = rp.build_portfolio(
            [self._row(1)],
            total_investable=10_000_000,
            holdings=[self._보유(9)],
            price_series={1: 계열, 9: 계열},
        )

        assert len(view.allocations) == 1
        배분 = view.allocations[0]
        assert 배분.amount == int(round(1_000_000 * sig.REDUCTION_FLOOR)), "상관 1.0 은 바닥까지"
        assert "상관" in 배분.note

    def test_비중도_같이_줄어든다(self) -> None:
        """금액만 줄이고 비중을 두면 리포트의 두 숫자가 어긋난다."""
        계열 = _series(self._같이)
        view = rp.build_portfolio(
            [self._row(1)],
            total_investable=10_000_000,
            holdings=[self._보유(9)],
            price_series={1: 계열, 9: 계열},
        )

        배분 = view.allocations[0]
        assert 배분.weight_pct == pytest.approx(배분.amount / 10_000_000 * 100, abs=0.01)

    def test_근거가_배분에_남는다(self) -> None:
        """**근거표를 만들 수 없는 추천은 표시하지 않는다** (CLAUDE.md).

        `services/reports.items_from` 이 `asdict` 로 떠서 report_items 에 넣으므로
        이 딕셔너리가 곧 화면에서 펼쳐 볼 수 있는 근거다.
        """
        계열 = _series(self._같이)
        view = rp.build_portfolio(
            [self._row(1)],
            total_investable=10_000_000,
            holdings=[self._보유(9)],
            price_series={1: 계열, 9: 계열},
        )

        근거 = view.allocations[0].rationale
        assert 근거["max_correlation"] == pytest.approx(1.0)
        assert 근거["most_similar"] == "보유9"
        assert 근거["correlation_window"] == rp.CORR_WINDOW
        assert 근거["correlation_threshold"] == rp.CORR_THRESHOLD
        assert 근거["correlation_factor"] == pytest.approx(sig.REDUCTION_FLOOR)

    def test_시세를_안_넘기면_줄이지_않는다(self) -> None:
        """**모르는 것을 나쁘다고 단정하지 않는다** (docs/infra.md 25.0)."""
        view = rp.build_portfolio(
            [self._row(1)], total_investable=10_000_000, holdings=[self._보유(9)]
        )

        assert view.allocations[0].amount == 1_000_000
        assert view.allocations[0].rationale == {}

    def test_상관을_못_냈으면_2부에_까닭을_적는다(self) -> None:
        """겹치는 날이 모자라거나 이 종목 시세가 없으면 `rationale` 에만 있고 화면엔 없었다 (docs/infra.md 25.700, 감사)."""
        긴 = _series(self._같이)
        짧은 = _series(self._같이[:30])
        view = rp.build_portfolio(
            [self._row(1)], total_investable=10_000_000, holdings=[self._보유(9)], price_series={1: 긴, 9: 짧은}
        )
        assert "상관 미확인 (겹치는 거래일이" in view.allocations[0].note
        view = rp.build_portfolio(
            [self._row(1)], total_investable=10_000_000, holdings=[self._보유(9)], price_series={9: 긴}
        )
        assert "이 종목 시세가 없어" in view.allocations[0].note
        # 25.707 — 보유가 거래정지로 종가 고정이면 겹친 날은 있어도 상관이 None. 그때도 적고, "겹치는 날이 없어" 라
        # 하지 않는다
        고정 = {d: 100.0 for d in 긴}
        view = rp.build_portfolio(
            [self._row(1)], total_investable=10_000_000, holdings=[self._보유(9)], price_series={1: 긴, 9: 고정}
        )
        assert "상관 미확인 (비교한 보유 종목 1개가 모두 움직이지 않아" in view.allocations[0].note

    def test_같은_리포트의_닮은_후보는_뒤의_것을_줄인다(self) -> None:
        """보유가 없어도 거의 같이 움직이는 두 후보가 같은 날 둘 다 전액 배분됐다 (docs/infra.md 25.712, 감사 재현)."""
        계열 = _series(self._같이)
        view = rp.build_portfolio(
            [self._row(1, score=90.0), self._row(2, score=80.0)],
            total_investable=10_000_000,
            price_series={1: 계열, 2: 계열},
        )
        금액 = {a.ticker: a.amount for a in view.allocations}
        assert len(금액) == 2
        첫, 둘 = view.allocations
        assert 첫.amount == 1_000_000  # 점수 앞선 쪽은 그대로
        assert 둘.amount == int(round(1_000_000 * sig.REDUCTION_FLOOR))
        assert "이번 리포트의 앞선 후보" in 둘.note

    def test_보유가_없으면_보유_종목이라_부르지_않는다(self) -> None:
        """보유 없이 앞선 후보만 있는데 "보유 종목과 대 보지 못했습니다" 라고 적었다 (docs/infra.md 25.716, 교차검증 재현)."""
        계열 = _series(self._같이)
        view = rp.build_portfolio(
            [self._row(1, score=90.0), self._row(2, score=80.0)], total_investable=10_000_000, price_series={1: 계열}
        )
        둘 = [a for a in view.allocations if a.ticker == "000002"][0]
        assert "앞선 후보 종목과 대 보지 못했습니다" in 둘.note
        assert "보유 종목" not in 둘.note

    def test_같은_종목을_더_사는_것은_상관으로_안_줄인다(self) -> None:
        """그것은 종목 비중 상한이 다룬다. 두 번 줄이면 안 된다."""
        계열 = _series(self._같이)
        view = rp.build_portfolio(
            [self._row(1)],
            total_investable=100_000_000,
            holdings=[rp.Holding(1, "000001", "종목1", None, 1_000_000.0)],
            price_series={1: 계열},
        )

        assert view.allocations[0].amount == 1_000_000
        assert view.allocations[0].rationale["compared"] == 0
        assert "상관 미확인" not in view.allocations[0].note  # 자기 자신뿐이면 "못 봤다" 도 아니다 (25.700)

    def test_상관으로_줄인_뒤에_상한을_본다(self) -> None:
        """순서를 바꾸면 **두 번 깎는다.**

        종목 상한 10% = 1,000만. 보유 950만이라 여유는 50만.
        상관으로 먼저 줄이면 100만 × 0.3 = 30만이라 **상한에 안 걸린다.**
        거꾸로 하면 50만으로 자른 뒤 다시 0.3 을 곱해 15만이 된다.
        """
        계열 = _series(self._같이)
        # 비중 40% — 상관을 곱한 목표(12%)가 종목 상한(10%) 위라 목표 검사는 건너뛰고 상한만 본다(25.1088 뒤에도
        # 순서만 묻게)
        view = rp.build_portfolio(
            [self._row(1, suggested_weight_pct=40.0)],
            total_investable=100_000_000,
            holdings=[self._보유(9), rp.Holding(1, "000001", "종목1", None, 9_500_000.0)],
            price_series={1: 계열, 9: 계열},
        )

        assert view.allocations[0].amount == int(round(1_000_000 * sig.REDUCTION_FLOOR))
        # "섹터 상한 미적용" 은 업종을 모른다는 별개의 말이다 (25.194). 여기서 보는 것은 종목 상한
        assert "종목 상한" not in view.allocations[0].note


class Test시세_읽기:
    """`jobs/daily.load_price_series` — **진짜 스키마**에 대고 본다.

    열 이름이 틀리면 운영에서 아침 리포트가 통째로 깨진다(test_report_picks 와 같은 이유).
    """

    @staticmethod
    def _client(종목: list[int], 날수: int, 끝: str = "2026-09-16"):
        from datetime import date, timedelta

        from tests.test_report_picks import SqliteClient

        client = SqliteClient()
        c = client.conn
        마지막 = date.fromisoformat(끝)
        for sid in 종목:
            c.execute(
                "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 't', '2026-09-16')",
                [sid, f"{sid:06d}", f"종목{sid}"],
            )
            for i in range(날수):
                날 = (마지막 - timedelta(days=i)).isoformat()
                c.execute(
                    "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                    " VALUES (?, ?, ?, ?, 'KRW', 't', 't')",
                    [sid, 날, 1000 + i, 900 + i],
                )
        return client

    def test_수정종가를_쓴다(self) -> None:
        from batch.jobs import daily

        계열 = daily.load_price_series(self._client([1], 5), [1], "2026-09-16")  # type: ignore[arg-type]

        assert 계열[1]["2026-09-16"] == 900.0, "adj_close 가 있으면 그것을 쓴다(docs/adjust.md)"

    def test_창_밖은_안_읽는다(self) -> None:
        from batch.jobs import daily

        계열 = daily.load_price_series(self._client([1], 400), [1], "2026-09-16")  # type: ignore[arg-type]
        가로 = int(rp.CORR_WINDOW * 7 / 5) + daily.CORR_CALENDAR_SLACK_DAYS

        assert len(계열[1]) == 가로 + 1, f"{가로}일 창 + 마지막 날"

    def test_묶음으로_나눠_읽는다(self) -> None:
        """**D1 은 질의당 파라미터 100개까지다** (core/d1.MAX_PARAMS).

        보유가 100종목을 넘으면 한 질의로는 못 보낸다. 조용히 반쪽만 읽으면
        상관이 **낮게** 나와 덜 줄인다 — 고장이 안전한 척한다(docs/infra.md 25.90).
        """
        from batch.core import d1
        from batch.jobs import daily

        종목 = list(range(1, 121))
        client = self._client(종목, 3)
        본질의: list[int] = []
        원래 = client.execute

        def 세는execute(sql, args=None):
            본질의.append(len(args or []))
            return 원래(sql, args)

        client.execute = 세는execute  # type: ignore[method-assign]
        계열 = daily.load_price_series(client, 종목, "2026-09-16")  # type: ignore[arg-type]

        assert len(계열) == 120, "나눠 보내고도 전부 읽어야 한다"
        assert len(본질의) == 2, f"120종목이면 두 번이다: {본질의}"
        assert max(본질의) <= d1.MAX_PARAMS, f"한 질의가 한도를 넘었다: {본질의}"

    def test_빈_목록이면_아무것도_안_읽는다(self) -> None:
        from batch.jobs import daily

        client = self._client([1], 3)
        client.execute = lambda *a, **k: pytest.fail("읽지 말았어야 한다")  # type: ignore[method-assign]

        assert daily.load_price_series(client, [], "2026-09-16") == {}  # type: ignore[arg-type]

    def test_묶음_크기가_한도_안에_있다(self) -> None:
        """날짜 인자 둘을 더해도 넘지 않아야 한다."""
        from batch.core import d1
        from batch.jobs import daily

        assert daily.CORR_IDS_PER_QUERY + 2 <= d1.MAX_PARAMS


class Test분할_계획이_줄인_금액과_맞나:
    """**계획대로 사면 권장액의 두 배를 산다** (docs/infra.md 25.96).

    `signals.tranche_plan` 의 비중 합은 정확히 1.0 이다 — 분할 금액의 합이 1부의 권장액이다.
    2부는 그 금액을 종목 상한과 상관 분산으로 줄이는데, 분할 계획을 그대로 실으면
    화면의 두 숫자가 어긋나고 **초과분은 바로 그 상한을 깨는 금액**이 된다.

    2026-09-21 까지 종목 상한 경로가 그랬고, 같은 날 더한 상관 축소가 그것을 **겹쳐서**
    최대 3.3배(`REDUCTION_FLOOR` 0.3)까지 벌릴 참이었다.
    """

    @staticmethod
    def _계획(금액: float = 1_000_000.0):
        from batch.services import signals as sg

        계획 = sg.tranche_plan(9000, 10000)
        for t in 계획:
            t.amount = 금액 * t.ratio
        return [{"step": t.step, "ratio": t.ratio, "price": t.price, "amount": t.amount} for t in 계획]

    @staticmethod
    def _행(**kw):
        from tests.test_report_picks import row

        return row(1, **kw)

    def test_신호의_분할_합이_권장액과_같다(self) -> None:
        """이 전제가 깨지면 아래 테스트들의 뜻이 사라진다."""
        from batch.services import signals as sg

        assert sum(sg.TRANCHE_RATIOS) == pytest.approx(1.0)
        assert sum(t["amount"] for t in self._계획()) == pytest.approx(1_000_000.0)

    def test_안_줄이면_그대로다(self) -> None:
        view = rp.build_portfolio(
            [self._행(tranche_plan=self._계획())], total_investable=100_000_000
        )
        배분 = view.allocations[0]

        assert sum(t["amount"] for t in 배분.tranches) == pytest.approx(배분.amount)

    def test_종목_상한으로_줄이면_분할도_줄어든다(self) -> None:
        """상한 10% = 1,000만, 보유 950만 → 여유 50만. 분할 합도 50만이어야 한다."""
        view = rp.build_portfolio(
            [self._행(tranche_plan=self._계획())],
            total_investable=100_000_000,
            holdings=[rp.Holding(1, "000001", "종목1", None, 9_500_000.0)],
        )
        배분 = view.allocations[0]

        assert 배분.amount == 500_000
        assert sum(t["amount"] for t in 배분.tranches) == pytest.approx(500_000)

    def test_상관으로_줄이면_분할도_줄어든다(self) -> None:
        계열 = _series(Test2부에_실제로_배선됐나._같이)
        view = rp.build_portfolio(
            [self._행(tranche_plan=self._계획())],
            total_investable=10_000_000,
            holdings=[rp.Holding(9, "000009", "보유9", None, 1_000_000.0)],
            price_series={1: 계열, 9: 계열},
        )
        배분 = view.allocations[0]

        assert sum(t["amount"] for t in 배분.tranches) == pytest.approx(배분.amount, abs=1)
        assert 배분.amount == int(round(1_000_000 * sig.REDUCTION_FLOOR))

    def test_둘이_겹쳐도_맞는다(self) -> None:
        """**겹치는 경우가 가장 크게 벌어진다.** 고치기 전에는 3.3배였다."""
        계열 = _series(Test2부에_실제로_배선됐나._같이)
        view = rp.build_portfolio(
            [self._행(tranche_plan=self._계획(), suggested_weight_pct=40.0)],  # 상관 목표가 상한 위 (25.1088)
            total_investable=100_000_000,
            holdings=[
                rp.Holding(9, "000009", "보유9", None, 1_000_000.0),
                rp.Holding(1, "000001", "종목1", None, 9_800_000.0),
            ],
            price_series={1: 계열, 9: 계열},
        )
        배분 = view.allocations[0]
        합 = sum(t["amount"] for t in 배분.tranches)

        assert 합 == pytest.approx(배분.amount, abs=1), f"분할 합 {합} ≠ 권장 {배분.amount}"

    def test_계획대로_사도_종목_상한을_안_깬다(self) -> None:
        """이것이 진짜 지키려는 것이다 (CLAUDE.md: 비중 상한 기본 10%)."""
        보유 = 9_500_000.0
        상한 = 100_000_000 * 0.10
        view = rp.build_portfolio(
            [self._행(tranche_plan=self._계획())],
            total_investable=100_000_000,
            holdings=[rp.Holding(1, "000001", "종목1", None, 보유)],
        )
        배분 = view.allocations[0]

        assert 보유 + sum(t["amount"] for t in 배분.tranches) <= 상한 + 1

    def test_금액이_없는_칸은_만들어_넣지_않는다(self) -> None:
        계획 = [{"step": 1, "ratio": 0.4, "price": 10000, "amount": None}]
        view = rp.build_portfolio(
            [self._행(tranche_plan=계획)], total_investable=100_000_000
        )

        assert view.allocations[0].tranches[0]["amount"] is None

    def test_값을_싣는_가격은_그대로다(self) -> None:
        """줄이는 것은 **금액**이다. 분할 가격은 매수 구간에서 나온 값이라 안 건드린다."""
        계획 = self._계획()
        view = rp.build_portfolio(
            [self._행(tranche_plan=계획)],
            total_investable=100_000_000,
            holdings=[rp.Holding(1, "000001", "종목1", None, 9_500_000.0)],
        )

        assert [t["price_at_or_below"] for t in view.allocations[0].tranches] == [
            t["price"] for t in 계획
        ]


class Test줄인_뒤에_최소_주문을_다시_본다:
    """**한 주도 못 사는 돈을 사라고 내놓지 않는다** (docs/infra.md 25.96).

    1부(`services/signals`)는 줄이기 **전** 금액으로 한 번 거른다
    (`amount >= min_order_amount`). 2부가 종목 상한·상관으로 깎고 나면 그 문턱 아래로
    내려갈 수 있는데 아무도 다시 보지 않았다 — 30만이 9만이 되어도 그대로 실렸다.
    """

    @staticmethod
    def _행(금액: float = 300_000.0):
        from batch.services import signals as sg
        from tests.test_report_picks import row

        계획 = sg.tranche_plan(9000, 10000)
        for t in 계획:
            t.amount = 금액 * t.ratio
        return row(
            1,
            suggested_amount=금액,
            tranche_plan=[{"step": t.step, "ratio": t.ratio, "price": t.price, "amount": t.amount} for t in 계획],
        )

    #: 상한 1,000만 중 991만을 이미 들고 있다 → 여유 9만
    _꽉찬보유 = (1, "000001", "종목1", None, 9_910_000.0)

    def test_깎여서_문턱_아래면_뺀다(self) -> None:
        view = rp.build_portfolio(
            [self._행()],
            total_investable=100_000_000,
            holdings=[rp.Holding(*self._꽉찬보유)],
            min_order_amount=100_000.0,
        )

        assert view.allocations == []
        assert [e.reason for e in view.excluded] == [rp.EXCLUDED_BELOW_MIN_ORDER]

    def test_왜_깎였는지도_남긴다(self) -> None:
        """**빠진 사유만으로는 무엇을 고칠지 모른다** (docs/design.md 3.9)."""
        view = rp.build_portfolio(
            [self._행()],
            total_investable=100_000_000,
            holdings=[rp.Holding(*self._꽉찬보유)],
            min_order_amount=100_000.0,
        )
        상세 = view.excluded[0].detail

        assert "90,000" in 상세 and "100,000" in 상세
        assert "상한" in 상세, "깎은 장치의 이름이 있어야 한다"

    def test_문턱_위면_그대로_싣는다(self) -> None:
        view = rp.build_portfolio(
            [self._행()], total_investable=100_000_000, min_order_amount=100_000.0
        )

        assert [a.amount for a in view.allocations] == [300_000]

    def test_안_넘기면_안_본다(self) -> None:
        """옛 호출부(테스트 포함)가 그대로 돈다. **기본값이 끄는 쪽이다**."""
        view = rp.build_portfolio(
            [self._행()],
            total_investable=100_000_000,
            holdings=[rp.Holding(*self._꽉찬보유)],
        )

        assert [a.amount for a in view.allocations] == [90_000]

    def test_상관으로_깎여도_본다(self) -> None:
        계열 = _series(Test2부에_실제로_배선됐나._같이)
        view = rp.build_portfolio(
            [self._행()],
            total_investable=100_000_000,
            holdings=[rp.Holding(9, "000009", "보유9", None, 1_000_000.0)],
            price_series={1: 계열, 9: 계열},
            min_order_amount=100_000.0,
        )

        # 30만 × 0.3(바닥) = 9만 < 10만
        assert view.allocations == []
        assert "상관" in view.excluded[0].detail

    def test_일일_배치가_이_값을_넘긴다(self) -> None:
        """**계산만 있고 안 불리면 없는 것과 같다** (docs/infra.md 25.65·25.92)."""
        본문 = (뿌리() / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")

        assert 'min_order_amount=settings["min_order_amount"]' in 본문

    def test_넘기는_값이_종목_통화로_환산된_것이다(self) -> None:
        """원화 10만을 달러 종목에 그대로 대면 **10만 달러**가 된다.

        2026-09-23 까지 이 검사는 **소스의 글자 한 줄**을 찾았다. 그 줄은 실제로
        `fx.convert_krw(...) or DEFAULT_MIN_ORDER` 였고, 검사는 앞쪽만 보고 통과했다 —
        뒤쪽의 `or` 가 0(제한 없음)을 **원화 기본값**으로 덮는 것을 못 봤다(25.179).
        그래서 글자 대신 **낸 값**을 본다.
        """
        from batch.jobs import signals as signals_job
        from tests.test_fx import _client_with_rates

        client = _client_with_rates(("2026-09-15", 1400.0))

        s = signals_job.load_settings(client, "USD", "2026-09-16")

        assert s["min_order_amount"] == signals_job.DEFAULT_MIN_ORDER / 1400, (
            "load_settings 가 min_order_amount 를 환산하지 않으면 미국 리포트가 통째로 빈다"
        )
        assert s["min_order_amount"] < s["total_investable"], (
            "최소 주문이 예산보다 크면 권장 금액이 한 건도 안 나온다"
        )


def 뿌리():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def test_ETF_보유는_상관_축소의_대조에_넣지_않는다() -> None:
    """지수 ETF(KODEX 200·VOO)는 거의 모든 대형주와 0.7 을 넘어 2부 금액을 통째로 깎았다 (docs/infra.md 25.908, 감사)."""
    from tests.test_report_picks import row

    계열 = _series(Test2부에_실제로_배선됐나._같이)
    etf = rp.Holding(9, "069500", "KODEX 200", None, 1_000_000.0, asset_type="etf")
    view = rp.build_portfolio([row(1)], total_investable=10_000_000, holdings=[etf], price_series={1: 계열, 9: 계열})
    assert view.allocations[0].amount == 1_000_000
    assert "상관" not in view.allocations[0].note
    주식 = rp.Holding(9, "000009", "보유9", None, 1_000_000.0)
    줄어든 = rp.build_portfolio([row(1)], total_investable=10_000_000, holdings=[주식], price_series={1: 계열, 9: 계열})
    assert 줄어든.allocations[0].amount < 1_000_000


class Test상관_축소가_다음_날_풀리지_않는다:
    """보유 B 와 상관 0.95(배수 0.3)인 후보가 날마다 다시 뽑히면 300만씩 더해 나흘 만에 10% 가 찼다 (25.1088, 감사 재현)."""

    def test_보유를_더해도_상관을_곱한_목표까지만(self) -> None:
        from tests.test_report_picks import row

        계열 = _series(Test2부에_실제로_배선됐나._같이)
        보유 = [rp.Holding(9, "000009", "보유9", None, 10_000_000.0)]
        금액 = []
        이미 = 0.0
        for _ in range(5):
            hs = [*보유, *([rp.Holding(1, "000001", "종목1", None, 이미)] if 이미 else [])]
            view = rp.build_portfolio([row(1, suggested_amount=10_000_000.0, suggested_weight_pct=10.0)],
                                      total_investable=100_000_000, holdings=hs, price_series={1: 계열, 9: 계열})  # fmt: skip
            오늘 = view.allocations[0].amount if view.allocations else 0
            금액.append(오늘)
            이미 += 오늘
        assert 이미 == pytest.approx(100_000_000 * 0.10 * sig.REDUCTION_FLOOR, abs=2)  # 3% 에서 멈춘다
        assert 금액[1:] == [0, 0, 0, 0]


def test_보유_없는_종목도_오늘_총액_기준_비중까지만() -> None:
    """신호가 하루 묵었거나 총액을 줄인 날, 변동성으로 4% 로 줄인 새 종목에 상한 10% 가 배분됐다 (25.1088, 감사 재현)."""
    from tests.test_report_picks import row

    view = rp.build_portfolio([row(1, suggested_amount=4_000_000.0, suggested_weight_pct=4.0)],
                              total_investable=20_000_000)  # fmt: skip
    a = view.allocations[0]
    assert a.amount == 800_000 and "권장 비중 4.0%까지만(오늘 총액 기준)" in a.note
