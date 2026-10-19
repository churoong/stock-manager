"""핵심 H 다년 백테스트의 장부형 전략 다섯 — 조각 1c (docs/backtest.md 10장, docs/infra.md 25.1532). 순수 함수.

`make_core_strategy(variant, market, sessions=…)` 가 엔진(`bt.simulate`)의 판단 함수를 돌려준다. 날마다
`bt.LedgerOrders(sells, buys)` — 운영 가상모의투자 한 계좌의 하루(`jobs/paper.run_step`)를 그 시점으로 다시 돈다:
① 보유 매도 판정(`sell_flags.evaluate` + 추세 계좌는 `jobs/paper._추적손절`) → ② 계좌 규칙(`pp.sells_for`) →
③ 판 뒤 쉼(`pp.REBUY_COOLDOWN_SESSIONS`) → ④ 매수(`jobs/paper.buy_orders` — 2부 `build_portfolio`·`size_buys`,
무작위 대조는 `random_substitute`) → ⑤ 사전 위험 점검(`risk_gate.gate`).

**판단식을 다시 적지 않는다.** 운영 함수는 DB 클라이언트를 받으므로 `_ViewClient` 가 그 자리에 들어가
**cutoff 로 자른 가격 창(`PriceView`)만으로** 세 가지 읽기(마지막 종가·첫 매수 뒤 고점·기업행위 창)에 답한다 —
모르는 읽기는 오류로 멈춘다(운영이 읽기를 더하면 여기서 조용히 빈 값이 되지 않게).

**제 장부를 엔진과 맞춘다.** 첫 매수일·기간·주당 원가(수수료 포함)·고점 시작일을 전략이 들고, 날마다 `view.weights`·
`view.holding` 과 맞춘다 — 엔진이 못 산 것(정지·상장폐지·체결가 없음)은 버리고, 엔진이 판 것(상장폐지)은 지운다. 산 날의
원가는 **다음 결정에서야** 그날 종가가 창에 들어와 정한다 — cutoff 뒤 가격은 읽을 길이 없다.

운영과 다른 것(알고 둔다, docs/backtest.md 10장 조각 1c): 체결은 t 종가 전량(운영 3회 분할 지정가·시가 시장가) ·
상관 축소 없음 · 정수 주·최소 주문 금액은 명목 자본(`NOTIONAL_EQUITY`)이 커서 묶이지 않는다 ·
기업행위 멈춤 없음(수정주가) · 정지 매도의 20거래일 청산 없음 · 하루 손실 관문의 수익률은 직전 cutoff 비중 × 종가 변화.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Any

from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import risk_gate as rg
from batch.services import sell_flags as sf


@dataclass(frozen=True)
class Variant:
    """운영 계좌 칸(`paper_accounts`) 그대로 — 마이그레이션 0070(B)·0075(R1·R2)·0082(G)·0083(H)."""

    account: str  # 운영 계좌 id — 무작위 씨앗 `계좌|시장|신호 종목` 에 들어간다
    sell_rule: str  # `pp.SELL_RULES` 열쇠
    entry: str  # tranche(3회 분할 지정가) · open(시가 일괄)
    top_n: int
    selection: str  # signal · random · weighted(지주사 H)


#: 핵심 변형 다섯 — 짝 차이가 운영과 같은 뜻(H−G 선정, G−B 매도, B−R 선정 대 무작위)
VARIANTS: dict[str, Variant] = {
    "core_h": Variant("H", "trend", "tranche", 5, "weighted"),
    "core_g": Variant("G", "trend", "tranche", 5, "signal"),
    "core_b": Variant("B", "core", "tranche", 5, "signal"),
    "core_r1": Variant("R1", "core", "open", 5, "random"),
    "core_r2": Variant("R2", "core", "open", 5, "random"),
}

#: 비중(자본 1)을 운영 함수가 먹는 원화 금액으로 바꾸는 명목 자본. 크게 잡아 정수 주 내림이 비중에 1e-6 아래로만
#: 남는다 — 운영의 정수 주·최소 주문 금액(작은 계좌에서 비싼 종목을 못 사는 일)은 재현하지 않는다(알고 둔 차이)
NOTIONAL_EQUITY = 1e12


@dataclass
class Lot:
    """전략 장부의 한 보유 — 운영 `pp.Position` 에서 매도 판정이 쓰는 칸만."""

    stock_id: int
    first_buy: str  # 첫 매수 체결일 = 그 결정일 t(엔진은 t 종가에 산다). 기간초과·고점의 시작
    horizon: str | None  # 첫 매수 신호의 기간 — 더 사도 바뀌지 않는다(운영 `positions`)
    unit_cost: float  # 주당 원가, 수수료(시장가는 슬리피지도) 포함 — 운영 `Position.cost / quantity`
    substitute_of: int | None = None  # 무작위 대조: 어느 신호 자리로 산 종목인가(나중 주문이 이긴다)
    last_buy: str = ""


@dataclass(frozen=True)
class _Bought:
    stock_id: int
    day: str
    weight: float  # 그날 자본 대비 더 산 비중
    horizon: str | None
    substitute_of: int | None


@dataclass
class DayLog:
    """하루 판단 기록 — 시험·1d 저장이 읽는다."""

    day: str
    cutoff: str
    sells: dict[int, list[str]] = field(default_factory=dict)  # 오늘 낸 매도와 사유(정지로 미룬 것은 ["미룬 매도"])
    buys: dict[int, float] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)  # 운영 결정 원장 모양(buy·skip·blocked)
    cooldown: frozenset[int] = frozenset()
    day_return_pct: float | None = None
    random: dict[str, Any] = field(default_factory=dict)  # 무작위 대체 기록(운영 `기록["random"]`)


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def dicts(self) -> list[dict[str, Any]]:
        return []


#: `jobs/monitor_targets.action_factors` 의 국내 읽기 머리 — 수정주가 창에는 기업행위 점프가 없다고 보고 빈 답을 준다
_ACTION_SQL_HEAD = "SELECT close, change_pct, date FROM prices WHERE stock_id = ?"


class _ViewClient:
    """운영 `jobs/paper` 함수가 DB 대신 읽는 창 — cutoff 로 자른 `PriceView` 만 답한다(미래를 볼 것이 없다)."""

    def __init__(self, view: bt.PriceView) -> None:
        from batch.jobs import paper as jp

        self.view, self._last, self._peak = view, jp.LAST_CLOSE_SQL, jp.PEAK_SQL

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> _Rows:
        if sql == self._last:
            sid, upto = int(params[0]), str(params[1])
            앞 = [(c, d) for d, c in self.view.closes(sid) if d <= upto and c > 0]
            return _Rows([앞[-1]] if 앞 else [])
        if sql == self._peak:
            sid, 부터, 까지 = int(params[0]), str(params[1]), str(params[2])
            값 = [c for d, c in self.view.closes(sid) if 부터 <= d <= 까지 and c > 0]
            return _Rows([(max(값) if 값 else None,)])
        if sql.startswith(_ACTION_SQL_HEAD):
            return _Rows([])
        raise LookupError(f"핵심 H 백테스트 창이 모르는 읽기 — 운영 함수가 바뀌었다: {sql[:80]}")


def _close_on(view: bt.PriceView, sid: int, day: str) -> float | None:
    """그날 종가(없으면 None — 그날 거래되지 않았다)."""
    rows = view.closes(sid)
    k = bisect.bisect_left(rows, (day, -math.inf))
    return rows[k][1] if k < len(rows) and rows[k][0] == day else None


def _close_upto(view: bt.PriceView, sid: int, day: str) -> float | None:
    rows = view.closes(sid)
    k = bisect.bisect_right(rows, (day, math.inf))
    return rows[k - 1][1] if k else None


def _substitute_of(order: dict[str, Any]) -> int | None:
    """운영 `held_substitutes` 가 주문 근거에서 읽는 `substitute_of`."""
    for c in (order.get("rationale") or {}).get("signal", {}).get("criteria") or []:
        if isinstance(c, dict) and c.get("substitute_of") is not None:
            return int(c["substitute_of"])
    return None


class CoreStrategy:
    """변형 하나의 장부형 판단 함수(`bt.WeightsAt`). 시장 판단기(`CoreMarket`)는 변형마다 따로 —
    cutoff 가 순차여야 한다.

    `sessions` = 거래일(엔진 `dates`, 워밍업 포함 가능) — 판 뒤 쉼을 거래일로 센다. 결정은 **날마다**(리밸런스 날 =
    모든 거래일)를 전제한다: 산 날 원가·더 산 몫의 평균은 "직전 결정일 = 지금 cutoff" 로 맞춘다
    """

    def __init__(
        self, variant: str, market: cb.CoreMarket, *, sessions: list[str], costs: bt.Costs | None = None,
        max_sector_pct: float = report_picks.DEFAULT_MAX_SECTOR_PCT,
    ) -> None:  # fmt: skip
        from batch.jobs import paper as jp

        if variant not in VARIANTS:
            raise ValueError(f"모르는 변형 {variant} — {sorted(VARIANTS)}")
        self.variant, self.v = variant, VARIANTS[variant]
        if self.v.sell_rule in jp.PREBUY_CHECK_RULES:
            # 사면 바로 팔 후보(재무악화·감성·점검)는 백테스트에 자료가 없다 —
            # 핵심 변형은 core·trend 라 운영도 보지 않는다(`prebuy_sell_flags`)
            raise NotImplementedError(f"매도 규칙 {self.v.sell_rule} 은 매수 전 매도 점검이 필요하다")
        self.market = market
        self.sessions = sorted(sessions)
        c = costs if costs is not None else bt.Costs.for_country(market.country)
        # 운영 `make_fill`: 지정가 매수는 슬리피지 없이 수수료만, 시장가(시가 일괄)는 슬리피지도 원가에
        self.unit_factor = 1 + c.commission_pct / 100 + (c.slippage_pct / 100 if self.v.entry == "open" else 0.0)
        self.settings = {"max_weight_per_stock": float(market.max_weight_per_stock),
                         "max_weight_per_sector": float(max_sector_pct), "min_order_amount": 0.0}  # fmt: skip
        self._cache = {
            int(r["stock_id"]): {
                "id": int(r["stock_id"]), "ticker": str(r["ticker"]), "name": str(r.get("name") or r["ticker"]),
                "sector": r.get("sector") or None, "country": market.country, "currency": r.get("currency") or "KRW",
                "asset_type": r.get("asset_type") or "stock",
            }
            for r in market.universe
        }  # fmt: skip
        self.lots: dict[int, Lot] = {}
        self.sold: dict[int, str] = {}  # 종목 → 마지막 매도 체결일(엔진이 실제로 판 결정일)
        self._selling: dict[int, str] = {}  # 낸 매도 가운데 아직 안 팔린 것(정지) → 처음 낸 날
        self._bought: list[_Bought] = []
        self._prev: tuple[str, str, dict[int, float]] | None = None  # (결정일, cutoff, cutoff 비중)
        self.log: list[DayLog] = []

    # ── 장부 맞추기 ──────────────────────────────────────────────────────────

    def _reconcile(self, view: bt.PriceView, w: dict[int, float]) -> None:
        held = set(view.holding) if view.holding is not None else set(w)
        직전 = self._prev[0] if self._prev else None
        for sid in list(self._selling):
            if sid not in held:
                # 어제 다시 낸 매도가 찼다 — 판 날은 그 결정일(운영 체결일)
                self.sold[sid] = 직전 or self._selling[sid]
                self.lots.pop(sid, None)
                del self._selling[sid]
        for sid in list(self.lots):
            if sid not in held:
                del self.lots[sid]  # 엔진이 판 것(상장폐지 — 마지막 종가)
        for b in self._bought:
            px = _close_on(view, b.stock_id, b.day)
            if px is None or px <= 0 or b.stock_id not in held:
                continue  # 엔진이 못 산 것(그날 체결가 없음·정지·상장폐지)
            단가 = px * self.unit_factor
            lot = self.lots.get(b.stock_id)
            if lot is None:
                self.lots[b.stock_id] = Lot(b.stock_id, b.day, b.horizon, 단가, b.substitute_of, b.day)
                continue
            # 더 산 몫 — 주식 수 비율은 그날 종가로 잰 비중(cutoff = 산 날)에서: 옛 몫 (h − w)/h, 새 몫 w/h
            h = w.get(b.stock_id, 0.0)
            옛 = max(h - b.weight, 0.0) / h if h > 0 else 0.0
            lot.unit_cost = 옛 * lot.unit_cost + (1 - 옛) * 단가
            lot.last_buy = b.day
            if b.substitute_of is not None:
                lot.substitute_of = b.substitute_of
        self._bought = []

    def _cooldown_start(self, cutoff: str) -> str:
        """운영 `쉼_시작` — 리포트 거래일(= cutoff)에서 `REBUY_COOLDOWN_SESSIONS − 1` 거래일 앞."""
        k = bisect.bisect_right(self.sessions, cutoff) - 1
        if k < 0:
            return cutoff
        return self.sessions[max(k - (pp.REBUY_COOLDOWN_SESSIONS - 1), 0)]

    def _day_return(self, view: bt.PriceView) -> float | None:
        """사전 위험 점검의 하루 수익(%) — 직전 cutoff 비중 × 그 뒤 종가 변화. 첫날은 None(운영 첫 기록과 같다)."""
        if self._prev is None:
            return None
        _d, 앞cut, 앞w = self._prev
        r = 0.0
        for sid, x in 앞w.items():
            a, b = _close_upto(view, sid, 앞cut), view.last_close(sid)
            if a and b:
                r += x * (b / a - 1)
        return r * 100

    # ── 매도 ─────────────────────────────────────────────────────────────────

    def _sell_codes(
        self, client: _ViewClient, view: bt.PriceView, lot: Lot, h: float, today: date,
    ) -> set[str]:  # fmt: skip
        """운영 `sell_decisions` 한 종목 — 같은 `evaluate`, 추세 계좌는 같은 `_추적손절`(고점은 창에서)."""
        from batch.jobs import paper as jp

        px = view.last_close(lot.stock_id)
        if px is None or px <= 0 or h <= 0:
            return set()
        수량 = h * NOTIONAL_EQUITY / px
        행 = {"quantity": 수량, "market_value": px * 수량, "cost": lot.unit_cost * 수량,
             "first_buy_date": lot.first_buy}  # fmt: skip
        hi = sf.HoldingInput(
            stock_id=lot.stock_id, name=self._cache.get(lot.stock_id, {}).get("name", str(lot.stock_id)),
            horizon=lot.horizon, first_buy_date=lot.first_buy, currency="KRW", cost=행["cost"],
            market_value=행["market_value"], price_date=view.last_date(lot.stock_id), horizon_since=lot.first_buy,
        )  # fmt: skip
        코드들 = {f.reason_code for f in sf.evaluate(hi, today, self.market.targets)}
        if self.v.sell_rule == "trend" and jp._추적손절(client, hi, 행, self.market.targets, today):
            코드들.add(pp.TRAIL_CODE)
        return 코드들

    # ── 하루 ─────────────────────────────────────────────────────────────────

    def __call__(self, day: str, view: bt.PriceView) -> bt.LedgerOrders:
        if not view.stocks():
            # 엔진 첫날(워밍업 없음)의 빈 창 — 판단기를 부르지 않는다(다음 날 같은 cutoff 로 다시 부르게 된다)
            return bt.LedgerOrders()
        w = {int(s): float(x) for s, x in (view.weights or {}).items() if x > 0}
        self._reconcile(view, w)
        dv = self.market.at(view, day)
        client = _ViewClient(view)
        today = date.fromisoformat(day[:10])
        log = DayLog(day=day, cutoff=view.cutoff, day_return_pct=self._day_return(view))

        # ① 매도 — 계좌 규칙 전의 판정에서 손절을 따로 모은다(운영 검증 [필수] 4)
        매도: dict[int, list[str]] = {}
        손절: set[int] = set()
        for sid, lot in sorted(self.lots.items()):
            코드들 = self._sell_codes(client, view, lot, w.get(sid, 0.0), today)
            if "손절" in 코드들:
                손절.add(sid)
            팔것 = pp.sells_for(self.v.sell_rule, {sid: 코드들})
            if 팔것:
                매도[sid] = 팔것[sid]
        for sid in self._selling:
            매도.setdefault(sid, ["미룬 매도"])  # 정지로 못 판 매도 — 운영은 열린 주문이 다음 장으로 밀린다
        log.sells = 매도

        # ② 판 뒤 쉼 — 운영 `최근판`: 쉼 시작 뒤 체결된 매도 + 오늘 매도
        쉼 = self._cooldown_start(view.cutoff)
        최근판 = {sid for sid, d in self.sold.items() if d >= 쉼} | set(매도)
        log.cooldown = frozenset(최근판)

        # ③ 매수 — 운영 `buy_orders` 그대로(현금은 오늘 판 돈을 넣지 않은 cutoff 현금)
        현금 = max(1.0 - sum(w.values()), 0.0)
        buys, bought = self._buys(client, view, dv, w, 현금, 손절, 최근판, today, log)

        for sid in 매도:
            self._selling.setdefault(sid, day)
        self._bought = bought
        self._prev = (day, view.cutoff, w)
        log.buys = buys
        self.log.append(log)
        return bt.LedgerOrders(sells=frozenset(매도), buys=buys)

    def _rows(self, dv: cb.DayView) -> tuple[list[report_picks.SignalRow], list[report_picks.SignalRow] | None]:
        """(신호 행, 미리 고른 행) — H 는 지주사 합의(`pick_rows`), 나머지는 `buy_orders` 가 점수 상위를 고른다."""
        if self.v.selection == "weighted":
            return dv.rows, list(dv.pick_rows)
        return dv.rows, None

    def _buys(
        self, client: _ViewClient, view: bt.PriceView, dv: cb.DayView, w: dict[int, float], 현금: float,
        손절: set[int], 최근판: set[int], today: date, log: DayLog,
    ) -> tuple[dict[int, float], list[_Bought]]:  # fmt: skip
        from batch.jobs import paper as jp

        rows, 미리 = self._rows(dv)
        if not rows:
            return {}, []
        N = NOTIONAL_EQUITY
        v = pp.Valuation(
            invested_krw=N, cash_krw=현금 * N, holdings_krw=sum(w.values()) * N, equity_krw=N, pnl_krw=0.0, pnl_pct=0.0,
            rows=[{"stock_id": sid, "value_krw": x * N} for sid, x in sorted(w.items())],
        )  # fmt: skip
        바꾸기 = None
        if self.v.selection == "random":
            pool = [int(r["stock_id"]) for r in self.market._pool(view.cutoff)
                    if (r.get("asset_type") or "stock") == "stock"]  # fmt: skip
            # 운영 `held_substitutes` — 지금 든 대체 종목, 나중에 산 것이 이긴다
            든대체 = {lot.substitute_of: sid for sid, lot in sorted(self.lots.items(), key=lambda kv: kv[1].last_buy)
                      if lot.substitute_of is not None and w.get(sid, 0.0) > 0}  # fmt: skip
            바꾸기 = partial(jp.random_substitute, client, account=self.v.account, market=self.market.country,
                            pool=pool, cooldown=최근판, stock_cache=self._cache, today=today, 기록=log.random,
                            held=든대체)  # fmt: skip
        주문, b기록 = jp.buy_orders(
            client, market=self.market.country, account=self.v.account, account_fills=[], valuation=v, open_buys=[],
            signal_rows=rows, settings=self.settings, price_series={}, series_unknown={}, fx_rate=None,
            stop_ids=손절, sold_recently=최근판, stock_cache=self._cache, top_n=self.v.top_n, entry=self.v.entry,
            substitute=바꾸기, preselected=미리, preselected_reason="지주사 합의",
        )  # fmt: skip
        # ④ 사전 위험 점검 — 운영 `run_step` 과 같은 후보·같은 문맥(보유는 오늘 매도 전, 업종은 후보까지)
        후보 = [rg.Candidate(o["stock_id"], "buy", o["cost_krw"], o["ref_date"], i, o["cash_krw"])
                for i, o in enumerate(주문)]  # fmt: skip
        sector_of = {sid: self._cache.get(sid, {}).get("sector") for sid in {*w, *(c.stock_id for c in 후보)}}
        문맥 = rg.Context(
            equity_krw=N, day_return_pct=log.day_return_pct, cash_available_krw=현금 * N,
            holding_krw={sid: x * N for sid, x in w.items()}, sector_of=sector_of,
            max_stock_pct=self.settings["max_weight_per_stock"], max_sector_pct=self.settings["max_weight_per_sector"],
            today=today.isoformat(),
        )  # fmt: skip
        통과, 막힘 = rg.gate(후보, 문맥)
        주문 = [주문[c.key] for c in 통과]
        낸 = {o["stock_id"] for o in 주문}
        막힌까닭 = {c.stock_id: "위험 점검: " + "; ".join(t for _c, t in 까닭) for c, 까닭 in 막힘}
        for d in b기록.pop("_decisions", []):
            if d["kind"] == "buy" and d["stock_id"] not in 낸:
                d = {**d, "kind": "blocked", "reason": 막힌까닭.get(d["stock_id"], "위험 점검")}
            log.decisions.append(d)
        buys: dict[int, float] = {}
        첫주문: dict[int, dict[str, Any]] = {}
        for o in 주문:
            단가 = o["limit_price"] if o.get("limit_price") is not None else o["ref_price"]
            buys[o["stock_id"]] = buys.get(o["stock_id"], 0.0) + 단가 * o["quantity"] / N
            첫주문.setdefault(o["stock_id"], o)
        bought = [_Bought(sid, log.day, x, 첫주문[sid].get("horizon"), _substitute_of(첫주문[sid]))
                  for sid, x in sorted(buys.items())]  # fmt: skip
        return buys, bought


def make_core_strategy(
    variant: str, market: cb.CoreMarket, *, sessions: list[str], costs: bt.Costs | None = None,
    max_sector_pct: float = report_picks.DEFAULT_MAX_SECTOR_PCT,
) -> CoreStrategy:  # fmt: skip
    """변형 하나의 판단 함수 — `bt.simulate(prices, dates, dates, make_core_strategy(…), costs)` 로 돌린다.

    `costs` 는 엔진에 넘기는 것과 같은 값을 준다(원가의 수수료·슬리피지). 변형마다 새 `CoreMarket` 을 준다"""
    return CoreStrategy(variant, market, sessions=sessions, costs=costs, max_sector_pct=max_sector_pct)

