"""전략 깔때기 ③ 일치 시험 — 리플레이(`core_strategy`)와 운영 가상모의투자(`jobs/paper.run_step`)가 같은 입력에 같은 결정을
내는가 (docs/funnel.md 4장 ③, docs/infra.md 25.1910).

같은 합성 자료(실제 국내 거래일, 시가 = 종가)를 두 길로 날마다 돈다.

- 리플레이: `make_core_strategy(variant, CoreMarket(…))` 를 `bt.simulate` 안에서 — 날마다 본 하루 재료(`DayView.rows`)·
  판단 직전 비중·전략 장부(`lots`)·엔진의 체결 뒤 비중(`Rebalance`)을 남긴다
- 운영: 메모리 SQLite(마이그레이션 전부)에 그 계좌 하나만 두고 `run_step(trade_date = 리플레이 cutoff)` — 신호 행은 리플레이가
  그날 본 것 그대로, 시세는 그날 판단 **전**까지만(cutoff 종가까지) 넣고, 입금은 명목 자본(`NOTIONAL_EQUITY` 원)

견주는 단 — 정직하게 견줄 수 있는 가장 깊은 단까지:
  L0 판단 직전 장부(보유·첫 매수일·기간·주당 원가) · L1 그날 파는 종목과 사유 코드 · L2 결정 원장(buy·skip·blocked 의 사유·held)
  · L3 매수 비중(회차 금액 합 ÷ 평가 총액)
L4(체결·현금)는 견주지 않는다 — 리플레이는 t 종가 전량, 운영 B·G 는 3회 분할 지정가다(docs/backtest.md 10장 "운영과 다른 것").
그 차이를 지우려고 운영 장부에 날마다 **엔진의 체결을 그대로 베낀다**(허용 ①) — 그래야 다음 날 두 쪽이 같은 보유에서 판단한다.

비용 두 판: `zero`(수수료·세금 0) 는 허용 없이 1e-6 까지 같아야 하고, `fees`(운영 설정 수수료 0.015%·시행일 거래세) 는
엔진의 비용 장부(허용 ⑨)가 낳는 보유 비중 흔들림만큼만 갈려도 된다. 허용 차이는 모두 이름 붙인 상수로 둔다 — 목록 밖 차이는
결함이다(`xfail(strict=True)` 로 적고 보고한다).
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import date, datetime
from functools import cache
from typing import Any

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import core_strategy as cs
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, 설정

# ── 이름 붙인 허용 차이 (docs/backtest.md 10장 조각 1b·1c "운영과 다른 것", 3장 거래비용) ─────────────────────

#: ① t 종가 전량 체결 — backtest.md 10장 조각 1c "체결은 t 종가 전량(운영 3회 분할 지정가 …)". 운영 체결 판정(`fill_orders`)은
#: 견주지 않는다(L4). 운영이 낸 주문은 판단 뒤 만료시키고, 엔진 체결(그날 종가·수량 = 엔진 비중 × 자본 × 명목)을 베껴 넣는다
ALLOW_T_CLOSE_FULL_FILLS = "체결은 t 종가 전량 — 운영 장부에 엔진 체결을 베낀다"
#: ② 상관 축소 없음 — 같은 곳 "상관 축소 없음(`price_series` 비움)". 운영 `run_step` 에도 `price_series={}` 를 준다
ALLOW_NO_CORRELATION_SHRINK: dict = {}
#: ③ 명목 자본 — 같은 곳 "`NOTIONAL_EQUITY` = 1e+12 원이라 정수 주·최소 주문 금액이 묶이지 않는다". 운영 입금도 1e12 원.
#: 남는 것은 정수 주 내림(1e-7 자리) — 비중을 이 오차로 견준다
ALLOW_NOTIONAL_EQUITY = 1e-6
#: ④ 기업행위 멈춤 없음 — 같은 곳 "기업행위 멈춤 없음(수정주가)". 운영 `frozen_ids` 를 빈 집합으로(리플레이 `_ViewClient` 는
#: 기업행위 읽기에 빈 답 = 멈추지 않음)
ALLOW_NO_CORPORATE_ACTION_FREEZE = "frozen_ids → set()"
#: ⑤ 정지 매도의 20거래일 청산 없음 — 같은 곳. 합성 자료에 정지·상장폐지를 넣지 않아 이 갈래를 지나지 않는다(지나지 않음)
ALLOW_NO_HALT_LIQUIDATION = "자료에 정지·상장폐지 없음"
#: ⑥ 하루 손실 관문 수익률 — 같은 곳 "직전 cutoff 비중 × 종가 변화(비용 뺌 없음)". 운영은 시간가중 지수(그날 체결 비용이 든다).
#: 이 창에서는 하루 수익이 −1.03% 아래로 간 날이 없어(한도 −3%) 관문 결정이 갈릴 자리가 없었다 — 두 수익률의 차이 상한만
#: 잰다(%p, 2026-10-28 실측 B 0.022·G 0.021. 비용 0 판은 운영 기록의 소수 넷째 자리 반올림 5e-5 뿐)
ALLOW_GATE_RETURN_PCTP = 0.05
#: ⑦ 더 산 몫의 원가 평균 — 같은 곳 "원가 평균은 줄이기 전 비중으로 잰다"(리플레이 `_reconcile` 은 산 날 종가 비중의 주식 수
#: 비율로 근사, 운영 `pp.positions` 는 주식 수 가중 평균). L0 에서 더 산 적 있는 로트의 주당 원가가 이 상대 오차 안이어야 한다.
#: 한 번만 산 로트는 1e-9. 2026-10-28 실측 최대 B 5.5e-5·G 3.1e-5(비용 0 판 3e-8 — 남는 것은 ⑨ 의 비중 흔들림이 근사에 든 몫).
#: 이 창의 매도는 이 차이로 갈리지 않았다(L1 허용 없음)
ALLOW_COST_AVERAGING_REL = 1e-4
#: ⑧ 하루 재료의 근사(팩터 점수 주 1회·감성 없음·수정종가·`gov_covered=False` — backtest.md 10장 조각 1b) — 이 시험은 리플레이가
#: 본 신호 행을 운영에 **그대로** 먹이므로 두 쪽 사이의 차이를 만들지 않는다(재료를 만드는 쪽의 근사 — 견줄 거리 밖)
ALLOW_DAYVIEW_APPROXIMATIONS = "같은 신호 행을 양쪽에 먹임"
#: ⑨ 엔진의 비용 장부 — backtest.md 3장 "비용은 리밸런스마다 바뀐 비중의 절댓값 합(회전율)에 매긴다"(`bt.Costs.rebalance_cost`,
#: `simulate` 의 `equity = drifted_total × (1 − cost)`). 엔진은 비용을 **자본 전체에 비례로** 빼 든 종목도 함께 줄이고, 운영은
#: 체결의 현금에서만 뺀다 → 거래(특히 매도세 0.18%)가 있을 때마다 운영의 든 종목 비중이 엔진보다 조금씩 커진다. 그래서
#: (가) 보유 비중 차이는 이 상한 안, (나) 더 사기(목표 비중까지 채우기) 금액의 차이는 그 종목 보유 비중 차이로 다 설명되어야 하고,
#: (다) 목표까지 남은 몫이 그 흔들림보다 작은 날의 "아주 작은 더 사기 ↔ 최소 주문 단위 미만·종목 비중 상한 도달" 뒤집힘만 견딘다.
#: 10장 "운영과 다른 것" 목록에는 없다(3장에만) — 보고에 적는다. `zero` 판에서는 허용 ③ 안이어야 한다.
#: 상한은 2026-10-28 실측 최대(B 9.6e-5·G 1.25e-4, 150 거래일)의 두 배
ALLOW_ENGINE_COST_BOOKING = 2.5e-4
#: ⑨(다) 에서 뒤집혀도 되는 결정 사유 — 든 종목을 목표 비중까지 채우는 자리의 세 결과
_채우기_사유 = frozenset({"2부 배분", "최소 주문 단위 미만", "종목 비중 상한 도달"})

# ── 합성 자료 — 실제 국내 거래일(운영은 거래소 달력으로 쉼·다음 장을 센다) ─────────────────────────────────

#: 워밍업 260 거래일 — 모멘텀·위험 지표가 1년 창을 채워야 신호가 난다(130 이면 모의 100일 내내 신호 0). 모의 150 거래일·30종목:
#: B 는 매수 73·매도 16(목표도달·손절), G 는 매수 66·매도 11(추적손절·기간초과·손절)
W, S, N = 260, 150, 30
_달력 = cal.exchange_calendar("KR").sessions_in_range("2023-01-02", "2024-12-30")
날들 = [d.date().isoformat() for d in _달력][: W + S]
모의 = 날들[W:]
NOTIONAL = cs.NOTIONAL_EQUITY
#: 비용 두 판 — 운영 설정 `fees`(매수·매도 편도), 엔진 `bt.Costs`(슬리피지 0: 지정가 매수는 운영도 슬리피지 없음, 베낀 매도도 0)
비용판 = {
    "fees": ({"kr_buy_pct": 0.015, "kr_sell_pct": 0.015},
             dc_replace(bt.Costs.for_country("KR", {"kr_buy_pct": 0.015, "kr_sell_pct": 0.015}, {}), slippage_pct=0.0)),
    "zero": ({"kr_buy_pct": 0.0, "kr_sell_pct": 0.0}, bt.Costs.zero()),
}  # fmt: skip
#: 베낀 체결의 주문 회차 — 운영이 같은 결정일에 낸 회차(매수 1~3·매도 0)와 유일 제약이 겹치지 않게
_베낌_회차 = {"buy": 91, "sell": 90}


@cache
def _자료() -> tuple:
    """`tests/test_core_strategy_1532._자료` 와 같은 꼴(난수 11) — 날짜만 실제 국내 거래일. 정지·상장폐지는 넣지 않는다(허용 ⑤)."""
    rng = random.Random(11)
    universe, prices, turn, snaps = [], {}, {}, {}
    for i in range(1, N + 1):
        universe.append({"stock_id": i, "ticker": f"{i:06d}", "name": f"종목{i}", "market": "KOSPI" if i % 3 else "KOSDAQ",
                         "sector": ["반도체", "은행", "화학", "유통"][i % 4], "currency": "KRW", "listed_shares": 10_000_000})  # fmt: skip
        p, drift, vol = 10_000.0, rng.uniform(-0.0005, 0.0015), rng.uniform(0.01, 0.03)
        prices[i], turn[i] = {}, {}
        for k, d in enumerate(날들):
            p *= math.exp(drift + vol * rng.gauss(0, 1))
            prices[i][d] = round(p, 2)
            turn[i][d] = 1e9 * rng.uniform(0.8, 1.2) * (1.7 if (k + 7 * i) // 30 % 2 == 0 else 1.0)
        rev, g, 마진 = rng.uniform(1e11, 1e12), rng.uniform(-0.1, 0.4), rng.uniform(0.05, 0.15)
        snaps[i] = []
        for fy in range(2017, 2023):
            rev *= 1 + g + rng.uniform(-0.05, 0.05)
            oi = rev * 마진
            snaps[i].append({"as_of_date": f"{fy + 1}-03-15", "fiscal_year": fy, "receipt_no": f"{fy}{i:04d}",
                             "consolidated": True, "values": {
                                 "revenue": rev, "operating_income": oi, "net_income": oi * 0.7,
                                 "total_equity": rev * rng.uniform(0.3, 1.5), "total_assets": rev * 2,
                                 "total_liabilities": rev * 0.8, "currency": "KRW"}})  # fmt: skip
    idx: dict[str, dict[str, float]] = {"KOSPI": {}, "KOSDAQ": {}}
    a, b = 2500.0, 800.0
    for d in 날들:
        a *= math.exp(0.0003 + 0.01 * rng.gauss(0, 1))
        b *= math.exp(0.0002 + 0.012 * rng.gauss(0, 1))
        idx["KOSPI"][d], idx["KOSDAQ"][d] = a, b
    return universe, prices, turn, snaps, idx


# ── 리플레이 ─────────────────────────────────────────────────────────────────


@dataclass
class 리플레이:
    s: cs.CoreStrategy
    r: bt.BacktestResult
    재료: dict[str, tuple[str, list[report_picks.SignalRow]]]  # 결정일 → (cutoff, 신호 행)
    주문: dict[str, bt.LedgerOrders]
    기간: dict[str, dict[int, str | None]]  # 결정일 → 그날 산 종목의 첫 주문 기간(전략 `_Bought.horizon`)
    비중: dict[str, dict[int, float]]  # 결정일 → 판단 직전 비중(cutoff 종가)
    장부: dict[str, dict[int, tuple[str, str | None, float, bool]]]  # 결정일 → 종목 → (첫 매수일, 기간, 주당 원가, 더 삼)


@cache
def _리플레이(variant: str, 판: str) -> 리플레이:
    universe, prices, turn, snaps, idx = _자료()
    fees, costs = 비용판[판]
    s = cs.make_core_strategy(variant, cb.CoreMarket(universe, snaps, idx), sessions=날들, costs=costs,
                              buy_fee_pct=job._수수료("KR", fees)[0])  # fmt: skip
    재료: dict[str, tuple[str, list]] = {}
    원래 = s.market.at

    def 엿봄(view: bt.PriceView, day: str | None = None) -> cb.DayView:
        dv = 원래(view, day)
        재료[dv.day] = (dv.cutoff, list(dv.rows))
        return dv

    s.market.at = 엿봄  # type: ignore[method-assign]
    주문: dict[str, bt.LedgerOrders] = {}
    기간: dict[str, dict[int, str | None]] = {}
    비중: dict[str, dict[int, float]] = {}
    장부: dict[str, dict[int, tuple]] = {}

    def 판단(day: str, view: bt.PriceView) -> bt.LedgerOrders:
        o = s(day, view)  # 첫머리 `_reconcile` 뒤 장부 = 판단 직전 장부
        주문[day] = o
        기간[day] = {b.stock_id: b.horizon for b in s._bought}
        비중[day] = {int(k): float(x) for k, x in (view.weights or {}).items() if x > 0}
        장부[day] = {sid: (lot.first_buy, lot.horizon, lot.unit_cost, lot.last_buy != lot.first_buy)
                    for sid, lot in s.lots.items()}  # fmt: skip
        return o

    r = bt.simulate(prices, 모의, 모의, 판단, costs, turnover=turn, has_delisted=True)
    return 리플레이(s, r, 재료, 주문, 기간, 비중, 장부)


# ── 운영 ─────────────────────────────────────────────────────────────────────


@dataclass
class 운영하루:
    day: str
    cutoff: str
    sells: dict[int, list[str]] = field(default_factory=dict)
    decisions: list[tuple] = field(default_factory=list)  # (종목, kind, 사유, held)
    buys: dict[int, float] = field(default_factory=dict)  # 평가 총액 대비 회차 금액 합
    weights: dict[int, float] = field(default_factory=dict)  # 판단 때 평가 비중
    lots: dict[int, tuple[str, str | None, float]] = field(default_factory=dict)  # (첫 매수일, 기간, 주당 원가)
    gate_pct: float | None = None  # 관문이 본 하루 수익(%)
    record: dict[str, Any] = field(default_factory=dict)


def _db(aid: str, fees: dict) -> Client:
    c = Client(accounts=(aid,))
    c.conn.execute("DELETE FROM stocks")
    for u in _자료()[0]:
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source, fetched_at,"
            " asset_type) VALUES (?, ?, ?, 'KR', ?, ?, 'KRW', 'active', 't', 't', 'stock')",
            [u["stock_id"], u["ticker"], u["market"], u["name"], u["sector"]],
        )
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('fees', ?, 't')", [json.dumps(fees)])
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES (?, ?, 'deposit', '2000-01-01T00:00:00+00:00', 'parity')", [날들[0], int(NOTIONAL)])  # fmt: skip
    return c


def _시세넣기(c: Client, day: str) -> None:
    """그날 종가(시가 = 고가 = 저가 = 종가) — 그날 판단이 끝난 뒤에 넣는다(cutoff 뒤 가격을 보지 않게)."""
    for sid, by in _자료()[1].items():
        if day in by:
            px = by[day]
            c.conn.execute(
                "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [sid, day, px, px, px, px])  # fmt: skip


def _베끼기(c: Client, aid: str, rp: 리플레이, 판: str, k: int, cutoff: str, rows: list) -> None:
    """허용 ① — 엔진이 그날(모의[k]) 판 것·산 것을 운영 장부에 그날 종가 체결로 적는다. 비용은 엔진과 같은 수수료·세율을
    운영 `pp.make_fill` 로(지정가 — 슬리피지 없음) — 원가 평균은 운영 `pp.positions` 가 낸다(허용 ⑦ 을 L0 에서 잰다)."""
    day = 모의[k]
    reb = rp.r.rebalances[k]
    assert reb.date == day
    자본 = dict(rp.r.curve)[day]
    prices = _자료()[1]
    fees, costs = 비용판[판]
    매수, 매도 = job._수수료("KR", fees)
    비용 = pp.Costs(buy_fee_pct=매수, sell_fee_pct=매도, sell_tax_pct=costs.tax_at(day), slippage_pct=0.0)
    든 = pp.positions([f for f in job._fills(c)[0] if f.account_id == aid])
    점수: dict[int, float | None] = {}
    for r in rows:
        점수.setdefault(r.stock_id, r.total_score)
    체결: list[tuple[str, int, int, str | None]] = []  # (side, 종목, 수량, 기간)
    for sid, p in sorted(든.items()):
        if reb.weights.get(sid, 0.0) <= 0:
            체결.append(("sell", sid, p.quantity, p.horizon))
    산 = rp.주문[day].buys
    for sid, w in sorted(reb.weights.items()):
        if sid not in 산:
            assert sid in 든, f"{day}: 엔진이 운영 장부 모르는 {sid} 를 든다"
            continue
        더 = round(w * 자본 * NOTIONAL / prices[sid][day]) - (든[sid].quantity if sid in 든 else 0)
        if 더 > 0:
            체결.append(("buy", sid, 더, rp.기간[day].get(sid)))
    for side, sid, q, h in 체결:
        px = prices[sid][day]
        cur = c.conn.execute(
            "INSERT INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
            " decided_on, fill_session, fill_basis, tranche, reason, horizon, score_at_order, score_as_of, rule_version,"
            " status, closed_on, close_note, created_at) VALUES (?, 'KR', ?, ?, 'limit', ?, ?, 'KRW', ?, ?, 'close', ?,"
            " '엔진 체결 베낌', ?, ?, ?, 1, 'filled', ?, ?, ?)",
            [aid, sid, side, px, q, cutoff, day, _베낌_회차[side], h, 점수.get(sid), cutoff, day,
             ALLOW_T_CLOSE_FULL_FILLS, f"{day}T09:00:00+00:00"],
        )  # fmt: skip
        o = pp.Order(id=int(cur.lastrowid), account_id=aid, stock_id=sid, side=side, order_type="limit", limit_price=px,
                     quantity=q, currency="KRW", fill_session=day, horizon=h, score_at_order=점수.get(sid),
                     score_as_of=cutoff)  # fmt: skip
        f = pp.make_fill(o, px, day, 1.0, 비용)
        c.conn.execute(job.INSERT_FILL_SQL, [
            f.order_id, aid, sid, side, day, px, q, "KRW", 1.0, None, f.fee, f.tax, f.slippage, f.cash_krw, h,
            f.score_at_trade, f.snapshot_as_of, f"{day}T09:00:00+00:00", px, None, 0.0 if side == "sell" else None,
        ])  # fmt: skip


@cache
def _운영(variant: str, 판: str) -> dict[str, 운영하루]:
    """운영 `run_step` 을 리플레이의 결정일마다 — 그날 cutoff 를 trade_date 로, 그날을 국내 오늘로."""
    rp = _리플레이(variant, 판)
    aid = cs.VARIANTS[variant].account
    c = _db(aid, 비용판[판][0])
    for d in 날들[:W]:
        _시세넣기(c, d)
    mp = pytest.MonkeyPatch()
    out: dict[str, 운영하루] = {}
    try:
        mp.setattr(job, "frozen_ids", lambda *_a, **_k: set())  # 허용 ④
        for k, day in enumerate(모의[:-1]):  # 엔진은 마지막 날 평가만 한다
            cutoff, rows = rp.재료[day]
            오늘 = date.fromisoformat(day)
            mp.setattr(cal, "local_today", lambda _m=None, _d=오늘: _d)
            h = 운영하루(day=day, cutoff=cutoff)
            든 = pp.positions([f for f in job._fills(c)[0] if f.account_id == aid])
            h.lots = {sid: (p.first_buy_date, p.horizon, p.cost / p.quantity) for sid, p in 든.items()}
            _줄, 기록 = job.run_step(
                c, market="KR", trade_date=cutoff, late=False, signal_rows=rows, settings=설정,
                price_series=dict(ALLOW_NO_CORRELATION_SHRINK), series_unknown={}, allocate=True,
                now=datetime.fromisoformat(f"{day}T00:30:00+00:00"),
            )  # fmt: skip
            h.record = 기록["accounts"][aid]
            for sid, 사유 in c.q("SELECT stock_id, reason FROM paper_orders WHERE account_id = ? AND decided_on = ?"
                                 " AND side = 'sell' AND tranche = 0", [aid, cutoff]):  # fmt: skip
                h.sells[sid] = 사유.split(",")
            for sid, kind, 사유, detail in c.q(
                "SELECT stock_id, kind, reason, detail_json FROM paper_decisions WHERE account_id = ? AND decided_on = ?"
                " AND kind IN ('buy', 'skip', 'blocked') ORDER BY id", [aid, cutoff]):  # fmt: skip
                h.decisions.append((sid, kind, 사유, (json.loads(detail) if detail else {}).get("held")))
            자본, 줄들 = c.q("SELECT equity_krw, positions_json FROM paper_daily WHERE account_id = ? AND as_of = ?",
                           [aid, day])[0]  # fmt: skip
            h.weights = {int(r["stock_id"]): float(r["value_krw"]) / 자본 for r in json.loads(줄들)}
            for sid, 단가, q in c.q("SELECT stock_id, COALESCE(limit_price, ref_price), quantity FROM paper_orders"
                                   " WHERE account_id = ? AND decided_on = ? AND side = 'buy' AND tranche < 90",
                                   [aid, cutoff]):  # fmt: skip
                h.buys[sid] = h.buys.get(sid, 0.0) + 단가 * q / 자본
            h.gate_pct = (h.record.get("gate_day") or {}).get("gate")
            out[day] = h
            # 허용 ① — 운영이 낸 주문은 체결 판정에 넘기지 않고 엔진 체결을 베낀다
            c.conn.execute("UPDATE paper_orders SET status = 'expired', close_note = ? WHERE status = 'open'",
                           [ALLOW_T_CLOSE_FULL_FILLS])  # fmt: skip
            _시세넣기(c, day)
            _베끼기(c, aid, rp, 판, k, cutoff, rows)
    finally:
        mp.undo()
    return out


# ── 견주기 ───────────────────────────────────────────────────────────────────


def _날별(rp: 리플레이) -> dict[str, cs.DayLog]:
    return {lg.day: lg for lg in rp.s.log}


def _흔들림(rp: 리플레이, h: 운영하루, sid: int) -> float:
    """그 종목 보유 비중의 두 쪽 차이(허용 ⑨)."""
    return abs(rp.비중[h.day].get(sid, 0.0) - h.weights.get(sid, 0.0))


def _L0(variant: str, 판: str) -> list[str]:
    """판단 직전 장부 — 보유 종목·첫 매수일·기간은 같고, 주당 원가는 한 번 산 로트 1e-9·더 산 로트 허용 ⑦ 안. 보유 비중은 허용 ⑨."""
    rp, live = _리플레이(variant, 판), _운영(variant, 판)
    틀림 = []
    for day, h in live.items():
        리플 = rp.장부[day]
        if set(리플) != set(h.lots):
            틀림.append(f"{day}: 보유 리플레이 {sorted(리플)} ≠ 운영 {sorted(h.lots)}")
            continue
        for sid, (첫, 기간, 원가, 더삼) in 리플.items():
            운첫, 운기간, 운원가 = h.lots[sid]
            한도 = ALLOW_COST_AVERAGING_REL if 더삼 else 1e-9
            if (첫, 기간) != (운첫, 운기간) or abs(원가 / 운원가 - 1) > 한도:
                틀림.append(f"{day} 종목 {sid}: 리플레이 {(첫, 기간, 원가)} ≠ 운영 {(운첫, 운기간, 운원가)}")
        한도 = ALLOW_ENGINE_COST_BOOKING if 판 == "fees" else ALLOW_NOTIONAL_EQUITY
        for sid in set(rp.비중[day]) | set(h.weights):
            if _흔들림(rp, h, sid) > 한도:
                틀림.append(f"{day} 종목 {sid}: 비중 리플레이 {rp.비중[day].get(sid)} ≠ 운영 {h.weights.get(sid)}")
    return 틀림


def _L1(variant: str, 판: str) -> list[str]:
    """그날 파는 종목과 사유 코드 — 허용 없음(⑦ 은 이 창에서 매도 문턱 가까이 간 더 산 로트가 없어 쓰지 않았다)."""
    rp, live = _리플레이(variant, 판), _운영(variant, 판)
    로그 = _날별(rp)
    return [f"{day}: 리플레이 {로그[day].sells} ≠ 운영 {h.sells}" for day, h in live.items() if 로그[day].sells != h.sells]


def _L2(variant: str, 판: str) -> list[str]:
    """결정 원장(buy·skip·blocked)의 (종목, kind, 사유, held). `fees` 판은 허용 ⑨(다) 의 채우기 뒤집힘만 견딘다."""
    rp, live = _리플레이(variant, 판), _운영(variant, 판)
    로그 = _날별(rp)
    틀림 = []
    for day, h in live.items():
        리플 = sorted((d["stock_id"], d["kind"], d["reason"], (d.get("detail") or {}).get("held"))
                    for d in 로그[day].decisions)  # fmt: skip
        운 = sorted(h.decisions)
        리플만, 운만 = [x for x in 리플 if x not in 운], [x for x in 운 if x not in 리플]
        if 판 == "fees":
            견딤 = {x[0] for x in 리플만 + 운만 if x[3] is True and x[2] in _채우기_사유
                  and max(로그[day].buys.get(x[0], 0.0), h.buys.get(x[0], 0.0))
                  <= _흔들림(rp, h, x[0]) + ALLOW_NOTIONAL_EQUITY}  # fmt: skip
            리플만 = [x for x in 리플만 if x[0] not in 견딤]
            운만 = [x for x in 운만 if x[0] not in 견딤]
        if 리플만 or 운만 or len(리플) != len(운):
            틀림.append(f"{day}: 리플레이만 {리플만} · 운영만 {운만} (리플레이 {len(리플)}줄, 운영 {len(운)}줄)")
    return 틀림


def _L3(variant: str, 판: str) -> list[str]:
    """매수 비중 — 종목마다 |리플레이 − 운영| ≤ 허용 ③ (+ `fees` 판은 그 종목 보유 비중 차이, 허용 ⑨(나))."""
    rp, live = _리플레이(variant, 판), _운영(variant, 판)
    로그 = _날별(rp)
    틀림 = []
    for day, h in live.items():
        리플 = 로그[day].buys
        for sid in set(리플) | set(h.buys):
            한도 = ALLOW_NOTIONAL_EQUITY + (_흔들림(rp, h, sid) if 판 == "fees" else 0.0)
            if abs(리플.get(sid, 0.0) - h.buys.get(sid, 0.0)) > 한도:
                틀림.append(f"{day} 종목 {sid}: 리플레이 {리플.get(sid)} ≠ 운영 {h.buys.get(sid)}")
    return 틀림


def _관문(variant: str, 판: str) -> float:
    """허용 ⑥ — 하루 손실 관문이 본 두 수익률의 가장 큰 차이(%p)."""
    rp, live = _리플레이(variant, 판), _운영(variant, 판)
    로그 = _날별(rp)
    return max(abs(로그[d].day_return_pct - h.gate_pct) for d, h in live.items()
               if h.gate_pct is not None and 로그[d].day_return_pct is not None)  # fmt: skip


# ── 시험 ─────────────────────────────────────────────────────────────────────


def test_합성_창이_견줄_거리를_지난다() -> None:
    """25.1910 (전략 깔때기 ③ 일치 시험 — 매도·결정 원장·매수 비중) — 시험이 헛돌지 않게: 창 안에서 두 계좌가 사고 팔고,
    매도 사유·skip 사유·더 사기(든 종목 목표까지 채우기)·하루 손실 관문 수익이 실제로 나온다. 실제 거래일 축이다."""
    assert all(cal.is_session("KR", date.fromisoformat(d)) for d in 날들)
    for variant, 사유들 in (("core_b", {"목표도달", "손절"}), ("core_g", {pp.TRAIL_CODE, "기간초과", "손절"})):
        rp = _리플레이(variant, "fees")
        assert sum(len(o.buys) for o in rp.주문.values()) >= 50 and sum(len(o.sells) for o in rp.주문.values()) >= 10
        assert {c for lg in rp.s.log for x in lg.sells.values() for c in x} == 사유들, variant
        결정 = {d["reason"] for lg in rp.s.log for d in lg.decisions}
        assert {"2부 배분", f"판 뒤 {pp.REBUY_COOLDOWN_SESSIONS}거래일 안", "종목 비중 상한 도달"} <= 결정, 결정
        assert any(더삼 for 장 in rp.장부.values() for *_x, 더삼 in 장.values())  # 더 산 로트(허용 ⑦·⑨ 자리)
    # 운영 쪽 설정이 리플레이 `CoreStrategy.settings` 와 같다(종목·섹터 상한, 최소 주문 금액)
    s = _리플레이("core_b", "fees").s
    assert (설정["max_weight_per_stock"], 설정["max_weight_per_sector"], 설정["min_order_amount"]) == (
        s.settings["max_weight_per_stock"], s.settings["max_weight_per_sector"], s.settings["min_order_amount"])


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant: str) -> None:
    """25.1910 (전략 깔때기 ③ 일치 시험 — 매도·결정 원장·매수 비중) — 수수료·세금 0 판. 엔진 체결을 베낀 운영 장부(허용 ①)에서
    `run_step` 이 날마다 낸 매도(L1)·결정 원장(L2)·매수 비중(L3)이 리플레이와 1e-6 까지 같다. 판단 직전 장부(L0)도 같다."""
    for 단 in (_L0, _L1, _L2, _L3):
        틀림 = 단(variant, "zero")
        assert not 틀림, (단.__name__, 틀림[:5])
    assert _관문(variant, "zero") <= 1e-4  # 운영 기록 `gate_day.gate` 의 소수 넷째 자리 반올림만


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_매도_종목과_사유가_같다(variant: str) -> None:
    """25.1910 (전략 깔때기 ③ 일치 시험 — 매도·결정 원장·매수 비중) — L1(운영 설정 수수료·시행일 거래세 판). 매도는 허용 없이
    같다 — 주당 원가(L0)는 허용 ⑦ 안, 보유 비중은 허용 ⑨ 안이다."""
    assert not _L0(variant, "fees"), _L0(variant, "fees")[:5]
    assert not _L1(variant, "fees"), _L1(variant, "fees")[:5]
    assert _관문(variant, "fees") <= ALLOW_GATE_RETURN_PCTP


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_결정_원장이_같다(variant: str) -> None:
    """25.1910 (전략 깔때기 ③ 일치 시험 — 매도·결정 원장·매수 비중) — L2. 견디는 것은 허용 ⑨(다) 하나: 든 종목의 목표까지 남은
    몫이 엔진 비용 장부의 보유 비중 흔들림보다 작은 날, 한쪽은 아주 작게 더 사고 다른 쪽은 "최소 주문 단위 미만"·"종목 비중 상한
    도달" 로 건너뛴다(B 2024-05-30 종목 22, G 2024-06-13 종목 22)."""
    assert not _L2(variant, "fees"), _L2(variant, "fees")[:5]


@pytest.mark.parametrize("variant", ["core_b", "core_g"])
def test_매수_비중이_같다(variant: str) -> None:
    """25.1910 (전략 깔때기 ③ 일치 시험 — 매도·결정 원장·매수 비중) — L3. 새로 사는 종목은 1e-6, 든 종목을 채우는 더 사기는 그
    종목 보유 비중 차이(허용 ⑨(나))까지 — 차이가 다른 데서 오면 깨진다."""
    assert not _L3(variant, "fees"), _L3(variant, "fees")[:5]
