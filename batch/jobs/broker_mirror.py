"""내부 모의 계좌의 주문을 KIS 모의투자에 베껴 내고, 어제 것을 맞춘다 (docs/paper.md 15장, docs/infra.md 25.1475).

국내 일일 배치가 모의 단계(`jobs/paper.run_step`) 바로 뒤에 부른다. 곁다리다 — 실패해도 리포트·내부 모의는 그대로.
키(`KIS_VTS_*`)가 없으면 아무것도 하지 않고 `{"off": …}` 만 돌려준다.

1. 맞추기: 어제까지 낸 증권사 주문의 체결을 받아 마감(`services/broker_mirror.reconcile`)
2. 잔고: 증권사 모의 보유·예수금을 적고 내부 장부와 어긋난 종목을 센다(`paper_broker_daily`)
3. 보내기: 정리(증권사에만 남은 수량) → 오늘 체결 판정할 내부 주문(매도 → 매수). 줄을 먼저 `sending` 으로 적고 보낸다

계좌번호·수량·금액은 실행 기록에 넣지 않는다 — 건수와 까닭만
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from typing import Any

import requests

from batch.core import calendar as cal
from batch.core import db
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from batch.sources.kis import KisFailed

BROKER = kv.SOURCE
#: 베낄 내부 계좌 — 지금 시스템(아침 리포트 2부 그대로). 회사(A·C·D·E)로 바꾸는 것은 판 2 성적이 쌓인 뒤
#: (docs/paper.md 15장)
MIRROR_ACCOUNT = "B"
#: 장전 동시호가 접수 시작(08:30 KST) 전이면 이만큼까지만 기다린다. 더 이르면 보내지 않는다
#: — 모의 접수 시작 시각은 공식 자료에서 못 찾았다 `[확인필요]`
ORDER_OPEN_KST = dtime(8, 30)
WAIT_MAX = timedelta(minutes=10)
#: 종가 동시호가(15:20) 뒤에는 보내지 않는다 — 하루짜리 주문이 체결 기회 없이 끝난다
ORDER_CLOSE_KST = dtime(15, 20)

OPEN_ORDERS_SQL = (
    "SELECT id, stock_id, side, order_type, limit_price, quantity FROM paper_orders"
    " WHERE account_id = ? AND market = 'KR' AND status = 'open' AND fill_session = ? ORDER BY id"
)
SENT_SQL = "SELECT paper_order_id, stock_id FROM paper_broker_orders WHERE broker = ? AND trade_date = ?"
PENDING_SQL = (
    "SELECT id, code, side, quantity, trade_date, broker_order_no FROM paper_broker_orders"
    " WHERE broker = ? AND status IN ('sending', 'accepted') AND trade_date < ?"
)
#: 오늘 이미 낸 베낀 매수(거부 아닌 것) — 같은 날 다시 돌 때 정리가 오늘 산 것을 팔지 않게 (검증 [필수] 1)
BOUGHT_TODAY_SQL = (
    "SELECT stock_id, SUM(quantity) FROM paper_broker_orders WHERE broker = ? AND trade_date = ? AND side = 'buy'"
    " AND status <> 'rejected' GROUP BY stock_id"
)
OLD_SQL = (
    "UPDATE paper_broker_orders SET status = 'unknown', message = '3개월 지나 맞추지 못함', fetched_at = ?"
    " WHERE broker = ? AND status IN ('sending', 'accepted') AND trade_date < ?"
)
BOUGHT_SQL = (
    "SELECT DISTINCT stock_id FROM paper_broker_orders WHERE broker = ? AND side = 'buy' AND status <> 'rejected'"
)
INTERNAL_QTY_SQL = (
    "SELECT stock_id, SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) AS q FROM paper_fills"
    " WHERE account_id = ? GROUP BY stock_id"
)
ACCOUNT_SQL = "SELECT id FROM paper_accounts WHERE id = ?"
INSERT_SQL = (
    "INSERT OR IGNORE INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
    " order_type, limit_price, quantity, trade_date, reason, status, source, created_at)"
    " VALUES (?, ?, ?, 'KR', ?, ?, ?, ?, ?, ?, ?, ?, 'sending', ?, ?)"
)
#: 방금 적은 줄 — 같은 시각 도장(`created_at`)으로 내 줄인지 가린다(동시에 돈 다른 실행의 줄이면 보내지 않는다)
MINE_SQL = (
    "SELECT id FROM paper_broker_orders WHERE broker = ? AND trade_date = ? AND stock_id = ? AND side = ?"
    " AND COALESCE(paper_order_id, -1) = ? AND status = 'sending' AND created_at = ?"
)
SENT_UPDATE_SQL = (
    "UPDATE paper_broker_orders SET status = ?, broker_order_no = ?, sent_at = ?, message = ?, fetched_at = ?"
    " WHERE id = ?"
)
RECON_SQL = (
    "UPDATE paper_broker_orders SET status = ?, broker_order_no = COALESCE(?, broker_order_no), filled_qty = ?,"
    " avg_price = ?, message = COALESCE(?, message), fetched_at = ? WHERE id = ?"
)
DAILY_SQL = (
    "INSERT INTO paper_broker_daily (broker, as_of, account_id, cash_krw, total_krw, holdings_json, diff_json, source,"
    " fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (broker, as_of) DO UPDATE SET"
    " account_id = excluded.account_id, cash_krw = excluded.cash_krw, total_krw = excluded.total_krw,"
    " holdings_json = excluded.holdings_json, diff_json = excluded.diff_json, fetched_at = excluded.fetched_at"
)


CODES_SQL = "SELECT id, ticker FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
IDS_SQL = "SELECT id, ticker FROM stocks WHERE country = 'KR' AND ticker IN (SELECT value FROM json_each(?))"


def _codes(client: Any, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {int(i): str(t) for i, t in client.execute(CODES_SQL, [json.dumps(sorted(ids))]).rows if t}


def _ids(client: Any, codes: set[str]) -> dict[str, int]:
    if not codes:
        return {}
    return {str(t): int(i) for i, t in client.execute(IDS_SQL, [json.dumps(sorted(codes))]).rows}


def run(
    client: Any, market: str, now: datetime, *, session: Any = None, sleep: Any = time.sleep, clock: Any = None,
    frozen: set[int] | None = None,
) -> dict[str, Any]:
    """하루 한 번. 돌려주는 것은 실행 기록(`step_log.broker`)에 넣을 건수와 까닭. `clock` 은 보낸 시각(시험용)."""
    clock = clock or (lambda: datetime.now(UTC))
    if market != "KR":
        return {"off": "국내만 — 해외 모의 주문은 [확인필요]"}
    if not kv.configured():
        return {"off": "모의투자 키 없음"}
    if not client.execute(ACCOUNT_SQL, [MIRROR_ACCOUNT]).rows:
        return {"off": f"베낄 계좌 {MIRROR_ACCOUNT} 없음"}
    지금 = now.isoformat()
    현지 = now.astimezone(cal.market_tz("KR"))
    오늘 = 현지.date()
    sess = session or kv.Session(client, now)
    기록: dict[str, Any] = {}

    # 1. 맞추기 — 어제까지 낸 하루짜리 주문은 끝났다. 체결 조회는 3개월 안만 되므로 그보다 오래된 줄은 모름으로 닫는다
    한계 = (오늘 - timedelta(days=kv.CCLD_MAX_DAYS)).isoformat()
    client.execute(OLD_SQL, [지금, BROKER, 한계])
    남은 = client.execute(PENDING_SQL, [BROKER, 오늘.isoformat()]).dicts()
    if 남은:
        시작 = min(str(r["trade_date"]) for r in 남은).replace("-", "")
        끝 = (오늘 - timedelta(days=1)).strftime("%Y%m%d")
        체결, 다받음 = sess.executions(시작, 끝)
        결과 = bm.reconcile(남은, 체결, complete=다받음)
        client.batch([(RECON_SQL, [o.status, o.order_no, o.filled, o.avg_price, o.message, 지금, o.row_id])
                      for o in 결과])  # fmt: skip
        기록["reconciled"] = dict(Counter(o.status for o in 결과))
        if not 다받음:
            기록["reconcile_truncated"] = True

    # 2. 잔고 — 내부 장부와 대 본다
    보유, 합 = sess.balance()
    코드표 = _ids(client, {h.code for h in 보유})
    증권 = {코드표[h.code]: (h.quantity, h.sellable) for h in 보유 if h.code in 코드표}
    내부 = {int(r["stock_id"]): int(r["q"]) for r in client.execute(INTERNAL_QTY_SQL, [MIRROR_ACCOUNT]).dicts()
            if int(r["q"] or 0) > 0}  # fmt: skip
    어긋남 = bm.holdings_diff({k: v[0] for k, v in 증권.items()}, 내부)
    client.execute(DAILY_SQL, [
        BROKER, 오늘.isoformat(), MIRROR_ACCOUNT, 합.get("cash"), 합.get("total"),
        json.dumps([[코드표.get(h.code, h.code), h.quantity, h.avg_price] for h in 보유]),
        json.dumps({str(k): list(v) for k, v in 어긋남.items()}), BROKER, 지금,
    ])  # fmt: skip
    기록["holdings"] = len(보유)
    기록["diff"] = len(어긋남)
    if len(코드표) < len({h.code for h in 보유}):
        기록["unknown_codes"] = len({h.code for h in 보유}) - len(코드표)

    # 3. 보내기
    if not cal.is_session("KR", 오늘):
        기록["send"] = "오늘은 장이 없다"
        return 기록 | {"calls": sess.calls}
    if 현지.time() >= ORDER_CLOSE_KST:
        기록["send"] = "장 끝난 뒤 — 보내지 않음"
        return 기록 | {"calls": sess.calls}
    if 현지.time() < ORDER_OPEN_KST:
        기다림 = datetime.combine(오늘, ORDER_OPEN_KST, tzinfo=현지.tzinfo) - 현지
        if 기다림 > WAIT_MAX:
            기록["send"] = "접수 시작 전 — 보내지 않음"
            return 기록 | {"calls": sess.calls}
        sleep(기다림.total_seconds() + 5)
    멈춤값 = db.get_setting(client, "paper_halt", None)
    멈춤 = bool(멈춤값.get("on")) if isinstance(멈춤값, dict) else bool(멈춤값)
    주문들 = client.execute(OPEN_ORDERS_SQL, [MIRROR_ACCOUNT, 오늘.isoformat()]).dicts()
    보냄 = client.execute(SENT_SQL, [BROKER, 오늘.isoformat()]).dicts()
    이미 = {int(r["paper_order_id"]) for r in 보냄 if r["paper_order_id"] is not None}
    정리함 = {int(r["stock_id"]) for r in 보냄 if r["paper_order_id"] is None}
    산적 = {int(r[0]) for r in client.execute(BOUGHT_SQL, [BROKER]).rows}
    코드 = _codes(client, {int(o["stock_id"]) for o in 주문들} | set(증권))
    오늘산 = {int(r[0]): int(r[1] or 0) for r in client.execute(BOUGHT_TODAY_SQL, [BROKER, 오늘.isoformat()]).rows}
    낼것, 안냄 = bm.plan(주문들, 코드, 내부, 증권, 산적, already=이미, cleaned_today=정리함, halted=멈춤,
                       bought_today=오늘산, frozen=frozen)  # fmt: skip
    상태: Counter[str] = Counter()
    for it in 낼것:
        client.execute(INSERT_SQL, [
            BROKER, it.paper_order_id, MIRROR_ACCOUNT, it.stock_id, it.code, it.side,
            "market" if it.price is None else "limit", it.price, it.quantity, 오늘.isoformat(), it.reason, BROKER, 지금,
        ])  # fmt: skip
        내것 = -1 if it.paper_order_id is None else it.paper_order_id
        mine = client.execute(MINE_SQL, [BROKER, 오늘.isoformat(), it.stock_id, it.side, 내것, 지금]).rows
        if not mine:
            상태["skipped_other_run"] += 1
            continue
        rid = int(mine[0][0])
        보낸시각 = clock().isoformat()
        try:
            받음 = sess.order(it.side, it.code, it.quantity, it.price)
            client.execute(SENT_UPDATE_SQL, ["accepted", 받음.order_no, 보낸시각, None, 보낸시각, rid])
            상태["accepted"] += 1
        except (kv.NoResponse, requests.RequestException) as exc:
            # 갔는지 모른다 — 다시 보내지 않는다(두 번 주문). 다음 날 체결 조회로 맞춘다. `requests` 예외는 세션이
            # 이미 이름만 남긴 `NoResponse` 로 바꾸지만, 바뀌지 않은 것이 와도 문장(주소)을 싣지 않는다
            글 = str(exc)[:200] if isinstance(exc, kv.NoResponse) else f"응답 없음 ({type(exc).__name__})"
            client.execute(SENT_UPDATE_SQL, ["sending", None, 보낸시각, 글, None, rid])
            상태["no_response"] += 1
        except KisFailed as exc:
            # KIS 가 오류 코드를 준 거부 — 받아들여지지 않았다
            client.execute(SENT_UPDATE_SQL, ["rejected", None, 보낸시각, str(exc)[:200], 보낸시각, rid])
            상태["rejected"] += 1
    기록["sent"] = dict(상태)
    기록["planned"] = {"cleanup": sum(x.reason == bm.REASON_CLEANUP for x in 낼것),
                     "mirror": sum(x.reason == bm.REASON_MIRROR for x in 낼것)}  # fmt: skip
    if 안냄:
        기록["not_sent"] = dict(Counter(까닭 for _o, _s, 까닭 in 안냄))
    기록["halted"] = 멈춤
    return 기록 | {"calls": sess.calls}
