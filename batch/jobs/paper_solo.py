"""거장 단독 규칙 계좌 Z3(그린블랫)·Z4(오닐)의 매수 — 읽고 순수 판정(`services/solo`)을 부른다
(docs/company.md 12.5, docs/paper.md 18장, docs/infra.md 25.2319).

신호 종목이 아니라 **그날 유니버스 편입 종목**에서 고른다(사전 등록 12.5) — 그래서 `jobs/paper.run_step` 의 신호 배분
갈래(`build_portfolio`)를 타지 않고 ETF 전술처럼 따로 주문 줄을 만든다. 크기는 평가 총액 × 고정 비중(Z3 1/24 · Z4 10%),
진입은 다음 장 시가 시장가, 수량은 기준가(그날까지 마지막 종가)로 나눈다(`paper_etf.buy_order` 와 같은 잣대).
현금·하루 손실·멈춤 스위치·종목·업종 상한·주문 수 막이는 다른 계좌와 같은 위험 점검(`risk_gate`)이 부르는 쪽에서 한다.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from batch.core import calendar as cal
from batch.services import company as co
from batch.services import paper as pp
from batch.services import solo
from batch.services.sell_flags import _criterion

#: 기준가 — 그날까지 마지막 종가(`jobs/paper.LAST_CLOSE_SQL` 와 같은 글, 순환 import 를 피하려고 여기 둔다 — 시험이
#: 대조)
LAST_CLOSE_SQL = (
    "SELECT close, date FROM prices WHERE stock_id = ? AND date <= ? AND close > 0 ORDER BY date DESC LIMIT 1"
)


def universe_dossiers(client: Any, market: str, ids: list[int], as_of: str, cache: dict[str, Any]) -> list[co.Dossier]:
    """유니버스 종목의 거장 자료(배당·베타 빼고 — `master_facts(light=True)`) — 실행 하나에 시장·기준일마다 한 번.
    Z3·Z4 가 같은 실행에서 같이 쓴다. 위원회 길의 캐시(`agenda`·`population`)와 열쇠가 달라 섞이지 않는다"""
    from batch.jobs import company as cjob

    열쇠 = f"solo_universe:{market}:{as_of}"
    if 열쇠 not in cache:
        사실 = cjob.master_facts(client, sorted(set(ids)), market, as_of, light=True)
        cache[열쇠] = [co.Dossier(stock_id=sid, ticker="", as_of=as_of, masters=f) for sid, f in sorted(사실.items())]
    return cache[열쇠]


def plan(
    client: Any, selection: str, *, market: str, as_of: str, fill_session: str, universe_ids: list[int],
    held: set[int], excluded: set[int], cache: dict[str, Any],
) -> tuple[list[solo.Ranked], dict[str, Any]]:
    """그날 새로 살 종목(순위 순)과 기록. `held` = 이 시장 보유 + 열린 매수(그날 파는 것은 뺀다 — 같은 장에 나간다),
    `excluded` = 매도가 열린·다시 사기 쉼 종목. 쉬는 날은 ([], {"skipped": 까닭})"""
    if selection == solo.MAGIC:
        첫장 = date.fromisoformat(fill_session)
        if not solo.is_month_first_session(첫장, cal.previous_session(market, 첫장)):
            return [], {"skipped": "그 달 첫 거래일 장이 아님", "fill_session": fill_session}
        if len(held) >= solo.Z3_MAX_HOLD:
            return [], {"skipped": f"보유 {len(held)} — 상한 {solo.Z3_MAX_HOLD}", "held": len(held)}
        자료 = universe_dossiers(client, market, universe_ids, as_of, cache)
        순위 = solo.magic_ranks(자료)
        고름 = solo.picks(순위, held, excluded, solo.Z3_MAX_HOLD, 1)
    elif selection == solo.CANSLIM:
        from batch.services import trend

        if len(held) >= solo.Z4_MAX_HOLD:
            return [], {"skipped": f"보유 {len(held)} — 상한 {solo.Z4_MAX_HOLD}", "held": len(held)}
        지수 = solo.M_INDEX[market]
        국면 = trend.regimes_for_country(client, market, as_of).get(지수)
        상태 = getattr(국면, "state", None)
        if not solo.canslim_market_ok(상태):
            return [], {"skipped": f"시장 국면 {trend.STATE_LABEL.get(str(상태), '모름')}({지수}) — 강세일 때만",
                        "regime": 상태, "regime_note": getattr(국면, "note", None)}  # fmt: skip
        자료 = universe_dossiers(client, market, universe_ids, as_of, cache)
        순위 = solo.canslim_ranks(자료)
        고름 = solo.picks(순위, held, excluded, solo.Z4_MAX_HOLD, None)
    else:  # 부르는 쪽이 단독 고르기만 넘긴다
        raise ValueError(f"단독 고르기가 아님: {selection}")
    return 고름, {"universe": len(자료), "ranked": len(순위), "held": len(held), "picks": [r.stock_id for r in 고름]}


def _근거(selection: str, r: solo.Ranked, as_of: str, n_ranked: int) -> list[dict[str, Any]]:
    """근거표 — 고른 순위 줄(어느 표·언제·출처)"""
    if selection == solo.MAGIC:
        ey, roc = r.detail.get("ey"), r.detail.get("roc")
        값 = f"{r.pct:.1f} 백분위 (EY {ey * 100:.1f}% · ROC {roc * 100:.1f}%)" if ey is not None and roc is not None \
            else f"{r.pct:.1f} 백분위"  # fmt: skip
        기준 = f"유니버스 {n_ranked}종목 가운데 높은 순 · 안 든 1종목 (magic_formula_v1)"
        return [_criterion("마법공식 순위", 값, 기준, "financial_snapshots·factors", as_of)]
    return [_criterion("오닐 두 부서 찬성 · 12-1 모멘텀", f"{r.pct:.1f} 백분위",
                       f"유니버스에서 실적 A·주가 N·L 모두 찬성 {n_ranked}종목 가운데 높은 순 (canslim_v1)",
                       "financial_snapshots·factors", as_of)]  # fmt: skip


def size_orders(
    client: Any, selection: str, picks: list[solo.Ranked], *, market: str, valuation: pp.Valuation,
    open_buys: list[pp.Order], fx_rate: float | None, as_of: str, n_ranked: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """고른 종목마다 다음 장 시가 시장가 한 줄 — 수량 = ⌊min(평가 총액 × 비중 ÷ 기준가, 남은 쓸 현금 ÷ 여유 기준가)⌋.

    비중은 Z3 1/24 · Z4 10%. 기준가 = 오늘(그 시장)까지 마지막 종가. 1주도 못 사는 종목은 건너뛰고 까닭을 남긴다"""
    if not picks:
        return [], {}
    통화 = "KRW" if market == "KR" else "USD"
    환율 = 1.0 if 통화 == "KRW" else fx_rate
    if not 환율:
        return [], {"skipped": "환율 없음"}
    예약 = pp.reserved_krw(open_buys, {"USD": fx_rate})
    if 예약 is None:
        return [], {"skipped": "미체결 달러 주문의 환율 모름"}
    현금 = valuation.cash_krw - 예약
    비중 = 1 / solo.Z3_SLICES if selection == solo.MAGIC else solo.Z4_WEIGHT_PCT / 100
    오늘 = cal.local_today(market).isoformat()
    주문: list[dict[str, Any]] = []
    못삼: dict[int, str] = {}
    for r in picks:
        시세 = client.execute(LAST_CLOSE_SQL, [r.stock_id, 오늘]).rows
        if not 시세:
            못삼[r.stock_id] = "시세 없음"
            continue
        종가, 날 = float(시세[0][0]), str(시세[0][1])[:10]
        주당 = 종가 * 환율
        주당현금 = 주당 * (1 + pp.FEE_BUFFER) * (1 + pp.FX_BUFFER if 통화 != "KRW" else 1.0)
        수량 = math.floor(min(valuation.equity_krw * 비중 / 주당, max(현금, 0.0) / 주당현금)) if 주당 > 0 else 0
        if 수량 < 1:
            못삼[r.stock_id] = "1주도 못 삼"
            continue
        현금 -= 수량 * 주당현금
        근거 = _근거(selection, r, as_of, n_ranked)
        규칙 = solo.RULE_NAMES[selection]
        주문.append({
            "stock_id": r.stock_id, "limit_price": None, "quantity": 수량, "currency": 통화, "tranche": 0,
            "reason": f"{규칙}: {r.pct:.0f} 백분위 · 비중 {비중 * 100:.1f}%",
            "horizon": "long", "score": None, "score_as_of": None,
            # 근거표는 `signal.criteria` 에도 — 웹 거래내역은 그 자리와 `flags[].criteria` 만 읽는다(ETF 와 같다,
            # 25.2168)
            "rationale": {"rule": 규칙, "rank_pct": round(r.pct, 4), "detail": r.detail, "weight": 비중,
                          "criteria": 근거, "signal": {"criteria": 근거}},
            "ref_price": 종가, "cost_krw": 수량 * 주당현금, "ref_date": 날, "cash_krw": 수량 * 주당현금,
        })  # fmt: skip
    return 주문, {"orders": len(주문), **({"not_bought": 못삼} if 못삼 else {})}
