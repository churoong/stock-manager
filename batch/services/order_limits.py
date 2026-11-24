"""절대 한도·가격 깃의 그림자 계측 — 이미 낸 KIS모의투자 주문이 "둘째 방어선" 에 걸렸을 건수 (docs/paper.md 15.12).

docs/roadmap.md 1장 #10, docs/infra.md 25.2284. 지금 사전 위험 점검(`risk_gate`)은 모두 **비율**이라 평가 총액·환율이
틀리면 주문도 같이 커진다. 실전 전에 비율과 무관한 깃을 둘 것인데, 켜기 전에 "지금까지의 주문이 걸렸을 건수" 를 먼저
센다(0 이어야 정상 주문을 막지 않는다). **주문 경로에 닿지 않는다** — 기록된 주문(`paper_broker_orders`)과 시세를 읽어
사후에 세는 순수 계산이다. 막지 않고, 쓰지 않는다.

세는 것(거래대금 깃·크기는 매수만 "걸림" 으로 — 위험을 줄이는 매도는 막지 않는다, 참고 수만. 가격 깃은 양쪽 모두):
- 가격 깃(국내 지정가): 지정가가 앞 거래일 종가 × (1 ± 가격제한폭 `markets.KR_DAILY_LIMIT`) 밖 — 거래소가 받지 않는 값
- 거래대금 깃: 주문 금액 > 앞 `ADV_DAYS` 거래일 평균 거래대금 × `ADV_SHARE_MAX`(깔때기 수용량 문턱과 같은 1%)
- 주문 크기 분포(그 시장 통화): 주문당 최대·95 분위, 하루 매수 합 최대 — **주문당·하루 총액 절대 상한의 숫자는 사용자
  결정**이라 여기서 정하지 않는다. 분포는 그 결정의 재료이고 금액이라 비공개 운영 이슈로만 나간다(`scripts/ops_tee`)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from batch.core import markets
from batch.services import scoreboard

#: 보는 창(달력일) — roadmap "4주 세고 0 이면 켠다"
WINDOW_DAYS = 28
#: 거래대금 평균의 창(거래일)과 그 하한 — 하한보다 적으면 "모름"(신규 상장·시세 구멍)
ADV_DAYS = 20
ADV_MIN_DAYS = 10
#: 주문 금액 ÷ 평균 거래대금의 상한 — 깔때기 0 → 1 수용량 문턱과 같은 값을 쓴다(따로 정하지 않는다)
ADV_SHARE_MAX = scoreboard.STAGE01_MAX_CAPACITY
#: 시세를 읽을 때 창 앞으로 더 읽는 달력일 — 거래일 `ADV_DAYS` 를 덮는 여유(연휴 포함)
PRICE_LOOKBACK_DAYS = 45


def _앞_시세(bars: list[dict[str, Any]], day: str) -> tuple[float | None, float | None]:
    """그 종목의 일봉(날짜 오름차순) → (앞 거래일 종가, 앞 `ADV_DAYS` 거래일 평균 거래대금). 못 내면 None.
    거래대금 칸이 빈 행(미국 — 거래량만 온다)은 종가 × 거래량으로 채운다"""
    앞 = [b for b in bars if str(b["date"]) < day and b.get("close")]
    if not 앞:
        return None, None
    값들 = []
    for b in 앞[-ADV_DAYS:]:
        v = b.get("value") or (float(b["close"]) * float(b["volume"]) if b.get("volume") else None)
        if v and float(v) > 0:
            값들.append(float(v))
    평균 = sum(값들) / len(값들) if len(값들) >= ADV_MIN_DAYS else None
    return float(앞[-1]["close"]), 평균


def classify(order: dict[str, Any], prev_close: float | None, adv: float | None) -> dict[str, Any]:
    """주문 하나 → {notional, band, adv_share, adv_breach} — 못 재면 None(모름은 "안 걸림" 이 아니다).

    `band` = 국내 지정가만: True 면 가격제한폭 밖. 금액은 지정가(시장가는 앞 종가) × 수량, 그 시장 통화"""
    가격 = order.get("limit_price") if order.get("order_type") == "limit" else prev_close
    금액 = float(가격) * int(order["quantity"]) if 가격 else None
    밖: bool | None = None
    국내지정가 = order.get("market") == "KR" and order.get("order_type") == "limit" and order.get("limit_price")
    if 국내지정가 and prev_close:
        폭 = markets.KR_DAILY_LIMIT
        밖 = not (prev_close * (1 - 폭) <= float(order["limit_price"]) <= prev_close * (1 + 폭))
    몫 = 금액 / adv if 금액 is not None and adv else None
    return {"notional": 금액, "band": 밖, "adv_share": 몫, "adv_breach": None if 몫 is None else 몫 > ADV_SHARE_MAX}


def _분위(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def summarize(orders: list[dict[str, Any]], bars: dict[int, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """시장마다 {orders, buys, sells, band_breach, band_unknown, adv_breach, adv_unknown, sell_adv_over,
    max_notional, p95_notional, max_daily_buy, days}. 거래대금 깃·크기는 매수만, 가격 깃은 양쪽 모두 센다"""
    out: dict[str, dict[str, Any]] = {}
    하루합: dict[tuple[str, str], float] = defaultdict(float)
    금액들: dict[str, list[float]] = defaultdict(list)
    for o in orders:
        m = str(o.get("market"))
        s = out.setdefault(m, {"orders": 0, "buys": 0, "sells": 0, "band_breach": 0, "band_unknown": 0, "adv_breach": 0,
                               "adv_unknown": 0, "sell_adv_over": 0, "days": set()})  # fmt: skip
        day = str(o["trade_date"])[:10]
        앞종가, 평균 = _앞_시세(bars.get(int(o["stock_id"]), []), day)
        c = classify(o, 앞종가, 평균)
        s["orders"] += 1
        s["days"].add(day)
        매수 = o.get("side") == "buy"
        s["buys" if 매수 else "sells"] += 1
        if m == "KR" and o.get("order_type") == "limit":
            s["band_breach"] += c["band"] is True
            s["band_unknown"] += c["band"] is None
        if not 매수:
            s["sell_adv_over"] += c["adv_breach"] is True
            continue
        s["adv_breach"] += c["adv_breach"] is True
        s["adv_unknown"] += c["adv_breach"] is None
        if c["notional"] is not None:
            금액들[m].append(c["notional"])
            하루합[(m, day)] += c["notional"]
    for m, s in out.items():
        s["days"] = len(s["days"])
        s["max_notional"] = max(금액들[m], default=None)
        s["p95_notional"] = _분위(금액들[m], 0.95)
        s["max_daily_buy"] = max((v for (mm, _d), v in 하루합.items() if mm == m), default=None)
    return out


def _돈(v: float | None, market: str) -> str:
    return "-" if v is None else f"{v:,.0f}원" if market == "KR" else f"${v:,.2f}"


def lines(summary: dict[str, dict[str, Any]], since: str, today: str) -> list[str]:
    """사람이 읽을 줄 — **금액이 들어 있다**(비공개 운영 이슈로만 낸다)"""
    머리 = f"[절대 한도·가격 깃 그림자 계측 — docs/paper.md 15.12 · KIS모의투자 주문 {since}~{today}"
    머리 += " · 막지 않고 세기만]"
    줄 = [머리]
    if not summary:
        return [*줄, "  주문 없음"]
    for m in sorted(summary):
        s = summary[m]
        줄.append(f"  {m}: 주문 {s['orders']}건(매수 {s['buys']}·매도 {s['sells']}) · 거래일 {s['days']}일")
        if m == "KR":
            줄.append(
                f"    가격 깃(지정가가 가격제한폭 ±{markets.KR_DAILY_LIMIT:.0%} 밖): 걸렸을 {s['band_breach']}건"
                f" · 앞 종가 모름 {s['band_unknown']}건"
            )
        줄.append(
            f"    거래대금 깃(매수 금액 > {ADV_DAYS}일 평균 거래대금의 {ADV_SHARE_MAX:.0%}): 걸렸을 {s['adv_breach']}건"
            f" · 모름 {s['adv_unknown']}건 · 참고 — 같은 잣대를 넘은 매도 {s['sell_adv_over']}건(매도는 막지 않는다)"
        )
        줄.append(
            f"    매수 주문 크기: 최대 {_돈(s['max_notional'], m)} · 95분위 {_돈(s['p95_notional'], m)}"
            f" · 하루 매수 합 최대 {_돈(s['max_daily_buy'], m)} — 주문당·하루 총액 상한은 사용자 결정(한도 미정)"
        )
    return 줄
