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
from datetime import date, timedelta
from typing import Any

from batch.core.rounding import fixed, half_up
from batch.services import scoring as sc
from batch.services import sentiment, signals

# CLAUDE.md 매매 규칙 기본값. 설정 horizon_targets 가 있으면 그것을 쓴다. **정의처는 `signals.DEFAULT_TARGETS` 하나**
# (25.1746, 중복 규칙 감사 ⑦ — 두 벌이 따로 적혀 있어 한쪽만 바꾸면 가상모의투자 매도·장중 손절가(이쪽)와 신호 손절가·
# 운용 화면 손절 근접(저쪽)이 다른 손절선을 말했다). 웹 기본값과의 대조는 `tests/test_settings_keys.py`
DEFAULT_TARGETS = signals.DEFAULT_TARGETS
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
        return f"{half_up(value / 1e8, 1):,.1f}억원"
    if currency == "USD":
        return f"${half_up(value / 1e6, 1):,.1f}M"
    return f"{value:,.0f} {currency}" if currency else f"{value:,.0f} (통화 모름)"


def _짧은해_주석(days: tuple[Any, Any]) -> str:
    """적자 전환 근거의 기간 주석 (docs/infra.md 25.1326, 20회차 P2). 부호 판정이라 짧은 해의 손실도 진짜 손실이다 —
    지우지 않고(검증 A·B: 보수) 어느 해가 12개월이 아닌지만 적는다. 기간을 모르면(옛 행) 아무것도 붙이지 않는다"""
    붙임 = [f"{이름} 사업연도 {int(d)}일" for 이름, d in zip(("올해", "전년"), days, strict=True)
            if sc.short_year({"period_days": d})]  # fmt: skip
    return f" — {', '.join(붙임)}(12개월 아님)" if 붙임 else ""


_팩터_이름 = {"value": "밸류", "quality": "퀄리티", "growth": "성장", "momentum": "모멘텀", "risk": "리스크",
            "sentiment": "감성"}  # fmt: skip


def _기여_글(parts: dict[str, float] | None) -> str:
    """② 근거 칸 끝의 팩터별 기여 — **사실만**, 많이 내린 것부터 (25.1342, 21회차 P3 1단계).

    인과 문장·"재무" 묶음을 쓰지 않는다: 밸류는 가격이 내리면 오르는 팩터라 퀄리티·성장 악화를 가릴 수 있다(검증 둘).
    0 인 몫은 뺀다. 이름표는 그대로라 [확인] 이 유지된다(25.564 — 기준 이름만 본다)"""
    if not parts:
        return ""
    줄 = [f"{_팩터_이름.get(k, k)} {fixed(v, 1, sign=True)}" for k, v in sorted(parts.items(), key=lambda kv: kv[1])
          if abs(v) >= 0.05]  # fmt: skip
    return f" — 팩터별 기여: {' · '.join(줄)}" if 줄 else ""


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
    #: 잣대(계산 판·가중치)가 바뀌었는데 같은 잣대의 기준점을 못 찾은 날 — 점수 하락을 **판정할 수 없었다** (25.1405)
    score_scale_unknown: bool = False
    #: 잣대가 바뀐 보유의 **같은 잣대 구간마다 이은 하락**과 바뀐 횟수 (25.1412). 있으면 이것으로 판정한다
    score_drop_chained: float | None = None
    score_scale_changes: int = 0
    #: ② 하락의 팩터별 기여(지금 − 기준) — 합이 실제 차와 맞을 때만 잡이 채운다 (25.1342). 표시에만 쓴다
    score_drop_parts: dict[str, float] | None = None
    op_income_latest: float | None = None
    op_income_prev: float | None = None
    fiscal_year_latest: int | None = None
    fin_report_date: str | None = None
    #: 재무 행의 표시 통화 (25.937). 종목 통화와 다를 수 있다(국내 상장사가 USD 로 보고 — 25.915)
    op_income_currency: str | None = None
    op_income_prev_currency: str | None = None
    #: (올해, 전년) 사업연도 일수 — 짧은 해면 적자 전환 근거에 주석만 단다 (25.1326). 플래그는 지우지 않는다
    op_income_period_days: tuple[Any, Any] = (None, None)
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
    #: 20거래일 안 **유상증자 권리락**만으로 확인된 계수 곱 (`monitor_targets.action_scale`, 25.1158).
    #: 1.0 이 아니면 원가에
    #: 곱해 수익률을 낸다 — 권리락 날 시세는 신주인수권 값만큼 기계적으로 떨어지는데(장중 손절선은 25.1140 부터 같은
    #: 계수를 곱한다) 원가를 그대로 두면 실질 0% 인 날 "손절선에 닿았습니다" 가 났다
    rights_scale: float = 1.0
    #: 매수 뒤 지난 배당 기준일 사실(`dividend_note`, 26회차 P1, docs/infra.md 25.1413). 국내만, None 은 없음·못 읽음
    dividend_note: dict | None = None
    #: 매수 때(그때 쓰던 스냅샷)부터 지금까지 유니버스에 **한 번이라도** 들었나 (docs/infra.md 25.1095). None 은 모름 —
    #: 예전처럼 "빠졌습니다" 로 판정한다
    universe_included_since_buy: bool | None = None


@dataclass
class Flag:
    stock_id: int
    level: str
    reason_code: str
    rationale_text: str
    criteria: list[dict] = field(default_factory=list)


def chained_score_drop(
    start: dict[str, Any], history: list[dict[str, Any]], now_date: str, same_scale: Any,
) -> tuple[float, int] | None:
    """매수 때 점수부터 지금까지 **같은 잣대 구간마다의 하락을 이어 더한** 값과 잣대가 바뀐 횟수 (infra 25.1412).

    계산 판·가중치가 바뀌면 그 경계의 점프는 종목이 아니라 잣대 탓이라 빼고, 구간 안의 움직임만 더한다. 25.961 은 "새
    잣대의 첫 점수" 를 기준점으로 다시 잡아, 판이 주에 몇 번씩 오르면 사실상 "지난 판 이후 하락" 만 봤다(매도 플래그
    감사). `history` 는 매수일 뒤~지금(포함)의 행 {as_of_date, total_score, …잣대 칸}, 날짜 오름차순·같은 날은 판
    내림차순. 같은 날 여러 판이면 **지금 잣대를 잇는 행**으로 하락을 더한 뒤 그날 첫 행(가장 높은 판)으로 갈아탄다 —
    날마다 그날 첫 행에서 끝나므로 마지막 날은 지금 점수와 같은 행이다. 점수가 빈 행은 건너뛴다.
    **한계**(교차검증 C2·C3): 잣대가 바뀐 날 같은 날 두 판이 없으면 그날 하루 움직임이 통째로 빠진다(오차 약 √N × 하루
    변동폭, 방향 없음 — 판을 올리는 날 전 거래일을 새 판으로 다시 내면 사라진다). 잣대마다 점수 퍼짐이 달라(가중치가
    몰리면) 같은 10점도 크기가 조금 다르다 — 20점 문턱이 출발값이라 막지 않는다"""
    def 점수(r: dict[str, Any]) -> float | None:
        v = r.get("total_score")
        return float(v) if isinstance(v, int | float) else None

    if 점수(start) is None:
        return None
    날별: dict[str, list[dict[str, Any]]] = {}
    for r in history:
        if 점수(r) is not None:
            날별.setdefault(str(r["as_of_date"]), []).append(r)
    if now_date not in 날별:
        return None
    지금, 하락, 바뀜, 마지막_견줌 = start, 0.0, 0, False
    for 날 in sorted(날별):
        행들 = 날별[날]
        # 그날 지금 잣대를 잇는 행이 있으면 그 행으로 하락을 더하고, 그날 더 높은 판(첫 행)이 다른 잣대면 **같은 날
        # 갈아탄다** — 같은 날이라 실제 움직임이 빠지지 않는다 (25.1412 교차검증 C1: 판을 올리며 과거를 새 판으로 다시
        # 낸 날 옛 판을 고집해 "모름" 이 되고 예전 방식과 번갈아 판정이 깜빡였다)
        이음 = next((r for r in 행들 if same_scale(지금, r)), None)
        if 이음 is not None:
            하락 += 점수(지금) - 점수(이음)  # type: ignore[operator]
            지금 = 이음
        첫 = 행들[0]
        if not same_scale(지금, 첫):
            바뀜 += 1
            지금 = 첫
        마지막_견줌 = 이음 is not None
    # **지금 잣대 구간에서 견준 쌍이 없으면 모름** — 마지막 걸음이 잣대 바뀜이면 지금 잣대로 얼마나 움직였는지 모른다.
    # 부르는 쪽이 예전 기준점 방식·"판정 못 한 날"(25.1405)로 물러난다
    return (round(하락, 6), 바뀜) if 마지막_견줌 else None


#: 배당 기준일 이 날수 앞부터 매매가 있으면 그 기준일에 배당 받을 수량을 지금 수량으로 볼 수 없다 — 락일은 기준일 전
#: 거래일이고 결제는 T+2 라 기준일 이틀(거래일) 전까지 산 주식만 받는다. 달력 하한 7일 (25.1413). 적재는 거래소
#: 달력으로 "기준일 두 거래일 전" 과 견줘 이른 쪽을 쓴다 — 추석 같은 긴 연휴면 7일로는 락일 전 매수를 못 덮는다(25.1416)
DIVIDEND_TRADE_GUARD_DAYS = 7


def _원(text: object) -> float | None:
    try:
        v = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _날(ymd: object) -> str | None:
    s = str(ymd or "").replace("/", "").replace("-", "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def _달력_경계(record_date: str) -> str:
    return (date.fromisoformat(record_date) - timedelta(days=DIVIDEND_TRADE_GUARD_DAYS)).isoformat()


def dividend_note(events: list[dict], trade_dates: list[str], quantity: float, cost: float, first_buy_date: str,
                  today: date, covered_since: str | None, guard_from: Any = None) -> dict | None:  # fmt: skip
    """매수 뒤 **이미 지난** 배당 기준일의 사실 (docs/sell_flags.md 1.2, 26회차 P1 — 검증 B 의 사실 문구).

    평가 수익률은 주가만 본다 — 락일에 빠진 가격만큼 받을 현금배당은 들어 있지 않다. 판정은 바꾸지 않고 사실만 센다.
    `events` 는 예탁원 배당 일정(`kr_corp_events.detail` 을 푼 dict 와 `record_date`). 주당배당 `per_sto_divi_amt` 가
    "0" 이면 **금액 미정**(10-15 실측 — 정해지기 전에는 0 으로 온다). 주식배당(`stk_divi_rate` > 0)은 따로 센다.
    기준일 7일 전(또는 `guard_from(기준일)` — 적재가 거래소 달력으로 준다)부터 매매가 있으면 그 기준일의 받을 수량을
    모른다 — 금액에 넣지 않고 센다. 지급일을 읽지 못하면 "지급 전" 이 아니라 "지급일 모름" 으로 센다(25.1416 — 수집 창이
    기준일 + 7일에서 끝나 빈 지급일이 다시 채워지지 않는다). `covered_since` 가 None 이면 수집 범위를 모른다고 적는다"""
    경계함수 = guard_from or _달력_경계
    지난 = sorted((e for e in events if first_buy_date < str(e.get("record_date") or "") <= today.isoformat()),
                key=lambda e: str(e["record_date"]))  # fmt: skip
    if not 지난:
        return None
    합, 주당, 지급전, 지급모름, 미정, 주식, 수량모름 = 0.0, 0.0, 0, 0, 0, 0, 0
    for e in 지난:
        d = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        if (_원(d.get("stk_divi_rate")) or 0) > 0:
            주식 += 1
        경계 = 경계함수(str(e["record_date"]))
        if any(t >= 경계 for t in trade_dates):
            수량모름 += 1
            continue
        amt = _원(d.get("per_sto_divi_amt"))
        if not amt or amt <= 0:
            미정 += 1
            continue
        합 += amt * quantity
        주당 += amt
        지급일 = _날(d.get("divi_pay_dt"))
        if 지급일 is None:
            지급모름 += 1
        elif 지급일 > today.isoformat():
            지급전 += 1
    return {
        "n": len(지난), "dates": [str(e["record_date"]) for e in 지난],
        "per_share": round(주당, 2), "pct": round(합 / cost * 100, 6) if cost > 0 and 합 > 0 else None,
        "pending": 지급전, "pay_unknown": 지급모름, "undetermined": 미정, "stock_div": 주식, "qty_unknown": 수량모름,
        "uncovered_since": covered_since if covered_since and first_buy_date < covered_since else None,
        "coverage_unknown": covered_since is None,
    }  # fmt: skip


def dividend_row(note: dict, as_of: str | None) -> dict:
    """`dividend_note` 를 근거표 참고 행으로. 해석 문장("배당락으로 빠진 몫")은 쓰지 않는다 — 락일 하락 ≠ 주당배당"""
    글 = f"{note['n']}건 ({'·'.join(note['dates'][-3:])}{' 외' if note['n'] > 3 else ''})"
    if note.get("pct") is not None:
        글 += (f" · 현금배당 주당 합 {fixed(note['per_share'], 0)}원 = 원가의 {fixed(note['pct'], 2)}%p"
               f" (지급 전 {note['pending']}건)")  # fmt: skip
    말들 = (("pay_unknown", "지급일 모름"), ("undetermined", "금액 미정(기준일 직후까지 받은 값)"),
            ("stock_div", "주식배당"), ("qty_unknown", "기준일 앞뒤 매매로 수량 모름"))  # fmt: skip
    for 키, 말 in 말들:
        if note.get(키):
            글 += f" · {말} {note[키]}건"
    if note.get("uncovered_since"):
        글 += f" · {note['uncovered_since']} 전 기준일은 수집 전이라 모름"
    elif note.get("coverage_unknown"):
        글 += " · 수집 시작을 몰라 더 이른 기준일은 모름"
    행 = _criterion("매수 뒤 배당 기준일", 글,
                    "참고 — 평가 수익률은 주가만 보고 받을 현금배당은 넣지 않는다. 판정 불변 (docs/sell_flags.md 1.2)",
                    "kr_corp_events·trades", as_of)  # fmt: skip
    행["passed"] = None
    return 행


def _criterion(label: str, display: str, threshold: str, source: str, as_of: str | None) -> dict:
    return {"label": label, "display": display, "threshold": threshold, "source": source, "as_of": as_of,
            "passed": True}  # fmt: skip


def low_touch(h: HoldingInput, close: float | None, low: float | None, targets: dict | None = None) -> bool | None:
    """전일 **저가**가 손절선 이하인데 **종가**는 손절선 위였나 (28회차 P1, docs/sell_flags.md 3장, 25.1419 — 기록만).

    장중 감시가 꺼진 동안(10-08 사용자 결정) 장중 손절 터치는 아무도 보지 않는다 — 판정은 종가 그대로 두고 센다.
    잣대는 판정식과 같다: 평가액 × (저가 ÷ 종가) 를 수수료 포함 원가 × 권리락 계수와 견준다(장중 평균단가 식을 쓰면 같은
    근거표 안에서 숫자가 둘이 된다, 검증 B). 모름(None): 평가액·종가·저가 없음, 최근 20거래일 기업행위(수량 확인 전)"""
    if (h.market_value is None or not close or not low or close <= 0 or low <= 0 or h.cost <= 0
            or h.corporate_action_recent):  # fmt: skip
        return None
    stop = signals.rules_for(h.horizon, targets)["stop_pct"]
    원가 = h.cost * (h.rights_scale if h.rights_scale != 1.0 and h.rights_scale > 0 else 1.0)
    종가수익 = round((h.market_value / 원가 - 1) * 100, 6)
    저가수익 = round((h.market_value * (low / close) / 원가 - 1) * 100, 6)
    return 저가수익 <= stop < 종가수익


def evaluate(h: HoldingInput, today: date, targets: dict | None = None) -> list[Flag]:
    """한 보유 종목의 플래그. 여러 개가 동시에 걸릴 수 있다(예: 손절 + 재무 악화)."""
    horizon = h.horizon if h.horizon in DEFAULT_TARGETS else "long"
    rules = signals.rules_for(h.horizon, targets)
    flags: list[Flag] = []
    label = HORIZON_LABEL[horizon]
    # **문턱이 어디서 왔는지와 기간을 고른 규칙을 근거표에** (docs/infra.md 25.1410, 매도 플래그 감사 ③). 신호 쪽은
    # 25.522 에서 적게 했는데 매도 플래그는 "≤ -7% (단기)" 만 적어, 설정값인지 기본값인지, 섞인 보유가 왜 단기인지
    # 화면에서 확인할 수 없었다(CLAUDE.md 근거표 규칙)
    문턱출처 = (f"설정 horizon_targets.{horizon}" if (targets or {}).get(horizon)
               else "기본값 (CLAUDE.md 매매 규칙)")  # fmt: skip
    기간글 = label + ("" if h.horizon in DEFAULT_TARGETS else " (기간 없음 — 장기로 봄)")
    # 등록된 도우미로 짓는다 — 근거표 계약 검사(`test_criteria_contract`)가 모양을 대 본다. 판정이 아닌 참고 행
    기간행 = _criterion("투자 기간", 기간글,
                       "남은 매수 가운데 원가가 가장 큰 기간 (기간 없음은 장기와 합침, docs/sell_flags.md 3장)",
                       "positions.horizon", h.price_date)  # fmt: skip
    기간행["passed"] = None

    if h.market_value is not None and h.cost > 0:
        # 경계(정확히 −7% 등)가 부동소수 찌꺼기(−6.9999…)로 빗나가지 않게 소수 여섯째 자리에서 반올림
        권리락 = h.rights_scale != 1.0 and h.rights_scale > 0
        ret = round((h.market_value / (h.cost * (h.rights_scale if 권리락 else 1.0)) - 1) * 100, 6)
        수익 = fixed(ret, 1, sign=True)  # .5 는 0 에서 멀어지게 (25.1324)
        shown = f"평가 수익률 {수익}% (주가 기준, 수수료 포함 원가 대비)" + (
            f" — 유상증자 권리락 계수 {h.rights_scale:.4g} 를 원가에 반영" if 권리락 else "")
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
        ) + (
            [_criterion("유상증자 권리락", f"계수 {h.rights_scale:.4g} (원가 × 계수와 견줌)",
                        "권리락 하락은 손절이 아니다 — 장중 손절선과 같은 잣대 (25.1140·25.1158)",
                        "prices·kr_corp_events", h.price_date)]
            if 권리락
            else []
        ) + ([dividend_row(h.dividend_note, h.price_date)] if h.dividend_note else [])  # fmt: skip
        if ret <= rules["stop_pct"]:
            flags.append(Flag(h.stock_id, "red", "손절",
                              f"{h.name}: {label} 손절선 {rules['stop_pct']:g}% 에 닿았습니다 ({수익}%){덧}",
                              [_criterion("손절선", shown, f"≤ {rules['stop_pct']:g}% ({label} · {문턱출처})",
                                          "positions", h.price_date), 기간행, *묵음행]))  # fmt: skip
        elif ret >= rules["target_pct"]:
            flags.append(Flag(h.stock_id, "green", "목표도달",
                              f"{h.name}: {label} 목표 {rules['target_pct']:g}% 에 닿았습니다 ({수익}%)."
                              f" 차익 실현을 검토하세요{덧}",
                              [_criterion("목표 수익률", shown, f"≥ {rules['target_pct']:g}% ({label} · {문턱출처})",
                                          "positions", h.price_date), 기간행, *묵음행]))  # fmt: skip

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
        reasons.append(_criterion(FIN_LOSS_LABEL,
                                  f"FY{h.fiscal_year_latest} {_재무금액(h.op_income_latest, h.op_income_currency)}"
                                  f" (전년 {_재무금액(h.op_income_prev, h.op_income_prev_currency)})"
                                  f"{_짧은해_주석(h.op_income_period_days)}",
                                  "전년 흑자 → 올해 0 이하", "financials", h.fin_report_date))  # fmt: skip
    # **묵은 "지금 점수" 로는 판정하지 않는다** (docs/infra.md 25.564, 감사 재현). 유니버스에서 빠진 종목은 점수를 새로
    # 내지 않아 3월 점수가 9월에도 "지금" 으로 쓰였다 — 25.161 의 "묵은 값은 판정하지 않는다" 와 같은 잣대(종가 묵음
    # 11일)를 건다. 그 종목은 "점검" 플래그가 따로 뜬다
    점수나이 = _며칠_전(h.score_now_date, today)
    점수_쓸_수_있다 = 점수나이 is None or 점수나이 <= _종가_묵음_일수()
    # 하락폭도 소수 여섯째 자리에서 반올림한다 (docs/infra.md 25.1323, 매도 플래그 감사 #1)
    # — 점수는 소수 한 자리로 저장되고 64.1 − 44.1 = 19.99999… 라 "20점 이상" 이 빗나갔다
    # (50~100 사이 정확히 20점 하락의 약 9%). 손익률(위)과 같은 처리
    이음 = h.score_drop_chained is not None and h.score_now is not None and h.score_at_buy is not None
    drop = (h.score_drop_chained if 이음 else round(h.score_at_buy - h.score_now, 6)  # type: ignore[operator]
            if h.score_now is not None and h.score_at_buy is not None else None)  # fmt: skip
    if 점수_쓸_수_있다 and drop is not None and drop >= SCORE_DROP_POINTS and 이음:
        # 잣대가 바뀐 보유 — 같은 잣대 구간마다 이은 하락으로 판정한다 (25.1412). 두 점수의 차는 잣대 탓 점프를 섞는다
        texts.append(f"종합 점수 {fixed(h.score_at_buy)}→{fixed(h.score_now)}점"
                     f"(같은 잣대끼리 이은 하락 {fixed(drop, 1)}점)")  # fmt: skip
        reasons.append(_criterion("종합 점수 하락",
                                  f"매수 당시({h.score_at_buy_date}) {fixed(h.score_at_buy, 1)}"
                                  f" → {fixed(h.score_now, 1)},"
                                  f" 계산 판·가중치가 {h.score_scale_changes}번 바뀌어 같은 잣대 구간마다 이은 하락"
                                  f" −{fixed(drop, 1)} (바뀐 날의 점프는 뺌)",
                                  f"−{SCORE_DROP_POINTS:.0f}점 이상", "scores (매수일~지금 점수 이력)",
                                  h.score_now_date))  # fmt: skip
    elif 점수_쓸_수_있다 and drop is not None and drop >= SCORE_DROP_POINTS:
        texts.append(f"종합 점수 {fixed(h.score_at_buy)}→{fixed(h.score_now)}점")  # .5 올림 (25.1105)
        # 기준점을 다시 잡았으면 그렇다고 적는다 (25.961) — "매수 당시" 라고 하면 거짓이다
        기준 = (f"기준 재설정({h.score_at_buy_date}, 매수 뒤 계산 판·가중치가 바뀌어 같은 잣대의 첫 점수)"
                if h.score_rebased else f"매수 당시({h.score_at_buy_date})")  # fmt: skip
        reasons.append(_criterion("종합 점수 하락",
                                  f"{기준} {fixed(h.score_at_buy, 1)}"
                                  f" → {fixed(h.score_now, 1)} (−{fixed(drop, 1)}){_기여_글(h.score_drop_parts)}",
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
        # 반올림은 웹 `toFixed` 와 같은 규칙 — 파이썬 서식은 .5 를 짝수로 보내 −41.5 가
        # 문장 −42·웹 −41 로 갈렸다 (25.1303)
        정수, 한자리 = fixed(변화, sign=True), fixed(변화, 1, sign=True)
        말 = f"기사가 적던 종목에 감성이 {정수}점까지" if 중립에서 else f"뉴스 감성이 7일 사이 {정수}점"
        값 = f"{한자리}점 (7일 전 기사 부족 — 중립 0 기준)" if 중립에서 else f"{한자리}점"
        flags.append(Flag(h.stock_id, "yellow", "감성급락",
                          f"{h.name}: {말},"
                          f" 부정 기사 {h.sentiment_negative_7d}건. 무슨 일인지 확인하세요",
                          [_criterion("감성 7일 변화",
                                      f"{값}, 부정 기사 {h.sentiment_negative_7d}건",
                                      f"≤ −{sentiment.DROP_POINTS_7D:.0f}점 그리고"
                                      f" 부정 ≥ {sentiment.DROP_MIN_NEGATIVE_7D}건",
                                      "sentiment_scores", h.sentiment_date)]))  # fmt: skip

    # **처음부터 유니버스 밖이던 보유는 "빠졌습니다" 가 아니다** (docs/infra.md 25.1095, 감사 재현). 우선주(보통주아님)·
    # 시총미달 종목을 일부러 사면 다음 배치부터 거짓 문장의 황색이 NEW 로 떴다. 그런 보유는 **결격**(관리종목·거래정지·
    # 정리매매·상장폐지)으로 넘어갈 때만 알린다 — 25.564 가 지키려던 순간
    밖이던 = h.universe_included_since_buy is False
    if h.universe_excluded_reason and (not 밖이던 or h.universe_excluded_reason in 결격_사유):
        문장 = (f"유니버스 밖에서 산 종목이 지금 {h.universe_excluded_reason}입니다" if 밖이던
                else f"최신 유니버스에서 빠졌습니다({h.universe_excluded_reason})")  # fmt: skip
        flags.append(Flag(h.stock_id, "yellow", "점검",
                          f"{h.name}: {문장}",
                          [_criterion("유니버스 제외", h.universe_excluded_reason, "included = 0",
                                      "universe_members", h.universe_date)]))  # fmt: skip

    return flags


#: **수치·자료 사유는 한 무리로 본다** (docs/infra.md 25.614, 교차검증). 25.612 가 판정 순서를 바꿔 상장일을 모르는
#: 종목은 거래대금이 문턱을 오르내릴 때마다 사유가 "거래대금미달" ↔ "데이터없음" 으로 오갔다 — 속 사유가 바뀌면 [확인]
#: 을
#: 이어받지 않으므로 같은 종목이 되풀이해 다시 떴다. 이 넷 사이의 이동은 심해진 것이 아니다. 관리종목·거래정지·정리매매·
#: 상장폐지 같은 **결격으로 넘어갈 때만** 다시 알린다(25.564 가 지키려던 순간)
수치_사유 = frozenset({"데이터없음", "시총미달", "거래대금미달", "상장1년미만"})
#: 보유를 다시 봐야 하는 **결격** — 처음부터 유니버스 밖이던 보유도 이리로 넘어가면 알린다 (25.1095).
#: **투자주의환기도 결격이다** (25.1268, 매도 플래그 감사) — 25.425 전엔 "관리종목" 으로 적히던 같은 무리를 이름만
#: 바로잡은 것인데, 이 목록을 만들 때 빠져 유니버스 밖에서 산 보유가 투자주의환기로 지정돼도(관리종목·상장폐지로 가는
#: 첫 단계) 말이 없었다
결격_사유 = frozenset({"관리종목", "투자주의환기", "거래정지", "정리매매", "상장폐지"})


def _점검_무리(display: str) -> str:
    return "수치미달" if display in 수치_사유 else display


def unjudged(h: HoldingInput, today: date) -> set[str]:
    """오늘 **판정할 수 없었던** 사유 (docs/infra.md 25.1277, 매도 플래그 감사 재현). 그 어제 플래그는 풀지 않고 잇는다.

    "판정했더니 아니다" 와 "판정할 자료가 없다" 는 다르다. 예전엔 둘 다 플래그가 안 나와 어제 행이 `resolved_at` 으로
    풀렸고, 자료가 돌아온 날 다시 걸리면 이어받을 행이 없어 [확인] 이 사라지고 NEW 로 다시 떴다 — 국내 감성 채점이 하루
    실패하면 확인한 "감성급락" 이 그다음 날 NEW 로 재발했다(문서는 "조건이 풀리면" 이라 했다)."""
    out: set[str] = set()
    if h.market_value is None or not h.cost > 0:
        out |= {"손절", "목표도달"}
    점수나이 = _며칠_전(h.score_now_date, today)
    if h.score_now is None or (점수나이 is not None and 점수나이 > _종가_묵음_일수()):
        out.add("재무악화")
    # **잣대가 바뀐 날도 판정하지 못한 날이다** (docs/infra.md 25.1405, 매도 플래그 감사 재현). 같은 잣대의 기준점이
    # 없어 `score_at_buy` 가 비면 점수 하락을 보지 않는데, 지금 점수가 신선해 "판정함" 으로 쳐 어제 걸린 재무악화가
    # 풀렸다(계산 판은 주에 몇 번씩 오른다). 그날은 잇는다
    if h.score_scale_unknown:
        out.add("재무악화")
    기준 = date.fromisoformat(h.sentiment_ref_date) if h.sentiment_ref_date else today
    감성나이 = _며칠_전(h.sentiment_date, 기준)
    오늘감성 = h.sentiment_now if h.sentiment_tracked_7d else None
    if (감성나이 is not None and 감성나이 > sentiment.MAX_AGE_DAYS) or sentiment.drop_basis(
        h.sentiment_delta_7d, 오늘감성
    ) is None:
        out.add("감성급락")
    return out


#: 재무악화 속 근거 가운데 점수와 상관없이 재무 행만으로 판정하는 것 — 점수를 못 본 날에도 판정했다(25.1790)
FIN_LOSS_LABEL = "영업이익 적자 전환"


def carry_criteria(h: HoldingInput, reason_code: str, criteria: list[dict]) -> list[dict]:
    """판정 못 한 사유를 이을 때 어제 근거 가운데 **오늘 판정할 수 없었던 근거만** (docs/infra.md 25.1790, 결함 루프).

    재무악화는 "점수를 못 봄" 이면 통째로 `unjudged` 에 드는데, 그 안의 적자 전환은 점수와 상관없이 재무 행으로 판정한다
    — 오늘 흑자로 돌아 풀린 적자 전환이 "자료가 없어 이어 둠" 으로 최대 `MAX_CARRY_DAYS` 남았다. 오늘 두 해 영업이익이
    다 있으면 적자 전환은 판정한 것이라 뺀다. 빈 목록이면 잇지 않는다(풀림)"""
    if reason_code != "재무악화":
        return criteria
    판정함 = h.op_income_latest is not None and h.op_income_prev is not None
    return [c for c in criteria if not (판정함 and c.get("label") == FIN_LOSS_LABEL)]


def without_fin_loss(text: str) -> str:
    """재무악화 문장에서 적자 전환 조각("FY2024 영업이익 적자 전환, ")을 뺀다 — 이어 둘 때 판정한 근거를 문장에서도
    (25.1790)"""
    import re

    return re.sub(rf"FY\S* {FIN_LOSS_LABEL}(, )?", "", text)


#: 일부 잇기(25.1845)의 문장 표시 — 이은 근거 이름을 괄호에 담는다
_PARTIAL_NOTE = " ※ 일부 근거({})는 오늘 판정할 자료가 없어 지난 판정을 이어 둡니다"


def partial_carry_note(labels: list[str]) -> str:
    """오늘 판정한 근거에 어제 근거를 덧붙일 때 문장 끝 표시 (25.1847)"""
    return _PARTIAL_NOTE.format(", ".join(labels))


def full_carry_text(text: str, labels: list[str]) -> str:
    """통째로 이을 때의 문장 — 어제 일부 잇기 표시를 떼고, 적자 조각을 빼 사유가 빈 문장(`이름: .`)이면 남은 근거
    이름으로 채운다 (25.1847, 25.1845 교차검증 [권고]: "에이: . 보유 이유가 …" 가 리포트에 나갈 뻔했다).

    뗀 표시에 있던 이은 근거가 여전히 근거표에 있는데 문장에 없으면 통째 잇기 표시에 이름을 남긴다 (25.1848 — 재무를
    못 본 날은 적자 조각이 그대로라 빈 사유 채우기를 타지 않아 "종합 점수 하락" 이 문장에서만 사라졌다)"""
    import re

    m = re.search(r" ※ 일부 근거\(([^)]*)\)는 오늘 판정할 자료가 없어 지난 판정을 이어 둡니다", text)
    이은 = [x for x in m.group(1).split(", ") if x] if m else []
    글 = text.replace(m.group(0), "") if m else text
    if labels and ": ." in 글:
        글 = 글.replace(": .", ": " + ", ".join(labels) + ".", 1)
    빠진 = [x for x in 이은 if x in labels and x not in 글]
    if 빠진:
        글 += f" ※ 오늘 판정할 자료가 없어 지난 판정을 이어 둡니다({', '.join(빠진)} 포함)"
    return 글


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
