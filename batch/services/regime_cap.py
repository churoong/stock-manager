"""45회차 (가) `regime_cap_v1` — 기준 B 쌍둥이 계좌 Z1 의 계좌 수준 노출 덮개(순수 계산)
(docs/factors.md 12.48 "(가) `regime_cap_v1` 사전 등록", 결과 보기 전 — 그 문단이 이 코드보다 먼저).

**계산만 한다** — DB·시각·jobs 층을 모른다. 지금은 꺼진 채다: `EXPLORE_VARIANTS`·`run_step`·계좌 개설·`PLANNED`·
`SEPARATE_IDS` 잇기는 2026-12 판정 실행이 국내·미국 모두 닫힌 뒤의 등록 커밋이다(12.48 일정, 12.45 공통 규칙).

정한 것(12.48 그대로 — 새 숫자는 `MIN_BEAR_EVENTS` 하나, 12.48 이 '새 판정 요소' 라 적은 그것):
- 국면 = `trend.regime_from_closes`(200거래일 SMA, 종가 < SMA 면 약세)를 **끝난 월말**(as_of 달보다 앞선 달의 마지막
  지수 행)에만 적용. 둘 중 하나라도 unknown(200일 미만·7일 넘게 묵음)이면 전환이 아니며 아무것도 하지 않는다
- ① 강세→약세 월말: 그 시장 지수에 딸린 보유 로트마다 ⌊q × (1 − 0.5)⌋주 감축, 까닭 '국면감축'
- ② 약세→강세 월말: 약세 동안 줄인 **주식 수** 만큼 되산다(아직 들고 있고 매도 플래그·손절이 없고, 감축일 뒤
  재매수 로트가 아닌 것). 상한(10%·30%·위험 점검)에 걸리면 가능한 만큼 — 모자란 수량은 남긴다. 결정 kind '되채움'
- 감축 대금은 **예약 현금** — 새 신호 매수에 쓰지 않고 되채움에만 쓴다
- 평균 원가는 장부(`paper.positions`) 그대로 — 갈라짐은 '원가 경로 차이' 로 이름 붙여 센다
- 판정: ⓐ MDD_Z1 ≥ 0.7 × MDD_B(음수 규약) ⓑ 시간가중 ≥ B − 5%p ⓒ 노출 맞춘 비교 (i)(ii) — 셋 모두.
  국내·미국 강세→약세 월말 사건 2개 미만이면 '판정 불가'
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from batch.services import desk, trend
from batch.services import paper as pp
from batch.services.trend import Regime

RULE = "regime_cap_v1"
#: 계좌 id(12.48 "id `Z1` 을 고른 까닭") — 이 파일은 계좌를 열지 않는다. 글자만 고정한다
ACCOUNT_ID = "Z1"
#: 탐색 리플레이 변형 이름(같은 지문) — `EXPLORE_VARIANTS` 등록은 12월 판정 실행 뒤
VARIANT = "core_b_rc"

#: 약세 국면 노출 덮개 — **계좌 상수**(지문). 설정 `trend_filter.bear_factor` 를 읽지 않는다(12.48 검증 B 조건 3 —
#: `trend.DEFAULT_BEAR_FACTOR` 는 설정 기본값이라 실험 도중 바뀔 수 있다). 값은 그 기본값과 같은 0.5
BEAR_EXPOSURE = 0.5
#: 강세 국면 덮개 — 덮개 없음(B 와 같음)
BULL_EXPOSURE = 1.0
#: 국면 창·묵음 문턱 — 정의처는 `trend`(시장 추세 필터, signals.md 3.5)
SMA_DAYS = trend.SMA_DAYS
MAX_STALE_DAYS = trend.MAX_STALE_DAYS

BULL, BEAR, UNKNOWN = "bull", "bear", "unknown"
TO_BEAR, TO_BULL = "bull_to_bear", "bear_to_bull"

#: 결정 원장 글(12.48 ①②) — 감축은 매도(kind 'sell'), 되채움은 신호 매수 'buy' 와 다른 kind(UNIQUE 0071)
TRIM_KIND = "sell"
TRIM_REASON = "국면감축"
REFILL_KIND = "되채움"
REFILL_REASON = "되채움"

#: 판정 ⓐ — |MDD_Z1| ≤ 0.7 × |MDD_B|. X·Y 판 1 의 근거 없는 첫 값 `[확인필요]`(paper.md:956)을 다시 쓴다
MDD_RATIO = 0.7
#: 판정 ⓑ — 시간가중 수익(소수) ≥ B − 0.05. 같은 X·Y 첫 값
TWR_MARGIN = 0.05
#: 판정 시점 — 국내·미국 강세→약세 전환 월말 합 2개 이상(12.48 '사건 기준', KOSPI·KOSDAQ 같은 달은 한 사건)
MIN_BEAR_EVENTS = 2
#: ⓒ 에서 B 투자 비중이 이보다 작은 날은 뺀다 — 정의처 `desk.EXPOSURE_MIN`(desk.py:581)
EXPOSURE_MIN = desk.EXPOSURE_MIN

PASS, FAIL, UNDECIDABLE = "통과", "탈락", "판정 불가"

#: 이름 붙은 차이(12.48 — 판정 기록에 함께 적는다). 고치지 않고 센다
COST_PATH_DIFF = "원가 경로 차이"
#: 원가가 문턱을 바꾸는 매도 사유 — `sell_flags` 의 목표·손절 코드(B `core` 규칙의 셋 가운데 원가 문턱인 둘)
TARGET_STOP_CODES = frozenset({"목표도달", "손절"})
NAMED_DIFFERENCES: dict[str, str] = {
    COST_PATH_DIFF: "되채움 뒤 평균 원가가 장부 규칙(paper.positions, 수량 비례 덜어 내기 + 새 매수 원가)대로"
                    " B 와 갈라져 목표·손절 문턱이 달라진다 — 갈라진 로트 수와 그 로트의 목표·손절 체결 수를 센다",
    "체결 시점 차이": "탐색 엔진은 t 종가로, 운영은 다음 장 시가로 체결한다(backtest.md 10장)",
    "정수 주 차이": "엔진은 비중 × 0.5, 운영은 ⌊q × 0.5⌋ 정수 주(1910 틀 명목 자본)",
}  # fmt: skip


# ---------------------------------------------------------------- 월말 국면


def _달_첫날(d: str) -> str:
    return d[:7] + "-01"


def month_end_regime(index_code: str, closes: Sequence[tuple[str, float]], as_of: str,
                     source: str | None = None) -> Regime:
    """as_of 에서 본 **끝난 월말** 국면 — as_of 의 달보다 앞선 달의 마지막 지수 행에 `trend.regime_from_closes`.

    - as_of 당일 행은 보지 않는다(운영은 그달 첫 거래일 **아침** 배치라 오늘 행이 없다 — 리플레이가 같은 날 종가를 보면
      운영과 갈린다). 그래서 묵음은 as_of − (as_of 전 마지막 지수 행) > `MAX_STALE_DAYS` 이면 unknown
    - 끝난 월말이 없거나, 그 월말까지 200일이 안 차면 unknown(`regime_from_closes` 그대로)
    - 알려진 국면의 `date` 는 그 월말 날짜다
    """
    앞 = sorted((d[:10], float(c)) for d, c in closes if d[:10] < as_of)
    if not 앞:
        return Regime(index_code, UNKNOWN, note="지수 없음", source=source)
    마지막 = 앞[-1][0]
    age = (date.fromisoformat(as_of) - date.fromisoformat(마지막)).days
    if age > MAX_STALE_DAYS:
        return Regime(index_code, UNKNOWN, 마지막, 앞[-1][1], None, 0, source, note=f"지수가 {age}일 오래됨")
    첫날 = _달_첫날(as_of)
    월말까지 = [(d, c) for d, c in 앞 if d < 첫날]
    if not 월말까지:
        return Regime(index_code, UNKNOWN, 마지막, 앞[-1][1], None, 0, source, note="끝난 월말 없음")
    return trend.regime_from_closes(index_code, 월말까지, 월말까지[-1][0], source)


def month_end_pair(index_code: str, closes: Sequence[tuple[str, float]], as_of: str,
                   source: str | None = None) -> tuple[Regime, Regime]:
    """(직전 끝난 월말 국면, 지금 끝난 월말 국면).

    직전 것은 **그때 내렸을 판정 그대로** 다시 낸다 — as_of = 지금 월말이 든 달의 첫 지수 행(그달 첫 거래일 아침)으로
    `month_end_regime`. 그래서 직전 월말의 묵음도 그 판정일 기준이고, 리플레이와 운영이 같은 판정을 본다.
    지금 국면이 알려지지 않았으면 직전 것도 unknown 으로 돌려준다(전환을 낼 수 없다)
    """
    cur = month_end_regime(index_code, closes, as_of, source)
    if cur.state == UNKNOWN or cur.date is None:
        return Regime(index_code, UNKNOWN, note="지금 월말 미판정", source=source), cur
    달 = cur.date[:7]
    그달첫행 = min(d[:10] for d, _c in closes if d[:10][:7] == 달)
    return month_end_regime(index_code, closes, 그달첫행, source), cur


def transition(prev: Regime, cur: Regime) -> str | None:
    """월말 전환 — `TO_BEAR`·`TO_BULL`·None. 하나라도 unknown 이면 None(아무것도 하지 않는다, 12.48).

    두 월말이 같은 달이면(같은 판정을 두 번 본 것) None
    """
    if prev.state == UNKNOWN or cur.state == UNKNOWN:
        return None
    if prev.date is None or cur.date is None or prev.date[:7] >= cur.date[:7]:
        return None
    if prev.state == BULL and cur.state == BEAR:
        return TO_BEAR
    if prev.state == BEAR and cur.state == BULL:
        return TO_BULL
    return None


def should_act(prev: Regime, cur: Regime, last_acted_month_end: str | None) -> str | None:
    """이번 실행이 그 전환을 처리해야 하나 — 같은 월말 전환은 한 번만(이미 처리한 월말이면 None)"""
    t = transition(prev, cur)
    if t is None or cur.date == last_acted_month_end:
        return None
    return t


def exposure_cap(state: str) -> float | None:
    """국면별 목표 노출 덮개 — 약세 `BEAR_EXPOSURE`, 강세 `BULL_EXPOSURE`, 모름 None(바꾸지 않는다)"""
    return {BEAR: BEAR_EXPOSURE, BULL: BULL_EXPOSURE}.get(state)


# ---------------------------------------------------------------- 감축


def trim_quantity(quantity: int, cap: float = BEAR_EXPOSURE) -> int:
    """⌊q × (1 − cap)⌋ — 덮개 위 초과 주식 수. 부동소수 끝자리로 한 주 덜 내지 않게 반올림 뒤 내림"""
    if quantity <= 0:
        return 0
    return int(math.floor(round(quantity * (1 - cap), 9)))


def trim_plan(
    holdings: Mapping[int, pp.Position], stock_index: Mapping[int, str | None], turned_bear: Iterable[str],
    prices: Mapping[int, float | None] | None = None, selling: Iterable[int] = (), cap: float = BEAR_EXPOSURE,
) -> list[dict[str, Any]]:
    """강세→약세로 바뀐 지수(`turned_bear`)에 딸린 보유만 덮개까지 줄인다 — 다른 지수 보유는 그대로.

    `holdings` = `paper.positions(fills)`(종목마다 로트 하나), `stock_index` = 종목 → 지수(`trend.index_for_market`
    으로 미리 낸 것), `selling` = 그날 이미 매도 플래그로 전량 파는(또는 매도가 열린) 종목 — 감축하지 않는다(B 와 같은
    전량 매도가 앞선다). `prices` 는 근거·예상 대금(종목 통화)일 뿐 수량을 바꾸지 않는다.
    줄 = {stock_id, index_code, held, quantity, keep, ref_price, est_amount, reason, kind} — stock_id 순
    """
    약세 = set(turned_bear)
    파는 = set(selling)
    out: list[dict[str, Any]] = []
    for sid in sorted(holdings):
        p = holdings[sid]
        지수 = stock_index.get(sid)
        if 지수 not in 약세 or sid in 파는 or p.quantity <= 0:
            continue
        q = trim_quantity(p.quantity, cap)
        if q < 1:
            continue
        가격 = (prices or {}).get(sid)
        out.append({"stock_id": sid, "index_code": 지수, "held": p.quantity, "quantity": q, "keep": p.quantity - q,
                    "ref_price": 가격, "est_amount": None if 가격 is None else 가격 * q,
                    "reason": TRIM_REASON, "kind": TRIM_KIND})  # fmt: skip
    return out


def excess_shares(holdings: Mapping[int, pp.Position], stock_index: Mapping[int, str | None],
                  index_code: str, cap: float = BEAR_EXPOSURE) -> int:
    """그 지수 보유의 덮개 위 초과 주식 수 합 Σ(q − ⌈q × cap⌉) — 남길 수(덮개 안 최대 정수 주)를 뺀 몫.
    `trim_plan` 수량 합과 같아야 한다(⌊q(1 − cap)⌋ = q − ⌈q·cap⌉ — 다른 길로 낸 같은 값, 시험이 견준다)"""
    return sum(p.quantity - math.ceil(round(p.quantity * cap, 9))
               for sid, p in holdings.items() if stock_index.get(sid) == index_code and p.quantity > 0)  # fmt: skip


# ---------------------------------------------------------------- 예약 현금


def reserve_after(
    reserve: Mapping[str, float], *, trimmed: Mapping[str, float] | None = None,
    refilled: Mapping[str, float] | None = None, released: Iterable[str] = (),
) -> dict[str, float]:
    """지수마다 예약 현금(원). 감축 체결 대금(`trimmed`, 체결 `cash_krw` 합)을 더하고, 되채움 체결 비용(`refilled`,
    양수)을 빼고(0 아래로 가지 않음), 되채움을 마친 지수(`released`)는 남은 예약을 풀어 0 으로 한다"""
    out = {k: float(v) for k, v in reserve.items()}
    for k, v in (trimmed or {}).items():
        out[k] = out.get(k, 0.0) + float(v)
    for k, v in (refilled or {}).items():
        out[k] = max(0.0, out.get(k, 0.0) - float(v))
    for k in released:
        out[k] = 0.0
    return {k: v for k, v in out.items() if v > 0}


def signal_cash(cash_krw: float, reserve: Mapping[str, float]) -> float:
    """새 신호 매수에 쓸 수 있는 현금 = 장부 현금 − 예약 합(0 아래 없음). `size_buys(cash_available=…)` 에 넘긴다"""
    return max(0.0, float(cash_krw) - sum(max(0.0, float(v)) for v in reserve.values()))


# ---------------------------------------------------------------- 되채움


def refill_plan(
    holdings: Mapping[int, pp.Position], trims: Mapping[int, Mapping[str, Any]], turned_bull: Iterable[str],
    ref_prices: Mapping[int, float | None], reserved_krw: float, fx_rate: float, *, tranche: int,
    flagged: Iterable[int] = (), room: Mapping[int, int] | None = None, currency: str = "KRW",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """약세→강세로 바뀐 지수(`turned_bull`)의 감축분을 되산다 — (주문, 빠진 것).

    - `trims` = {stock_id: {"index_code", "date"(감축 체결일), "quantity"(되채우지 않은 감축 주식 수 누계)}}
    - 대상: 아직 들고 있고(`holdings`), 매도 플래그·손절(`flagged`)이 없고, **감축일 뒤 첫 매수일이 새로 잡힌 보유가
      아닌** 로트(전량 매도 뒤 재매수 — `Position.first_buy_date` > 감축일)
    - 수량 = min(감축 주식 수, 상한 남은 수량 `room`(10%·30%·위험 점검을 부르는 쪽이 낸 최대 주식 수 —
      None 이면 무제한))
    - 현금: **예약 현금 `reserved_krw` 안에서만**(원). 잣대는 `size_buys(entry='open')` 와 같다 — 기준가 ×
      (1 + `OPEN_BUY_BUFFER`) × (1 + `FEE_BUFFER`)(달러는 × (1 + `FX_BUFFER`)) × 환율
    - `tranche`: 주문 표 UNIQUE(…side, tranche)(0070)의 되채움 회차 값 — 12.48 이 `[확인필요]` 로 남겨 **부르는 쪽이
      정한다**(기본값 없음)
    주문 = {stock_id, tranche, limit_price: None, ref_price, quantity, kind, reason}
    (size_buys 시가 일괄 꼴 + 결정 kind),
    빠진 것 = {stock_id, wanted, quantity(산 수), short, why}
    """
    if isinstance(tranche, bool) or not isinstance(tranche, int):
        raise TypeError("tranche 는 정수 — 12.48 [확인필요: 0070 tranche 값 범위], 등록 커밋에서 정한다")
    강세 = set(turned_bull)
    플래그 = set(flagged)
    주문: list[dict[str, Any]] = []
    빠짐: list[dict[str, Any]] = []
    if fx_rate <= 0:
        return [], [{"stock_id": sid, "wanted": int(t.get("quantity") or 0), "quantity": 0,
                     "short": int(t.get("quantity") or 0), "why": "환율 없음"}
                    for sid, t in sorted(trims.items()) if t.get("index_code") in 강세]  # fmt: skip
    여유 = (1 + pp.FEE_BUFFER) * (1 if currency == "KRW" else 1 + pp.FX_BUFFER)
    남은 = max(0.0, float(reserved_krw)) / fx_rate
    for sid in sorted(trims):
        t = trims[sid]
        if t.get("index_code") not in 강세:
            continue
        원함 = int(t.get("quantity") or 0)
        if 원함 < 1:
            continue
        p = holdings.get(sid)

        def 뺌(why: str, 산: int = 0, sid: int = sid, 원함: int = 원함) -> None:
            빠짐.append({"stock_id": sid, "wanted": 원함, "quantity": 산, "short": 원함 - 산, "why": why})

        if p is None or p.quantity <= 0:
            뺌("보유 없음")
            continue
        if sid in 플래그:
            뺌("매도 플래그·손절")
            continue
        if p.first_buy_date and str(t.get("date") or "") and p.first_buy_date > str(t["date"]):
            뺌("감축 뒤 재매수 로트")
            continue
        기준 = float(ref_prices.get(sid) or 0)
        if 기준 <= 0:
            뺌("기준가 없음")
            continue
        상한 = 원함 if room is None else max(0, min(원함, int(room.get(sid, 0))))
        단가 = 기준 * (1 + pp.OPEN_BUY_BUFFER) * 여유
        살 = int(min(상한, 남은 // 단가))
        if 살 >= 1:
            남은 -= 살 * 단가
            주문.append({"stock_id": sid, "tranche": tranche, "limit_price": None, "ref_price": 기준,
                         "quantity": 살, "kind": REFILL_KIND, "reason": REFILL_REASON})  # fmt: skip
        if 살 < 원함:
            뺌("상한" if 상한 < 원함 and 살 == 상한 else "예약 현금 부족", 살)
    return 주문, 빠짐


def room_after_refill(room: Mapping[int, int], refill_orders: Iterable[Mapping[str, Any]]) -> dict[int, int]:
    """같은 날 같은 종목에 신호 매수가 있으면 되채움을 먼저 내고, 신호 매수는 상한 안 **남은 몫만**(12.48 충돌 처리)"""
    out = {int(k): int(v) for k, v in room.items()}
    for o in refill_orders:
        sid = int(o["stock_id"])
        out[sid] = max(0, out.get(sid, 0) - int(o["quantity"]))
    return out


# ---------------------------------------------------------------- 노출 맞춘 비교 ⓒ


def exposure(cash_krw: float, equity_krw: float) -> float:
    """투자 비중 = 1 − 현금 ÷ 평가, [0, 1] 로 자름 — `desk.exposure_matched_r` 의 비중과 같은 식"""
    return min(max(1 - cash_krw / equity_krw, 0.0), 1.0)


def mean_exposure(e_prev: Sequence[float]) -> float:
    return sum(e_prev) / len(e_prev)


def fixed_weight_control(e_prev: Sequence[float], s: Sequence[float]) -> float:
    """같은 평균 노출 ē 의 고정 비중 대조 수익(소수) Π(1 + ē·s_t) − 1"""
    ē = mean_exposure(e_prev)
    곱 = 1.0
    for st in s:
        곱 *= 1 + ē * st
    return 곱 - 1


def spanning_alpha(e_prev: Sequence[float], s: Sequence[float]) -> float:
    """시점 선택 항 Σ(e_{t−1} − ē)·s_t — 고정 비중 위로 노출을 옮겨 번 몫(12.48 ⓒ(ii)·0단계 거름)"""
    ē = mean_exposure(e_prev)
    return sum((e - ē) * st for e, st in zip(e_prev, s, strict=True))


def matched_comparison(
    z1_series: Sequence[tuple[str, float]], z1_cash: Sequence[tuple[str, float, float]],
    b_series: Sequence[tuple[str, float]], b_cash: Sequence[tuple[str, float, float]],
) -> dict[str, Any] | None:
    """ⓒ 재료 — `desk.exposure_matched_r` 꼴(12.48 검증 A ①).

    `*_series` = (날, 시간가중 지수), `*_cash` = (날, 현금, 평가)(`paper_daily`). 이웃한 B 지수 날 (a, b) 마다
    s_t = r_B,t ÷ e_B,t−1, e_{t−1} = Z1 의 a 날 투자 비중. e_B,t−1 < `EXPOSURE_MIN` 인 날은 빼고 센다 — ē 도 남은 날로
    낸다(고정 비중 대조가 실제로 쓴 날과 같은 평균 노출을 갖게). 어느 날이든 현금·평가가 비면
    None(0 으로 채우지 않는다).
    z1_twr = Z1 지수의 같은 구간(첫 날 → 끝 날) 수익
    """
    b = sorted(b_series)
    z = dict(z1_series)
    zc = {d: (c, e) for d, c, e in z1_cash}
    bc = {d: (c, e) for d, c, e in b_cash}
    if len(b) < 2:
        return None
    e_prev: list[float] = []
    s: list[float] = []
    뺀 = 0
    for (da, ta), (_db, tb) in zip(b, b[1:], strict=False):
        h, r = zc.get(da), bc.get(da)
        if h is None or r is None or not h[1] or not r[1] or h[1] <= 0 or r[1] <= 0 or ta <= 0:
            return None
        er = exposure(float(r[0]), float(r[1]))
        if er < EXPOSURE_MIN:
            뺀 += 1
            continue
        e_prev.append(exposure(float(h[0]), float(h[1])))
        s.append((tb / ta - 1) / er)
    첫, 끝 = b[0][0], b[-1][0]
    if not s or 첫 not in z or 끝 not in z or z[첫] <= 0:
        return None
    return {"z1_twr": z[끝] / z[첫] - 1, "fixed_weight": fixed_weight_control(e_prev, s),
            "alpha": spanning_alpha(e_prev, s), "mean_exposure": mean_exposure(e_prev), "days": len(s),
            "skipped_days": 뺀}  # fmt: skip


# ---------------------------------------------------------------- 0단계 거름 — 지수 장기 대리


def proxy_path(index_code: str, closes: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """지수 일봉에 같은 월말 전환 규칙 — 노출 e(1, 약세 전환이면 0.5, 강세 전환이면 1).

    판정은 달마다 **첫 지수 행 날 아침**(`month_end_pair` — 그날 행을 보지 않는다), 새 노출은 그날부터 든다 — 그날
    수익 r_t 는 전날 노출 e_{t−1} 을 쓴다(미래 정보 없음). 시작 노출은 1(B 쌍둥이, 덮개 없음).
    {"e_prev": [...], "r": [...], "events": 강세→약세 전환 수, "transitions": [(월말, 전환)]}
    """
    줄 = sorted((d[:10], float(c)) for d, c in closes if c is not None and float(c) > 0)
    e = BULL_EXPOSURE
    e_prev: list[float] = []
    r: list[float] = []
    바뀜: list[tuple[str, str]] = []
    본달: set[str] = set()
    for i, (d, c) in enumerate(줄):
        if i:
            e_prev.append(e)
            r.append(c / 줄[i - 1][1] - 1)
        if d[:7] in 본달:
            continue
        본달.add(d[:7])
        prev, cur = month_end_pair(index_code, 줄, d)
        t = transition(prev, cur)
        if t == TO_BEAR:
            e = BEAR_EXPOSURE
        elif t == TO_BULL:
            e = BULL_EXPOSURE
        if t is not None:
            바뀜.append((str(cur.date), t))
    return {"e_prev": e_prev, "r": r, "events": sum(1 for _d, t in 바뀜 if t == TO_BEAR), "transitions": 바뀜}


def proxy_events(index_code: str, closes: Sequence[tuple[str, float]]) -> int:
    """강세→약세 전환 사건 수 — 항을 내기 **전에** 기록한다(12.48)"""
    return int(proxy_path(index_code, closes)["events"])


def proxy_timing_term(index_code: str, closes: Sequence[tuple[str, float]]) -> float | None:
    """시점 선택 항 Σ(e_{t−1} − ē)·r_t — 수익 관측이 없으면 None"""
    p = proxy_path(index_code, closes)
    return spanning_alpha(p["e_prev"], p["r"]) if p["r"] else None


def proxy_filter(terms: Mapping[str, float | None]) -> bool:
    """시장마다 항 > 0 이어야 계좌를 연다. 하나라도 ≤ 0 이거나 모름(None)이면 거짓. 빈 입력도 거짓"""
    return bool(terms) and all(v is not None and v > 0 for v in terms.values())


# ---------------------------------------------------------------- 판정


def bear_events(transitions: Iterable[tuple[str, str, str]]) -> int:
    """판정 사건 수 — (나라 'KR'·'US', 월말 날짜, 전환) 가운데 강세→약세를 (나라, 월)로 묶어 센다.
    KOSPI·KOSDAQ 같은 달 전환은 한 사건, 국내·미국 같은 달은 두 사건"""
    return len({(c, d[:7]) for c, d, t in transitions if t == TO_BEAR})


def judge(
    *, mdd_z1: float | None, mdd_b: float | None, twr_z1: float | None, twr_b: float | None,
    matched: Mapping[str, Any] | None, events: int,
) -> dict[str, Any]:
    """계좌 판정(desk.PREREG (Z1,1)) — MDD 는 음수 소수(`etf_tactical._mdd` 규약), 수익은 소수.

    - 사건 `events` < `MIN_BEAR_EVENTS` → '판정 불가'(그 전에는 판정하지 않는다, 두 번째 날짜 없음)
    - 재료가 비면(`None`) '판정 불가' — 0 으로 채우지 않는다 `[확인필요: 12.48 은 이 경우를 적지 않음]`
    - ⓐ mdd_z1 ≥ 0.7 × mdd_b ⓑ twr_z1 ≥ twr_b − 0.05 ⓒ (i) twr_z1 ≥ 고정 비중 대조 (ii) α ≥ 0 — 모두 참이면 통과
    MDD 가 양수면 규약 위반이라 ValueError
    """
    if events < MIN_BEAR_EVENTS:
        return {"verdict": UNDECIDABLE, "why": f"강세→약세 전환 월말 {events}개 < {MIN_BEAR_EVENTS}", "checks": {}}
    고정 = None if matched is None else matched.get("fixed_weight")
    알파 = None if matched is None else matched.get("alpha")
    if mdd_z1 is None or mdd_b is None or twr_z1 is None or twr_b is None or 고정 is None or 알파 is None:
        return {"verdict": UNDECIDABLE, "why": "판정 재료 빔", "checks": {}}
    if mdd_z1 > 0 or mdd_b > 0:
        raise ValueError("MDD 는 음수(고점 대비 하락률) — 부호를 확인할 것")
    checks = {
        "a_mdd": mdd_z1 >= MDD_RATIO * mdd_b,
        "b_twr": twr_z1 >= twr_b - TWR_MARGIN,
        "c1_fixed": twr_z1 >= float(고정),
        "c2_alpha": float(알파) >= 0,
    }
    ok = all(checks.values())
    return {"verdict": PASS if ok else FAIL, "why": "" if ok else ",".join(k for k, v in checks.items() if not v),
            "checks": checks}  # fmt: skip


# ---------------------------------------------------------------- 이름 붙은 차이 — 원가 경로


def avg_cost(p: pp.Position) -> float | None:
    """장부 평균 원가(종목 통화, 매수 비용·슬리피지 포함) = cost ÷ 수량 — `paper.positions` 가 낸 그대로"""
    return p.cost / p.quantity if p.quantity > 0 else None


def cost_path_divergence(
    z1_holdings: Mapping[int, pp.Position], b_holdings: Mapping[int, pp.Position],
    exits: Mapping[int, Iterable[str]] | None = None, tol: float = 1e-9,
) -> dict[str, Any]:
    """'원가 경로 차이' 를 센다 — 두 장부에 모두 든 종목 가운데 평균 원가가 갈라진 로트, 그 로트의 목표·손절 체결 수.

    `exits` = {stock_id: [Z1 매도 체결 사유 코드…]} — `TARGET_STOP_CODES`('목표도달'·'손절', sell_flags 코드)만 센다.
    B 의 매도 규칙 `core` 가 따르는 '기간초과' 는 원가와 무관해 세지 않는다
    """
    갈라진 = sorted(
        sid for sid in set(z1_holdings) & set(b_holdings)
        if (a := avg_cost(z1_holdings[sid])) is not None and (b := avg_cost(b_holdings[sid])) is not None
        and abs(a - b) > tol * max(1.0, abs(b))
    )  # fmt: skip
    체결 = sum(1 for sid in 갈라진 for code in (exits or {}).get(sid, ()) if code in TARGET_STOP_CODES)
    return {"name": COST_PATH_DIFF, "lots": 갈라진, "n_lots": len(갈라진), "target_stop_exits": 체결}
