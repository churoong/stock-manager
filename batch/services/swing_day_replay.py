"""스윙 S·당일 단타 T 의 **계좌형 리플레이** — 주식 수·현금 장부 (깔때기 ⑦, docs/funnel.md 4장, 25.2036).

왜 비중 엔진(`services/backtest.simulate`)에 끼우지 않나(25.2030): 그 엔진은 한 종목 하루 한 체결을 t 종가로
기록해 T 의 같은 날 시가 매수·종가 매도(두 체결)와 S 의 시가 매도를 못 담고, 운영 체결 판정(상한가 잠김·종가 짝
매도·정수 주)도 아니다. 그래서 이 장부는 **운영 순수 함수를 그대로** 부른다:

- 매도 판정: S `shadow.swing_exit`(종가 +5%/−3%/10거래일), T 는 남은 보유를 `DAY_LEFT_CODE` 로 판다
- 매수 후보·쉼: `pp.short_candidates`·`pp.rest_set`, 배분 `jobs/paper.buy_orders`(2부와 같은 함수), 관문 `rg.gate`
- 체결: 짝 매도 `pp.pair_sell_state` → `pp.judge_order`(judge·T 진입 막기·정지·하한가 미룸) → `pp.make_fill` →
  `pp.settle_check`(현금·보유), 순서 `pp.side_rank`

날 d 의 결정은 cutoff = d 앞 거래일(운영: 장 전 실행이 어제 종가까지 본다)이고 그 주문은 d 의 시가·종가로 판정한다.
가격은 **수정 계열**(로더 `opens` 와 종가가 같은 계수 — 25.2029)이라 권리락 계수는 비운다(이름 붙은 차이 ①).
이름 붙은 차이(일치 시험 ⑦-5 에서 묶는다):
① 권리락 계수 빔(수정 계열이 같은 비율을 반영) ② 시세 기다림·시장 시세 없는 날·기업행위 멈춤·환율 없음(운영 DB 둘레)
은 없다 — 그날 행이 없으면 정지로 본다(등락은 그 앞 행 있는 날 종가로 재 하한가 잠김을 판정한다, 25.2039)
③ 거래량은 거래대금(`turnover`)이 0 인지로만 본다 ④ 늦은 실행·장 끝난 뒤 손 실행 없음(늘 장 전 결정)
⑤ S 판정 종가는 시가가 있는 날만(운영 `SWING_CLOSES_SQL` 의 시가·고가·저가 > 0 근사)
⑥ 입금 규모(기본 10억)·최소 주문 0·종목·업종 상한은 시장 판단기·2부 기본값 — 운영 사용자 설정을 읽지 않는다
(실행기 25.2044)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from batch.services import backtest as bt
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import risk_gate as rg
from batch.services import shadow as sh

#: 리플레이 계좌 — 운영 0092 의 칸(selection short, entry open, top_n 10)
ACCOUNTS: dict[str, str] = {"S": pp.SWING_RULE, "T": pp.DAY_RULE}
TOP_N = 10


@dataclass
class STResult:
    account: str
    curve: list[tuple[str, float]]  # (날, 자본 ÷ 입금)
    fills: list[pp.Fill]
    #: 날마다 {day, buys, sells, expired: {까닭: 수}, deferred, blocked(관문 막힘 수), blocked_by: {관문 코드: 수},
    #: sold: {매도 사유 코드: 수}}
    #: — 일치 시험·보고용
    log: list[dict[str, Any]] = field(default_factory=list)
    #: 날마다 (날, {종목: max(하루 끝 평가, 그날 산 금액) ÷ 자본}) — 판정판의 노출·수용량 칸(`bt.Rebalance`)을
    #: 실제 값으로(25.2045·25.2047 — T 는 장중 보유)
    weights: list[tuple[str, dict[int, float]]] = field(default_factory=list)
    #: 날마다 (날, 회전) — (그날 산 금액 + 판 금액) ÷ 2 ÷ 자본(25.2047)
    turnover: list[tuple[str, float]] = field(default_factory=list)


def _bar(sid: int, d: str, prices: dict[int, dict[str, float]], opens: dict[int, dict[str, float]],
         turnover: dict[int, dict[str, float]] | None) -> pp.Bar | None:  # fmt: skip
    종가 = prices.get(sid, {}).get(d)
    if 종가 is None:
        return None  # 그날 행 없음 — 정지로 본다(이름 붙은 차이 ②)
    대금 = (turnover or {}).get(sid, {}).get(d)
    # 전일 종가 = 그날 앞 **마지막으로 행이 있는 날** — cutoff 날 행이 빠진 정지 뒤 첫날에도 등락을 내 하한가 잠김을
    # 판정한다(운영은 DB change_pct, 25.2039 — 예전엔 None 이라 잠긴 시가에 팔았다)
    앞 = [k for k in prices.get(sid, {}) if k < d]
    전 = prices[sid][max(앞)] if 앞 else None
    등락 = (종가 / 전 - 1) * 100 if 전 else None
    return pp.Bar(d, opens.get(sid, {}).get(d), 종가, 대금, 등락)


def simulate_account(
    account: str,
    *,
    rows_at: Callable[[bt.PriceView, str], list[report_picks.SignalRow]],
    prices: dict[int, dict[str, float]],
    opens: dict[int, dict[str, float]],
    dates: list[str],
    costs_at: Callable[[str], pp.Costs],
    stock_cache: dict[int, dict[str, Any]],
    settings: dict[str, Any],
    deposit: float = 1_000_000_000.0,
    turnover: dict[int, dict[str, float]] | None = None,
    market: str = "KR",
) -> STResult:
    """한 계좌(S·T)를 `dates` 날마다 장 전 결정 → 그날 체결로 굴린다. `rows_at(view, d)` = 그날 신호 행(시점 창 —
    `CoreMarket.at(view, d).rows`). `stock_cache` = 운영 `_stock` 꼴(id·ticker·name·sector·country·currency·
    asset_type)."""
    from batch.jobs import paper as jp
    from batch.services.core_strategy import _ViewClient

    if account not in ACCOUNTS:
        raise ValueError(f"리플레이 계좌는 S·T — {account}")
    if market != "KR":
        # 주문 통화를 "KRW" 로 박아(국내 가격제한·상한가 시가 막기가 달러 종목에도 걸린다) 환율 없이 사이징한다 —
        # 통화·`fx_at`(깔때기 ⑧-4) 전까지 막는다(25.2056)
        raise NotImplementedError("S·T 리플레이는 아직 국내(KR)만 — 미국 통화는 깔때기 ⑧-4")
    규칙 = ACCOUNTS[account]
    dates = sorted(dates)
    flows = [pp.CashFlow(1, dates[0], float(deposit), "deposit")]
    fills: list[pp.Fill] = []
    진입: dict[int, tuple[str, float]] = {}  # 종목 → (첫 매수일, 체결가 — 슬리피지 전)
    미룬: list[pp.Order] = []  # 정지·하한가로 미룬 매도(다음 날 다시)
    번호 = 0
    out = STResult(account, [], fills)
    # 날마다 창을 다시 정렬하지 않게 한 번 정렬(엔진 `simulate` 와 같다, 25.1522) — 5년 실행기(25.2044)
    미리 = (bt.SortedSeries(prices), bt.SortedSeries(turnover), bt.SortedSeries(None))

    def 값(d: str) -> tuple[float, dict[int, float]]:
        보유 = pp.positions(fills)
        몫: dict[int, float] = {}
        for sid, p in 보유.items():
            줄 = prices.get(sid, {})
            앞 = [k for k in 줄 if k <= d]
            몫[sid] = p.quantity * (줄[max(앞)] if 앞 else 0.0)
        return pp.cash(flows, fills) + sum(몫.values()), 몫

    for i, d in enumerate(dates):
        if i == 0:
            out.curve.append((d, 1.0))
            continue
        cutoff = dates[i - 1]
        보유 = pp.positions(fills)
        기록: dict[str, Any] = {"day": d, "buys": 0, "sells": 0, "expired": {}, "deferred": 0}

        # ① 매도 — S 넘음(그날 앞 종가까지), T 남은 보유. 미룬 매도는 그대로 다시
        주문: list[tuple[pp.Order, str | None]] = [(replace(o, fill_session=d), o.reason) for o in 미룬]
        미룬 = []
        파는것 = {o.stock_id for o, _r in 주문}
        for sid, p in sorted(보유.items()):
            if sid in 파는것 or p.quantity <= 0:
                continue
            까닭코드 = None
            if 규칙 == pp.DAY_RULE:
                까닭코드 = pp.DAY_LEFT_CODE
            elif sid in 진입:
                들어간날, 진입가 = 진입[sid]
                종가들 = [(k, v) for k, v in sorted(prices.get(sid, {}).items())
                         if 들어간날 <= k <= cutoff and k in opens.get(sid, {})]  # fmt: skip
                넘음 = sh.swing_exit(들어간날, 진입가, 종가들[: sh.SWING_CLOSE.max_sessions])
                if 넘음 is not None:
                    까닭코드 = pp.SWING_CODES[넘음[0]]
            if 까닭코드:
                번호 += 1
                주문.append((pp.Order(번호, account, sid, "sell", "market", None, int(p.quantity), "KRW", d,
                                      reason=까닭코드), 까닭코드))  # fmt: skip
                파는것.add(sid)

        # ② 매수 — 운영 run_step 과 같은 거름·배분·관문
        # 운영 run_step 과 같이 **판단일(cutoff = dates[i-1])** 에서 4장 앞 — 예전 dates[i-4] 는 체결일 기준이라
        # 쉼이 한 장 짧아 운영보다 하루 일찍 다시 샀다(25.2039, 25.2036 교차검증 [필수])
        쉼_시작 = dates[max(0, i - pp.REBUY_COOLDOWN_SESSIONS)]
        쉼 = pp.rest_set(규칙, fills, 쉼_시작, 파는것, set())
        view = bt.PriceView(prices, cutoff, turnover, holding=frozenset(보유), presorted=미리)
        행 = pp.short_candidates(rows_at(view, d), set(보유))
        if 행:
            현금 = pp.cash(flows, fills)
            평가행 = [{"stock_id": sid,
                     "value_krw": p.quantity * prices[sid][max(k for k in prices[sid] if k <= cutoff)]}
                    for sid, p in 보유.items()]  # fmt: skip
            자본 = 현금 + sum(r["value_krw"] for r in 평가행)
            v = pp.Valuation(invested_krw=deposit, cash_krw=현금, holdings_krw=자본 - 현금, equity_krw=자본,
                             pnl_krw=자본 - deposit, pnl_pct=None, rows=평가행)  # fmt: skip
            client = _ViewClient(view)
            낸, _b = jp.buy_orders(
                client, market=market, account=account, account_fills=fills, valuation=v, open_buys=[],
                signal_rows=행, settings=settings, price_series={}, series_unknown={}, fx_rate=None,
                stop_ids=set(), sold_recently=쉼, stock_cache=stock_cache, top_n=TOP_N, entry="open",
            )  # fmt: skip
            # 견줄 어제 평가가 없으면 하루 수익 모름(None) — 운영 첫 평가와 같다(`prev_twr` 없음). 예전엔 0.0 이라
            # 관문 결과는 같지만 문맥이 갈렸다(25.2053 — 관문 문맥 일치 시험이 찾음)
            어제 = out.curve[-1][1] if out.curve else None
            그제 = out.curve[-2][1] if len(out.curve) > 1 else None
            후보 = [rg.Candidate(o["stock_id"], "buy", o["cost_krw"], o["ref_date"], k, o["cash_krw"])
                    for k, o in enumerate(낸)]  # fmt: skip
            문맥 = rg.Context(
                equity_krw=자본, day_return_pct=(어제 / 그제 - 1) * 100 if 어제 and 그제 else None,
                cash_available_krw=현금,
                holding_krw={r["stock_id"]: r["value_krw"] for r in 평가행},
                sector_of={sid: stock_cache.get(sid, {}).get("sector") for sid in {*보유, *(c.stock_id for c in 후보)}},
                max_stock_pct=settings["max_weight_per_stock"], max_sector_pct=settings["max_weight_per_sector"],
                today=d, ref_floor=rg.ref_floor(market, date.fromisoformat(d)),
            )  # fmt: skip
            통과, 막힘 = rg.gate(후보, 문맥)
            if 막힘:
                기록["blocked"] = 기록.get("blocked", 0) + len(막힘)
                # 관문 까닭 코드별(종목마다 첫 코드 — 운영 `rg.summary` 의 by_reason 과 같은 잣대가 아니라 시험·보고용)
                for _c, 까닭 in 막힘:
                    코드 = 까닭[0][0] if 까닭 else "?"
                    기록.setdefault("blocked_by", {})[코드] = 기록.get("blocked_by", {}).get(코드, 0) + 1
            for c in 통과:
                o = 낸[c.key]
                번호 += 1
                주문.append((pp.Order(번호, account, o["stock_id"], "buy", "market", None, int(o["quantity"]), "KRW", d,
                                      horizon=o.get("horizon"), ref_price=o.get("ref_price")), None))  # fmt: skip
                if 규칙 == pp.DAY_RULE:
                    번호 += 1
                    주문.append((pp.Order(번호, account, o["stock_id"], "sell", "market", None, int(o["quantity"]),
                                          "KRW", d, fill_basis="close", reason=pp.DAY_CLOSE_CODE),
                                 pp.DAY_CLOSE_CODE))  # fmt: skip

        # ③ 체결 — 운영 fill_orders 와 같은 순서·판정
        산금액: dict[int, float] = {}
        판금액 = 0.0
        남은매수 = {o.id: o for o, _r in 주문 if o.side == "buy"}
        for o, 사유 in sorted(주문, key=lambda x: (pp.side_rank(account, x[0].side), x[0].id)):
            짝 = pp.pair_sell_state(o, 사유, fills, list(남은매수.values()))
            if 짝 is not None:  # "expire" — 리플레이는 시세를 기다리지 않아 "wait" 이 없다
                기록["expired"]["단타 짝 매수 안 참"] = 기록["expired"].get("단타 짝 매수 안 참", 0) + 1
                continue
            bar = _bar(o.stock_id, d, prices, opens, turnover)

            def _마지막(sid: int = o.stock_id, 오늘: str = d) -> float | None:
                # 운영 `LAST_CLOSE_SQL`(date <= 그날)과 같이 그날 행까지 — 하한가에 잠긴 날은 그날 종가로(25.2039)
                앞 = [k for k in prices.get(sid, {}) if k <= 오늘]
                return prices[sid][max(앞)] if 앞 else None

            판정, 가격, 까닭 = pp.judge_order(o, bar, _마지막)
            if 판정 == "defer":
                미룬.append(replace(o, deferrals=o.deferrals + 1))
                기록["deferred"] += 1
                continue
            if 판정 == "expire" or 가격 is None:
                남은매수.pop(o.id, None)
                기록["expired"][까닭] = 기록["expired"].get(까닭, 0) + 1
                continue
            체결 = pp.make_fill(o, 가격, d, 1.0, costs_at(d))
            예약 = 0.0
            if o.side == "buy":
                남은매수.pop(o.id, None)
                예약 = pp.reserved_krw(list(남은매수.values()), {"USD": None}) or 0.0
            안됨 = pp.settle_check(o, 체결, fills, flows, 예약)
            if 안됨:
                기록["expired"][안됨] = 기록["expired"].get(안됨, 0) + 1
                continue
            fills.append(체결)
            # 그날 체결 금액(슬리피지 전 가격 × 수량) — 노출·회전 칸(25.2047)
            금액 = float(가격) * o.quantity
            if o.side == "buy":
                산금액[o.stock_id] = 산금액.get(o.stock_id, 0.0) + 금액
            else:
                판금액 += 금액
            if o.side == "buy":
                기록["buys"] += 1
                진입.setdefault(o.stock_id, (d, float(가격)))
            else:
                기록["sells"] += 1
                기록.setdefault("sold", {})[o.reason or ""] = 기록.get("sold", {}).get(o.reason or "", 0) + 1
                if o.stock_id not in pp.positions(fills):
                    진입.pop(o.stock_id, None)
        out.log.append(기록)
        자본, 몫 = 값(d)
        out.curve.append((d, 자본 / deposit))
        # 노출 = 종목마다 max(하루 끝 평가, 그날 산 금액) ÷ 자본 — 당일 단타 T 는 하루 끝 보유가 늘 0 이라
        # 하루 끝 평가만 쓰면 "투자한 달 0%" 가 됐다(25.2047). 회전 = (그날 산 금액 + 판 금액) ÷ 2 ÷ 자본 —
        # 엔진처럼 값 움직임(drift)은 빼고 T 의 장중 왕복은 넣는다
        if 자본 > 0:
            w = {sid: max(몫.get(sid, 0.0), 산금액.get(sid, 0.0)) / 자본 for sid in set(몫) | set(산금액)}
            out.weights.append((d, {sid: x for sid, x in w.items() if x > 0}))
            out.turnover.append((d, (sum(산금액.values()) + 판금액) / 2 / 자본))
        else:
            out.weights.append((d, {}))
            out.turnover.append((d, 0.0))
    return out
