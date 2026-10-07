"""리포트 두 부분 구성 테스트.

가장 중요한 것은 **2부가 1부의 부분집합**이라는 규칙이다.
1부에 없는 종목이 2부에 나오면 "추천하지 않은 종목을 사라"는 리포트가 된다.
1부에 있는데 2부에 사유 없이 빠지면 "왜 0원인지" 알 수 없다.

네트워크도 데이터베이스도 타지 않는다.
"""

from __future__ import annotations

from batch.notify import report_sections as rs


def pick(ticker: str = "005930", name: str = "삼성전자", **kwargs) -> rs.StockPick:
    base = {
        "ticker": ticker,
        "name": name,
        "market": "KOSPI",
        "horizon": "mid",
        "close": 248500.0,
        "currency": "KRW",
    }
    base.update(kwargs)
    return rs.StockPick(**base)


def allocation(ticker: str = "005930", name: str = "삼성전자", **kwargs) -> rs.Allocation:
    base = {"ticker": ticker, "name": name, "amount": 3_000_000, "weight_pct": 6.0}
    base.update(kwargs)
    return rs.Allocation(**base)


class Test부분집합규칙:
    def test_배분이_추천의_부분집합이면_문제없다(self) -> None:
        picks = [pick("005930"), pick("000660", "SK하이닉스")]
        portfolio = rs.PortfolioView(
            allocations=[allocation("005930")],
            excluded=[rs.Excluded("000660", "SK하이닉스", rs.EXCLUDED_SECTOR_CAP)],
        )

        assert rs.check_subset(picks, portfolio) == []

    def test_추천에_없는_종목이_배분에_있으면_잡는다(self) -> None:
        # "추천하지 않은 종목을 사라" 는 리포트가 된다. 명백한 버그다
        picks = [pick("005930")]
        portfolio = rs.PortfolioView(allocations=[allocation("000660", "SK하이닉스")])

        problems = rs.check_subset(picks, portfolio)

        assert len(problems) >= 1
        assert "SK하이닉스" in problems[0]
        assert "추천에 없습니다" in problems[0]

    def test_사유_없이_빠지면_잡는다(self) -> None:
        # 좋은 종목인데 살 금액이 0원인 이유를 밝히지 않으면 리포트를 믿기 어렵다
        picks = [pick("005930"), pick("000660", "SK하이닉스")]
        portfolio = rs.PortfolioView(allocations=[allocation("005930")])

        problems = rs.check_subset(picks, portfolio)

        assert len(problems) == 1
        assert "SK하이닉스" in problems[0]
        assert "사유가 없습니다" in problems[0]

    def test_사유가_있으면_통과한다(self) -> None:
        picks = [pick("005930")]
        portfolio = rs.PortfolioView(
            excluded=[rs.Excluded("005930", "삼성전자", rs.EXCLUDED_NO_BUDGET)]
        )

        assert rs.check_subset(picks, portfolio) == []

    def test_둘_다_비어_있으면_문제없다(self) -> None:
        assert rs.check_subset([], rs.PortfolioView()) == []


class Test1부:
    def test_기간별로_묶는다(self) -> None:
        # 단기·중기·장기는 판단 근거가 달라 섞지 않는다
        picks = [
            pick("005930", "삼성전자", horizon="short"),
            pick("000660", "SK하이닉스", horizon="long"),
        ]
        text = rs.render_picks(picks)

        assert "[단기]" in text
        assert "[장기]" in text
        assert text.index("[단기]") < text.index("[장기]")

    def test_같은_종목이_두_기간에_실리면_둘째는_기간_고유_줄만(self) -> None:
        """종가·팩터·과거는 종목의 값이라 되풀이하지 않는다 (docs/reports.md 3.5, 25.952). 신호·매수 구간·근거는 기간마다 다르다."""
        picks = [
            pick("005930", "삼성전자", horizon="mid", factor_scores={"밸류": 60.0}, cagr=0.1, close=70000.0,
                 stability={"kept": 15, "total": 15, "dropped_by": []}, history={"times": 0, "streak": 0}),
            pick("005930", "삼성전자", horizon="long", factor_scores={"밸류": 60.0}, cagr=0.1, close=70000.0,
                 signal_type="밸류에이션 밴드", buy_zone_low=60000.0, buy_zone_high=70000.0, rationale="PBR 1.1배",
                 stability={"kept": 15, "total": 15, "dropped_by": []}, history={"times": 0, "streak": 0}),
        ]  # fmt: skip
        text = rs.render_picks(picks)
        assert text.count("  팩터 ") == 1 and text.count("  종가 ") == 1 and text.count("CAGR") == 1
        assert text.count("흔들기 15/15") == 1 and text.count("첫 추천") == 1
        장기 = text[text.index("[장기]"):]
        assert "(종가·팩터·과거는 위 [중기] 참고)" in 장기
        assert "신호 밸류에이션 밴드" in 장기 and "매수 구간 60,000원 ~ 70,000원" in 장기 and "PBR 1.1배" in 장기
        # 한 번만 실린 종목은 전과 같다
        assert "참고)" not in rs.render_picks(picks[:1])

    def test_미국_목록_이름의_꼬리를_뗀다(self) -> None:
        """2026-10-05 미국 리포트에서 이름 꼬리가 한 줄을 먹었다 (docs/infra.md 25.957)."""
        text = rs.render_picks([pick("KMT", "Kennametal Inc. Common Stock")])
        assert "\nKennametal Inc.\n" in text and "Common Stock" not in text

    def test_센티먼트를_팩터와_따로_적는다(self) -> None:
        # 기준 문서의 규칙. 종합 점수에는 가중치만큼만 들어가고 표시는 항상 분리
        text = rs.render_picks(
            [
                pick(
                    factor_scores={"밸류": 72, "퀄리티": 85},
                    sentiment=-30,
                )
            ]
        )

        assert "팩터 밸류 72 · 퀄리티 85" in text
        assert "센티먼트 -30" in text

    def test_금액도_비중도_싣지_않는다(self) -> None:
        # 1부는 포트폴리오 제약을 보지 않는다
        text = rs.render_picks([pick(total_score=82.5)])

        assert "원)" not in text
        assert "%" not in text.split("종가")[0]

    def test_매수_구간을_보여준다(self) -> None:
        text = rs.render_picks([pick(buy_zone_low=230000, buy_zone_high=245000)])

        assert "매수 구간" in text
        assert "230,000원" in text
        assert "245,000원" in text

    def test_근거_문장을_그대로_싣는다(self) -> None:
        text = rs.render_picks([pick(rationale="ROE 10.4%로 업종 상위 20%입니다")])
        assert "ROE 10.4%로 업종 상위 20%입니다" in text

    def test_과거_성과를_요약한다(self) -> None:
        text = rs.render_picks([pick(cagr=0.152, mdd=-0.38, sharpe=0.85)])

        assert "CAGR 15.2%" in text
        assert "MDD -38.0%" in text
        assert "샤프 0.85" in text

    def test_값이_없으면_그_줄을_빼고_무너지지_않는다(self) -> None:
        text = rs.render_picks([pick(close=None, total_score=None)])

        assert "삼성전자" in text
        assert "종가" not in text

    def test_종가에_날짜를_붙이고_현재가라_부르지_않는다(self) -> None:
        """신호 기준일 이하의 마지막 종가다 — 묵은 값이 "현재가" 로 나가면 안 된다 (docs/infra.md 25.348)."""
        text = rs.render_picks([pick(close=250000, close_date="2026-09-23")])
        assert "종가 250,000원 (2026-09-23)" in text
        assert "현재가" not in text

    def test_추천이_없으면_없다고_말한다(self) -> None:
        text = rs.render_picks([])
        assert "추천할 종목이 없습니다" in text


class Test2부:
    def test_금액과_비중을_보여준다(self) -> None:
        portfolio = rs.PortfolioView(
            allocations=[allocation(amount=5_000_000, weight_pct=10.0)],
            total_budget=50_000_000,
        )
        text = rs.render_portfolio(portfolio, [pick()])

        assert "5,000,000원" in text
        assert "10.0%" in text
        assert "50,000,000원" in text

    def test_분할_계획을_보여준다(self) -> None:
        portfolio = rs.PortfolioView(
            allocations=[
                allocation(
                    tranches=[
                        {"price_at_or_below": 245000, "amount": 1_000_000},
                        {"price_at_or_below": 235000, "amount": 1_000_000},
                    ]
                )
            ]
        )
        text = rs.render_portfolio(portfolio, [pick()])

        assert "1차 245,000원 이하에서 1,000,000원" in text
        assert "2차 235,000원" in text

    def test_회차마다_몇_주인지_적고_한_주에_못_미치면_말한다(self) -> None:
        """배분이 작으면 회차 금액이 한 주 값보다 작아 실행할 수 없다 (docs/infra.md 25.214)."""
        portfolio = rs.PortfolioView(
            allocations=[
                allocation(
                    amount=100_000,
                    tranches=[
                        {"price_at_or_below": 71_000, "amount": 40_000},
                        {"price_at_or_below": 35_000, "amount": 30_000},
                        {"price_at_or_below": 10_000, "amount": 30_000},
                    ],
                )
            ]
        )
        줄들 = rs.render_portfolio(portfolio, [pick()]).splitlines()
        회차 = [줄 for 줄 in 줄들 if "차 " in 줄 and "이하에서" in 줄]

        assert 회차[0].endswith("(1주 값에 못 미침)")  # 4만원으로 7.1만원짜리 못 산다
        assert 회차[1].endswith("(1주 값에 못 미침)")
        assert 회차[2].endswith("(약 3주)")

    def test_빠진_종목의_사유를_반드시_적는다(self) -> None:
        portfolio = rs.PortfolioView(
            excluded=[
                rs.Excluded("000660", "SK하이닉스", rs.EXCLUDED_SECTOR_CAP, "반도체 30% 도달")
            ]
        )
        text = rs.render_portfolio(portfolio, [pick("000660", "SK하이닉스")])

        assert "빠진 종목" in text
        assert "SK하이닉스" in text
        assert "섹터 상한 도달" in text
        assert "반도체 30% 도달" in text

    def test_섹터_집중도를_보여준다(self) -> None:
        portfolio = rs.PortfolioView(
            allocations=[allocation()],
            sector_concentration={"반도체": 28.0, "자동차": 12.0},
        )
        text = rs.render_portfolio(portfolio, [pick()])

        assert "반도체 28.0%" in text

    def test_표기_세_가지(self) -> None:
        """센티먼트 "-0", 섹터 30.4% 가 "30%", 거래량 "10000만주" (docs/infra.md 25.421)."""
        from batch.notify import formatter

        assert "센티먼트 +0" in rs.render_picks([pick(sentiment=-0.3)])
        view = rs.PortfolioView(allocations=[allocation()], sector_concentration={"반도체": 30.4})
        assert "반도체 30.4%" in rs.render_portfolio(view, [pick()])
        assert formatter._volume(99_995_000) == "1.0억주"
        assert formatter._volume(99_994_999) == "9999만주"

    def test_매도_플래그를_보여준다(self) -> None:
        portfolio = rs.PortfolioView(
            sell_flags=[
                {"level": "red", "name": "어떤종목", "rationale": "손절선 -15% 도달"}
            ]
        )
        text = rs.render_portfolio(portfolio, [])

        assert "매도 플래그" in text
        assert "[적]" in text
        assert "손절선 -15% 도달" in text

    def test_정합성_문제를_리포트에_드러낸다(self) -> None:
        # 조용히 넘어가면 잘못된 리포트가 그대로 나간다
        portfolio = rs.PortfolioView(allocations=[allocation("000660", "SK하이닉스")])
        text = rs.render_portfolio(portfolio, [pick("005930")])

        assert "정합성 경고" in text


class Test전체:
    def test_포트폴리오가_없으면_1부만_낸다(self) -> None:
        # 개별 종목만 참고하는 것도 쓸모 있는 사용법이다
        text = rs.render([pick()])

        assert "1부 개별 종목" in text
        assert "2부 포트폴리오" not in text

    def test_둘_다_있으면_순서대로_붙인다(self) -> None:
        text = rs.render([pick()], rs.PortfolioView(allocations=[allocation()]))

        assert text.index("1부 개별 종목") < text.index("2부 포트폴리오")

    def test_1부가_그_자체로_완결된다(self) -> None:
        # 2부를 안 봐도 판단할 수 있어야 한다
        text = rs.render(
            [
                pick(
                    total_score=82,
                    factor_scores={"밸류": 70},
                    buy_zone_low=230000,
                    buy_zone_high=245000,
                    rationale="근거 문장",
                )
            ]
        )

        assert "82점" in text
        assert "매수 구간" in text
        assert "근거 문장" in text


class Test제외사유:
    def test_사유가_네_가지로_정의돼_있다(self) -> None:
        reasons = {
            rs.EXCLUDED_SECTOR_CAP,
            rs.EXCLUDED_STOCK_CAP,
            rs.EXCLUDED_NO_BUDGET,
            rs.EXCLUDED_BEAR_ZERO,
        }
        assert len(reasons) == 4

    def test_사유_문구가_사람이_읽을_수_있다(self) -> None:
        for reason in (
            rs.EXCLUDED_SECTOR_CAP,
            rs.EXCLUDED_STOCK_CAP,
            rs.EXCLUDED_NO_BUDGET,
            rs.EXCLUDED_BEAR_ZERO,
        ):
            assert len(reason) > 4
            assert reason.isascii() is False  # 한국어 문구다


class Test매매_딥링크:
    """1부 종목 줄의 "매매 입력" 링크 (docs/infra.md 25.944). 폰에서 리포트를 보고 산 뒤 종목을 다시 찾던 길을 탭 한 번으로."""

    def test_주소와_종목_번호가_있으면_그_종목_그_기간으로_여는_링크를_붙인다(self) -> None:
        text = rs.render_picks([pick(stock_id=42, horizon="mid")], app_url="https://app.example")
        assert "  매매 입력 https://app.example/stocks/42?trade=buy&horizon=mid" in text

    def test_주소가_비었거나_번호가_없으면_붙이지_않는다(self) -> None:
        assert "매매 입력" not in rs.render_picks([pick(stock_id=42)])
        assert "매매 입력" not in rs.render_picks([pick()], app_url="https://app.example")
        assert rs.trade_link("", pick(stock_id=1)) is None

    def test_체결가_수량은_채우지_않는다(self) -> None:
        # trades 는 사용자 입력값만 담는다(CLAUDE.md) — 주소에 가격·수량이 들어가면 "실제로 체결한 값" 이 아닌 숫자가
        # 저장될 수 있다
        링크 = rs.trade_link("https://app.example", pick(stock_id=1, buy_zone_low=1000.0, buy_zone_high=1100.0))
        assert 링크 is not None and "price" not in 링크 and "qty" not in 링크 and "quantity" not in 링크
