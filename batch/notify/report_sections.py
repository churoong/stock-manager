"""리포트 두 부분 구성.

설계는 docs/design.md 3.9절에 있다.

  1부 개별 종목   이 종목 자체가 어떤가. 포트폴리오 제약을 보지 않는다
  2부 포트폴리오   내 돈을 어떻게 나눌까. 상한과 보유를 함께 본다

**2부는 1부의 부분집합이다.** 1부에 없는 종목이 2부에 나오지 않는다.
1부에 있는데 2부에 없으면 사유를 반드시 적는다. 좋은 종목인데 살 금액이
0원인 경우가 실제로 생기고, 그건 종목이 나쁜 게 아니라 포트폴리오가 찬 것이다.

**1부만 보고 판단해도 되게 만든다.** 포트폴리오를 안 짜고 개별 종목만
참고하는 것도 쓸모 있는 사용법이다.

숫자는 전부 DB에 저장된 값이다. 여기서 만들어 내지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from batch.core.rounding import fixed
from batch.services import holding_scores, pick_history, robustness
from batch.services.accumulation import display_name

# 2부에서 빠진 사유. 화면과 텔레그램이 같은 문구를 쓴다.
#
# **여덟이 전부다** (25.649 — 여덟째가 더해진 뒤에도 "일곱" 이라 적혀 있었다). docs/design.md "두 부를 잇는 규칙" 표가
# 정의처이고
# `tests/test_excluded_reasons.py` 가 그 표와 양방향으로 대 본다 (docs/infra.md 25.190).
# 둘은 `services/report_picks` 에 따로 적혀 있었다 — 문서는 넷만 알고 있었고,
# "사유는 **이 중 하나다**" 라는 문장이 그날부터 거짓이었다.
EXCLUDED_SECTOR_CAP = "섹터 상한 도달"
EXCLUDED_STOCK_CAP = "종목 비중 상한 도달"
EXCLUDED_NO_BUDGET = "투자 여력 부족"
# 예전 이름은 "변동성 축소로 0원" 이었다 (docs/infra.md 25.340). 변동성·MDD 축소는 바닥이 0.3 이라
# 0 을 만들지 못한다 — 0 이 되는 길은 약세장 배수(`trend_filter.bear_factor`) 0 하나뿐이다
EXCLUDED_BEAR_ZERO = "약세장 비중 0으로 0원"
# 금액이 비는 이유가 **설정 때문인지 종목 때문인지** 구분해야 무엇을 고칠지 안다
EXCLUDED_NO_SETTING = "총 투자가능금액 미설정"
EXCLUDED_BELOW_MIN_ORDER = "최소 주문 단위 미만"
# 미국은 원화 총액을 환율로 나눠 쓴다. 환율이 없으면 총액이 0 이 되는데, 그것을 "미설정" 이라 부르면
# 이미 넣은 금액을 또 넣으라고 보낸다 (docs/infra.md 25.292)
EXCLUDED_NO_FX = "환율 없음"
# 신호 계산 때 금액을 내지 못했는데(그때 총액 미설정·환율 없음) 오늘은 총액이 있는 경우 (docs/infra.md 25.600, 감사).
# 예전에는 "최소 주문 단위 미만" 으로 떨어져 무엇을 고칠지 틀리게 가리켰다 —
# 1부 근거표는 "환율이 없어 내지 않았습니다" 인데 2부는 주문 단위라 했다
EXCLUDED_SIGNAL_NO_AMOUNT = "신호에 금액 없음"

#: 위 여덟 전부. 그물이 이것으로 문서와 대 본다
EXCLUDED_REASONS = (
    EXCLUDED_SECTOR_CAP,
    EXCLUDED_STOCK_CAP,
    EXCLUDED_NO_BUDGET,
    EXCLUDED_BEAR_ZERO,
    EXCLUDED_NO_SETTING,
    EXCLUDED_BELOW_MIN_ORDER,
    EXCLUDED_NO_FX,
    EXCLUDED_SIGNAL_NO_AMOUNT,
)

HORIZON_LABEL = {"short": "단기", "mid": "중기", "long": "장기"}


@dataclass
class StockPick:
    """1부에 실리는 종목 하나.

    포트폴리오 제약이 없다. 금액도 비중도 여기 없다.
    """

    ticker: str
    name: str
    market: str
    horizon: str  # short mid long
    #: 종목 번호 — 매매 입력 딥링크(`trade_link`)와 웹 리포트의 종목 링크에 쓴다 (25.944). 옛 리포트 항목에는 없다
    stock_id: int | None = None
    close: float | None = None
    #: 그 종가의 날짜 (docs/infra.md 25.348). 없으면 날짜 없이 적는다
    close_date: str | None = None
    currency: str = "KRW"
    #: 신호 계산일과 근거표 (docs/infra.md 25.349). 글에는 쓰지 않고 리포트 항목(`report_items.payload`)으로 남아
    #: 웹 리포트 화면이 추천마다 펼친다. 예전에는 실리지 않아 **지난 리포트의 추천을 확인할 길이 없었다**
    #: (종목 상세는 오늘 근거를 보여 준다)
    as_of_date: str | None = None
    criteria: list[dict[str, Any]] = field(default_factory=list)

    total_score: float | None = None
    rank_in_market: int | None = None
    factor_scores: dict[str, float] = field(default_factory=dict)
    sentiment: float | None = None
    #: 가중치 흔들기 (docs/reports.md 3.2, 25.947) — `robustness.Stability.as_payload()`. 열여섯 세계 가운데 몇에서
    #: 상위에 남았나·어느 세계에서 빠지나. 점수가 아니라 **점수가 설정값에 얼마나 기대는지**다. 옛 항목에는 없다
    stability: dict[str, Any] | None = None
    #: 추천 이력 (docs/reports.md 3.3, 25.950) — `pick_history.PickHistory.as_payload()`. 지난 리포트에 몇 번 실렸나,
    #: 처음 본 날과 그날 종가. None 이면 못 읽은 것이라 줄을 적지 않는다(옛 항목도 None)
    history: dict[str, Any] | None = None

    signal_type: str | None = None
    buy_zone_low: float | None = None
    buy_zone_high: float | None = None

    cagr: float | None = None
    mdd: float | None = None
    sharpe: float | None = None
    #: 성과 지표의 창(3Y·1Y)과 계산 기준일 — 3Y 가 없으면 1Y 로 내려가므로 적어야 읽힌다 (docs/infra.md 25.489)
    perf_window: str | None = None
    perf_as_of: str | None = None

    rationale: str = ""

    @property
    def horizon_label(self) -> str:
        return HORIZON_LABEL.get(self.horizon, self.horizon)


@dataclass
class Allocation:
    """2부에 실리는 배분 하나."""

    ticker: str
    name: str
    amount: float  # 신호의 통화 그대로. 국내는 원(정수), 미국은 달러(센트까지, 25.563)
    weight_pct: float
    tranches: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    currency: str = "KRW"
    #: 금액을 줄인 근거. 지금은 상관 기반 분산 하나다 (docs/signals.md 3.6).
    #: `services/reports.items_from` 이 `asdict` 로 떠서 `report_items.payload` 에 넣는다. 웹 리포트 화면은
    #: 그 요약인 `note`(어느 보유와 상관 얼마 — 몇 % 줄임)를 2부 줄에 적는다 (docs/infra.md 25.349. 그전에는
    #: "화면에서 펼쳐 볼 수 있다" 고 적어 두고 화면이 아무것도 그리지 않았다)
    rationale: dict[str, Any] = field(default_factory=dict)


@dataclass
class Excluded:
    """1부에 있었으나 2부에서 빠진 종목."""

    ticker: str
    name: str
    reason: str
    detail: str = ""


@dataclass
class PortfolioView:
    allocations: list[Allocation] = field(default_factory=list)
    excluded: list[Excluded] = field(default_factory=list)
    sector_concentration: dict[str, float] = field(default_factory=dict)
    remaining_budget: float | None = None
    total_budget: float | None = None
    # 미국 리포트는 원화 설정을 달러로 환산한 값이다. 환산식을 함께 적는다(docs/signals.md 3.4)
    budget_currency: str = "KRW"
    budget_note: str | None = None
    sell_flags: list[dict[str, Any]] = field(default_factory=list)
    # 현재 보유 (Step 30). 여력·종목 상한·섹터 집중도가 보유를 포함해 계산됐다는 것을 보인다
    holdings_value: float | None = None
    holdings_count: int = 0
    #: 섹터 상한의 한계 한 줄 (docs/infra.md 25.509)
    sector_note: str | None = None
    #: 2부 전체를 배분하지 않은 사유 — 묵은 신호 (docs/infra.md 25.820·25.822). 있으면 종목별 "사유 없음" 검사를 하지
    #: 않는다
    no_allocation_reason: str | None = None
    #: 보유 종목의 샀을 때·지금 점수 (docs/reports.md 3.6, 25.953). 글에만 싣고 report_items 에는 두지 않는다
    holding_scores: list[holding_scores.HoldingScore] = field(default_factory=list)


def check_subset(picks: list[StockPick], portfolio: PortfolioView) -> list[str]:
    """2부가 1부의 부분집합인지 확인한다.

    어긋나면 문제를 문장으로 돌려준다. 1부에 없는 종목이 2부에 나오면
    "추천하지 않은 종목을 사라"는 리포트가 되고, 그건 명백한 버그다.
    """
    problems: list[str] = []
    pick_tickers = {p.ticker for p in picks}

    for allocation in portfolio.allocations:
        if allocation.ticker not in pick_tickers:
            problems.append(
                f"{allocation.name}({allocation.ticker})이 배분에는 있는데 "
                "개별 종목 추천에 없습니다"
            )

    # 2부 전체를 배분하지 않은 날은 사유가 한 줄로 이미 있다 — 종목마다 "사유 없음" 을 거짓 정합성 경고로 내지 않는다
    # (25.822, 교차검증)
    if portfolio.no_allocation_reason:
        return problems
    excluded_tickers = {e.ticker for e in portfolio.excluded}
    allocated_tickers = {a.ticker for a in portfolio.allocations}
    unexplained = pick_tickers - allocated_tickers - excluded_tickers
    for ticker in sorted(unexplained):
        name = next(p.name for p in picks if p.ticker == ticker)
        problems.append(f"{name}({ticker})이 배분에서 빠졌는데 사유가 없습니다")

    return problems


# ----------------------------------------------------------------------
# 표시
# ----------------------------------------------------------------------
def _shares_note(amount: float, price: float) -> str:
    """회차 금액이 그 가격에서 **몇 주**인가 (docs/infra.md 25.214).

    분할 계획은 금액만 적었다. 배분이 작으면 회차 금액이 **한 주 값보다 작아** 그 회차는 실행할 수 없다
    (삼성전자 7만원에 배분 10만원이면 2·3차가 각 3만원). 비율(40/30/30)을 바꾸는 것은 규칙이라 두고,
    **사실을 적는다.** 소수점 거래가 되는지는 증권사마다 다르다 `[확인필요]` — 온주 기준으로 센다.
    """
    if price <= 0:
        return ""
    주 = int(amount // price)
    return f" (약 {주:,}주)" if 주 >= 1 else " (1주 값에 못 미침)"


def _money(value: float | None, currency: str = "KRW") -> str:
    if value is None or value != value:  # NaN 도 모름이다 (25.613)
        return "-"
    if currency == "KRW":
        return f"{round(value):,}원"  # 정수로 반올림 — "-0원" 이 나오지 않는다 (25.613)
    r = round(value, 2)
    return f"{'-' if r < 0 else ''}${abs(r):,.2f}"  # "$-1,234.50" 이 아니라 "-$1,234.50" (25.613)


def _pct(value: float | None, digits: int = 1) -> str:
    if value is None or value != value:  # NaN 은 "nan%" 가 아니라 모름 (25.613)
        return "-"
    return f"{round(value, digits) + 0.0:.{digits}f}%"  # 반올림한 뒤 부호 — "-0.0%" 가 나오지 않는다 (25.613)


def _score(value: float | None) -> str:
    if value is None:
        return "-"
    return fixed(value)  # .5 를 올린다 — 근거 문장·웹과 같게 (25.1105)


def trade_link(app_url: str | None, pick: StockPick) -> str | None:
    """종목 상세의 매매 폼이 **그 종목·그 기간으로 채워진 채** 열리는 주소 (docs/infra.md 25.944).

    폰에서 리포트를 보고 산 뒤 웹앱을 열어 종목을 다시 찾던 길을 탭 한 번으로 줄인다. 체결가·수량은 채우지 않는다 —
    trades 는 사용자 입력값만 담는다(CLAUDE.md). 주소를 모르거나(`APP_URL` 비어 있음) 종목 번호가 없으면 None.
    """
    if not app_url or pick.stock_id is None:
        return None
    return f"{app_url}/stocks/{pick.stock_id}?trade=buy&horizon={pick.horizon}"


def render_picks(picks: list[StockPick], app_url: str | None = None, footer: str | None = None) -> str:
    """1부. 기간별로 묶어 보여 준다.

    단기·중기·장기는 판단 근거가 다르므로 섞지 않는다.
    `footer` 는 1부 끝의 한 줄 — 가중치를 흔들면 들어오는 종목(docs/reports.md 3.2).
    """
    if not picks:
        return "1부 개별 종목\n\n오늘 추천할 종목이 없습니다."

    lines = ["1부 개별 종목"]
    # **같은 종목이 두 기간에 실리면 둘째부터는 기간 고유 줄만** (docs/reports.md 3.5, 25.952). 종가·팩터·센티먼트·
    # 흔들기·이력·과거는 종목의 값이라 기간마다 같다 — 되풀이하면 한 종목이 24줄이 되고 한 조각(3,900자)을 넘긴다
    첫_기간: dict[str, str] = {}

    for horizon in ("short", "mid", "long"):
        group = [p for p in picks if p.horizon == horizon]
        if not group:
            continue

        lines.append("")
        lines.append(f"[{HORIZON_LABEL[horizon]}]")

        for pick in group:
            반복 = pick.ticker in 첫_기간
            첫_기간.setdefault(pick.ticker, horizon)
            # 미국 목록 이름의 꼬리(" Common Stock" 등)는 뗀다 (25.957). report_items 에는 원래 이름이 남는다
            head = f"{display_name(pick.name)}"
            if pick.total_score is not None:
                head += f"  {_score(pick.total_score)}점"
                if pick.rank_in_market:
                    head += f" ({pick.market} {pick.rank_in_market}위)"
            lines.append(head)
            # 매매 입력 딥링크 — 텔레그램은 평문 주소를 그대로 링크로 만든다. 미리보기는 `send` 가 끈다 (25.944)
            링크 = trade_link(app_url, pick)
            if 링크:
                lines.append(f"  매매 입력 {링크}")
            if 반복:
                lines.append(f"  (종가·팩터·과거는 위 [{HORIZON_LABEL[첫_기간[pick.ticker]]}] 참고)")

            # **"현재가" 가 아니다** (docs/infra.md 25.348). 신호 기준일 이하의 마지막 종가이고, 신호가 묵었거나
            # 그날 시세가 없던 종목은 며칠 전 값이다. 대표 종목 줄(25.326)처럼 날짜를 붙인다
            if not 반복 and pick.close is not None:
                when = f" ({pick.close_date})" if pick.close_date else ""
                lines.append(f"  종가 {_money(pick.close, pick.currency)}{when}")

            if not 반복 and pick.factor_scores:
                parts = [
                    f"{name} {_score(value)}" for name, value in pick.factor_scores.items()
                ]
                lines.append(f"  팩터 {' · '.join(parts)}")

            # 센티먼트는 항상 따로 적는다. 팩터에 섞지 않는다
            if not 반복 and pick.sentiment is not None:
                # 정수로 먼저 반올림한다 — `:+.0f` 는 −0.3 을 "-0" 으로 찍었다 (docs/infra.md 25.421)
                lines.append(f"  센티먼트 {fixed(pick.sentiment, sign=True)}")  # .5 올림 (25.1105)

            # 가중치 흔들기 — 이 점수가 설정값에 얼마나 기대는지 (docs/reports.md 3.2). 점수 줄 바로 아래
            흔들기 = None if 반복 else robustness.stability_line(pick.stability)
            if 흔들기:
                lines.append(f"  {흔들기}")

            # 건너뛴 추천 영수증 — 몇 번째 추천이고 처음 본 날 종가 대비 얼마나 움직였나 (docs/reports.md 3.3)
            if not 반복 and pick.history is not None:
                통화 = pick.currency
                lines.append(f"  {pick_history.history_line(pick.history, pick.close, lambda v, c=통화: _money(v, c))}")

            if pick.signal_type:
                lines.append(f"  신호 {pick.signal_type}")

            if pick.buy_zone_low is not None and pick.buy_zone_high is not None:
                low = _money(pick.buy_zone_low, pick.currency)
                high = _money(pick.buy_zone_high, pick.currency)
                lines.append(f"  매수 구간 {low} ~ {high}")

            performance = []
            if pick.cagr is not None:
                performance.append(f"CAGR {_pct(pick.cagr * 100)}")
            if pick.mdd is not None:
                performance.append(f"MDD {_pct(pick.mdd * 100)}")
            if pick.sharpe is not None:
                # "-0.00" 을 내지 않는다 — CAGR·MDD 의 `_pct` 와 같게 (25.821, 리포트 감사)
                performance.append(f"샤프 {round(pick.sharpe, 2) + 0.0:.2f}")
            if performance and not 반복:
                # 창과 기준일을 붙인다 — 1Y 값이 3년 연환산처럼 읽히지 않게 (25.489, 텔레그램 감사)
                기준 = f"기준 {pick.perf_as_of}" if pick.perf_as_of else None
                창 = ", ".join(x for x in (pick.perf_window, 기준) if x)
                lines.append(f"  과거{f'({창})' if 창 else ''} {' · '.join(performance)}")

            if pick.rationale:
                lines.append(f"  {pick.rationale}")

    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines)


def render_portfolio(portfolio: PortfolioView, picks: list[StockPick]) -> str:
    """2부. 돈을 어떻게 나눌지."""
    lines = ["2부 포트폴리오"]

    if portfolio.total_budget is not None:
        line = f"총 투자가능금액 {_money(portfolio.total_budget, portfolio.budget_currency)}"
        if portfolio.budget_note:
            line += f" ({portfolio.budget_note})"
        lines.append(line)

    if not portfolio.allocations:
        lines.append("")
        사유 = portfolio.no_allocation_reason
        lines.append(f"{사유}." if 사유 else "배분할 종목이 없습니다.")
    else:
        lines.append("")
        for allocation in portfolio.allocations:
            lines.append(
                f"{allocation.name}  {_money(allocation.amount, allocation.currency)} "
                f"({fixed(allocation.weight_pct, 1)}%)"
            )
            for i, tranche in enumerate(allocation.tranches, 1):
                price = tranche.get("price_at_or_below")
                amount = tranche.get("amount")
                if price is None or amount is None:
                    continue
                lines.append(
                    f"  {i}차 {_money(price, allocation.currency)} 이하에서 {_money(amount, allocation.currency)}"
                    + _shares_note(float(amount), float(price))
                )
            if allocation.note:
                lines.append(f"  {allocation.note}")

    # 1부에 있었는데 빠진 종목은 반드시 사유를 적는다.
    # 좋은 종목인데 살 금액이 0원인 이유를 밝히지 않으면 리포트를 믿기 어렵다
    if portfolio.excluded:
        lines.append("")
        lines.append("배분에서 빠진 종목")
        for item in portfolio.excluded:
            line = f"  {item.name} — {item.reason}"
            if item.detail:
                line += f" ({item.detail})"
            lines.append(line)

    if portfolio.sector_concentration:
        lines.append("")
        top = sorted(
            portfolio.sector_concentration.items(), key=lambda kv: -kv[1]
        )[:5]
        # 한 자리까지 — 제외 사유 "이미 30.4%, 상한 30%"(25.369)와 같은 리포트에서 "30%" 로 어긋나지 않게 (25.421)
        parts = [f"{sector} {weight:.1f}%" for sector, weight in top]
        lines.append(f"섹터 비중 {' · '.join(parts)}")
    if portfolio.sector_note:
        lines.append(portfolio.sector_note)

    if portfolio.holdings_value is not None:
        lines.append(
            f"현재 보유 평가 {_money(portfolio.holdings_value, portfolio.budget_currency)}"
            f" ({portfolio.holdings_count}종목) — 여력·상한·섹터 비중에 포함"
        )
    if portfolio.remaining_budget is not None:
        lines.append(f"남은 여력 {_money(portfolio.remaining_budget, portfolio.budget_currency)}")
    # 보유 종목 점수 영수증 (docs/reports.md 3.6) — 추천과 보유를 같은 자로. 표시일 뿐 매도 신호가 아니다
    if portfolio.holding_scores:
        lines.append("")
        lines.extend(holding_scores.render(portfolio.holding_scores))

    if portfolio.sell_flags:
        lines.append("")
        lines.extend(render_sell_flags(portfolio.sell_flags))

    problems = check_subset(picks, portfolio)
    if problems:
        lines.append("")
        lines.append("리포트 정합성 경고")
        for problem in problems:
            lines.append(f"  - {problem}")

    return "\n".join(lines)


def render_sell_flags(flags: list[dict[str, Any]]) -> list[str]:
    """보유 종목 매도 플래그 (docs/sell_flags.md). 표시와 알림만. 새로 걸린 것에 NEW."""
    # **판정일을 머리에 적는다** (docs/infra.md 25.492, 텔레그램 감사). 매도 플래그 작업이 실패한 날에는
    # MAX(as_of_date) 인 어제 플래그가 날짜 없이 오늘 것처럼 실렸다. 날짜가 여럿이면 모두 적는다
    날들 = sorted({str(f["as_of_date"]) for f in flags if f.get("as_of_date")})
    lines = [f"매도 플래그 (자동 매도 없음{', 판정일 ' + ', '.join(날들) if 날들 else ''})"]
    for flag in flags:
        mark = {"green": "녹", "red": "적", "yellow": "황"}.get(str(flag.get("level")), "?")
        new = " NEW" if flag.get("new") else ""
        lines.append(f"  [{mark}]{new} {flag.get('rationale', '')}")
    return lines


def render(
    picks: list[StockPick],
    portfolio: PortfolioView | None = None,
    app_url: str | None = None,
    picks_footer: str | None = None,
) -> str:
    """두 부를 이어 붙인다.

    포트폴리오가 없으면 1부만 낸다. 개별 종목만 참고하는 것도
    쓸모 있는 사용법이라 1부가 그 자체로 완결되어야 한다.
    `app_url` 이 있으면 1부 종목마다 매매 입력 링크를 붙인다 (25.944).
    """
    blocks = [render_picks(picks, app_url, footer=picks_footer)]
    if portfolio is not None:
        blocks.append(render_portfolio(portfolio, picks))
    return "\n\n".join(blocks)
