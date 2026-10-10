"""아침 리포트에 실을 추천 고르기. 순수 계산만 한다.

리포트 구조는 docs/design.md 3.9절(1부 개별 종목 · 2부 포트폴리오), 그리는 일은
batch/notify/report_sections.py 가 한다. 여기서는 **무엇을 실을지**만 정한다.

규칙 (2026-09-17 사용자 확정: 시장별 상위 5개)
  - 한 리포트(국내 또는 미국)에 종합 점수가 높은 **서로 다른 종목 5개**
  - 한 종목이 여러 기간에 신호가 났으면 그 기간을 모두 1부에 싣는다.
    기간마다 근거가 달라 하나만 고르면 나머지 근거가 사라진다
  - 2부는 그 5개 종목만 본다(1부의 부분집합). 종목당 배분은 하나다.
    기간별 권장 금액은 서로 독립으로 계산돼 더하면 비중 상한을 넘을 수 있어서,
    가장 큰 한 건만 쓴다
  - 1부에 있는데 2부에 없으면 사유를 붙인다
  - 2부는 현재 보유(positions)를 포함해 여력·종목 상한·섹터 집중도를 낸다 (Step 30)

숫자는 전부 signals · scores · performance_metrics 에 저장된 값이다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from batch.notify import report_sections as rs
from batch.services import criteria as crit
from batch.services import holding_scores as hsc
from batch.services import pick_history as ph
from batch.services import robustness
from batch.services import signals as sig

TOP_STOCKS = 5

# ----------------------------------------------------------------------
# 상관 기반 분산 (docs/signals.md 3.6). 2026-09-21 추가
# ----------------------------------------------------------------------
#
# **지금 분산 장치가 꺼져 있다.** 섹터 상한은 `stocks.sector` 가 비어 있어 적용되지
# 않는다(docs/signals.md 3.2). 상관은 **시세만으로** 나므로 그 구멍을 실제로 메운다.
# 그리고 섹터가 있더라도 부족하다 — 업종이 달라도 같이 움직이는 종목이 있다.
#
# 1부가 아니라 **2부**다. 1부는 포트폴리오 제약을 보지 않는다(CLAUDE.md).

#: 상관을 볼 거래일. `scoring.MOMENTUM_6M_DAYS` 와 **같은 숫자**다 — 새 값을 만들지 않았다
CORR_WINDOW = 126
#: 겹치는 날이 이보다 적은 보유 종목은 **세지 않는다**. 베타·잔차 변동성과 같은 하한
CORR_MIN_POINTS = 60
#: 이 값을 넘는 상관부터 줄인다. `[확인필요: 실측으로 조정]` (docs/signals.md 3.6)
CORR_THRESHOLD = 0.7

# 2부 제외 사유의 정의처는 `notify/report_sections` 하나다 (docs/infra.md 25.190).
# 여기 따로 적혀 있어서 문서 표가 넷만 알고 있었다. 이름은 그대로 두어 부르는 쪽이 안 바뀐다
EXCLUDED_NO_SETTING = rs.EXCLUDED_NO_SETTING
EXCLUDED_BELOW_MIN_ORDER = rs.EXCLUDED_BELOW_MIN_ORDER

FACTOR_LABELS = (
    ("value", "밸류"),
    ("quality", "퀄리티"),
    ("growth", "성장"),
    ("momentum", "모멘텀"),
    ("risk", "안정성"),
)


@dataclass
class SignalRow:
    """signals 한 행과 그 종목의 점수·성과·종가. DB 에서 읽은 그대로다."""

    stock_id: int
    ticker: str
    name: str
    market: str
    horizon: str
    signal_type: str
    currency: str
    buy_zone_low: float | None
    buy_zone_high: float | None
    suggested_weight_pct: float | None
    suggested_amount: float | None
    size_reduction: float
    tranche_plan: list[dict[str, Any]] = field(default_factory=list)
    rationale_text: str = ""
    as_of_date: str = ""
    total_score: float | None = None
    rank_in_market: int | None = None
    factor_scores: dict[str, float | None] = field(default_factory=dict)
    close: float | None = None
    close_date: str | None = None
    #: 신호의 근거표(`signals.rationale_data.criteria`). 리포트 항목에 실려 화면이 펼친다 (docs/infra.md 25.349)
    criteria: list[dict[str, Any]] = field(default_factory=list)
    perf_window: str | None = None  # 성과 지표 창·기준일 (25.489)
    perf_as_of: str | None = None
    cagr: float | None = None
    mdd: float | None = None
    sharpe: float | None = None
    sector: str | None = None
    #: 종합 점수에 들어간 센티먼트(−100~+100, `scores.sentiment_score`). 1부에 분리 표시한다 (docs/infra.md 25.323)
    sentiment: float | None = None


def parse_json(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def select_top(rows: list[SignalRow], n: int = TOP_STOCKS) -> list[SignalRow]:
    """점수 높은 서로 다른 종목 n 개의 신호 전부.

    점수가 없는 종목은 뒤로 보낸다. 같으면 티커 순으로 고정해 실행마다 같은
    결과가 나오게 한다.
    """
    best: dict[int, SignalRow] = {}
    for row in rows:
        if row.stock_id not in best:
            best[row.stock_id] = row
    ranked = sorted(
        best.values(),
        key=lambda r: (r.total_score is None, -(r.total_score or 0), r.ticker),
    )
    chosen = {r.stock_id for r in ranked[:n]}
    order = {r.stock_id: i for i, r in enumerate(ranked)}
    horizon_order = {"short": 0, "mid": 1, "long": 2}
    return sorted(
        (r for r in rows if r.stock_id in chosen),
        key=lambda r: (order[r.stock_id], horizon_order.get(r.horizon, 9)),
    )


def to_picks(
    rows: list[SignalRow],
    shaken: robustness.Shaken | None = None,
    history: dict[int, ph.PickHistory] | None = None,
) -> list[rs.StockPick]:
    """1부 항목. `shaken` 이 있으면 종목마다 흔들기 결과(docs/reports.md 3.2)를, `history` 가 있으면 추천 이력(3.3)을
    붙인다 — 기간 행마다 같은 값이다. `history` 가 None 이면(못 읽음·옛 호출부) 이력 줄을 적지 않는다; 사전에 없는
    종목은 "첫 추천" 이다."""
    picks: list[rs.StockPick] = []
    for row in rows:
        흔들기 = shaken.by_stock.get(row.stock_id) if shaken else None
        이력 = None if history is None else (history.get(row.stock_id) or ph.PickHistory(0, None, None, 0)).as_payload()
        factors = {
            label: row.factor_scores[key]
            for key, label in FACTOR_LABELS
            if row.factor_scores.get(key) is not None
        }
        picks.append(
            rs.StockPick(
                ticker=row.ticker,
                stock_id=row.stock_id,
                name=row.name,
                market=row.market,
                horizon=row.horizon,
                close=row.close,
                close_date=row.close_date,
                criteria=row.criteria,
                as_of_date=row.as_of_date or None,
                currency=row.currency,
                total_score=row.total_score,
                rank_in_market=row.rank_in_market,
                factor_scores=factors,  # type: ignore[arg-type]
                signal_type=row.signal_type,
                buy_zone_low=row.buy_zone_low,
                buy_zone_high=row.buy_zone_high,
                cagr=row.cagr,
                mdd=row.mdd,
                sharpe=row.sharpe,
                perf_window=row.perf_window,
                perf_as_of=row.perf_as_of,
                rationale=row.rationale_text,
                sentiment=row.sentiment,
                stability=흔들기.as_payload() if 흔들기 else None,
                history=이력,
            )
        )
    return picks


DEFAULT_MAX_SECTOR_PCT = 30.0  # CLAUDE.md 매매 규칙 "단일 섹터 상한 30%". 설정 max_weight_per_sector
DEFAULT_MAX_STOCK_PCT = 10.0  # CLAUDE.md "비중 상한 기본 10%". 설정 max_weight_per_stock



def pearson(a: list[float], b: list[float]) -> float | None:
    """피어슨 상관. 표본이 모자라거나 한쪽이 안 움직이면 **모른다**.

    분모가 0 이면 None 이다 — 0 으로 나누지 않고, "상관이 0" 이라고 단정하지도 않는다.
    """
    n = min(len(a), len(b))
    if n < 2:
        return None
    x, y = a[:n], b[:n]
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((p - mx) * (q - my) for p, q in zip(x, y, strict=True))
    sxx = sum((p - mx) ** 2 for p in x)
    syy = sum((q - my) ** 2 for q in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _aligned_returns(
    left: dict[str, float], right: dict[str, float]
) -> tuple[list[float], list[float]]:
    """겹치는 날짜만 남겨 일간 수익률 두 줄. 날짜를 안 맞추면 값이 통째로 틀린다.

    창은 **뒤에서** 자른다(최근이 중요하다). 맞추는 규칙은 **`scoring.aligned_returns` 를 그대로 부른다**
    (docs/infra.md 25.277) — 예전에는 "같은 규칙" 이라 적고 따로 구현해, 11일 넘게 벌어진 쌍(거래정지 뒤 재개, 25.228)을
    하루 수익률로 셌다. 그 하루가 상관계수를 끌어 2부의 금액 축소(`correlation_reduction`)가 달라졌다.
    """
    from batch.services import scoring as sc_

    공통 = sorted(set(left) & set(right))[-(CORR_WINDOW + 1) :]
    if len(공통) < 2:
        return [], []
    return sc_.aligned_returns({d: left[d] for d in 공통}, {d: right[d] for d in 공통})


def correlation_reduction(
    candidate: dict[str, float],
    holdings: list[tuple[int, str, dict[str, float]]],
    *,
    exclude_stock_id: int | None = None,
    threshold: float = CORR_THRESHOLD,
    floor: float = sig.REDUCTION_FLOOR,
) -> tuple[float, dict]:
    """(배수, 근거). 보유와 가장 닮은 정도만큼 2부 금액을 줄인다 (docs/signals.md 3.6).

    `holdings` 는 (종목번호, 이름, {날짜: 종가}) 다.

    **모르면 줄이지 않는다** (3.1·3.5 와 같은 원칙). 보유가 없거나, 겹치는 날이
    모자라거나, 상관을 못 내면 배수는 1.0 이고 그 사실을 근거에 남긴다.

    **가장 닮은 한 종목**을 본다. 평균은 한 종목과 0.95 인 사실을 낮은 상관들이 희석한다 —
    분산의 적은 가장 닮은 하나다.

    **이미 보유한 그 종목 자신은 뺀다**(`exclude_stock_id`). 같은 종목을 더 사는 것은
    상관이 아니라 종목 비중 상한이 다룬다. 두 번 줄이면 안 된다.

    `floor` 의 기본값은 `signals.REDUCTION_FLOOR` 를 **가져다 쓴다** — 여기서 0.3 을
    다시 적으면 한 규칙이 두 곳에 생긴다(docs/infra.md 25.0).
    """
    최고: float | None = None
    닮은이름: str | None = None
    닮은번호: int | None = None
    본것 = 0
    for stock_id, name, series in holdings:
        if exclude_stock_id is not None and stock_id == exclude_stock_id:
            continue
        좌, 우 = _aligned_returns(candidate, series)
        if len(좌) < CORR_MIN_POINTS:
            continue
        본것 += 1
        r = pearson(좌, 우)
        if r is None:
            continue
        if 최고 is None or r > 최고:
            최고, 닮은이름, 닮은번호 = r, name, stock_id

    if 최고 is None:
        return 1.0, {
            "note": (
                "보유 종목이 없어 상관으로 줄이지 않았습니다"
                if not holdings
                # 겹친 날은 됐는데 한쪽이 움직이지 않았다(거래정지로 종가 고정 등) — "겹치는 날이 없어" 는 틀린
                # 말이었다 (25.707)
                else f"비교한 보유 종목 {본것}개가 모두 움직이지 않아(거래정지 등) 상관을 내지 못했습니다"
                if 본것
                else f"겹치는 거래일이 {CORR_MIN_POINTS}일 이상인 보유 종목이 없어 상관을 내지 못했습니다"
            ),
            "compared": 본것,
        }

    근거 = {
        "max_correlation": 최고,
        "most_similar": 닮은이름,
        "most_similar_id": 닮은번호,
        "correlation_window": CORR_WINDOW,
        "correlation_threshold": threshold,
        "compared": 본것,
    }
    if 최고 <= threshold:
        근거["correlation_factor"] = 1.0
        return 1.0, 근거

    배수 = min(max(1.0 - (최고 - threshold) / (1.0 - threshold), floor), 1.0)
    근거["correlation_factor"] = 배수
    return 배수, 근거


def _맞춘_분할(
    tranches: list[dict[str, Any]], total: float, 단위: Any
) -> list[dict[str, Any]]:
    """회차 금액을 통화 단위로 맞추고 합이 `total` 이 되게 끝수를 1차에 모은다 (25.568·25.570).

    2·3차는 **내림**, 1차가 나머지를 받는다 — 1차는 반올림 전보다 **작아지지 않는다**. 한 주 검사(25.514)는 이 함수가
    낸 회차 그대로 한다(25.572) — 예전에는 반올림 전 금액으로 해, 검사는 통과했는데 표시된 모든 회차가 한 주도 못 사는
    배분이 실렸다 (25.570·25.572, 교차검증)
    """
    금액들 = [t for t in tranches if t.get("amount") is not None]
    if not 금액들:
        return tranches
    한_단위 = 1.0 if 단위(0.4) == 0.0 else 0.01  # 원은 정수, 달러는 센트
    for t in 금액들[1:]:
        t["amount"] = 단위(math.floor(float(t["amount"]) / 한_단위 + 1e-9) * 한_단위)
    금액들[0]["amount"] = 단위(total - sum(t["amount"] for t in 금액들[1:]))
    return tranches


def _scaled_tranches(plan: list[dict[str, Any]], 원래: float, 줄인것: float) -> list[dict[str, Any]]:
    """2부에서 금액을 줄였으면 **분할 계획도 같은 비율로 줄인다** (docs/infra.md 25.96).

    왜 필요한가. `signals.tranche_plan` 의 비중 합은 **정확히 1.0** 이다 — 즉 분할 금액의
    합이 1부의 권장 금액이다. 2부는 그 금액을 종목 상한(3.2)과 상관 분산(3.6)으로 줄이는데,
    분할 계획을 그대로 실으면 화면이 이렇게 된다.

        종목A  50만원 · 보유 포함 종목 상한 10%까지만
        분할: 1차 40만 → 2차 30만 → 3차 30만          ← 합이 100만이다

    **계획대로 사면 권장액의 두 배를 산다.** 상관 바닥(0.3)까지 겹치면 3.3배다. 그리고
    그 초과분은 다름 아닌 **종목 비중 상한을 깨는 금액**이다 — 줄인 이유가 바로 그 상한이었다.
    `CLAUDE.md` 의 "비중 상한 기본 10%" 가 그 자리에서 무너진다.

    합이 아니라 **비율로** 곱한다. 원래 금액이 0 이하이면(권장 금액이 없던 신호) 줄일 것도
    없으므로 그대로 둔다. 금액이 없는 칸(`None`)은 만들어 넣지 않는다.
    """
    비율 = 1.0 if 원래 <= 0 else 줄인것 / 원래
    나온것: list[dict[str, Any]] = []
    for t in plan:
        금액 = t.get("amount")
        나온것.append({
            "price_at_or_below": t.get("price"),
            "amount": None if 금액 is None else float(금액) * 비율,
        })  # fmt: skip
    return 나온것


@dataclass(frozen=True)
class Holding:
    """현재 보유 하나. value 는 리포트 통화로 환산한 평가액 (docs/portfolio.md 5장)."""

    stock_id: int
    ticker: str
    name: str
    sector: str | None
    value: float
    #: 보유 종목의 나라. 섹터 상한이 나라를 넘어 합산되는지 말하는 데 쓴다 (docs/infra.md 25.509)
    country: str | None = None
    #: 'stock' 또는 'etf'(25.896). ETF 보유는 여력·상한에는 들어가지만 **상관 축소의 대조에는 넣지 않는다** (25.908)
    asset_type: str = "stock"



def _넘은_비율(값: float, 상한: float) -> str:
    """상한을 넘은 합계 비율. 반올림하면 "→ 30.0%, 상한 30%" 로 넘지 않은 듯 보여 자릿수를 늘린다 (25.653, 교차검증)."""
    for 자리 in (1, 2, 3):
        if round(값, 자리) > 상한:
            return f"{값:.{자리}f}%"
    return f"{값:.3f}% 초과" if 값 > 상한 else f"{값:.1f}%"

def build_portfolio(
    rows: list[SignalRow],
    total_investable: float,
    currency: str = "KRW",
    budget_note: str | None = None,
    max_sector_pct: float = DEFAULT_MAX_SECTOR_PCT,
    holdings: list[Holding] | None = None,
    max_stock_pct: float = DEFAULT_MAX_STOCK_PCT,
    price_series: dict[int, dict[str, float]] | None = None,
    min_order_amount: float = 0.0,
    fx_missing: bool = False,
    reserved: float = 0.0,
) -> rs.PortfolioView:
    """2부. 종목당 배분 하나, 빠지면 사유. 보유를 포함해 계산한다 (docs/design.md 3.9, Step 30).

    - 남은 여력 = 총 투자가능금액 − 현재 보유 평가액. 이미 산 것을 두 번 세지 않는다
    - 종목 상한: 보유 평가 + 새 배분 ≤ 총액 × 종목 상한. 이미 찼으면 "종목 비중 상한 도달"
    - 섹터 상한·집중도: 보유 + 배분. 업종을 모르는 종목은 상한을 적용할 수 없어 세지 않는다(signals.sector_cap_note)
    - **상관 기반 분산**: 보유와 가장 닮은 정도만큼 금액을 줄인다 (docs/signals.md 3.6).
      `price_series` 는 `{종목번호: {날짜: 종가}}` 이고 후보와 보유를 **둘 다** 담는다.
      **안 넘기면 줄이지 않는다** — 모르는 것을 나쁘다고 단정하지 않는다(docs/infra.md 25.0)

    - **최소 주문 금액**: 줄이고 나서 **다시 본다**(docs/infra.md 25.96).
      1부는 줄이기 **전** 금액으로 한 번 걸렀을 뿐이다. `0` 이면 안 본다(옛 호출부)

    상한들보다 **먼저** 줄인다. 줄인 뒤의 금액으로 상한을 보는 것이 맞다 — 순서를 바꾸면
    상한에 걸려 잘린 금액을 또 줄여 **두 번 깎는다**.
    """
    holdings = holdings or []
    price_series = price_series or {}
    # **ETF 보유는 대조하지 않는다** (docs/infra.md 25.908, 감사). 상관 축소(signals.md 3.6)는 닮은 개별 종목에 몰리는
    # 것을 막는
    # 규칙이다. 넓은 지수 ETF(KODEX 200·VOO)는 거의 모든 대형주와 0.7 을 넘어, 적립 ETF 를 산 사용자의 2부 금액이
    # 통째로 30~70% 깎였다. ETF 는 종목 추천과 따로 본다(CLAUDE.md)
    보유계열 = [
        (h.stock_id, h.name, price_series[h.stock_id]) for h in holdings
        if h.stock_id in price_series and h.asset_type != "etf"
    ]
    # 25.712 — 이번 리포트에서 먼저 배분돼 대조에 들어간 후보. 종목 번호로 센다 — 이름은 겹칠 수 있다 (25.716)
    앞선_후보: set[int] = set()
    held_total = sum(h.value for h in holdings)
    # **통화의 최소 단위로 반올림한다** (docs/infra.md 25.563, 감사 재현). 정수로만 자르면 미국 총액 $35,273.90 이
    # "$35,273.00"(버림), 권장 $1,234.56 이 "$1,235.00" 이 되어 분할 합($1,234.56)과 한 화면에서 어긋났다
    def 단위(v: float) -> float:
        return float(round(v)) if currency == "KRW" else round(v, 2)

    view = rs.PortfolioView(
        total_budget=단위(total_investable) if total_investable > 0 else None,
        budget_currency=currency,
        budget_note=budget_note,
        holdings_value=held_total if holdings else None,
        holdings_count=len(holdings),
    )
    held_by_stock = {h.stock_id: h.value for h in holdings}

    by_stock: dict[int, list[SignalRow]] = {}
    for row in rows:
        by_stock.setdefault(row.stock_id, []).append(row)

    # **원화 풀은 하나다** (docs/design.md 3.9, docs/infra.md 25.508, 감사). `reserved` 는 다른 나라 리포트가
    # 방금 2부에서 배분한 금액(리포트 통화) — 빼지 않으면 국내·미국 리포트가 같은 여력을 각자 전부 썼다
    remaining = max(total_investable - held_total - max(reserved, 0.0), 0.0)
    sector_used: dict[str, float] = {}
    for h in holdings:
        if h.sector:
            sector_used[h.sector] = sector_used.get(h.sector, 0.0) + h.value
    # **분모 = 총 투자가능금액, 보유가 더 크면 보유 합** (docs/design.md 원화 단일 풀, docs/infra.md 25.238·25.294).
    # 화면(25.238)만 고치고 여기는 총액만 썼다 — 보유가 총액을 넘은 날 두 곳의 % 와 사유가 달랐다
    분모 = max(total_investable, held_total) if total_investable > 0 else 0.0
    sector_limit = 분모 * max_sector_pct / 100 if 분모 > 0 else None
    stock_limit = 분모 * max_stock_pct / 100 if 분모 > 0 else None
    for stock_rows in by_stock.values():
        first = stock_rows[0]
        funded = [r for r in stock_rows if r.suggested_amount is not None]

        # **오늘 총액이 없으면 신호에 금액이 남아 있어도 그 사유로 뺀다** (docs/infra.md 25.507, 감사). 신호가 묵은 날
        # 예전에는 "투자 여력 부족 (남은 0원)" 이라 했다 — 원인은 총액 미설정·환율 없음이다
        if not funded or total_investable <= 0:
            view.excluded.append(
                rs.Excluded(first.ticker, first.name, _reason(first, total_investable, fx_missing, min_order_amount))
            )
            continue

        best = max(funded, key=lambda r: r.suggested_amount or 0)
        #: 1부(신호)가 낸 금액. **분할 계획의 합이 이것과 같다**(`signals.tranche_plan`: 비중 합 1.0).
        #: 2부에서 줄인 뒤 분할을 그대로 두면 둘이 어긋난다 — 아래 `_scaled_tranches` 가 맞춘다
        원래금액 = float(best.suggested_amount or 0)
        amount = 원래금액
        capped_note = ""

        # 상관 기반 분산 (docs/signals.md 3.6). 후보의 시세가 없으면 **그냥 안 줄인다**
        corr_note, corr_근거 = "", {}
        # 문구가 없어도(1% 미만) 금액은 줄었다 — 아래 "신호 낸 날 총액" 안내가 그 차이를 오해하지 않게 (25.689)
        상관_배수 = 1.0
        #: 상관을 **대 보지 못한** 까닭 (25.700, 감사). 줄인 문구(corr_note)와 달리 "신호 낸 날" 안내를 막지 않는다
        상관_못봄 = ""
        후보계열 = price_series.get(first.stock_id)
        # 같은 종목을 더 사는 것은 상관을 보지 않는다(종목 상한이 다룬다) — "못 봤다" 도 다른 보유가 있을 때만 말한다
        다른_보유 = any(sid != first.stock_id for sid, _n, _s in 보유계열)
        진짜_보유 = any(sid != first.stock_id and sid not in 앞선_후보 for sid, _n, _s in 보유계열)
        대상말 = "보유 종목" if not 앞선_후보 else ("보유·앞선 후보 종목" if 진짜_보유 else "앞선 후보 종목")
        if 후보계열 and 보유계열:
            배수, corr_근거 = correlation_reduction(
                후보계열, 보유계열, exclude_stock_id=first.stock_id
            )
            if 배수 < 1.0:
                amount *= 배수
                상관_배수 = 배수
            # 반올림해 1% 가 안 되는 축소는 적지 않는다 (25.687, 감사) — 상관 0.7005 가 "금액 0% 줄임" 으로 나갔다
            if round((1 - 배수) * 100) >= 1:
                닮은 = str(corr_근거["most_similar"])
                누구 = "이번 리포트의 앞선 후보" if corr_근거.get("most_similar_id") in 앞선_후보 else "보유"
                corr_note = (
                    f"{누구} {닮은} 와 상관 {corr_근거['max_correlation']:.2f}"
                    f" — 금액 {(1 - 배수) * 100:.0f}% 줄임"
                )
            elif 배수 >= 1.0 and 다른_보유 and corr_근거.get("note"):
                # 겹치는 거래일이 모자라 상관을 못 낸 것 — 예전에는 `rationale` 에만 있고 텔레그램·웹 어디에도 안 나왔다
                # 대조 대상에 앞선 후보가 섞였으면 "보유 종목" 이라 부르지 않는다 (25.716, 교차검증 — 보유가 없는데
                # "보유 종목과")
                상관_못봄 = f"상관 미확인 ({str(corr_근거['note']).replace('보유 종목', 대상말)})"
        elif 다른_보유 and not 후보계열:
            상관_못봄 = f"상관 미확인 (이 종목 시세가 없어 {대상말}과 대 보지 못했습니다)"

        held = held_by_stock.get(first.stock_id, 0.0)
        # **보유 종목은 1부가 낸 목표 금액(줄인 비중)까지만 채운다** (docs/infra.md 25.560, 감사 재현). 1부 금액은
        # `총액 × 줄인 비중` 인데 보유를 보지 않는다 — 여기서 10% 상한만 보면, 변동성·MDD·약세장으로 4.2% 로 줄인
        # 종목을 이미 4.2% 가진 날 또 4.2% 를 배분해 며칠 만에 10% 까지 찼다. 비중 축소가 보유 종목에서 무력해졌다
        # 목표는 **신호의 (줄인) 비중 × 오늘 분모**다 — 금액은 신호 낸 날 총액 기준이라 총액이 바뀌면 어긋난다(25.193)
        권장비중 = float(best.suggested_weight_pct or 0)
        # **목표에도 상관 배수를 곱한다** (docs/infra.md 25.1088, 감사 재현). 예전 목표는 (줄인) 비중 × 분모라 상관
        # 축소가 그날 산 몫에만 걸렸다 — 보유 B 와 상관 0.95(배수 0.3)인 후보가 날마다 다시 뽑히면 300만씩 더해
        # 나흘 만에 10% 가 찼다(의도는 3%). 리포트는 날마다 "금액 70% 줄임" 이라 했다
        목표 = (권장비중 / 100 * 분모 if 권장비중 > 0 and 분모 > 0 else 원래금액) * 상관_배수
        # 목표가 종목 상한 이상이면 아래 상한 검사가 같은 일을 한다(사유도 "종목 상한" 으로 남는다)
        if held <= 0 and 목표 > 0 and (stock_limit is None or 목표 < stock_limit) and amount > 목표 + 0.5:
            # **보유하지 않은 종목도 오늘 총액 기준 목표까지만** (25.1088, 감사 재현). 1부 금액은 신호 낸 날 총액
            # 기준이라, 신호가 하루 묵었거나 총액을 줄인 날 변동성·MDD 로 4% 로 줄인 종목에 상한 10% 까지 배분됐다
            amount = 목표
            capped_note = f"권장 비중 {목표 / 분모 * 100:.1f}%까지만(오늘 총액 기준)"
        if held > 0 and 목표 > 0 and (stock_limit is None or 목표 < stock_limit):
            목표_여지 = 목표 - held
            if 목표_여지 <= 0:
                # **종목 상한까지 넘었으면 그것을 적는다** (25.964). 10-06 리포트 "(종목) — 보유 33.1% — 이미 권장
                # 비중(4.1%)만큼 보유" 는 상한 10% 의 세 배를 들고 있다는 사실을 "만큼" 으로 덮었다
                넘음 = (
                    f", 종목 상한 {max_stock_pct:g}% 도 넘음"
                    if stock_limit is not None and held > stock_limit + 0.5 else ""
                )
                view.excluded.append(rs.Excluded(
                    first.ticker, first.name, rs.EXCLUDED_STOCK_CAP,
                    f"보유 {held / 분모 * 100:.1f}% — 이미 권장 비중({목표 / 분모 * 100:.1f}%) 이상 보유{넘음}",
                ))
                continue
            if amount > 목표_여지:
                amount = 목표_여지
                capped_note = f"보유 포함 권장 비중 {목표 / 분모 * 100:.1f}%까지만"
        # **보유하지 않은 종목도 상한을 본다** (docs/infra.md 25.290). 예전에는 `if held and …` 라
        # 새 종목은 건너뛰었다 —
        # 신호 금액은 **신호 낸 날의 총액** 기준이라, 신호가 묵은 채 사용자가 총액을 줄이면 오늘 총액의 10% 를 넘는다
        if stock_limit is not None:
            room = stock_limit - held
            if room <= 0:
                view.excluded.append(rs.Excluded(
                    first.ticker, first.name, rs.EXCLUDED_STOCK_CAP,
                    f"보유 {held / 분모 * 100:.1f}%, 상한 {max_stock_pct:g}%",
                ))
                continue
            if amount > room:
                amount = room
                capped_note = f"{'보유 포함 ' if held else ''}종목 상한 {max_stock_pct:g}%까지만"
        # 부동소수 여유 (25.658, 교차검증): 상한 10% 로 깎인 열 종목이 산술로 정확히 100% 인데 아홉 번 뺀 남은 여력이
        # 열째 금액보다 한 끗 작아져 "투자 여력 부족" 으로 빠졌다. 업종 상한(25.656)과 같이 0.5 통화 단위 안쪽은
        # 들어간다
        if amount > remaining + 0.5:
            view.excluded.append(
                rs.Excluded(first.ticker, first.name, rs.EXCLUDED_NO_BUDGET, f"남은 {rs._money(remaining, currency)}")
            )
            continue

        sector = first.sector
        # 부동소수 여유 (25.656, 교차검증): 종목 상한 10% 로 깎인 세 종목(분모×10/100 셋)이 산술로 정확히 30% 인데
        # `a+a+a > 분모×0.3` 이 참이 되어 빠졌다. 0.5 통화 단위(원화 1원 미만, 달러 50센트) 안쪽이면 상한 안으로 본다
        if sector and sector_limit is not None and sector_used.get(sector, 0.0) + amount > sector_limit + 0.5:
            view.excluded.append(
                rs.Excluded(
                    first.ticker, first.name, rs.EXCLUDED_SECTOR_CAP,
                    # 설정값은 그대로(`:g`), 쓴 비율은 한 자리까지 —
                    # 반올림으로 "이미 30%, 상한 30%" 가 되지 않게 (25.369)
                    # 이 종목 몫과 합친 비율도 적는다 — "이미 25%, 상한 30%" 만으로는 왜 빠졌는지 읽히지 않았다 (25.649)
                    f"{sector} 이미 {sector_used.get(sector, 0.0) / 분모 * 100:.1f}%"
                    f" + 이 종목 {amount / 분모 * 100:.1f}%"
                    f" → {_넘은_비율((sector_used.get(sector, 0.0) + amount) / 분모 * 100, max_sector_pct)},"
                    f" 상한 {max_sector_pct:g}%",
                )
            )  # fmt: skip
            continue

        # **줄이고 나서 최소 주문 금액을 다시 본다** (2026-09-21, docs/infra.md 25.96).
        # 1부(`services/signals`)는 줄이기 **전** 금액으로 한 번 걸렀다. 2부가 상한·상관으로
        # 깎고 나면 그 문턱 아래로 내려갈 수 있는데 아무도 다시 보지 않았다 —
        # 30만이 9만이 되어도 그대로 실렸다. 사라고 내놓은 금액이 **한 주도 못 사는 돈**이면
        # 그것은 추천이 아니다. 빼면서 **왜 빠졌는지**를 남긴다.
        if min_order_amount > 0 and amount < min_order_amount:
            깎인것 = " · ".join(n for n in (corr_note, capped_note) if n)
            view.excluded.append(rs.Excluded(
                first.ticker, first.name, EXCLUDED_BELOW_MIN_ORDER,
                # 통화를 붙인다 (25.324)
                f"{rs._money(amount, currency)} < 최소 {rs._money(min_order_amount, currency)}"
                + (f" ({깎인것})" if 깎인것 else ""),
            ))  # fmt: skip
            continue

        # **배분 전체가 한 주 값보다 작으면(또는 반올림해 0 이면) 뺀다** (docs/design.md 3.9 "최소 주문 단위 미만",
        # docs/infra.md 25.507, 감사). 최소 주문 금액만 봐서 $900 짜리 종목에 $500 이 배분으로 실리고 여력에서 빠졌다.
        # 한 주 값은 분할 회차 가운데 가장 낮은 가격 — 어느 회차에서도 한 주를 못 사면 추천이 아니다
        한주 = _cheapest_share(best)
        # **회차별로 본다** (docs/infra.md 25.514, 교차검증). 금액 전체를 가장 싼 회차 가격과 견주면 $1,000 을
        # 40/30/30 으로 나눠 $1,000·$950·$900 회차에 싣는 배분이 통과했다 — 세 회차 모두 "1주 값에 못 미침" 인데도.
        # 어느 회차에서도 한 주를 못 사면 뺀다. 회차가 없으면 금액 전체로 본다
        # **화면에 싣는 회차 그대로**(통화 단위로 맞추고 끝수를 1차에 모은 뒤) 본다 (25.572, 교차검증).
        # 반올림 전 금액으로 보면 검사는 3차 1주로 통과하는데 표시된 3차는 내림돼 한 주 아래였다 —
        # 모든 회차가 0주인 배분이 실렸다
        회차 = _맞춘_분할(_scaled_tranches(best.tranche_plan, 원래금액, 단위(amount)), 단위(amount), 단위)
        살_수_있는_주 = sum(
            int(float(t["amount"]) // float(t["price_at_or_below"])) for t in 회차
            if t.get("amount") and t.get("price_at_or_below") and float(t["price_at_or_below"]) > 0
        )  # fmt: skip
        회차로_봄 = any(t.get("amount") and t.get("price_at_or_below") for t in 회차)
        if int(round(amount)) <= 0 or (
            best.currency == currency
            and ((회차로_봄 and 살_수_있는_주 == 0) or (not 회차로_봄 and 한주 is not None and amount < 한주))
        ):
            # 사유가 거짓이 되지 않게 판정한 방식대로 적는다 (25.523, 교차검증) — 회차로 봤는데 "금액 < 가장 싼 1주" 라
            # 적으면 "$2,000.00 < 1주 $900.00" 이 됐다
            if int(round(amount)) <= 0:
                사유 = rs._money(amount, currency)
            elif 회차로_봄:
                사유 = f"{rs._money(amount, currency)} — 분할 회차마다 금액이 1주 값보다 작습니다"
            else:
                사유 = f"{rs._money(amount, currency)} < 1주 {rs._money(한주 or 0, currency)}"
            view.excluded.append(rs.Excluded(first.ticker, first.name, EXCLUDED_BELOW_MIN_ORDER, 사유))
            continue

        remaining = max(0.0, remaining - amount)  # 여유(25.658)로 한 끗 음수가 되지 않게
        if sector:
            sector_used[sector] = sector_used.get(sector, 0.0) + amount
        notes: list[str] = []
        if len(stock_rows) > 1:
            notes.append(f"{rs.HORIZON_LABEL.get(best.horizon, best.horizon)} 신호 기준 (기간별 금액은 더하지 않음)")
        if corr_note:
            notes.append(corr_note)
        if 상관_못봄:
            notes.append(상관_못봄)
        if capped_note:
            notes.append(capped_note)
        if not sector:
            # **업종을 모르면 섹터 상한을 못 본다 — 그 사실을 2부에 적는다** (docs/infra.md 25.194).
            # 위 `if sector and …` 는 업종이 없으면 조용히 건너뛴다. 1부 신호에는
            # `sector_cap_note` 가 붙어 **웹**에는 뜨는데, 돈을 나누는 **텔레그램 2부**에는
            # 아무 말이 없었다 (CLAUDE.md 매매 규칙: 단일 섹터 상한 30%).
            # 업종은 국내 DART `induty_code`·미국 SEC `sic` 로 채우지만(jobs/sectors) 둘 다
            # 빠지는 종목이 있고, 업종 배치가 멈춘 동안 새로 편입된 종목도 비어 있다
            notes.append(sig.SECTOR_CAP_UNAVAILABLE)

        # **비중은 늘 적은 금액에서 낸다** (2026-09-26, docs/infra.md 25.193).
        weight_pct = (
            amount / total_investable * 100
            if total_investable > 0
            else float(best.suggested_weight_pct or 0)
        )
        # 안 깎였는데도 신호의 비중과 다르면 금액이 **다른 총액**에서 나온 것이다.
        # 신호가 어제 것이면 그렇게 된다 — 신호 단계가 실패해도 리포트는 나간다
        신호비중 = float(best.suggested_weight_pct or 0)
        # 상관으로 1% 미만 줄인 몫은 빼고 견준다 (25.700, 교차검증) — 25.689 는 그때 안내를 통째로 지워, 어제 신호에
        # 총액이
        # 크게 바뀐 날(10% → 8%)의 차이가 설명 없이 남았다
        # 상관 문구가 있어도 막지 않는다 — 상관 배수를 이미 곱해 견주므로 남는 차이는 총액 변화뿐이다 (25.707, 교차검증)
        if not capped_note and 신호비중 > 0 and abs(weight_pct - 신호비중 * 상관_배수) > 0.05:
            notes.append(
                f"금액은 신호를 낸 날({best.as_of_date or '?'})의 총 투자가능금액 기준입니다"
                f" — 지금 총액으로는 {weight_pct:.1f}% (신호 때 {신호비중:.1f}%"
                # 상관으로 줄였으면 견준 값(신호 비중 × 상관 배수)을 함께 — "10% → 6%" 가 총액 탓으로 줄어든 것처럼
                # 읽혔다 (25.710)
                + (f", 상관 반영 {신호비중 * 상관_배수:.1f}%" if 상관_배수 < 1.0 else "")
                + ")"
            )
        note = " · ".join(notes)
        view.allocations.append(
            rs.Allocation(
                ticker=best.ticker,
                name=best.name,
                amount=단위(amount),
                # 줄였으면 비중도 줄인 금액 기준으로 (총액 대비).
                # **상관으로 줄인 것도 마찬가지다** — 금액만 줄이고 비중을 그대로 두면
                # 리포트의 두 숫자가 서로 어긋나고, 합계 비중이 실제보다 커 보인다.
                #
                # 2026-09-26 까지 이 다시 내기가 **깎였을 때만** 돌았다(docs/infra.md 25.193).
                # 안 깎였으면 신호가 저장해 둔 `suggested_weight_pct` 를 썼는데, 그 값은
                # **신호를 낸 날의 총액**으로 낸 것이다. 리포트는 그날 다시 읽은 총액을
                # 머리에 적으므로, 둘이 다르면 "총액 2억" 아래에 "100만원 (10.0%)" 이
                # 실린다 — 실제로는 0.5% 다. 위 주석이 그 이유를 이미 적고 있었고
                # **조건만 좁았다.**
                weight_pct=weight_pct,
                # 회차마다 통화 단위로 반올림하고 **어긋난 끝수는 1차에 모은다** (25.568, 교차검증) —
                # 조각마다 화면이 반올림해 합이 금액과 1원·1센트 어긋났다(무작위 10만 건 중 약 절반).
                # 25.567 의 분모만 바꾼 고침은 효과가 없었다
                tranches=회차,
                note=note,
                currency=best.currency,
                rationale=corr_근거,
            )
        )
        # **같은 리포트에서 앞서 배분된 후보도 다음 후보의 상관 대조에 넣는다** (docs/infra.md 25.712, 감사 재현).
        # 문서 3.6 은
        # "이미 보유한 종목과" 만 대 봐, 거의 같이 움직이는 두 후보가 같은 날 둘 다 전액 배분됐다 — 3.6 이 막으려던
        # 쏠림을
        # 하루 두 종목 매수로 그대로 우회한다. 점수 순으로 앞선 쪽은 그대로, 뒤의 닮은 쪽을 줄인다
        if 후보계열 and amount > 0 and first.stock_id not in {sid for sid, _n, _s in 보유계열}:
            보유계열.append((first.stock_id, best.name, 후보계열))
            앞선_후보.add(first.stock_id)

    # **다른 나라 보유의 업종은 이름이 같을 때만 합산된다** (docs/infra.md 25.509, 감사). 국내 KSIC·미국 SIC 중분류는
    # 같은 산업이어도 이름표가 다르다(반도체: "전자부품·컴퓨터·통신장비" vs "전자·전기장비"). 중분류로는 한 줄씩
    # 대응시킬 수 없어(SIC 28 은 화학과 의약품이 섞였다) 합산 규칙을 바꾸지 않고, 그 한계를 2부에 적는다
    이_나라 = "KR" if currency == "KRW" else "US"
    다른_나라 = [h for h in holdings if h.country and h.country != 이_나라 and h.sector]
    if 다른_나라 and sector_limit is not None:
        view.sector_note = (
            f"다른 나라 보유 {len(다른_나라)}종목의 업종은 분류표가 달라(국내 KSIC·미국 SIC)"
            " 이름이 같은 업종만 섹터 상한에 합산했습니다"
        )

    if total_investable > 0:
        view.remaining_budget = 단위(remaining)  # 총액·배분과 같은 단위 (25.567 — 남은 여력만 정수였다)
        view.sector_concentration = {k: v / 분모 * 100 for k, v in sector_used.items()}
    return view


def _cheapest_share(row: SignalRow) -> float | None:
    """분할 회차 가운데 가장 낮은 가격(없으면 매수 구간 하단). 모르면 None — 모르는 것을 막지 않는다."""
    가격 = [float(t["price"]) for t in (row.tranche_plan or []) if isinstance(t, dict) and t.get("price")]
    if 가격:
        return min(가격)
    return float(row.buy_zone_low) if row.buy_zone_low else None


def _reason(row: SignalRow, total_investable: float, fx_missing: bool = False, min_order_amount: float = 0.0) -> str:
    if total_investable <= 0:
        # 미국 총액이 0 인 까닭이 환율이면 그렇게 말한다 — 설정은 이미 있다 (docs/infra.md 25.292)
        return rs.EXCLUDED_NO_FX if fx_missing else EXCLUDED_NO_SETTING
    if row.size_reduction <= 0:
        return rs.EXCLUDED_BEAR_ZERO
    # 신호 금액이 비는 길은 둘이다 — 최소 주문 미만, 또는 신호 계산 때 총액·환율이 없었다.
    # **오늘 총액 × 신호 비중이 최소 주문 이상이면** 주문 단위 탓이 아니다 (docs/infra.md 25.600, 감사).
    # 예전에는 늘 "최소 주문 단위 미만" 이라 1부 근거표("환율이 없어 내지 않았습니다")와 어긋났다
    if row.suggested_weight_pct and total_investable * row.suggested_weight_pct / 100 >= max(min_order_amount, 1e-9):
        return rs.EXCLUDED_SIGNAL_NO_AMOUNT
    return EXCLUDED_BELOW_MIN_ORDER


@dataclass
class Composed:
    """리포트 본문과 그것을 만든 재료. 저장(services/reports)이 재료를 report_items 로 남긴다."""

    text: str
    chosen: list[SignalRow]
    picks: list[rs.StockPick]
    portfolio: rs.PortfolioView | None
    #: 가중치 흔들기 결과 (docs/reports.md 3.2). 후보가 적거나 가중치를 못 받았으면 None
    shaken: robustness.Shaken | None = None


def with_criteria(rows: list[SignalRow]) -> list[SignalRow]:
    """근거표가 있는 종목의 행만 (docs/infra.md 25.489). 한 기간의 근거표만 깨져도 종목 전체를 뺀다.

    **2부 후보를 미리 읽는 곳도 이것을 거친다** (docs/infra.md 25.507, 감사). 상관용 시세를 거르기 전 상위 다섯으로
    읽으면, 근거표가 빈 종목 대신 들어온 6위 종목은 시세가 없어 상관 축소를 받지 않았다.
    """
    근거없음 = {r.stock_id for r in rows if not crit.usable(r.criteria)}
    return [r for r in rows if r.stock_id not in 근거없음]


def compose(
    rows: list[SignalRow],
    total_investable: float,
    signals_as_of: str | None,
    currency: str = "KRW",
    budget_note: str | None = None,
    max_sector_pct: float = DEFAULT_MAX_SECTOR_PCT,
    sell_flags: list[dict] | None = None,
    regime_line: str | None = None,
    holdings: list[Holding] | None = None,
    max_stock_pct: float = DEFAULT_MAX_STOCK_PCT,
    price_series: dict[int, dict[str, float]] | None = None,
    min_order_amount: float = 0.0,
    fx_missing: bool = False,
    reserved: float = 0.0,
    no_allocation_reason: str | None = None,
    app_url: str | None = None,
    weights: dict[str, float] | None = None,
    sentiment_weight: float = 0.0,
    pick_history: dict[int, ph.PickHistory] | None = None,
    self_grade_line: str | None = None,
    holding_scores: list[hsc.HoldingScore] | None = None,
) -> Composed:
    """두 부를 이어 붙인다. 기준일을 맨 위에 적는다. "이게 언제 것인가" 가 첫 질문이다.

    매도 플래그는 추천이 없는 날에도 싣는다. 보유 종목의 경고는 추천과 무관하게 알려야 한다.
    regime_line 은 시장 국면 한 줄(docs/signals.md 3.5). 비중이 왜 줄었는지 머리에서 보인다.
    `weights`·`sentiment_weight` 는 점수 계산에 쓴 설정 가중치 — 있으면 1부 종목마다 가중치 흔들기
    (docs/reports.md 3.2)를 붙인다. 없으면(옛 호출부·테스트) 전과 같다.
    """
    # **근거표를 만들 수 없는 추천은 싣지 않는다** (CLAUDE.md, docs/infra.md 25.489 텔레그램 감사). `rationale_data` 가
    # 깨졌거나 기준이 비면 예전에는 `[중기] 이름` 한 줄로 그대로 나갔다. 뺀 수는 머리에 적는다(조용히 빼지 않는다)
    근거없음 = {r.stock_id for r in rows if not crit.usable(r.criteria)}
    rows = with_criteria(rows)
    chosen = select_top(rows)
    # **실제로 고른 수를 적는다** (docs/infra.md 25.420). 예전에는 늘 "상위 5종목" 이라 적어, 추천이 0건인 날
    # 바로 아래 "오늘 추천할 종목이 없습니다" 와 어긋났고 2종목인 날도 5종목이라 했다
    # 종목 수다 — `chosen` 은 고른 종목의 **기간별 신호 행**이라 그대로 세면 2종목이 "5종목" 이 됐다 (25.430, 교차검증)
    몇 = f", 상위 {len({r.stock_id for r in chosen})}종목" if chosen else ""
    header = f"추천 (신호 기준일 {signals_as_of}{몇})" if signals_as_of else "추천"
    if 근거없음:
        header += f"\n⚠️ 근거표를 만들 수 없는 {len(근거없음)}종목은 싣지 않았습니다"
    if regime_line:
        header += f"\n{regime_line}"
    # 리포트 자기 채점 (docs/reports.md 3.4, 25.951) — 지난 1부 종목이 그 뒤 어땠나. 1부를 읽기 전에 보이게 머리에
    if self_grade_line:
        header += f"\n{self_grade_line}"
    # **묵은 신호로는 금액을 배분하지 않는다** (docs/infra.md 25.820, 리포트 감사). 1부는 기준일과 함께 보이되, 2부는
    # 며칠 전 매수 구간으로 "○○ 100만원, 1차 70,000원 이하" 를 매일 냈다. 이유는 머리에 적는다
    if no_allocation_reason:
        header += f"\n⚠️ {no_allocation_reason}"
    # **가중치 흔들기** (docs/reports.md 3.2, 25.947). 후보 전체(근거표로 거른 뒤)를 열여섯 세계에서 다시 줄 세운다.
    # 점수도 고른 종목도 바꾸지 않는다 — 종목 줄에 "흔들기 13/16 유지" 가 붙고, 들어올 뻔한 종목이 1부 끝에 한 줄 남는다
    shaken = (
        robustness.shake(rows, weights, sentiment_weight, n=TOP_STOCKS, chosen={r.stock_id for r in chosen})
        if weights else None
    )
    # 건너뛴 추천 영수증 (docs/reports.md 3.3, 25.950) — 지난 리포트에 몇 번 실렸고 처음 본 날 종가 대비 얼마나 움직였나
    picks = to_picks(chosen, shaken, pick_history)
    portfolio = (
        build_portfolio(
            [] if no_allocation_reason else chosen,
            total_investable,
            currency=currency,
            budget_note=budget_note,
            max_sector_pct=max_sector_pct,
            holdings=holdings,
            max_stock_pct=max_stock_pct,
            price_series=price_series,
            min_order_amount=min_order_amount,
            fx_missing=fx_missing,
            reserved=reserved,
        )
        # **추천이 0건인 날에도 말할 것이 있으면 2부를 싣는다** (docs/infra.md 25.420). 예전에는 추천이 없으면
        # 2부가 통째로 빠져 현재 보유 평가·남은 여력이 사라졌다 — 보유 현황은 추천이 없는 날에도 알아야 한다.
        # 보유도 총액도 매도 플래그도 없으면 빈 2부를 싣지 않는다
        if picks or holdings or total_investable > 0 or sell_flags
        else None
    )
    if portfolio is not None and no_allocation_reason:
        portfolio.no_allocation_reason = no_allocation_reason
    if portfolio is not None and sell_flags:
        portfolio.sell_flags = sell_flags
    # 보유 종목의 샀을 때·지금 점수 (docs/reports.md 3.6, 25.953) — 2부 보유 평가 줄 아래
    if portfolio is not None and holding_scores:
        portfolio.holding_scores = list(holding_scores)
    그림자 = robustness.entrants_line(shaken.entrants, shaken.total) if shaken else None
    body = rs.render(picks, portfolio, app_url=app_url, picks_footer=그림자)
    if portfolio is None and sell_flags:
        body += "\n\n" + "\n".join(rs.render_sell_flags(sell_flags))
    return Composed(f"{header}\n\n{body}", chosen, picks, portfolio, shaken)


def render(*args: Any, **kwargs: Any) -> str:
    """compose 의 본문만. 옛 호출부와 테스트가 쓴다."""
    return compose(*args, **kwargs).text
