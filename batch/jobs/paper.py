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
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.core import settings_range as sr
from batch.services import backtest as bt
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import risk_gate as rg
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
#: 그 계좌·시장·거래일에 **결정**(주문이든 안 낸 결정이든)이 이미 있나 — 먼저 쓴 쪽이 이긴다 (docs/paper.md 2.4).
#: 주문만 보면 다 막힌 날(멈춤·현금 부족) 다시 돌 때 skip 위에 buy 가 더 적혀 같은 날 두 집단에 다 셌다 (25.1453)
ORDERS_EXIST_SQL = (
    "SELECT (SELECT COUNT(*) FROM paper_orders WHERE account_id = ? AND market = ? AND decided_on = ?)"
    " + (SELECT COUNT(*) FROM paper_decisions"
    "    WHERE account_id = ? AND market = ? AND decided_on = ? AND kind != 'missed')"
)
STOCK_SQL = (
    "SELECT id, ticker, COALESCE(name_ko, name_en, ticker) AS name, sector, country, currency, asset_type"
    " FROM stocks WHERE id = ?"
)
SCORE_NOW_SQL = (
    "SELECT total_score, as_of_date FROM scores WHERE stock_id = ? ORDER BY as_of_date DESC, calc_version DESC LIMIT 1"
)
INSERT_FILL_SQL = (
    "INSERT OR IGNORE INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
    " fx_rate, fx_date, fee, tax, slippage, cash_krw, horizon, score_at_trade, snapshot_as_of, created_at, day_close,"
    " realized_krw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
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
    " twr_index, kospi_krw, sp500_krw, positions_json, fills_today, recon_ok, recon_note, unknown_json, updated_at,"
    " realized_krw, unrealized_krw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (account_id, as_of) DO UPDATE SET invested_krw = excluded.invested_krw,"
    " cash_krw = excluded.cash_krw, holdings_krw = excluded.holdings_krw, equity_krw = excluded.equity_krw,"
    " pnl_krw = excluded.pnl_krw, twr_index = excluded.twr_index, kospi_krw = excluded.kospi_krw,"
    " sp500_krw = excluded.sp500_krw, positions_json = excluded.positions_json,"
    " fills_today = paper_daily.fills_today + excluded.fills_today, recon_ok = excluded.recon_ok,"
    " recon_note = excluded.recon_note, unknown_json = excluded.unknown_json, updated_at = excluded.updated_at,"
    " realized_krw = excluded.realized_krw, unrealized_krw = excluded.unrealized_krw"
)

PAPER_TWR_SQL = "SELECT account_id, as_of, twr_index FROM paper_daily ORDER BY account_id, as_of"
#: 실제 포트폴리오의 시간가중 지수 — 비교에만 읽는다(쓰지 않는다). `jobs/portfolio` 가 매매 기록에서 다시 만든 파생 표
REAL_TWR_SQL = "SELECT date, twr_index FROM portfolio_values WHERE date >= ? ORDER BY date"
#: 실제 계열을 모의 첫 기록보다 이만큼 앞부터 읽는다 — 시작일 이하의 마지막 행을 잡으려고(주말·휴일 사이)
REAL_LOOKBACK_DAYS = 14

#: 결정 원장 (docs/paper.md 9장, 25.1449) — 안 한 결정까지. 재실행은 유일 제약이 막는다
DECISION_INSERT_SQL = (
    "INSERT OR IGNORE INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, detail_json,"
    " ref_close, ref_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
#: **창마다 찰 때가 된 행만** (25.1453, 교차검증 [필수] 1) — 예전엔 `ret_60d IS NULL` 인 오래된 순 300행이라,
#: 5·20 은 채우고 60 을 기다리는 행이 앞자리를 차지해 새 결정의 20 거래일 성과가 두 달 넘게 밀렸다.
#: `ret_60d IS NULL` 은 부분 색인을 타게 둔다
PENDING_DECISIONS_SQL = (
    "SELECT id, stock_id, decided_on, ref_close, ref_date, ret_5d, ret_20d, ret_60d FROM paper_decisions"
    " WHERE ret_60d IS NULL AND outcome_note IS NULL AND market = ?"
    "   AND ((ret_5d IS NULL AND decided_on <= ?) OR (ret_20d IS NULL AND decided_on <= ?) OR decided_on <= ?)"
    " ORDER BY decided_on LIMIT ?"
)
AFTER_CLOSES_SQL = (
    "SELECT date, close FROM prices WHERE stock_id = ? AND date > ? AND close > 0 ORDER BY date LIMIT ?"
)
INDEX_ON_OR_BEFORE_SQL = (
    "SELECT close FROM index_prices WHERE index_code = ? AND date <= ? ORDER BY date DESC LIMIT 1"
)
UPDATE_OUTCOME_SQL = (
    "UPDATE paper_decisions SET ret_5d = COALESCE(ret_5d, ?), ret_20d = COALESCE(ret_20d, ?),"
    " ret_60d = COALESCE(ret_60d, ?), bench_5d = COALESCE(bench_5d, ?), bench_20d = COALESCE(bench_20d, ?),"
    " bench_60d = COALESCE(bench_60d, ?), outcome_note = ?, outcome_at = ? WHERE id = ?"
)
#: 한 번에 성과를 채우는 결정 수 — 하루 읽기 상한(행당 최대 60 시세 행 + 지수 몇 행)
OUTCOME_BATCH = 300
#: 성과 분해 (docs/paper.md 10장, 25.1450) — 성과가 찬 결정과 체결일 종가가 있는 매수만, 최근 1년
ATTR_DECISIONS_SQL = (
    "SELECT stock_id, decided_on, kind, reason, ret_20d, bench_20d FROM paper_decisions"
    " WHERE account_id = ? AND decided_on >= ? AND ret_20d IS NOT NULL"
)
ATTR_FILLS_SQL = (
    "SELECT order_id, price, day_close FROM paper_fills"
    " WHERE account_id = ? AND side = 'buy' AND day_close IS NOT NULL AND fill_date >= ?"
)
SAVE_ATTR_SQL = "UPDATE paper_daily SET attribution_json = ? WHERE account_id = ? AND as_of = ?"
#: 성과 분해가 보는 기간(달력일)
ATTR_WINDOW_DAYS = 365

#: 사람이 켜는 모의 매매 멈춤 스위치 — /paper 화면의 단추가 쓴다 (docs/paper.md 11장, 25.1451)
HALT_SETTING_KEY = "paper_halt"

#: 결정의 기준 지수
BENCH_INDEX = {"KR": "KOSPI", "US": "SP500"}

#: 미국 환율이 이 날수보다 묵었으면 쓰지 않는다 — 2부와 같은 기준(`signals_job.load_settings` 7일)
FX_MAX_AGE_DAYS = 7

KST_MARKET = "KR"


def _원(v: float) -> float:
    return float(round(v))


def _결정(
    aid: str, market: str, decided_on: str, sid: int, kind: str, reason: str, detail: dict[str, Any] | None,
    ref_close: Any, ref_date: Any, now: str,
) -> tuple[str, list[Any]]:
    """결정 원장 한 줄 (docs/paper.md 9장)."""
    return (DECISION_INSERT_SQL, [
        aid, market, decided_on, sid, kind, reason[:200],
        json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
        None if ref_close is None else float(ref_close), ref_date, now,
    ])  # fmt: skip


def fill_outcomes(client: Any, market: str, today: str, now: str) -> dict[str, int]:
    """결정 원장의 5·20·60 거래일 성과를 채운다 (docs/paper.md 9장, 25.1449).

    달력일이 지나 찼을 수 있는 창만 읽는다(`paper.due_horizons`). 시세가 끊겨 `OUTCOME_GIVE_UP_DAYS` 가 지나도
    못 채우면 까닭을 적고 그만 본다
    """
    from datetime import date as _date

    오늘 = _date.fromisoformat(today)
    기한 = {n: (오늘 - timedelta(days=d)).isoformat() for n, d in pp.HORIZON_CALENDAR_DAYS.items()}
    rows = client.execute(PENDING_DECISIONS_SQL, [market, 기한[5], 기한[20], 기한[60], OUTCOME_BATCH]).dicts()
    지수캐시: dict[str, float | None] = {}
    코드 = BENCH_INDEX.get(market, "KOSPI")

    def 지수(날: str) -> float | None:
        if 날 not in 지수캐시:
            r = client.execute(INDEX_ON_OR_BEFORE_SQL, [코드, 날]).rows
            지수캐시[날] = float(r[0][0]) if r else None
        return 지수캐시[날]

    채움, 포기 = 0, 0
    문장: list[tuple[str, list[Any]]] = []
    for r in rows:
        if r["ref_close"] is None or not r["ref_date"]:
            # 기준 종가가 없는 행은 영영 못 채운다 — 대기열에 남아 자리를 먹지 않게 바로 까닭을 적는다 (25.1453)
            문장.append((UPDATE_OUTCOME_SQL, [None] * 6 + ["기준 종가 없음", now, r["id"]]))
            포기 += 1
            continue
        창 = pp.due_horizons(str(r["decided_on"]), today, r)
        if not 창:
            continue
        행들 = client.execute(AFTER_CLOSES_SQL, [r["stock_id"], r["ref_date"], max(창)]).rows
        뒤 = [(str(d), float(c)) for d, c in 행들]
        그날들 = {뒤[n - 1][0]: 지수(뒤[n - 1][0]) for n in 창 if len(뒤) >= n}
        o = pp.outcomes(r["ref_close"], 뒤, 지수(str(r["ref_date"])), 그날들)
        지난 = (_date.fromisoformat(today) - _date.fromisoformat(str(r["decided_on"]))).days
        포기함 = "ret_60d" not in o and 지난 >= pp.OUTCOME_GIVE_UP_DAYS
        # 이미 채운 창을 다시 계산한 것뿐이면 쓰지 않는다 — 날마다 같은 행을 다시 쓰고 `filled` 가 부풀었다 (25.1453)
        새것 = any(r.get(f"ret_{n}d") is None and o.get(f"ret_{n}d") is not None for n in pp.DECISION_HORIZONS)
        if not 새것 and not 포기함:
            continue
        문장.append((UPDATE_OUTCOME_SQL, [
            *(o.get(f"ret_{n}d") for n in pp.DECISION_HORIZONS), *(o.get(f"bench_{n}d") for n in pp.DECISION_HORIZONS),
            f"{pp.OUTCOME_GIVE_UP_DAYS}일 지나도 시세 {len(뒤)}거래일뿐" if 포기함 else None, now, r["id"],
        ]))  # fmt: skip
        채움 += 새것
        포기 += 포기함
    if 문장:
        client.batch(문장)
    return {"pending_read": len(rows), "filled": 채움, "gave_up": 포기}


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
    전부 = client.execute(OPEN_ORDERS_SQL).dicts()
    대상 = [r for r in 전부 if r["market"] == market and str(r["fill_session"]) <= trade_date]
    if not 대상:
        return []
    시장날: dict[str, bool] = {}
    새것: list[pp.Fill] = []
    오늘환율 = _fx(client, trade_date)
    # 다른 시장의 미체결 매수도 현금을 묶고 있다 — 체결 검사에서 함께 뺀다 (25.1447, 교차검증 권장)
    남은매수 = {int(r["id"]): _order(r) for r in 전부 if r["side"] == "buy"}
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
        그날종가 = bar.close if bar is not None else None  # 진입 이득을 잴 체결일 종가 (25.1450)
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
            문장 = [(EXPIRE_SQL, [trade_date, 까닭, o.id])]
            if o.side == "buy" and bar is not None and not pp.halted(bar):
                # 지정가가 안 찬 회차 — 그 뒤 얼마나 올랐나(놓친 기회)를 결정 원장으로 잰다 (25.1449)
                문장.append(_결정(o.account_id, market, 날, o.stock_id, "missed", 까닭,
                                {"limit_price": o.limit_price, "quantity": o.quantity, "order_id": o.id},
                                bar.close, 날, now))  # fmt: skip
            _쓰기(client, 문장)
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
        실현 = None
        if o.side == "sell":
            보유 = pp.positions([f for f in [*fills, *새것] if f.account_id == o.account_id]).get(o.stock_id)
            if 보유 is not None and 보유.quantity > 0:
                # 매도의 실현손익(원) — 평균 원가 기준, 화면의 거래내역 (25.1455)
                실현 = _원(체결.cash_krw - 보유.cost_krw * o.quantity / 보유.quantity)
            if 보유 is None or 보유.quantity < o.quantity:
                _쓰기(client, [(EXPIRE_SQL, [trade_date, "팔 보유가 모자람", o.id])])
                기록["expired"] = 기록.get("expired", 0) + 1
                continue
        if any(f.order_id == o.id for f in fills):
            # 체결 행은 들어갔는데 주문 상태만 못 바꾼 채 끊겼던 것 — 상태만 맞추고 두 번 세지 않는다 (25.1447)
            _쓰기(client, [(FILLED_SQL, [trade_date, 까닭, o.id])])
            continue
        _쓰기(client, [
            (INSERT_FILL_SQL, [체결.order_id, 체결.account_id, 체결.stock_id, 체결.side, 체결.fill_date, 체결.price,
                               체결.quantity, 체결.currency, 체결.fx_rate, 환율날, 체결.fee, 체결.tax, 체결.slippage,
                               체결.cash_krw, 체결.horizon, 체결.score_at_trade, 체결.snapshot_as_of, now,
                               그날종가, 실현]),
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


def real_comparison(client: Any) -> dict[str, Any] | None:
    """모의 계좌와 실제 포트폴리오를 같은 기간 시간가중으로 (docs/paper.md 4장, 2026-10-15 사용자 지시 25.1448).

    읽기만 한다. 실제 포트폴리오가 없거나 표를 못 읽으면 None — 리포트는 그 줄만 빠진다
    """
    모의: dict[str, list[tuple[str, float]]] = {}
    for r in client.execute(PAPER_TWR_SQL).dicts():
        모의.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), float(r["twr_index"])))
    if not 모의:
        return None
    처음 = min(v[0][0] for v in 모의.values())
    부터 = (date.fromisoformat(처음) - timedelta(days=REAL_LOOKBACK_DAYS)).isoformat()
    실제 = [(str(r[0]), float(r[1])) for r in client.execute(REAL_TWR_SQL, [부터]).rows if r[1] is not None]
    return pp.compare_real(모의, 실제)


def save_attribution(client: Any, aid: str, today: str) -> dict[str, Any]:
    """그 계좌의 성과 분해를 내 오늘 평가 행에 적는다 (docs/paper.md 10장, 25.1450)."""
    부터 = (date.fromisoformat(today) - timedelta(days=ATTR_WINDOW_DAYS)).isoformat()
    결정 = client.execute(ATTR_DECISIONS_SQL, [aid, 부터]).dicts()
    체결행 = client.execute(ATTR_FILLS_SQL, [aid, 부터]).dicts()
    a = pp.attribution(결정, [(float(r["price"]), r["day_close"]) for r in 체결행])
    _쓰기(client, [(SAVE_ATTR_SQL, [json.dumps(a, ensure_ascii=False), aid, today])])
    return a


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
        새입금 = sum(f.amount_krw for f in flows if pp.after(flow_created.get(f.id, ""), 기준))
        새체결 = sum(f.cash_krw for f in 계좌체결 if pp.after(fill_created.get(f.order_id, now), 기준))
        ok, 글 = pp.reconcile(float(p["cash_krw"]), 새입금, 새체결, v.cash_krw)
        # **어제 행을 적은 뒤에 들어온 입금**이 흐름이다 (25.1447, 교차검증 [필수] 1) — 입금 날짜(`flow_date > as_of`)로
        # 고르면 미국 배치 뒤(같은 한국 날짜) 넣은 돈이 흐름에서 빠져 평가에만 더해져 지수가 +100% 로 뛰었다
        지수 = pp.twr_step(float(p["twr_index"]), float(p["equity_krw"]), v.equity_krw, 새입금)
    그림자 = {code: pp.shadow(flows, *shadows[code]) if code in shadows else None for code in ("KOSPI", "SP500")}
    _쓰기(client, [(UPSERT_DAILY_SQL, [
        account, today, v.invested_krw, v.cash_krw, v.holdings_krw, v.equity_krw, v.pnl_krw, 지수,
        그림자["KOSPI"], 그림자["SP500"], json.dumps(v.rows, ensure_ascii=False), new_fills, int(ok), 글,
        json.dumps(v.unknown, ensure_ascii=False) if v.unknown else None, now,
        # 계좌의 실현손익 합 · 보유 평가손익 합 (25.1455)
        float(sum(r["pnl_krw"] for r in pp.realized(계좌체결))), float(sum(r["unrealized_krw"] for r in v.rows)),
    ])])  # fmt: skip
    return v, {"recon_ok": ok, "recon_note": 글, "twr": 지수, "kospi": 그림자["KOSPI"], "sp500": 그림자["SP500"],
               "prev_twr": float(p["twr_index"]) if p else None}


def sell_decisions(
    client: Any, market: str, sell_rule: str, account_fills: list[pp.Fill], frozen: set[int],
    stock_cache: dict[int, dict[str, Any]], targets: dict | None, today: date, enrich_cache: dict[str, Any],
    notes: dict[int, dict[str, Any]] | None = None,
) -> dict[int, tuple[list[str], list[dict[str, Any]]]]:
    """이 시장 보유의 매도 판정 — 실제 보유와 같은 보강·같은 `evaluate` (docs/paper.md 2.2).

    `notes` 를 주면 종목마다 {ref: (종가, 날짜), hold: [계좌 규칙이 따르지 않은 플래그], flags: 근거} 를 채운다
    — 결정 원장용(25.1449)
    """
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
        근거 = [{"reason_code": f.reason_code, "level": f.level, "rationale": f.rationale_text,
                "criteria": f.criteria} for f in flags]  # fmt: skip
        if 팔것:
            out[h.stock_id] = (팔것[h.stock_id], 근거)
        if notes is not None and flags:
            따름 = set(팔것.get(h.stock_id, []))
            notes[h.stock_id] = {"ref": (행["market_value"] / 행["quantity"] if 행["market_value"] else None,
                                         행["price_date"]),
                                 "hold": sorted({f.reason_code for f in flags} - 따름), "flags": 근거}  # fmt: skip
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
    주문, 빠짐 = pp.size_buys(배분, valuation.cash_krw - 예약, 환율, sold_recently, 통화)
    by_sid = {b["stock_id"]: b for b in 배분}
    # 결정 원장 (25.1449) — 산 후보·못 산 후보(까닭)와 그때의 기준 종가
    결정: list[dict[str, Any]] = []
    낸 = {o["stock_id"] for o in 주문}
    for b in 배분:
        r0: report_picks.SignalRow = b["row"]
        까닭 = next((x["why"] for x in 빠짐 if x.get("stock_id") == b["stock_id"]), "주문 없음")
        결정.append({"stock_id": b["stock_id"], "kind": "buy" if b["stock_id"] in 낸 else "skip",
                     "reason": "2부 배분" if b["stock_id"] in 낸 else 까닭,
                     "ref_close": r0.close, "ref_date": r0.close_date,
                     "detail": {"horizon": r0.horizon, "score": r0.total_score, "weight_pct": r0.suggested_weight_pct,
                                "amount": b["amount"], "prices": [t.get("price_at_or_below") for t in b["tranches"]]}})
    for e in pv.excluded:
        r0 = (by_ticker.get(e.ticker) or [None])[0]  # type: ignore[assignment]
        if r0 is not None:
            결정.append({"stock_id": r0.stock_id, "kind": "skip", "reason": e.reason, "ref_close": r0.close,
                         "ref_date": r0.close_date,
                         "detail": {"horizon": r0.horizon, "score": r0.total_score, "note": e.detail[:200]}})
    out: list[dict[str, Any]] = []
    for o in 주문:
        b = by_sid[o["stock_id"]]
        r: report_picks.SignalRow = b["row"]
        out.append({
            **o, "currency": 통화, "horizon": r.horizon, "score": r.total_score, "score_as_of": r.as_of_date,
            "ref_date": r.close_date,
            # 위험 점검이 보는 원화 금액 — 예약과 같은 잣대 (25.1451)
            "cost_krw": o["limit_price"] * o["quantity"] * (1 + pp.FEE_BUFFER) * 환율
            * (1 if 통화 == "KRW" else 1 + pp.FX_BUFFER),
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
                 "excluded": len(pv.excluded), "_decisions": 결정}  # fmt: skip


def run_step(
    client: Any, *, market: str, trade_date: str, late: bool, signal_rows: list[report_picks.SignalRow],
    settings: dict[str, Any] | None, price_series: dict[int, dict[str, float]], series_unknown: dict[int, str],
    allocate: bool, now: datetime | None = None, series_loader: Callable[[list[int]], dict] | None = None,
    after_close: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """일일 배치의 모의 계좌 단계. (리포트 끝에 붙일 줄, `step_log.paper`).

    `late` 는 오늘 장이 열린 뒤 만든 리포트(매도는 종가), `after_close` 는 오늘 장이 끝난 뒤의 손 실행(주문은 다음 장)

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
    if after_close:
        # **장이 끝난 뒤의 손 실행** (25.1447, 교차검증 [필수] 3) — 오늘 장의 시가·종가 모두 주문보다 앞선다.
        # 다음 장 시가로
        다음장, 기준 = cal.next_session(market, date.fromisoformat(다음장)).isoformat(), "open"
    쉼_시작 = trade_date
    for _ in range(pp.REBUY_COOLDOWN_SESSIONS - 1):
        쉼_시작 = cal.previous_session(market, date.fromisoformat(쉼_시작)).isoformat()
    보강캐시: dict[str, Any] = {}
    멈춤값 = db.get_setting(client, "paper_halt", None)
    멈춤 = bool(멈춤값.get("on")) if isinstance(멈춤값, dict) else bool(멈춤값)
    기록["halted"] = 멈춤
    줄: list[str] = []
    계좌기록: dict[str, Any] = {}
    for acct in accounts:
        aid, 규칙, 판 = str(acct["id"]), str(acct["sell_rule"]), int(acct["rule_version"])
        a기록: dict[str, Any] = {"fills": sum(1 for f in 새체결 if f.account_id == aid)}
        v, 검 = mark(client, aid, 오늘, flows, flow_created, fills, fill_created, a기록["fills"], frozen, stock_cache,
                    shadows, 지금)  # fmt: skip
        a기록.update({"recon_ok": 검["recon_ok"], "unknown": len(v.unknown)})
        계좌체결 = [f for f in fills if f.account_id == aid]
        # 사전 위험 점검의 문맥 (docs/paper.md 11장, 25.1451) — 배분과 따로 한 번 더 막는다
        상한 = settings or {}
        예약 = pp.reserved_krw([o for o in 열린 if o.account_id == aid and o.side == "buy"],
                              {"USD": fx[0] if fx else None})
        문맥 = rg.Context(
            # 하루 수익은 **입출금을 뺀** 시간가중 지수로 (25.1453, 교차검증 [필수] 2) — 평가 총액끼리 견주면
            # 입금 취소일이 −50% 로 읽혀 그날 매수가 모두 막혔고, 입금일엔 진짜 −10% 가 +90% 로 가려졌다
            equity_krw=v.equity_krw,
            day_return_pct=(검["twr"] / 검["prev_twr"] - 1) * 100 if 검["prev_twr"] else None,
            cash_available_krw=v.cash_krw - (예약 if 예약 is not None else v.cash_krw),
            holding_krw={int(r["stock_id"]): float(r["value_krw"]) for r in v.rows},
            sector_of={sid: _stock(client, sid, stock_cache).get("sector")
                       for sid in {int(r["stock_id"]) for r in v.rows}},
            max_stock_pct=float(상한.get("max_weight_per_stock") or 10.0),
            max_sector_pct=float(상한.get("max_weight_per_sector") or 30.0),
            today=cal.local_today(market).isoformat(), halted=멈춤, frozen=frozenset(frozen),
        )  # fmt: skip
        if int(client.execute(ORDERS_EXIST_SQL, [aid, market, trade_date] * 2).scalar() or 0) > 0:
            a기록["orders"] = "이미 냄"
        else:
            판정메모: dict[int, dict[str, Any]] = {}
            매도 = sell_decisions(client, market, 규칙, 계좌체결, frozen, stock_cache, targets,
                               cal.local_today(market), 보강캐시, 판정메모)  # fmt: skip
            이미팖 = {o.stock_id for o in 열린 if o.account_id == aid and o.side == "sell"}
            보유 = pp.positions(계좌체결)
            문장: list[tuple[str, list[Any]]] = []
            _통과, 막힌매도 = rg.gate([rg.Candidate(sid, "sell", 0.0) for sid in sorted(매도)], 문맥)
            막힌매도id = {c.stock_id: 까닭 for c, 까닭 in 막힌매도}
            막힌수 = len(막힌매도)
            for sid, (사유, 근거) in sorted(매도.items()):
                if sid in 이미팖 or sid not in 보유 or sid in 막힌매도id:
                    continue
                문장.append((INSERT_ORDER_SQL, [
                    aid, market, sid, "sell", "market", None, 보유[sid].quantity, 보유[sid].currency, trade_date,
                    다음장, 기준, 0, ",".join(사유), None, None, None,
                    json.dumps({"flags": 근거, "sell_rule": 규칙}, ensure_ascii=False, default=str), 판, 지금,
                ]))  # fmt: skip
            a기록["sells"] = len(문장)  # 이 자리까지는 매도 주문만 있다
            # 결정 원장 (25.1449) — 판 것과, 플래그가 섰는데 계좌 규칙이 따르지 않아 들고 간 것(B 의 황색)
            for sid, 메모 in sorted(판정메모.items()):
                종가, 날 = 메모["ref"]
                if sid in 막힌매도id:
                    # 위험 점검이 매도를 막았다 — 들고 간 것으로 남긴다 (25.1451)
                    문장.append(_결정(aid, market, trade_date, sid, "blocked",
                                    "위험 점검(매도): " + "; ".join(t for _c, t in 막힌매도id[sid]),
                                    {"flags": [f["reason_code"] for f in 메모["flags"]]}, 종가, 날, 지금))  # fmt: skip
                elif sid in 매도 and sid not in 이미팖:
                    문장.append(_결정(aid, market, trade_date, sid, "sell", ",".join(매도[sid][0]),
                                    {"flags": [f["reason_code"] for f in 메모["flags"]]}, 종가, 날, 지금))  # fmt: skip
                if 메모["hold"]:
                    문장.append(_결정(aid, market, trade_date, sid, "hold_flag", ",".join(메모["hold"]),
                                    {"flags": [f["reason_code"] for f in 메모["flags"]], "sell_rule": 규칙},
                                    종가, 날, 지금))  # fmt: skip
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
                # 사전 위험 점검 (25.1451) — 통과한 회차만 주문. 한 회차도 못 지난 종목은 결정 원장에서 blocked 로
                후보 = [rg.Candidate(o["stock_id"], "buy", o["cost_krw"], o["ref_date"], i) for i, o in enumerate(주문)]
                # 후보 종목의 업종도 — 보유만 채우면 새 종목은 업종이 비어 섹터 상한이 꺼졌다 (25.1453, 검증 [필수] 3)
                문맥 = dc_replace(문맥, sector_of={**문맥.sector_of, **{
                    c.stock_id: _stock(client, c.stock_id, stock_cache).get("sector") for c in 후보}})
                통과, 막힘 = rg.gate(후보, 문맥)
                막힌수 += len(막힘)
                주문 = [주문[c.key] for c in 통과]
                낸종목 = {o["stock_id"] for o in 주문}
                막힌까닭 = {c.stock_id: "위험 점검: " + "; ".join(t for _c, t in 까닭) for c, 까닭 in 막힘}
                for d in b기록.pop("_decisions", []):
                    if d["kind"] == "buy" and d["stock_id"] not in 낸종목:
                        # 규칙은 사려 했는데 관문이 막았다 — skip 과 섞지 않는다 (25.1453)
                        d = {**d, "kind": "blocked", "reason": 막힌까닭.get(d["stock_id"], "위험 점검")}
                    문장.append(_결정(aid, market, trade_date, d["stock_id"], d["kind"], d["reason"], d["detail"],
                                    d["ref_close"], d["ref_date"], 지금))  # fmt: skip
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
            a기록["risk_blocked"] = 막힌수
            a기록["orders"] = sum(1 for sql, _a in 문장 if sql == INSERT_ORDER_SQL)
            a기록["decisions"] = sum(1 for sql, _a in 문장 if sql == DECISION_INSERT_SQL)
        계좌기록[aid] = a기록
        손익률 = f"{v.pnl_pct:+.1f}%" if v.pnl_pct is not None else "—"
        줄.append(f"{aid}: 평가 {v.equity_krw:,.0f}원 (손익 {손익률}, 시간가중 {(검['twr'] - 1) * 100:+.1f}%,"
                 f" 오늘 체결 {a기록['fills']}건)")  # fmt: skip
        if not 검["recon_ok"]:
            줄.append(f"⚠️ 모의 {aid} 장부 검산 어긋남 — {검['recon_note']}")
    기록["accounts"] = 계좌기록
    기록["frozen"] = sorted(frozen)
    # 결정 원장 성과 채우기 (25.1449) — 곁다리. 실패해도 오늘 단계는 그대로
    try:
        기록["decision_outcomes"] = fill_outcomes(client, market, 오늘, 지금)
    except Exception as exc:  # noqa: BLE001 — 성과 채우기만 빠진다. 까닭은 기록에
        기록["decision_outcomes"] = {"error": str(exc)[:200]}
    # 성과 분해 (25.1450) — 계좌마다 오늘 평가 행에. 곁다리
    try:
        for acct in accounts:
            a = save_attribution(client, str(acct["id"]), 오늘)
            기록.setdefault("attribution_n", {})[str(acct["id"])] = a["selection"]["n"]
    except Exception as exc:  # noqa: BLE001 — 분해만 빠진다
        기록["attribution_error"] = str(exc)[:200]
    if 읽기0 is not None:
        기록["reads"] = getattr(client.reads, "total", 0) - 읽기0
    머리 = ["🧪 모의 투자 " + " · ".join(글 for 글 in 줄 if not 글.startswith("⚠️"))]
    # 실제 투자와 같은 기간 비교 (2026-10-15 사용자 지시, 25.1448) — 곁다리. 못 읽으면 그 줄만 빠진다
    try:
        비교 = real_comparison(client)
    except Exception as exc:  # noqa: BLE001 — 비교 줄만 빠진다. 까닭은 기록에
        비교 = None
        기록["real_compare_error"] = str(exc)[:200]
    if 비교:
        기록["real_compare"] = 비교
        머리.append(str(pp.compare_line(비교)))
    if frozen:
        머리.append(f"모의 계좌 멈춘 종목 {len(frozen)}개(기업행위 경계 — 원가로 평가, docs/paper.md 2.3)")
    return 머리 + [글 for 글 in 줄 if 글.startswith("⚠️")], 기록


PAPER_SUMMARY_SQL = (
    "SELECT account_id, as_of, invested_krw, equity_krw, pnl_krw, twr_index, kospi_krw, sp500_krw, positions_json,"
    " attribution_json FROM paper_daily WHERE as_of >= ? ORDER BY account_id, as_of"
)
#: 월간 복기 — 한국 날짜로 그달 1~7일에 보내는 주간 요약이 지난달을 돌아본다 (docs/paper.md 6장)
MONTHLY_REVIEW_DAYS = 7


def summary_section(client: Any, now: datetime) -> list[str]:
    """주간 운영 요약의 모의 계좌 절 (docs/paper.md 6장). 입금이 없으면 빈 목록."""
    오늘 = now.astimezone(cal.market_tz("KR")).date()
    주초 = (오늘 - timedelta(days=7)).isoformat()
    달초 = ((오늘.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
            if 오늘.day <= MONTHLY_REVIEW_DAYS else None)  # fmt: skip
    시작 = min(x for x in ((오늘 - timedelta(days=14)).isoformat(), 달초) if x)
    rows = client.execute(PAPER_SUMMARY_SQL, [시작]).dicts()
    if not rows:
        return []
    daily: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        daily.setdefault(str(r["account_id"]), []).append(r)
    fills, _ = _fills(client)
    by_acct: dict[str, list[pp.Fill]] = {}
    for f in fills:
        by_acct.setdefault(f.account_id, []).append(f)
    캐시: dict[int, dict[str, Any]] = {}
    보유id = {int(p["stock_id"]) for r in rows for p in json.loads(r.get("positions_json") or "[]")}
    for sid in {f.stock_id for f in fills} | 보유id:
        _stock(client, sid, 캐시)
    rules, _ = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
    손절 = {h: {**sf.DEFAULT_TARGETS[h], **((rules or {}).get(h) or {})}["stop_pct"] for h in sf.DEFAULT_TARGETS}
    limits = {
        "max_stock_pct": db.get_setting_in_range(client, "max_weight_per_stock", 10.0)[0],
        "max_sector_pct": db.get_setting_in_range(client, "max_weight_per_sector", 30.0)[0],
        "stop_pct": 손절,
    }
    줄 = pp.summary_lines(daily, by_acct, 주초, 달초, {k: str(v.get("name")) for k, v in 캐시.items()},
                         {k: v.get("sector") for k, v in 캐시.items()}, limits)  # fmt: skip
    # 실제 투자와 같은 기간 비교 (25.1448) — 모의 절 바로 아래
    try:
        비교줄 = pp.compare_line(real_comparison(client))
    except Exception as exc:  # noqa: BLE001 — 비교 줄만 빠진다
        비교줄 = f"  실제 투자와 비교를 못 했습니다 ({type(exc).__name__})"
    if 줄 and 비교줄:
        자리 = next((i for i, x in enumerate(줄) if x.startswith("[모의 투자 월간")), len(줄))
        줄.insert(자리, "  " + 비교줄.strip())
    # 월간 복기에 성과 분해 (25.1450) — 계좌마다 마지막 평가 행의 것
    if 달초 and 줄:
        분해 = [x for aid in sorted(daily)
                for x in pp.attribution_lines(aid, json.loads(daily[aid][-1].get("attribution_json") or "null"))]
        if 분해:
            줄 += [f"[노하우 성적표 — {pp.ATTRIBUTION_HORIZON} 거래일 뒤, 지수 대비]", *분해]
    return 줄
