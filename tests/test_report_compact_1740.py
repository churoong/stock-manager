"""1부·2부를 덜 줄로, 미국 이름은 리포트 전체에서 같은 꼴로 (docs/infra.md 25.1740).

2026-10-23 사용자 "오늘 아침에 온 리포트보면 여전히 내용이 가독성이 떨어지는데" — 실물로 찍어 보니 1부 종목마다 11~12줄,
같은 기간 종목끼리 경계가 없었고, 2부 예산 줄이 괄호 안 괄호로 130자, 미국 이름이 절마다 달랐다.
"""

from __future__ import annotations

from batch.notify import report_sections as rs
from batch.notify import telegram as tg
from batch.services import holding_scores as hs
from tests.test_report_sections import allocation, pick


def test_1부_종목_블록_순서와_줄() -> None:
    p = pick(horizon="short", stock_id=7, total_score=71.0, rank_in_market=3, signal_type="기술적 추세",
             close_date="2026-10-22", buy_zone_low=240_000.0, buy_zone_high=245_000.0,
             factor_scores={"밸류": 71.0}, sentiment=7.0, rationale="20일선 위", cagr=0.028, mdd=-0.456, sharpe=0.27,
             perf_window="3Y")  # fmt: skip
    줄 = rs.render_picks([p, pick("000660", "SK하이닉스", horizon="short")], app_url="https://app.example").split("\n")
    i = 줄.index("[단기]")
    assert 줄[i + 1] == "삼성전자  71점 (KOSPI 3위) · 신호 기술적 추세"
    assert 줄[i + 2] == "  종가 248,500원 (2026-10-22) · 매수 구간 240,000원 ~ 245,000원"
    assert 줄[i + 3:i + 7] == ["  팩터 밸류 71", "  센티먼트 +7", "  20일선 위", "  과거(3Y) CAGR 2.8% · MDD -45.6% · 샤프 0.27"]
    # 매매 입력 주소는 끝, 같은 기간 다음 종목 앞에 빈 줄
    assert 줄[i + 7] == "  매매 입력 https://app.example/stocks/7?trade=buy&horizon=short"
    assert 줄[i + 8] == "" and 줄[i + 9].startswith("SK하이닉스")


def test_두_기간에_실린_종목은_기간_고유_줄만() -> None:
    a = pick(horizon="short", signal_type="기술적 추세", factor_scores={"밸류": 71.0})
    b = pick(horizon="mid", signal_type="실적 모멘텀", buy_zone_low=1.0, buy_zone_high=2.0, rationale="매출 20%")
    줄 = rs.render_picks([a, b]).split("\n")
    j = 줄.index("[중기]")
    assert 줄[j + 1:j + 5] == ["삼성전자 · 신호 실적 모멘텀", "  (종가·팩터·과거는 위 [단기] 참고)", "  매수 구간 1원 ~ 2원",
                             "  매출 20%"]  # fmt: skip


def test_미국_이름은_1부_2부_보유_모두_짧게_티커와() -> None:
    긴 = "Medical Properties Trust, Inc. common stock"
    p = pick("MPW", 긴, market="NYSE", currency="USD")
    assert rs.render_picks([p]).split("\n")[3].startswith("Medical Properties Trust(MPW)")
    view = rs.PortfolioView(allocations=[allocation("MPW", 긴, currency="USD")],
                            excluded=[rs.Excluded("MPW", 긴, rs.EXCLUDED_SECTOR_CAP)])  # fmt: skip
    글 = rs.render_portfolio(view, [p])
    assert "\nMedical Properties Trust(MPW)  $3,000,000.00 (6.0%)" in 글 and "  Medical Properties Trust(MPW) — " in 글
    h = hs.HoldingScore(긴, "NYSE", 63.0, 7, "2026-10-22", 62.0, "2026-09-02")
    assert hs.line(h).startswith("Medical Properties Trust: 지금 63점")
    # 국내는 그대로, 시장을 모르면 그대로
    assert hs.line(hs.HoldingScore("카카오", "KOSPI", 45.0, 28, None, None, None)).startswith("카카오: ")
    assert hs.line(hs.HoldingScore(긴, None, None, None, None, None, None)).startswith(긴)


def test_2부_예산_메모는_다음_줄에_배분_사이에_빈_줄() -> None:
    view = rs.PortfolioView(allocations=[allocation(), allocation("000660", "SK하이닉스")], total_budget=1e8,
                            budget_note="100,000,000원 ÷ 1,447.45원/달러 · 국내 리포트 2부 배분만큼 여력을 줄였습니다")
    줄 = rs.render_portfolio(view, [pick(), pick("000660", "SK하이닉스")]).split("\n")
    assert 줄[1:4] == ["총 투자가능금액 100,000,000원", "  100,000,000원 ÷ 1,447.45원/달러",
                      "  국내 리포트 2부 배분만큼 여력을 줄였습니다"]  # fmt: skip
    k = next(i for i, x in enumerate(줄) if x.startswith("SK하이닉스"))
    assert 줄[k - 1] == ""


def test_대표_종목은_끝_절이라_2부_이어서를_달지_않는다() -> None:
    """25.1738 이 대표 종목을 끝으로 옮겼다 — 가상모의투자 절이 없는 날 2부 바로 뒤라 "2부 (이어서)" 가 붙을 뻔했다"""
    조각 = tg._carry_headers(["2부 포트폴리오\n가  100만원 (3.3%)", "대표 종목 (2026-10-22 종가)\n  삼성전자 61,000원"])
    assert 조각[1].startswith("대표 종목")
