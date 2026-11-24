"""거장 단독 규칙 계좌 Z3(그린블랫)·Z4(오닐)의 매수 — 읽고 순수 판정(`services/solo`)을 부른다
(docs/company.md 12.5, docs/paper.md 18장, docs/infra.md 25.2319).

신호 종목이 아니라 **그날 유니버스 편입 종목**에서 고른다(사전 등록 12.5) — 그래서 `jobs/paper.run_step` 의 신호 배분
갈래(`build_portfolio`)를 타지 않고 ETF 전술처럼 따로 주문 줄을 만든다. 크기는 평가 총액 × 고정 비중(Z3 1/24 · Z4 10%),
진입은 다음 장 시가 시장가, 수량은 기준가(그날까지 마지막 종가)로 나눈다(`paper_etf.buy_order` 와 같은 잣대).
현금·하루 손실·멈춤 스위치·종목·업종 상한·주문 수 막이는 다른 계좌와 같은 위험 점검(`risk_gate`)이 부르는 쪽에서 한다.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date
from typing import Any

from batch.core import calendar as cal
from batch.services import company as co
from batch.services import paper as pp
from batch.services import risk_gate as rg
from batch.services import solo
from batch.services.sell_flags import _criterion

#: 한 자리에 넘길 수 있는 후보 수 — 넘으면 그만 본다(25.2321). 상위 10 이 모두 자료 문제면 그날 자료가 통째로 이상한
#: 것이다
SKIP_PER_SLOT = 10
#: 위험 점검의 종목·업종 상한에 막힌 후보는 다음 순위로 넘긴다(25.2321) — 그 밖의 막힘(멈춤 스위치·하루 손실·현금·주문
#: 수)은
#: 다음 순위로 넘겨도 같이 막혀 멈춘다. 다시 재는 횟수 상한
CAP_CODES = frozenset({"stock_cap", "sector_cap"})
MAX_GATE_ROUNDS = 5
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
) -> tuple[list[solo.Ranked], int, dict[str, Any]]:
    """그날 살 후보(순위 순 — 든 것·뺄 것 뺀 전부), 새로 살 자리 수, 기록. `held` = 이 시장 보유 + 열린 매수(그날 파는
    것은 뺀다 — 같은 장에 나간다), `excluded` = 매도가 열린·다시 사기 쉼 종목. 쉬는 날은 ([], 0, {"skipped": 까닭}).

    자리보다 후보를 넉넉히 돌려준다 — 고른 종목이 기업행위 경계·묵은 시세·1주 못 삼이면 다음 순위로 넘긴다(25.2320,
    25.2319 교차검증 [필수] 1·[권고] — 사전 등록 "상위에서 아직 안 든 1종목" 은 살 수 있는 다음 순위로 읽는다)"""
    if selection == solo.MAGIC:
        첫장 = date.fromisoformat(fill_session)
        if not solo.is_month_first_session(첫장, cal.previous_session(market, 첫장)):
            return [], 0, {"skipped": "그 달 첫 거래일 장이 아님", "fill_session": fill_session}
        상한, 하루 = solo.Z3_MAX_HOLD, 1
    elif selection == solo.CANSLIM:
        상한, 하루 = solo.Z4_MAX_HOLD, None
    else:  # 부르는 쪽이 단독 고르기만 넘긴다
        raise ValueError(f"단독 고르기가 아님: {selection}")
    자리 = solo.slots(len(held), 상한, 하루)
    if 자리 == 0:
        return [], 0, {"skipped": f"보유 {len(held)} — 상한 {상한}", "held": len(held)}
    if selection == solo.CANSLIM:
        from batch.services import trend

        지수 = solo.M_INDEX[market]
        국면 = trend.regimes_for_country(client, market, as_of).get(지수)
        상태 = getattr(국면, "state", None)
        if not solo.canslim_market_ok(상태):
            return [], 0, {"skipped": f"시장 국면 {trend.STATE_LABEL.get(str(상태), '모름')}({지수}) — 강세일 때만",
                           "regime": 상태, "regime_note": getattr(국면, "note", None)}  # fmt: skip
    자료 = universe_dossiers(client, market, universe_ids, as_of, cache)
    순위 = solo.magic_ranks(자료) if selection == solo.MAGIC else solo.canslim_ranks(자료)
    후보 = solo.candidates(순위, held, excluded)
    return 후보, 자리, {"rule": solo.RULE_NAMES[selection], "universe": len(자료), "ranked": len(순위),
                       "held": len(held), "slots": 자리}  # fmt: skip


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
    client: Any, selection: str, candidates: list[solo.Ranked], slots: int, *, market: str,
    valuation: pp.Valuation, open_buys: list[pp.Order], fx_rate: float | None, as_of: str, n_ranked: int,
    is_frozen: Callable[[int], bool],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """후보를 순위대로 보며 자리(`slots`)만큼 다음 장 시가 시장가 한 줄씩.

    - 수량 = ⌊min(평가 총액 × 비중 ÷ 기준가, 남은 쓸 현금 ÷ 예약 단가)⌋ — 비중 Z3 1/24 · Z4 10%, 기준가 = 오늘(그
      시장)까지 마지막 종가. **예약 단가는 `pp.reserved_krw` 와 같은 잣대**(기준가 × (1 + 시가 틈 여유 10%) × 수수료
      여유, 달러는 환율 × 환율 여유) — 여유 없이 나누면 같은 계좌 다른 시장의 열린 매수와 겹쳐 체결 때 "현금 부족" 으로
      취소됐다(25.2320, 25.2319 교차검증 [필수] 2 — 25.1458 "Candidate.cash_krw = 예약과 같은 값"). 크기 상한(평가 총액
      × 비중)은 기준가로 (`size_buys` 시가 일괄과 같은 짝)
    - 다음 순위로 넘기는 것: 기업행위 경계(`is_frozen` — 관문의 `frozen` 은 관심 집합만 봐 유니버스에서 새로 고른 종목을
      못 막는다, 25.2320 [필수] 1), 시세 없음, 묵은 종가(관문과 같은 거래일 잣대), 평가 총액 × 비중이 1주에 못 미침.
      **현금이 모자라 1주도 못 사면 멈춘다**(더 싼 다음 순위로 바꿔 사지 않는다 — 크기 규칙이 바뀐다)
    - 넘긴 까닭은 `not_bought` 에"""
    if not candidates or slots <= 0:
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
    오늘 = cal.local_today(market)
    묵음선 = rg.ref_floor(market, 오늘)
    여유 = (1 + pp.FEE_BUFFER) * (1 + pp.FX_BUFFER if 통화 != "KRW" else 1.0)
    주문: list[dict[str, Any]] = []
    못삼: dict[int, str] = {}
    넘김상한 = slots * SKIP_PER_SLOT
    for r in candidates:
        if len(주문) >= slots:
            break
        if len(못삼) >= 넘김상한:
            # 상위 후보가 줄줄이 자료 문제인 날(시세가 통째로 묵음·경계 읽기 장애) 유니버스 끝까지 읽지 않게(25.2321
            # [참고])
            못삼[-1] = f"넘긴 후보 {넘김상한}개 — 그만 봄"
            break
        # 싼 검사(시세·묵음·크기) 먼저, 경계(종목마다 DB 읽기)는 뒤에(25.2321 [참고])
        시세 = client.execute(LAST_CLOSE_SQL, [r.stock_id, 오늘.isoformat()]).rows
        if not 시세:
            못삼[r.stock_id] = "시세 없음 — 다음 순위로"
            continue
        종가, 날 = float(시세[0][0]), str(시세[0][1])[:10]
        if rg.stale(날, 오늘, 묵음선):
            못삼[r.stock_id] = f"묵은 종가({날}) — 다음 순위로"
            continue
        주당 = 종가 * 환율  # 원
        크기 = math.floor(valuation.equity_krw * 비중 / 주당) if 주당 > 0 else 0
        if 크기 < 1:
            못삼[r.stock_id] = "비중으로 1주에 못 미침 — 다음 순위로"
            continue
        if is_frozen(r.stock_id):
            못삼[r.stock_id] = "기업행위 경계(분할·병합 의심) — 다음 순위로"
            continue
        예약단가 = 주당 * (1 + pp.OPEN_BUY_BUFFER) * 여유  # `reserved_krw` 와 같은 식
        수량 = min(크기, math.floor(max(현금, 0.0) / 예약단가))
        if 수량 < 1:
            못삼[r.stock_id] = "현금 부족 — 멈춤"
            break
        현금 -= 수량 * 예약단가
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
            # 관문 종목·업종 상한은 기준가 × 수수료·환율 여유(`size_buys` 와 같은 짝, 25.2321), 현금 검사는 예약과 같은
            # 값
            "ref_price": 종가, "cost_krw": 수량 * 주당 * 여유, "ref_date": 날,
            "cash_krw": 수량 * 예약단가,
        })  # fmt: skip
    return 주문, {"orders": len(주문), "picks": [o["stock_id"] for o in 주문],
                  **({"not_bought": 못삼} if 못삼 else {})}  # fmt: skip


def orders_through_gate(
    client: Any, selection: str, candidates: list[solo.Ranked], slots: int, *, market: str,
    valuation: pp.Valuation, open_buys: list[pp.Order], fx_rate: float | None, as_of: str, n_ranked: int,
    is_frozen: Callable[[int], bool], gate: Callable[[list[dict[str, Any]]], tuple[list[Any], list[Any]]],
    account_id: str, fill_session: str,
) -> tuple[list[dict[str, Any]], list[Any], list[Any], dict[str, Any]]:
    """`size_orders` → 위험 점검(`gate` — 주문 줄 전체를 받아 (통과 Candidate, [(막힌 Candidate, 까닭)]))을 되풀이한다.

    새로 낸 줄이 **종목·업종 상한으로만** 막히면 그 종목을 빼고 남은 자리를 다음 순위로 다시 잰다(최대
    `MAX_GATE_ROUNDS`). 사전 등록 "상위에서 아직 안 든 1종목" 을 살 수 있는 다음 순위로 읽은 것과 같은 꼴(25.2321,
    25.2320 교차검증 [권고] — 예전엔 상한에 막힌 자리가 그날 비고 다음 날 같은 상위 후보가 또 막혀, 한 업종에 몰린 동안
    Z4 가 상한까지 영영 못 찼다). 다음 회차의 현금은 앞 회차에 통과한 줄을 열린 매수로 쳐 뺀다(예약과 같은 잣대).
    돌려주는 것: (모든 주문 줄, 통과, 막힘, 기록) — Candidate 의 `key` 는 주문 줄의 자리"""
    주문: list[dict[str, Any]] = []
    통과: list[Any] = []
    막힘: list[Any] = []
    기록: dict[str, Any] = {}
    넘김: set[int] = set()
    for 회 in range(MAX_GATE_ROUNDS):
        남은 = slots - len(통과)
        if 남은 <= 0:
            break
        든 = {주문[c.key]["stock_id"] for c in 통과}
        # 앞 회차에서 자료 문제로 넘긴 종목(시세 없음·묵음·1주 미만·경계)도 다시 보지 않는다 — 회차마다 다시 읽고
        # 넘김 상한을 다시 써 빈 자리를 못 채웠다(25.2322, 25.2321 교차검증 [참고]). "현금 부족 — 멈춤" 종목은 다음
        # 회차에 다시 본다
        자료문제 = {k for k, v in (기록.get("not_bought") or {}).items()
                    if k != -1 and not str(v).startswith("현금 부족")}  # fmt: skip
        더 = [r for r in candidates if r.stock_id not in 넘김 | 자료문제 and r.stock_id not in 든]
        예약 = open_buys + [pp.Order(id=-1 - c.key, account_id=account_id, stock_id=주문[c.key]["stock_id"], side="buy",
                                     order_type="market", limit_price=None, quantity=주문[c.key]["quantity"],
                                     currency=주문[c.key]["currency"], fill_session=fill_session,
                                     ref_price=주문[c.key]["ref_price"]) for c in 통과]  # fmt: skip
        새, 기 = size_orders(client, selection, 더, 남은, market=market, valuation=valuation, open_buys=예약,
                            fx_rate=fx_rate, as_of=as_of, n_ranked=n_ranked, is_frozen=is_frozen)  # fmt: skip
        for k, v in 기.items():
            기록[k] = {**기록.get(k, {}), **v} if isinstance(v, dict) else v
        if not 새:
            break
        시작 = len(주문)
        주문 += 새
        통과, 막힘 = gate(주문)
        if 회 > 0:
            기록["gate_rounds"] = 회 + 1  # 새 줄을 실제로 다시 잰 회차만 센다(25.2322)
        새막힘 = [(c, 까닭) for c, 까닭 in 막힘 if c.key >= 시작]
        넘김 |= {주문[c.key]["stock_id"] for c, _ in 새막힘}
        if not 새막힘 or not all(code in CAP_CODES for _c, 까닭 in 새막힘 for code, _t in 까닭):
            break
    기록["orders"] = len(통과)
    기록["picks"] = [주문[c.key]["stock_id"] for c in 통과]
    # 매수 쪽 막힌 종목 수 — 계좌의 `risk_blocked` 는 막힌 매도도 세어 경고가 그것을 쓰면 매도가 막힌 날 "사지 못함" 이
    # 거짓으로 떴다(25.2325, 25.2323 교차검증 [필수])
    if 막힘:
        기록["blocked"] = len({주문[c.key]["stock_id"] for c, _ in 막힘})
    # 앞 회차에 "현금 부족" 으로 적혔다가 다음 회차에 주문 줄이 된 종목은 못 산 것에서 뺀다 — 산 종목이 picks 와
    # not_bought 에 함께 적혔고(25.2322), 관문에 막힌 종목은 원장 blocked(위험 점검)와 까닭이 엇갈렸다(25.2324 — 막힌
    # 까닭은 원장에 있다)
    if 기록.get("not_bought"):
        줄종목 = {o["stock_id"] for o in 주문}
        기록["not_bought"] = {k: v for k, v in 기록["not_bought"].items() if k not in 줄종목}
        if not 기록["not_bought"]:
            del 기록["not_bought"]
    return 주문, 통과, 막힘, 기록
