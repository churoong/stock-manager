"""매도 플래그 판정 (docs/sell_flags.md, Step 13). 순수 함수만.

**표시와 알림만 한다. 이 모듈은 매매 기록(trades)을 만들지도 고치지도 않는다** (CLAUDE.md 절대 규칙:
자동 매도 절대 없음). tests/test_sell_flags.py 가 batch/ 전체에 매매 기록을 쓰는 코드가 없음을 확인한다.

레벨
  적(red)    손절선 도달
  녹(green)  목표 수익률 도달 (차익 실현 검토)
  황(yellow) 기간 초과 · 재무 악화 · 점검(유니버스 제외) · 감성 급락(오늘 감성이 있는 종목. 과거 점수가 없으면 25.641)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from batch.services import sentiment

# CLAUDE.md 매매 규칙 기본값. 설정 horizon_targets 가 있으면 그것을 쓴다
DEFAULT_TARGETS = {
    "short": {"target_pct": 10.0, "stop_pct": -7.0},
    "mid": {"target_pct": 25.0, "stop_pct": -15.0},
    "long": {"target_pct": 50.0, "stop_pct": -25.0},
}
# 기간 초과: 투자 기간의 위쪽 끝(단기 3개월, 중기 12개월). 장기는 끝이 없다
MAX_HOLDING_DAYS = {"short": 92, "mid": 365}
# 재무 악화: 매수 당시보다 종합 점수가 이만큼 떨어지면 [확인필요: 문턱 근거 없음. 운영하며 조정]
SCORE_DROP_POINTS = 20.0

HORIZON_LABEL = {"short": "단기", "mid": "중기", "long": "장기"}


def _종가_묵음_일수() -> int:
    """평가에 쓴 종가가 이보다 묵었으면 **판정에 그 사실을 붙인다**.

    `portfolio.stale_notes` 와 **같은 잣대**를 쓴다 — 거래일 사이 최장 간격 11일
    (`metrics.MAX_SESSION_GAP_DAYS`, exchange_calendars 실측). 정상 휴장으로는
    절대 안 걸리고, 걸리는 것은 거래정지나 수집 구멍뿐이다.
    """
    from batch.services.metrics import MAX_SESSION_GAP_DAYS

    return MAX_SESSION_GAP_DAYS


def _며칠_전(day: str | None, today: date) -> int | None:
    if not day:
        return None
    try:
        return (today - date.fromisoformat(str(day))).days
    except ValueError:
        return None


def stale_price_days(price_date: str | None, today: date) -> int | None:
    """종가가 문턱을 넘겨 묵었으면 며칠인지. 안 묵었거나 날짜를 모르면 None."""
    나이 = _며칠_전(price_date, today)
    return 나이 if 나이 is not None and 나이 > _종가_묵음_일수() else None


def _재무금액(value: float, currency: str | None) -> str:
    """재무 금액을 단위와 통화를 붙여 (docs/infra.md 25.937).

    예전에는 "-12,345,000,000" 처럼 원 단위 날값이라 원인지 달러인지, 억인지 알 수 없었다.
    신호 가격은 통화를 붙인다(25.385).
    """
    if currency == "KRW":
        return f"{value / 1e8:,.1f}억원"
    if currency == "USD":
        return f"${value / 1e6:,.1f}M"
    return f"{value:,.0f} {currency}" if currency else f"{value:,.0f} (통화 모름)"


@dataclass
class HoldingInput:
    stock_id: int
    name: str
    horizon: str | None
    first_buy_date: str
    currency: str
    cost: float  # 종목 통화, 수수료 포함
    market_value: float | None  # 종목 통화
    price_date: str | None
    score_now: float | None = None
    score_now_date: str | None = None
    score_at_buy: float | None = None
    score_at_buy_date: str | None = None
    #: 비교 기준점이 **매수 당시 점수가 아니라 다시 잡은 점수**인가 (docs/sell_flags.md, infra 25.961). 매수 뒤 계산
    #: 판·가중치가 바뀌면 매수 당시 점수와는 견주지 않고, 지금과 같은 잣대의 **첫 점수**(매수일 이후)를 기준점으로 쓴다
    score_rebased: bool = False
    op_income_latest: float | None = None
    op_income_prev: float | None = None
    fiscal_year_latest: int | None = None
    fin_report_date: str | None = None
    #: 재무 행의 표시 통화 (25.937). 종목 통화와 다를 수 있다(국내 상장사가 USD 로 보고 — 25.915)
    op_income_currency: str | None = None
    op_income_prev_currency: str | None = None
    universe_excluded_reason: str | None = None
    universe_date: str | None = None
    sentiment_delta_7d: float | None = None
    # 오늘 감성 점수. 7일 전 점수가 없을 때 급락 판정의 출발점을 중립으로 잡는 데 쓴다 (25.638)
    sentiment_now: float | None = None
    # 7일 전에도 그 종목 뉴스를 받고 있었나. 아니면 과거 점수가 없어도 중립 기준을 쓰지 않는다 (25.641)
    sentiment_tracked_7d: bool = False
    sentiment_negative_7d: int = 0
    sentiment_date: str | None = None
    #: 감성 나이를 잴 기준 — 그 종목 시장의 **직전 거래일** (docs/infra.md 25.401). 비면 오늘(달력일).
    #: 감성 기준일은 직전 거래일인데 사람 달력의 오늘과 달력일로 재면, 월·화 아침(주말)과 연휴 뒤에
    #: 3일을 넘겨 판정이 꺼졌다가 같은 날 저녁 다시 켜졌다 — 처음 걸린 날이 새로 찍혀 NEW 가 다시 나가고
    #: 누른 [확인] 이 사라졌다. `scores` 도 기준일(거래일)에서 3일을 잰다
    sentiment_ref_date: str | None = None
    #: 판정에 쓴 투자 기간(`horizon`)의 첫 매수일. 기간초과는 여기서 잰다 (docs/infra.md 25.417). 비면 first_buy_date
    horizon_since: str | None = None
    #: 최근 20거래일 안에 분할·병합 같은 기업행위가 감지됐나 (docs/infra.md 25.1093) — 장중 감시와 같은 판정
    #: (`monitor_targets.recent_action`). 수량은 사용자가 고치기 전까지 분할 전 값이라 손절·목표가 거짓일 수 있다
    corporate_action_recent: bool = False


@dataclass
class Flag:
    stock_id: int
    level: str
    reason_code: str
    rationale_text: str
    criteria: list[dict] = field(default_factory=list)


def _criterion(label: str, display: str, threshold: str, source: str, as_of: str | None) -> dict:
    return {"label": label, "display": display, "threshold": threshold, "source": source, "as_of": as_of,
            "passed": True}  # fmt: skip


def evaluate(h: HoldingInput, today: date, targets: dict | None = None) -> list[Flag]:
    """한 보유 종목의 플래그. 여러 개가 동시에 걸릴 수 있다(예: 손절 + 재무 악화)."""
    horizon = h.horizon if h.horizon in DEFAULT_TARGETS else "long"
    rules = {**DEFAULT_TARGETS[horizon], **((targets or {}).get(horizon) or {})}
    flags: list[Flag] = []
    label = HORIZON_LABEL[horizon]

    if h.market_value is not None and h.cost > 0:
        # 경계(정확히 −7% 등)가 부동소수 찌꺼기(−6.9999…)로 빗나가지 않게 소수 여섯째 자리에서 반올림
        ret = round((h.market_value / h.cost - 1) * 100, 6)
        shown = f"평가 수익률 {ret:+.1f}% (주가 기준, 수수료 포함 원가 대비)"
        # **묵은 종가로 판정하면 "닿았습니다" 가 거짓이 된다** (docs/infra.md 25.161).
        # 값을 지우지는 않는다 — 진짜로 손절선 아래일 수 있다. 대신 말한다 (25.136 과 같은 판단)
        묵음 = stale_price_days(h.price_date, today)
        덧 = (
            f" ※ 판정에 쓴 종가가 {h.price_date}({묵음}일 전) 값입니다."
            " 시세 수집을 확인한 뒤 다시 보세요"
            if 묵음 is not None
            else ""
        )
        # **기업행위 뒤에는 "수량을 확인하라" 를 플래그 문장에 붙인다** (docs/infra.md 25.1093, 감사 재현). 그 경고는
        # 국내 일일 배치가 감지한 **그날 하루**만 냈고 미국은 아예 없었다 — 4:1 분할이면 −75% "손절선에 닿았습니다" 가
        # 사유 없이 매일 실렸다. 플래그를 지우지는 않는다(진짜 손절일 수도 있다) — 묵은 종가(25.161)와 같은 판단
        if h.corporate_action_recent:
            덧 += (" ※ 최근 20거래일 안에 분할·병합 같은 기업행위가 감지됐습니다."
                  " 매매 기록의 수량을 확인한 뒤 다시 보세요")  # fmt: skip
        묵음행 = (
            [_criterion("종가 나이", f"{h.price_date} ({묵음}일 전)",
                        f"≤ {_종가_묵음_일수()}일 (거래일 사이 최장 간격)", "positions", h.price_date)]
            if 묵음 is not None
            else []
        ) + (
            [_criterion("기업행위", "최근 20거래일 안에 분할·병합 감지", "없어야 평가 수익률을 믿는다 — 수량 확인",
                        "prices·us_split_detections", h.price_date)]
            if h.corporate_action_recent
            else []
        )  # fmt: skip
        if ret <= rules["stop_pct"]:
            flags.append(Flag(h.stock_id, "red", "손절",
                              f"{h.name}: {label} 손절선 {rules['stop_pct']:g}% 에 닿았습니다 ({ret:+.1f}%){덧}",
                              [_criterion("손절선", shown, f"≤ {rules['stop_pct']:g}% ({label})",
                                          "positions", h.price_date), *묵음행]))  # fmt: skip
        elif ret >= rules["target_pct"]:
            flags.append(Flag(h.stock_id, "green", "목표도달",
                              f"{h.name}: {label} 목표 {rules['target_pct']:g}% 에 닿았습니다 ({ret:+.1f}%)."
                              f" 차익 실현을 검토하세요{덧}",
                              [_criterion("목표 수익률", shown, f"≥ {rules['target_pct']:g}% ({label})",
                                          "positions", h.price_date), *묵음행]))  # fmt: skip

    limit = MAX_HOLDING_DAYS.get(horizon)
    시작 = h.horizon_since or h.first_buy_date
    held_days = (today - date.fromisoformat(시작)).days
    if limit is not None and held_days > limit:
        flags.append(Flag(h.stock_id, "yellow", "기간초과",
                          f"{h.name}: {label} 투자 기간({limit}일)을 넘겨 {held_days}일째 보유 중입니다."
                          " 계획을 점검하세요",
                          [_criterion("보유 기간",
                                      f"{'첫 매수' if 시작 == h.first_buy_date else label + ' 첫 매수'}"
                                      f" {시작}부터 {held_days}일",
                                      f"> {limit}일 ({label})", "trades", today.isoformat())]))  # fmt: skip

    reasons: list[dict] = []
    texts: list[str] = []
    if h.op_income_latest is not None and h.op_income_prev is not None and h.op_income_prev > 0 >= h.op_income_latest:
        texts.append(f"FY{h.fiscal_year_latest} 영업이익 적자 전환")
        reasons.append(_criterion("영업이익 적자 전환",
                                  f"FY{h.fiscal_year_latest} {_재무금액(h.op_income_latest, h.op_income_currency)}"
                                  f" (전년 {_재무금액(h.op_income_prev, h.op_income_prev_currency)})",
                                  "전년 흑자 → 올해 0 이하", "financials", h.fin_report_date))  # fmt: skip
    # **묵은 "지금 점수" 로는 판정하지 않는다** (docs/infra.md 25.564, 감사 재현). 유니버스에서 빠진 종목은 점수를 새로
    # 내지 않아 3월 점수가 9월에도 "지금" 으로 쓰였다 — 25.161 의 "묵은 값은 판정하지 않는다" 와 같은 잣대(종가 묵음
    # 11일)를 건다. 그 종목은 "점검" 플래그가 따로 뜬다
    점수나이 = _며칠_전(h.score_now_date, today)
    점수_쓸_수_있다 = 점수나이 is None or 점수나이 <= _종가_묵음_일수()
    if (점수_쓸_수_있다 and h.score_now is not None and h.score_at_buy is not None
            and h.score_at_buy - h.score_now >= SCORE_DROP_POINTS):
        drop = h.score_at_buy - h.score_now
        texts.append(f"종합 점수 {h.score_at_buy:.0f}→{h.score_now:.0f}점")
        # 기준점을 다시 잡았으면 그렇다고 적는다 (25.961) — "매수 당시" 라고 하면 거짓이다
        기준 = (f"기준 재설정({h.score_at_buy_date}, 매수 뒤 계산 판·가중치가 바뀌어 같은 잣대의 첫 점수)"
                if h.score_rebased else f"매수 당시({h.score_at_buy_date})")  # fmt: skip
        reasons.append(_criterion("종합 점수 하락",
                                  f"{기준} {h.score_at_buy:.1f}"
                                  f" → {h.score_now:.1f} (−{drop:.1f})",
                                  f"−{SCORE_DROP_POINTS:.0f}점 이상", "scores / trades 스냅샷",
                                  h.score_now_date))  # fmt: skip
    if reasons:
        flags.append(Flag(h.stock_id, "yellow", "재무악화",
                          f"{h.name}: {', '.join(texts)}. 보유 이유가 유지되는지 점검하세요",
                          reasons))  # fmt: skip

    # **묵은 감성으로는 판정하지 않는다** (docs/infra.md 25.161). 종가와 달리 값을 버린다 —
    # 문장이 "7일 사이" 라는 창을 말하는데, 그 창이 3주 전에 끝났으면 문장 자체가 거짓이다.
    # `scores` 가 처음부터 이 잣대를 걸고 있었다(`sentiment.MAX_AGE_DAYS`)
    #
    # 날짜를 모르면(`None`) 막지 않는다. `sentiment_scores.as_of_date` 는 NOT NULL 이라
    # 값이 없다는 것은 **감성 행 자체가 없다**는 뜻이고, 그러면 `delta_7d` 도 없어
    # `is_sharp_drop` 이 이미 False 다. 여기서 또 막으면 판정이 두 겹이 된다
    기준 = date.fromisoformat(h.sentiment_ref_date) if h.sentiment_ref_date else today
    감성나이 = _며칠_전(h.sentiment_date, 기준)
    감성_쓸_수_있다 = 감성나이 is None or 감성나이 <= sentiment.MAX_AGE_DAYS
    오늘감성 = h.sentiment_now if h.sentiment_tracked_7d else None
    변화 = sentiment.drop_basis(h.sentiment_delta_7d, 오늘감성)
    if 감성_쓸_수_있다 and 변화 is not None and sentiment.is_sharp_drop(
        h.sentiment_delta_7d, h.sentiment_negative_7d, 오늘감성
    ):
        # 과거 점수가 없어 중립(0)에서 잰 것이면 문장이 그렇게 말한다 (25.638)
        중립에서 = h.sentiment_delta_7d is None
        말 = f"기사가 적던 종목에 감성이 {변화:+.0f}점까지" if 중립에서 else f"뉴스 감성이 7일 사이 {변화:+.0f}점"
        값 = f"{변화:+.1f}점 (7일 전 기사 부족 — 중립 0 기준)" if 중립에서 else f"{변화:+.1f}점"
        flags.append(Flag(h.stock_id, "yellow", "감성급락",
                          f"{h.name}: {말},"
                          f" 부정 기사 {h.sentiment_negative_7d}건. 무슨 일인지 확인하세요",
                          [_criterion("감성 7일 변화",
                                      f"{값}, 부정 기사 {h.sentiment_negative_7d}건",
                                      f"≤ −{sentiment.DROP_POINTS_7D:.0f}점 그리고"
                                      f" 부정 ≥ {sentiment.DROP_MIN_NEGATIVE_7D}건",
                                      "sentiment_scores", h.sentiment_date)]))  # fmt: skip

    if h.universe_excluded_reason:
        flags.append(Flag(h.stock_id, "yellow", "점검",
                          f"{h.name}: 최신 유니버스에서 빠졌습니다({h.universe_excluded_reason})",
                          [_criterion("유니버스 제외", h.universe_excluded_reason, "included = 0",
                                      "universe_members", h.universe_date)]))  # fmt: skip

    return flags


#: **수치·자료 사유는 한 무리로 본다** (docs/infra.md 25.614, 교차검증). 25.612 가 판정 순서를 바꿔 상장일을 모르는
#: 종목은 거래대금이 문턱을 오르내릴 때마다 사유가 "거래대금미달" ↔ "데이터없음" 으로 오갔다 — 속 사유가 바뀌면 [확인]
#: 을
#: 이어받지 않으므로 같은 종목이 되풀이해 다시 떴다. 이 넷 사이의 이동은 심해진 것이 아니다. 관리종목·거래정지·정리매매·
#: 상장폐지 같은 **결격으로 넘어갈 때만** 다시 알린다(25.564 가 지키려던 순간)
수치_사유 = frozenset({"데이터없음", "시총미달", "거래대금미달", "상장1년미만"})


def _점검_무리(display: str) -> str:
    return "수치미달" if display in 수치_사유 else display


def sub_reasons(reason_code: str, criteria: list[dict]) -> set[str]:
    """한 플래그의 **속 사유** (docs/infra.md 25.564, 감사 재현).

    "재무악화" 는 적자 전환·부채비율·점수 하락 중 무엇이 걸렸는지(근거 행 이름), "점검" 은 유니버스 제외 사유
    (시총미달 → 관리종목)가 속 사유다. [확인] 은 **본 속 사유까지만** 이어받는다 — 코드만 보면 시총미달을 확인한 뒤
    관리종목·정리매매로 넘어가는 가장 중요한 순간이 조용히 지나갔다. 손절·목표 같은 값 문턱은 속 사유가 없다(빈 집합)
    """
    if reason_code == "점검":
        return {f"{c.get('label')}:{_점검_무리(str(c.get('display')))}" for c in criteria}
    if reason_code == "재무악화":
        return {str(c.get("label")) for c in criteria}
    return set()


def keeps_dismissal(reason_code: str, before: list[dict], now: list[dict]) -> bool:
    """어제 [확인] 을 오늘도 이어받는가 — 오늘 속 사유가 어제 본 것 안에 있을 때만."""
    return sub_reasons(reason_code, now) <= sub_reasons(reason_code, before)


LEVEL_ORDER = {"red": 0, "yellow": 1, "green": 2}


def sort_flags(flags: list[Flag]) -> list[Flag]:
    """적 → 황 → 녹. 급한 것이 먼저 보이게."""
    return sorted(flags, key=lambda f: (LEVEL_ORDER.get(f.level, 9), f.stock_id, f.reason_code))
