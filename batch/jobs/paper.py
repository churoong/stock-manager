"""모의 투자 계좌의 하루 (docs/paper.md, docs/infra.md 25.1445).

일일 배치(`jobs/daily.run`)가 리포트를 만든 뒤 부른다 — **곁다리**다. 실패해도 리포트는 그대로 나간다.
순서: ① 지난 주문 체결 판정(그 장날짜의 시세로만) → ② 평가·검산·시간가중·그림자 기준을 `paper_daily` 에 →
③ 이 시장 보유의 매도 판정 → ④ 2부와 같은 함수로 매수 주문. 계좌·시장·거래일 주문이 이미 있으면 ③④ 를 건너뛴다
(재실행 멱등, docs/paper.md 2.4).

`trades`·`positions`·`sell_flags` 를 읽거나 쓰지 않는다(`tests/test_paper_engine_1444.py` 가 본다).
금액은 공개 로그에 찍지 않는다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace as dc_replace
from datetime import UTC, date, datetime
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.core import settings_range as sr
from batch.services import backtest as bt
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import sell_flags as sf

log = logging.getLogger(__name__)

ACCOUNTS_SQL = "SELECT id, sell_rule, rule_version FROM paper_accounts ORDER BY id"
FLOWS_SQL = "SELECT id, flow_date, amount_krw, kind, cancels_id, created_at FROM paper_cash_flows ORDER BY id"
FILLS_SQL = (
    "SELECT order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate, fee, tax, slippage,"
    " cash_krw, horizon, score_at_trade, snapshot_as_of, created_at FROM paper_fills ORDER BY fill_date, id"
)
OPEN_ORDERS_SQL = (
    "SELECT id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency, fill_session,"
    " fill_basis, deferrals, horizon, score_at_order, score_as_of FROM paper_orders WHERE status = 'open' ORDER BY id"
)
BAR_SQL = "SELECT date, open, close, volume FROM prices WHERE stock_id = ? AND date = ?"
#: 그 시장 전체에 그날 시세가 들어왔나 — 못 받은 날(배치 실패)과 정지를 가른다 (docs/paper.md 2.3)
MARKET_DAY_SQL = (
    "SELECT 1 FROM prices p CROSS JOIN stocks s ON s.id = p.stock_id WHERE p.date = ? AND s.country = ? LIMIT 1"
)
LAST_CLOSE_SQL = (
    "SELECT close, date FROM prices WHERE stock_id = ? AND date <= ? AND close > 0 ORDER BY date DESC LIMIT 1"
)
FX_SQL = "SELECT rate, date FROM fx_rates WHERE pair = 'USDKRW' AND date <= ? ORDER BY date DESC LIMIT 1"
INDEX_FROM_SQL = "SELECT close, date FROM index_prices WHERE index_code = ? AND date >= ? ORDER BY date LIMIT 1"
INDEX_NOW_SQL = "SELECT close, date FROM index_prices WHERE index_code = ? ORDER BY date DESC LIMIT 1"
PREV_DAILY_SQL = (
    "SELECT as_of, cash_krw, equity_krw, twr_index, updated_at FROM paper_daily"
    " WHERE account_id = ? AND as_of < ? ORDER BY as_of DESC LIMIT 1"
)
ORDERS_EXIST_SQL = "SELECT COUNT(*) FROM paper_orders WHERE account_id = ? AND market = ? AND decided_on = ?"
STOCK_SQL = (
    "SELECT id, ticker, COALESCE(name_ko, name_en, ticker) AS name, sector, country, currency, asset_type"
    " FROM stocks WHERE id = ?"
)
SCORE_NOW_SQL = (
    "SELECT total_score, as_of_date FROM scores WHERE stock_id = ? ORDER BY as_of_date DESC, calc_version DESC LIMIT 1"
)
INSERT_FILL_SQL = (
    "INSERT OR IGNORE INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
    " fx_rate, fx_date, fee, tax, slippage, cash_krw, horizon, score_at_trade, snapshot_as_of, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
FILLED_SQL = (
    "UPDATE paper_orders SET status = 'filled', closed_on = ?, close_note = ? WHERE id = ? AND status = 'open'"
)
EXPIRE_SQL = (
    "UPDATE paper_orders SET status = 'expired', closed_on = ?, close_note = ? WHERE id = ? AND status = 'open'"
)
DEFER_SQL = (
    "UPDATE paper_orders SET fill_session = ?, deferrals = deferrals + 1, close_note = ?"
    " WHERE id = ? AND status = 'open'"
)
INSERT_ORDER_SQL = (
    "INSERT OR IGNORE INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity,"
    " currency, decided_on, fill_session, fill_basis, tranche, reason, horizon, score_at_order, score_as_of,"
    " rationale_json, rule_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
UPSERT_DAILY_SQL = (
    "INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
    " twr_index, kospi_krw, sp500_krw, positions_json, fills_today, recon_ok, recon_note, unknown_json, updated_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (account_id, as_of) DO UPDATE SET invested_krw = excluded.invested_krw,"
    " cash_krw = excluded.cash_krw, holdings_krw = excluded.holdings_krw, equity_krw = excluded.equity_krw,"
    " pnl_krw = excluded.pnl_krw, twr_index = excluded.twr_index, kospi_krw = excluded.kospi_krw,"
    " sp500_krw = excluded.sp500_krw, positions_json = excluded.positions_json,"
    " fills_today = paper_daily.fills_today + excluded.fills_today, recon_ok = excluded.recon_ok,"
    " recon_note = excluded.recon_note, unknown_json = excluded.unknown_json, updated_at = excluded.updated_at"
)

#: 미국 환율이 이 날수보다 묵었으면 쓰지 않는다 — 2부와 같은 기준(`signals_job.load_settings` 7일)
FX_MAX_AGE_DAYS = 7

KST_MARKET = "KR"


def _원(v: float) -> float:
    return float(round(v))


def _쓰기(client: Any, statements: list[tuple[str, list[Any]]]) -> None:
    """한 번에 — `batch_atomic` 이 있으면 그것으로, 서버가 안 받으면 문장마다(유일 제약이 두 번 적기를 막는다)."""
    if not statements:
        return
    원자 = getattr(client, "batch_atomic", None)
    if 원자 is not None:
        from batch.core.turso import AtomicUnsupported

        try:
            원자(statements)
            return
        except AtomicUnsupported:
            pass
    client.batch(statements)


def _flows(client: Any) -> tuple[list[pp.CashFlow], dict[int, str]]:
    rows = client.execute(FLOWS_SQL).dicts()
    흐름 = [pp.CashFlow(int(r["id"]), str(r["flow_date"]), float(r["amount_krw"]), str(r["kind"]),
                      int(r["cancels_id"]) if r["cancels_id"] is not None else None) for r in rows]  # fmt: skip
    return 흐름, {int(r["id"]): str(r["created_at"]) for r in rows}


def _fills(client: Any) -> tuple[list[pp.Fill], dict[int, str]]:
    rows = client.execute(FILLS_SQL).dicts()
    out = [
        pp.Fill(
            order_id=int(r["order_id"]), account_id=str(r["account_id"]), stock_id=int(r["stock_id"]),
            side=str(r["side"]), fill_date=str(r["fill_date"]), price=float(r["price"]), quantity=int(r["quantity"]),
            currency=str(r["currency"]), fx_rate=float(r["fx_rate"]), fee=float(r["fee"]), tax=float(r["tax"]),
            slippage=float(r["slippage"]), cash_krw=float(r["cash_krw"]), horizon=r["horizon"],
            score_at_trade=r["score_at_trade"], snapshot_as_of=r["snapshot_as_of"],
        )  # fmt: skip
        for r in rows
    ]
    return out, {int(r["order_id"]): str(r["created_at"]) for r in rows}


def _order(r: dict[str, Any]) -> pp.Order:
    return pp.Order(
        id=int(r["id"]), account_id=str(r["account_id"]), stock_id=int(r["stock_id"]), side=str(r["side"]),
        order_type=str(r["order_type"]), limit_price=r["limit_price"], quantity=int(r["quantity"]),
        currency=str(r["currency"]), fill_session=str(r["fill_session"]), horizon=r["horizon"],
        score_at_order=r["score_at_order"], score_as_of=r["score_as_of"], fill_basis=str(r["fill_basis"] or "open"),
    )  # fmt: skip


def _fx(client: Any, day: str) -> tuple[float, str] | None:
    """그날(또는 직전) USDKRW — `FX_MAX_AGE_DAYS` 넘게 묵었으면 None."""
    row = client.execute(FX_SQL, [day]).rows
    if not row:
        return None
    rate, 날 = float(row[0][0]), str(row[0][1])
    if (date.fromisoformat(day[:10]) - date.fromisoformat(날[:10])).days > FX_MAX_AGE_DAYS:
        return None
    return rate, 날


def costs_for(market: str, fees: dict | None, taxes: dict | None, day: str) -> pp.Costs:
    """그 시장 비용 — 사용자 설정, 비면 백테스트 기본값(국내 매도세는 시행일 표) (docs/paper.md 2.3)."""
    c = bt.Costs.for_country(market, fees, taxes)
    return pp.Costs(buy_fee_pct=c.commission_pct, sell_fee_pct=c.commission_pct, sell_tax_pct=c.tax_at(day),
                    slippage_pct=c.slippage_pct)  # fmt: skip


def frozen_ids(client: Any, stock_ids: set[int], market_of: dict[int, str]) -> set[int]:
    """기업행위 경계(분할·병합 의심)에 든 종목 — 판 1 은 멈춘다 (docs/paper.md 2.3, 검증 [필수] 6)."""
    from batch.jobs import monitor_targets

    out: set[int] = set()
    for sid in sorted(stock_ids):
        try:
            if monitor_targets.action_factors(client, sid, market_of.get(sid, "KR")) is None:
                out.add(sid)
        except Exception as exc:  # noqa: BLE001 — 모르면 멈춘다(보수 쪽). 까닭은 기록에
            log.warning("기업행위 경계를 읽지 못해 멈춥니다 (종목 %s): %s", sid, exc)
            out.add(sid)
    return out


def fill_orders(
    client: Any, market: str, trade_date: str, flows: list[pp.CashFlow], fills: list[pp.Fill],
    fees: dict | None, taxes: dict | None, frozen: set[int], now: str, 기록: dict[str, Any],
) -> list[pp.Fill]:
    """이 시장의 미체결 주문 가운데 판정할 날이 된 것을 판정한다. 새 체결 목록을 돌려준다(장부에도 적었다)."""
    rows = [r for r in client.execute(OPEN_ORDERS_SQL).dicts() if r["market"] == market]
    대상 = [r for r in rows if str(r["fill_session"]) <= trade_date]
    if not 대상:
        return []
    시장날: dict[str, bool] = {}
    새것: list[pp.Fill] = []
    오늘환율 = _fx(client, trade_date)
    남은매수 = {int(r["id"]): _order(r) for r in rows if r["side"] == "buy"}
    # 매도 먼저(현금이 생긴다), 그다음 매수 — 같은 날 안에서는 주문 순서
    for r in sorted(대상, key=lambda x: (x["side"] != "sell", int(x["id"]))):
        o = _order(r)
        날 = o.fill_session
        if 날 not in 시장날:
            시장날[날] = bool(client.execute(MARKET_DAY_SQL, [날, market]).rows)
        if o.stock_id in frozen:
            _쓰기(client, [(EXPIRE_SQL, [trade_date, "기업행위 경계 — 멈춤 (docs/paper.md 2.3)", o.id])])
            남은매수.pop(o.id, None)
            기록["expired"] = 기록.get("expired", 0) + 1
            continue
        if not 시장날[날]:
            if 날 < trade_date:
                # 그 시장 전체에 그날 시세가 없다 — 정지가 아니라 못 받은 날. 매수는 취소, 매도는 다음 장으로
                if o.side == "buy":
                    _쓰기(client, [(EXPIRE_SQL, [trade_date, "그날 시세를 받지 못해 판정 못 함", o.id])])
                    남은매수.pop(o.id, None)
                    기록["expired"] = 기록.get("expired", 0) + 1
                else:
                    _쓰기(client, [(DEFER_SQL, [cal.next_session(market, date.fromisoformat(날)).isoformat(),
                                               "그날 시세를 받지 못함 — 다음 장으로", o.id])])  # fmt: skip
                    기록["deferred"] = 기록.get("deferred", 0) + 1
            continue
        행 = client.execute(BAR_SQL, [o.stock_id, 날]).dicts()
        bar = (pp.Bar(str(행[0]["date"]), 행[0]["open"], float(행[0]["close"]), 행[0]["volume"])
               if 행 and 행[0]["close"] is not None else None)  # fmt: skip
        가격, 까닭 = pp.judge(o, bar)
        if 가격 is None and o.side == "sell" and pp.halted(bar):
            if int(r["deferrals"] or 0) + 1 >= pp.HALT_LIQUIDATE_SESSIONS:
                마지막 = client.execute(LAST_CLOSE_SQL, [o.stock_id, 날]).rows
                if 마지막:
                    가격, 까닭 = float(마지막[0][0]), f"정지 {pp.HALT_LIQUIDATE_SESSIONS}거래일 — 마지막 종가로 청산"
            if 가격 is None:
                _쓰기(client, [(DEFER_SQL, [cal.next_session(market, date.fromisoformat(날)).isoformat(),
                                           f"{까닭} — 다음 장으로", o.id])])  # fmt: skip
                기록["deferred"] = 기록.get("deferred", 0) + 1
                continue
        if 가격 is None:
            _쓰기(client, [(EXPIRE_SQL, [trade_date, 까닭, o.id])])
            남은매수.pop(o.id, None)
            기록["expired"] = 기록.get("expired", 0) + 1
            continue
        환율, 환율날 = 1.0, None
        if o.currency != "KRW":
            fx = _fx(client, 날)
            if fx is None:
                if o.side == "buy":
                    _쓰기(client, [(EXPIRE_SQL, [trade_date, "쓸 수 있는 환율 없음", o.id])])
                    남은매수.pop(o.id, None)
                    기록["expired"] = 기록.get("expired", 0) + 1
                else:
                    _쓰기(client, [(DEFER_SQL, [cal.next_session(market, date.fromisoformat(날)).isoformat(),
                                               "쓸 수 있는 환율 없음 — 다음 장으로", o.id])])  # fmt: skip
                    기록["deferred"] = 기록.get("deferred", 0) + 1
                continue
            환율, 환율날 = fx
        체결 = pp.make_fill(o, 가격, 날, 환율, costs_for(market, fees, taxes, 날))
        if o.side == "buy":
            계좌체결 = [f for f in [*fills, *새것] if f.account_id == o.account_id]
            남은매수.pop(o.id, None)
            다른예약 = pp.reserved_krw([x for x in 남은매수.values() if x.account_id == o.account_id],
                                   {"USD": 오늘환율[0] if 오늘환율 else None})
            # 다른 달러 예약의 환율을 모르면(None) 예약을 빼지 않고 빚만 막는다 — 주문 때 이미 현금 안에서 냈다
            if pp.cash(flows, 계좌체결) + 체결.cash_krw - (다른예약 or 0.0) < 0:
                _쓰기(client, [(EXPIRE_SQL, [trade_date, "현금 부족 (다른 미체결 매수 예약 포함)", o.id])])
                기록["expired"] = 기록.get("expired", 0) + 1
                continue
        else:
            보유 = pp.positions([f for f in [*fills, *새것] if f.account_id == o.account_id]).get(o.stock_id)
            if 보유 is None or 보유.quantity < o.quantity:
                _쓰기(client, [(EXPIRE_SQL, [trade_date, "팔 보유가 모자람", o.id])])
                기록["expired"] = 기록.get("expired", 0) + 1
                continue
        _쓰기(client, [
            (INSERT_FILL_SQL, [체결.order_id, 체결.account_id, 체결.stock_id, 체결.side, 체결.fill_date, 체결.price,
                               체결.quantity, 체결.currency, 체결.fx_rate, 환율날, 체결.fee, 체결.tax, 체결.slippage,
                               체결.cash_krw, 체결.horizon, 체결.score_at_trade, 체결.snapshot_as_of, now]),
            (FILLED_SQL, [trade_date, 까닭, o.id]),
        ])  # fmt: skip
        새것.append(체결)
        기록["filled"] = 기록.get("filled", 0) + 1
    return 새것


def _stock(client: Any, sid: int, 캐시: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if sid not in 캐시:
        rows = client.execute(STOCK_SQL, [sid]).dicts()
        캐시[sid] = rows[0] if rows else {"id": sid, "ticker": str(sid), "name": str(sid), "sector": None,
                                          "country": None, "currency": None, "asset_type": "stock"}  # fmt: skip
    return 캐시[sid]


def _level_of(
    client: Any, flows: list[pp.CashFlow], code: str, fx: bool
) -> tuple[dict[int, float | None], float | None]:
    """그림자 기준 — 입금마다 그 돈이 처음 투자 가능해진 장의 원화 기준 값, 그리고 지금 값 (docs/paper.md 4장)."""
    지금 = client.execute(INDEX_NOW_SQL, [code]).rows
    if not 지금:
        return {}, None
    지금값, 지금날 = float(지금[0][0]), str(지금[0][1])
    if fx:
        환 = _fx(client, 지금날)
        if 환 is None:
            return {}, None
        지금값 *= 환[0]
    out: dict[int, float | None] = {}
    for f in flows:
        if f.kind != "deposit":
            continue
        행 = client.execute(INDEX_FROM_SQL, [code, f.flow_date]).rows
        if not 행:
            # 아직 장이 닫히지 않았다 — 지금 값으로 산 것으로 본다(평가 = 넣은 돈). 다음 날 진짜 값으로 바뀐다
            out[f.id] = 지금값
            continue
        값 = float(행[0][0])
        if fx:
            환 = _fx(client, str(행[0][1]))
            값 = 값 * 환[0] if 환 else 0.0
        out[f.id] = 값 or None
    return out, 지금값


def mark(
    client: Any, account: str, today: str, flows: list[pp.CashFlow], flow_created: dict[int, str],
    fills: list[pp.Fill], fill_created: dict[int, str], new_fills: int, frozen: set[int],
    stock_cache: dict[int, dict[str, Any]], shadows: dict[str, tuple[dict[int, float | None], float | None]], now: str,
) -> tuple[pp.Valuation, dict[str, Any]]:
    """평가·검산·시간가중·그림자 기준을 `paper_daily` 에 (한국 날짜 하루 한 행, 뒤 실행이 덮는다)."""
    계좌체결 = [f for f in fills if f.account_id == account]
    보유 = pp.positions(계좌체결)
    종가: dict[int, tuple[float, str] | None] = {}
    for sid in 보유:
        행 = client.execute(LAST_CLOSE_SQL, [sid, today]).rows
        종가[sid] = (float(행[0][0]), str(행[0][1])) if 행 else None
    fx = _fx(client, today)
    v = pp.value(flows, 계좌체결, 종가, {"USD": fx[0] if fx else None}, frozen)
    어제 = client.execute(PREV_DAILY_SQL, [account, today]).dicts()
    p = 어제[0] if 어제 else None
    if p is None:
        ok, 글 = True, "첫 기록"
        지수 = 1.0
    else:
        기준 = str(p["updated_at"])
        새입금 = sum(f.amount_krw for f in flows if flow_created.get(f.id, "") > 기준)
        새체결 = sum(f.cash_krw for f in 계좌체결 if fill_created.get(f.order_id, now) > 기준)
        ok, 글 = pp.reconcile(float(p["cash_krw"]), 새입금, 새체결, v.cash_krw)
        들어온 = sum(f.amount_krw for f in flows if f.flow_date > str(p["as_of"]))
        지수 = pp.twr_step(float(p["twr_index"]), float(p["equity_krw"]), v.equity_krw, 들어온)
    그림자 = {code: pp.shadow(flows, *shadows[code]) if code in shadows else None for code in ("KOSPI", "SP500")}
    _쓰기(client, [(UPSERT_DAILY_SQL, [
        account, today, v.invested_krw, v.cash_krw, v.holdings_krw, v.equity_krw, v.pnl_krw, 지수,
        그림자["KOSPI"], 그림자["SP500"], json.dumps(v.rows, ensure_ascii=False), new_fills, int(ok), 글,
        json.dumps(v.unknown, ensure_ascii=False) if v.unknown else None, now,
    ])])  # fmt: skip
    return v, {"recon_ok": ok, "recon_note": 글, "twr": 지수, "kospi": 그림자["KOSPI"], "sp500": 그림자["SP500"]}


def sell_decisions(
    client: Any, market: str, sell_rule: str, account_fills: list[pp.Fill], frozen: set[int],
    stock_cache: dict[int, dict[str, Any]], targets: dict | None, today: date, enrich_cache: dict[str, Any],
) -> dict[int, tuple[list[str], list[dict[str, Any]]]]:
    """이 시장 보유의 매도 판정 — 실제 보유와 같은 보강·같은 `evaluate` (docs/paper.md 2.2)."""
    from batch.jobs import sell_flags as sf_job

    행들: list[dict[str, Any]] = []
    for sid, p in sorted(pp.positions(account_fills).items()):
        s = _stock(client, sid, stock_cache)
        if s.get("country") != market or sid in frozen or s.get("asset_type") not in (None, "stock"):
            continue
        종가 = client.execute(LAST_CLOSE_SQL, [sid, today.isoformat()]).rows
        점수 = client.execute(SCORE_NOW_SQL, [sid]).rows
        로트 = [(f.fill_date, float(f.quantity), f.price) for f in account_fills
                if f.stock_id == sid and f.side == "buy" and f.fill_date >= p.first_buy_date]  # fmt: skip
        행들.append({
            "stock_id": sid, "name": s.get("name"), "country": market, "horizon": p.horizon,
            "first_buy_date": p.first_buy_date, "currency": p.currency, "cost": p.cost,
            "market_value": float(종가[0][0]) * p.quantity if 종가 else None,
            "price_date": str(종가[0][1]) if 종가 else None,
            "score_now": 점수[0][0] if 점수 else None, "score_now_date": 점수[0][1] if 점수 else None,
            "score_at_buy": p.score_at_buy, "score_at_buy_date": p.score_at_buy_date,
            "horizon_since": p.first_buy_date, "lots": 로트, "quantity": p.quantity,
        })  # fmt: skip
    out: dict[int, tuple[list[str], list[dict[str, Any]]]] = {}
    for 행 in 행들:
        # A·B 가 같은 로트를 들고 있으면 한 번만 보강한다 — 읽기를 두 번 쓰지 않는다
        열쇠 = json.dumps({k: v for k, v in 행.items() if k != "quantity"}, sort_keys=True, default=str)
        if 열쇠 not in enrich_cache:
            입력, 경고 = sf_job.enrich(client, [행], today, dividends=False)
            enrich_cache[열쇠] = (입력[0] if 입력 else None, 경고)
        h, _경고 = enrich_cache[열쇠]
        if h is None:
            continue
        flags = sf.evaluate(h, today, targets)
        팔것 = pp.sells_for(sell_rule, {h.stock_id: {f.reason_code for f in flags}})
        if 팔것:
            근거 = [{"reason_code": f.reason_code, "level": f.level, "rationale": f.rationale_text,
                    "criteria": f.criteria} for f in flags]  # fmt: skip
            out[h.stock_id] = (팔것[h.stock_id], 근거)
    return out


def rescale_rows(rows: list[report_picks.SignalRow], total: float, min_order: float) -> list[report_picks.SignalRow]:
    """신호 금액을 **모의 계좌 평가 총액 × 신호 비중**으로 다시 만든다 (docs/paper.md 2.1, 검증 [필수] 1).

    회차 금액도 같은 비율(40/30/30)로. 최소 주문 금액 미만이면 신호와 같이 비운다(`signals.py` 와 같은 규칙)
    """
    out: list[report_picks.SignalRow] = []
    for r in rows:
        금액 = total * float(r.suggested_weight_pct) / 100 if r.suggested_weight_pct and total > 0 else None
        if 금액 is not None and (금액 < min_order or 금액 <= 0):
            금액 = None
        회차 = [{**t, "amount": (금액 * float(t.get("ratio") or 0)) if 금액 is not None else None}
                for t in r.tranche_plan]  # fmt: skip
        out.append(dc_replace(r, suggested_amount=금액, tranche_plan=회차))
    return out


def buy_orders(
    client: Any, *, market: str, account: str, account_fills: list[pp.Fill], valuation: pp.Valuation,
    open_buys: list[pp.Order], signal_rows: list[report_picks.SignalRow], settings: dict[str, Any],
    price_series: dict[int, dict[str, float]], series_unknown: dict[int, str], fx_rate: float | None,
    stop_ids: set[int], sold_recently: set[int], stock_cache: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """2부와 같은 함수로 이 계좌의 매수 회차 주문을 낸다 (docs/paper.md 2.1). (주문 행, 기록)."""
    통화 = "KRW" if market == "KR" else "USD"
    환율 = 1.0 if 통화 == "KRW" else fx_rate
    if not 환율:
        return [], {"skipped": "환율 없음"}
    예약 = pp.reserved_krw(open_buys, {"USD": fx_rate})
    if 예약 is None:
        return [], {"skipped": "미체결 달러 주문의 환율 모름"}
    if valuation.cash_krw < 0:
        return [], {"skipped": "장부 현금이 음수"}
    보유 = [
        report_picks.Holding(
            stock_id=r["stock_id"], ticker=str(_stock(client, r["stock_id"], stock_cache).get("ticker")),
            name=str(_stock(client, r["stock_id"], stock_cache).get("name")),
            sector=_stock(client, r["stock_id"], stock_cache).get("sector"), value=r["value_krw"] / 환율,
            country=_stock(client, r["stock_id"], stock_cache).get("country"),
            asset_type=str(_stock(client, r["stock_id"], stock_cache).get("asset_type") or "stock"),
        )  # fmt: skip
        for r in valuation.rows
    ]
    총액 = valuation.equity_krw / 환율
    고른 = report_picks.select_top(report_picks.with_criteria(signal_rows))
    다시 = rescale_rows(고른, 총액, float(settings.get("min_order_amount") or 0))
    pv = report_picks.build_portfolio(
        다시, 총액, currency=통화, max_sector_pct=settings["max_weight_per_sector"], holdings=보유,
        max_stock_pct=settings["max_weight_per_stock"], price_series=price_series, series_unknown=series_unknown,
        min_order_amount=float(settings.get("min_order_amount") or 0), fx_missing=False, reserved=예약 / 환율,
        stop_flags={sid: "모의 계좌 손절 판정" for sid in stop_ids},
    )  # fmt: skip
    by_ticker: dict[str, list[report_picks.SignalRow]] = {}
    for r in 다시:
        by_ticker.setdefault(r.ticker, []).append(r)
    배분: list[dict[str, Any]] = []
    for a in pv.allocations:
        행 = by_ticker.get(a.ticker) or []
        if not 행:
            continue
        best = max(행, key=lambda r: (r.suggested_amount or 0, -report_picks._기간_순.get(r.horizon, 9)))
        배분.append({"stock_id": best.stock_id, "amount": a.amount, "tranches": a.tranches, "row": best, "alloc": a})
    주문, 빠짐 = pp.size_buys(배분, valuation.cash_krw - 예약, 환율, sold_recently)
    by_sid = {b["stock_id"]: b for b in 배분}
    out: list[dict[str, Any]] = []
    for o in 주문:
        b = by_sid[o["stock_id"]]
        r: report_picks.SignalRow = b["row"]
        out.append({
            **o, "currency": 통화, "horizon": r.horizon, "score": r.total_score, "score_as_of": r.as_of_date,
            "rationale": {
                "alloc": {"amount": b["alloc"].amount, "weight_pct": b["alloc"].weight_pct, "note": b["alloc"].note},
                "signal": {"horizon": r.horizon, "as_of": r.as_of_date, "total_score": r.total_score,
                           "weight_pct": r.suggested_weight_pct, "criteria": r.criteria},
                "settings": {k: settings.get(k) for k in ("max_weight_per_stock", "max_weight_per_sector",
                                                          "min_order_amount")},
                "equity": 총액, "reserved": 예약 / 환율, "fx": 환율,
            },
        })  # fmt: skip
    return out, {"allocations": len(pv.allocations), "orders": len(out), "skipped": 빠짐[:10],
                 "excluded": len(pv.excluded)}  # fmt: skip


def run_step(
    client: Any, *, market: str, trade_date: str, late: bool, signal_rows: list[report_picks.SignalRow],
    settings: dict[str, Any] | None, price_series: dict[int, dict[str, float]], series_unknown: dict[int, str],
    allocate: bool, now: datetime | None = None, series_loader: Callable[[list[int]], dict] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """일일 배치의 모의 계좌 단계. (리포트 끝에 붙일 줄, `step_log.paper`).

    `series_loader` 는 2부가 읽지 않은 모의 보유 종목의 시세를 읽는다(상관 축소용) — 없으면 그 보유는 대조하지 않는다
    (2부와 같은 "모르면 줄이지 않는다")
    """
    지금 = (now or datetime.now(UTC)).isoformat()
    읽기0 = getattr(getattr(client, "reads", None), "total", None)
    오늘 = cal.local_today(KST_MARKET).isoformat()
    기록: dict[str, Any] = {"trade_date": trade_date}
    flows, flow_created = _flows(client)
    if not flows:
        return [], {"skipped": "입금 없음"}
    accounts = client.execute(ACCOUNTS_SQL).dicts()
    fills, fill_created = _fills(client)
    fees = db.get_setting(client, "fees", {})
    taxes = db.get_setting(client, "taxes", {})
    stock_cache: dict[int, dict[str, Any]] = {}
    관심 = {f.stock_id for f in fills} | {int(r["stock_id"]) for r in client.execute(OPEN_ORDERS_SQL).dicts()}
    market_of = {sid: str(_stock(client, sid, stock_cache).get("country") or "KR") for sid in 관심}
    frozen = frozen_ids(client, 관심, market_of)

    새체결 = fill_orders(client, market, trade_date, flows, fills, fees, taxes, frozen, 지금, 기록)
    fills = [*fills, *새체결]
    for f in 새체결:
        fill_created[f.order_id] = 지금

    shadows = {"KOSPI": _level_of(client, flows, "KOSPI", fx=False),
               "SP500": _level_of(client, flows, "SP500", fx=True)}  # fmt: skip
    targets, _ = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
    fx = _fx(client, 오늘)
    열린 = [_order(r) for r in client.execute(OPEN_ORDERS_SQL).dicts()]
    다음장 = cal.next_session(market, date.fromisoformat(trade_date)).isoformat()
    기준 = "close" if late else "open"
    쉼_시작 = trade_date
    for _ in range(pp.REBUY_COOLDOWN_SESSIONS - 1):
        쉼_시작 = cal.previous_session(market, date.fromisoformat(쉼_시작)).isoformat()
    보강캐시: dict[str, Any] = {}
    줄: list[str] = []
    계좌기록: dict[str, Any] = {}
    for acct in accounts:
        aid, 규칙, 판 = str(acct["id"]), str(acct["sell_rule"]), int(acct["rule_version"])
        a기록: dict[str, Any] = {"fills": sum(1 for f in 새체결 if f.account_id == aid)}
        v, 검 = mark(client, aid, 오늘, flows, flow_created, fills, fill_created, a기록["fills"], frozen, stock_cache,
                    shadows, 지금)  # fmt: skip
        a기록.update({"recon_ok": 검["recon_ok"], "unknown": len(v.unknown)})
        계좌체결 = [f for f in fills if f.account_id == aid]
        if int(client.execute(ORDERS_EXIST_SQL, [aid, market, trade_date]).scalar() or 0) > 0:
            a기록["orders"] = "이미 냄"
        else:
            매도 = sell_decisions(client, market, 규칙, 계좌체결, frozen, stock_cache, targets,
                               cal.local_today(market), 보강캐시)  # fmt: skip
            이미팖 = {o.stock_id for o in 열린 if o.account_id == aid and o.side == "sell"}
            보유 = pp.positions(계좌체결)
            문장: list[tuple[str, list[Any]]] = []
            for sid, (사유, 근거) in sorted(매도.items()):
                if sid in 이미팖 or sid not in 보유:
                    continue
                문장.append((INSERT_ORDER_SQL, [
                    aid, market, sid, "sell", "market", None, 보유[sid].quantity, 보유[sid].currency, trade_date,
                    다음장, 기준, 0, ",".join(사유), None, None, None,
                    json.dumps({"flags": 근거, "sell_rule": 규칙}, ensure_ascii=False, default=str), 판, 지금,
                ]))  # fmt: skip
            a기록["sells"] = len(문장)
            최근판 = {f.stock_id for f in 계좌체결 if f.side == "sell" and f.fill_date >= 쉼_시작} | set(매도)
            if allocate and settings is not None and signal_rows:
                손절 = {sid for sid, (사유, _근거) in 매도.items() if "손절" in 사유}
                빠진시세 = sorted({int(r["stock_id"]) for r in v.rows} - set(price_series))
                if 빠진시세 and series_loader is not None:
                    price_series = {**price_series, **series_loader(빠진시세)}
                주문, b기록 = buy_orders(
                    client, market=market, account=aid, account_fills=계좌체결, valuation=v,
                    open_buys=[o for o in 열린 if o.account_id == aid and o.side == "buy"], signal_rows=signal_rows,
                    settings=settings, price_series=price_series, series_unknown=series_unknown,
                    fx_rate=fx[0] if fx else None, stop_ids=손절, sold_recently=최근판, stock_cache=stock_cache,
                )  # fmt: skip
                a기록["buy"] = b기록
                for o in 주문:
                    문장.append((INSERT_ORDER_SQL, [
                        aid, market, o["stock_id"], "buy", "limit", o["limit_price"], o["quantity"], o["currency"],
                        trade_date, 다음장, 기준, o["tranche"], "2부 배분", o["horizon"], o["score"], o["score_as_of"],
                        json.dumps(o["rationale"], ensure_ascii=False, default=str), 판, 지금,
                    ]))  # fmt: skip
            else:
                a기록["buy"] = {"skipped": "배분하지 않는 날(신호 없음·묵음·추천 못 읽음)"}
            _쓰기(client, 문장)
            a기록["orders"] = len(문장)
        계좌기록[aid] = a기록
        손익률 = f"{v.pnl_pct:+.1f}%" if v.pnl_pct is not None else "—"
        줄.append(f"{aid}: 평가 {v.equity_krw:,.0f}원 (손익 {손익률}, 시간가중 {(검['twr'] - 1) * 100:+.1f}%,"
                 f" 오늘 체결 {a기록['fills']}건)")  # fmt: skip
        if not 검["recon_ok"]:
            줄.append(f"⚠️ 모의 {aid} 장부 검산 어긋남 — {검['recon_note']}")
    기록["accounts"] = 계좌기록
    기록["frozen"] = sorted(frozen)
    if 읽기0 is not None:
        기록["reads"] = getattr(client.reads, "total", 0) - 읽기0
    머리 = ["🧪 모의 투자 " + " · ".join(글 for 글 in 줄 if not 글.startswith("⚠️"))]
    if frozen:
        머리.append(f"모의 계좌 멈춘 종목 {len(frozen)}개(기업행위 경계 — 원가로 평가, docs/paper.md 2.3)")
    return 머리 + [글 for 글 in 줄 if 글.startswith("⚠️")], 기록
