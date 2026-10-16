"""내부 모의 계좌의 주문을 KIS 모의투자에 베껴 내고, 어제 것을 맞춘다 (docs/paper.md 15장, docs/infra.md 25.1475).

국내·미국 일일 배치가 리포트를 보낸 뒤 부른다. 곁다리다 — 실패해도 리포트·내부 모의는 그대로.
키(`KIS_VTS_*`)가 없으면 아무것도 하지 않고 `{"off": …}` 만 돌려준다.
미국(2026-10-16 사용자 지시 "국내+미국 계좌로 개설했으니까 미국주식도 투자하는걸로")은 **예약주문**으로 낸다 —
미국 배치는 개장 약 1시간 전이라 정규 주문을 못 낸다. 시장가가 없어 시장가 주문은 종가 ± `bm.US_MARKETABLE_PCT`
지정가. 줄은 `broker='kis_vts_us'` 로 국내와 가른다(잔고 표의 키가 (broker, as_of) 라 같은 날 두 시장이 겹치지 않게)

1. 맞추기: 어제까지 낸 증권사 주문의 체결을 받아 마감(`services/broker_mirror.reconcile`)
2. 잔고: 증권사 모의 보유·예수금을 적고 내부 장부와 어긋난 종목을 센다(`paper_broker_daily`)
3. 보내기: 정리(증권사에만 남은 수량) → 오늘 체결 판정할 내부 주문(매도 → 매수). 줄을 먼저 `sending` 으로 적고 보낸다

계좌번호·수량·금액은 실행 기록에 넣지 않는다 — 건수와 까닭만
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
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
#: 미국 줄의 증권사 칸 — 같은 모의 계좌지만 국내와 날짜·잔고·비교를 따로 센다
BROKER_US = "kis_vts_us"
#: 베낄 가상 계좌 — **H(지주사, 성적 가중 합의)** (2026-10-16 사용자 결정 "KIS모의투자 연결도 바로 H로 진행해줘",
#: docs/company.md 10장). 처음 설계는 B(25.1475). H 가 아직 없는 DB(0083 전)에서는 "베낄 계좌 없음" 으로
#: 꺼진 채 지나간다
MIRROR_ACCOUNT = "H"


@dataclass(frozen=True)
class Slot:
    """모의 계좌 하나 — 증권사 칸 이름(국내·미국)·키 이름·글 (docs/paper.md 15.10, 25.1515)"""

    broker: str
    broker_us: str
    keys: kv.Keys
    label: str


#: 첫째 — 챔피언 `MIRROR_ACCOUNT` 를 베낀다
MAIN = Slot(BROKER, BROKER_US, kv.MAIN, "KIS모의투자")
#: 둘째 — 도전자(설정 `kis_challenger`, /kis 화면에서 고른다)를 베낀다. 키가 없으면(둘째 모의 계좌를 안 열었으면) 꺼짐.
#: 챔피언 교체 판단(docs/desk.md 6장)에 도전자의 **실제 주문 경로 체결**을 대 보고,
#: 챔피언이 안 쓰는 칸(시장가 매수 등)을 재어 체결 보정(desk 8장)의 "안 잰 회전" 을 줄인다
CHALLENGER = Slot("kis_vts2", "kis_vts2_us", kv.CHALLENGER, "KIS모의투자 도전자")


def challenger_account(client: Any) -> str | None:
    """설정 `kis_challenger` = {"account": "C", "at": …} 의 계좌. 없거나 비면 None"""
    v = db.get_setting(client, "kis_challenger", None)
    a = v.get("account") if isinstance(v, dict) else None
    return str(a) if a else None
#: 장전 동시호가 접수 시작(08:30 KST) 전이면 이만큼까지만 기다린다. 더 이르면 보내지 않는다
#: — 모의 접수 시작 시각은 공식 자료에서 못 찾았다 `[확인필요]`
ORDER_OPEN_KST = dtime(8, 30)
WAIT_MAX = timedelta(minutes=10)
#: 종가 동시호가(15:20) 뒤에는 보내지 않는다 — 하루짜리 주문이 체결 기회 없이 끝난다
ORDER_CLOSE_KST = dtime(15, 20)
#: 미국 예약주문 접수 시간(KST) — 10:00 부터 정규장 시작 10분 전까지(23:20, 서머타임 22:20), 16:30~16:45 정산 시간 제외
#: (공식 예제 order_resv.py 주석). 마감은 캘린더의 개장 시각에서 빼서 서머타임·반일장을 따른다
US_RESV_OPEN_KST = dtime(10, 0)
US_RESV_CUTOFF = timedelta(minutes=10)
US_RESV_BREAK_KST = (dtime(16, 30), dtime(16, 45))

OPEN_ORDERS_SQL = (
    "SELECT id, stock_id, side, order_type, limit_price, quantity FROM paper_orders"
    " WHERE account_id = ? AND market = ? AND status = 'open' AND fill_session = ? ORDER BY id"
)
SENT_SQL = "SELECT paper_order_id, stock_id FROM paper_broker_orders WHERE broker = ? AND trade_date = ?"
PENDING_SQL = (
    "SELECT id, code, side, quantity, trade_date, broker_order_no, limit_price FROM paper_broker_orders"
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
#: 그 시장 종목만 — 시장마다 따로 대 본다. 국내만 보던 때 미국 보유가 늘 "어긋남" 으로 남았다 (25.1483, 감사 [권장] 1)
INTERNAL_QTY_SQL = (
    "SELECT f.stock_id, SUM(CASE WHEN f.side = 'buy' THEN f.quantity ELSE -f.quantity END) AS q FROM paper_fills f"
    " JOIN stocks s ON s.id = f.stock_id WHERE f.account_id = ? AND s.country = ? GROUP BY f.stock_id"
)
ACCOUNT_SQL = "SELECT id FROM paper_accounts WHERE id = ?"
INSERT_SQL = (
    "INSERT OR IGNORE INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
    " order_type, limit_price, quantity, trade_date, reason, status, source, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sending', ?, ?)"
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
    " fetched_at, summary_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (broker, as_of) DO UPDATE SET"
    " account_id = excluded.account_id, cash_krw = excluded.cash_krw, total_krw = excluded.total_krw,"
    " holdings_json = excluded.holdings_json, diff_json = excluded.diff_json, fetched_at = excluded.fetched_at,"
    " summary_json = excluded.summary_json"
)
#: 비교 보고 (docs/paper.md 15.3, 25.1478) — 지난 `COMPARE_DAYS` 일의 베낀 주문에 가상모의투자의 판정을 붙인다
COMPARE_SQL = (
    "SELECT b.trade_date, b.side, b.status, b.filled_qty, b.quantity, b.avg_price, b.message, b.reason,"
    " o.status AS i_status, f.price AS i_price, f.fill_date AS i_fill_date FROM paper_broker_orders b"
    " LEFT JOIN paper_orders o ON o.id = b.paper_order_id LEFT JOIN paper_fills f ON f.order_id = b.paper_order_id"
    " WHERE b.broker = ? AND b.trade_date >= ? AND b.trade_date < ?"
)
#: 체결 칸 (docs/paper.md 15.9, 25.1514) — 베끼기 시작부터 누적. 내부 주문 유형·판정 잣대·장부 슬리피지(%)를 붙인다.
#: 증권사 주문 표의 order_type 은 미국 시장가를 limit 으로 적으므로 **내부** 주문 유형을 쓴다
CELLS_SQL = (
    "SELECT b.trade_date, b.side, b.status, b.filled_qty, b.avg_price, b.reason, o.status AS i_status,"
    " o.order_type AS i_order_type, o.fill_basis AS i_fill_basis, f.price AS i_price, f.fill_date AS i_fill_date,"
    " CASE WHEN f.price > 0 AND f.quantity > 0 THEN f.slippage / (f.price * f.quantity) * 100 END AS i_slip_pct"
    " FROM paper_broker_orders b LEFT JOIN paper_orders o ON o.id = b.paper_order_id"
    " LEFT JOIN paper_fills f ON f.order_id = b.paper_order_id WHERE b.broker = ? AND b.trade_date < ?"
)
#: 성과 계열 — 국내 배치가 적는 **계좌 전체 원화 총액**(`summary_json.account_krw.total_krw`, 25.1497).
#: 오늘 줄은 따로 붙인다
TOTALS_SQL = (
    "SELECT as_of, json_extract(summary_json, '$.account_krw.total_krw') FROM paper_broker_daily"
    " WHERE broker = ? AND as_of < ? ORDER BY as_of"
)
#: 같은 기간 H 의 (시간가중 지수, 평가손익) — 시작일 이하 마지막 · 오늘 이하 마지막
H_TWR_SQL = (
    "SELECT twr_index, pnl_krw FROM paper_daily WHERE account_id = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1"
)
#: 주간 요약이 읽는 마지막 잔고 줄
LATEST_DAILY_SQL = (
    "SELECT as_of, diff_json, summary_json FROM paper_broker_daily WHERE broker = ? ORDER BY as_of DESC LIMIT 1"
)


CODES_SQL = "SELECT id, ticker, market FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
IDS_SQL = "SELECT id, ticker FROM stocks WHERE country = ? AND ticker IN (SELECT value FROM json_each(?))"
#: 미국 시장가 대신 낼 지정가의 바탕 — 오늘(현지) 앞 마지막 종가. 배치는 개장 전이라 어제 종가다
CLOSES_SQL = (
    "SELECT j.value, (SELECT p.close FROM prices p WHERE p.stock_id = j.value AND p.date < ? AND p.close > 0"
    " ORDER BY p.date DESC LIMIT 1) FROM json_each(?) j"
)


def _codes(client: Any, ids: set[int]) -> dict[int, tuple[str, str]]:
    """{종목 id: (티커, stocks.market)}."""
    if not ids:
        return {}
    return {int(i): (str(t), str(m or "")) for i, t, m in client.execute(CODES_SQL, [json.dumps(sorted(ids))]).rows
            if t}  # fmt: skip


def _ids(client: Any, codes: set[str], country: str) -> dict[str, int]:
    if not codes:
        return {}
    return {str(t): int(i) for i, t in client.execute(IDS_SQL, [country, json.dumps(sorted(codes))]).rows}


def _closes(client: Any, ids: set[int], before: str) -> dict[int, float]:
    if not ids:
        return {}
    return {int(i): float(c) for i, c in client.execute(CLOSES_SQL, [before, json.dumps(sorted(ids))]).rows if c}


def _us_window(now: datetime, day: Any) -> str | None:
    """미국 예약주문을 지금 낼 수 없으면 그 까닭, 되면 None."""
    kst = now.astimezone(cal.market_tz("KR"))
    개장 = cal.session_open_utc("US", day)
    if 개장 is None:
        return "오늘은 장이 없다"
    if now >= 개장 - US_RESV_CUTOFF:
        return "예약 접수 마감 뒤 — 보내지 않음"
    if kst.time() < US_RESV_OPEN_KST:
        return "예약 접수 시작 전 — 보내지 않음"
    if US_RESV_BREAK_KST[0] <= kst.time() < US_RESV_BREAK_KST[1]:
        return "예약 접수 정산 시간 — 보내지 않음"
    return None


FX_SQL = "SELECT rate FROM fx_rates WHERE pair = 'USDKRW' AND date <= ? AND date >= ? ORDER BY date DESC LIMIT 1"


def _account_perf(client: Any, sess: Any, 증권사: str, 오늘: str, 합: dict[str, Any], 계좌: str,
                  ) -> tuple[dict[str, Any], dict[str, Any] | None]:  # fmt: skip
    """국내 배치의 계좌 전체 원화 총액과 성과 (docs/paper.md 15.7, 25.1497·25.1499).

    총액 = 국내 총평가 + (미국 보유 외화평가 합 + 달러 예수금) × KIS 최초고시환율. 고시 전이라 환율이 0·없으면 —
    미국 몫이 0 이면 환율이 상관없고, 아니면 우리 환율 표(7일 안)로 갈음한다(검토 [권장] 2). 한 조각이라도 모르면
    그날 총액은 모름
    """
    try:
        us = kv.us_totals(sess.present_us())
    except (KisFailed, requests.RequestException):
        us = {"value_usd": None, "cash_usd": None, "usd_krw": None}
    값, 현금, 환율 = us["value_usd"], us["cash_usd"], us["usd_krw"]
    if 값 is not None and 현금 is not None and not 환율:
        if 값 == 0 and 현금 == 0:
            환율 = 0.0
        else:
            from datetime import date as _date

            일주 = (_date.fromisoformat(오늘) - timedelta(days=7)).isoformat()
            줄 = client.execute(FX_SQL, [오늘, 일주]).rows
            환율 = float(줄[0][0]) if 줄 and 줄[0][0] else None
            us = us | {"usd_krw_source": "fx_rates"}
    조각 = [합.get("total"), 값, 현금, 환율]
    총 = None if any(x is None for x in 조각) else float(합["total"]) + (float(값) + float(현금)) * float(환율)  # type: ignore[arg-type]
    원화계좌 = us | {"kr_total": 합.get("total"), "total_krw": 총}
    계열 = [(str(d), v) for d, v in client.execute(TOTALS_SQL, [증권사, 오늘]).rows] + [(오늘, 총)]
    시작 = bm.perf(계열)
    if not 시작:
        return 원화계좌, None
    h0 = client.execute(H_TWR_SQL, [계좌, 시작["start"]]).rows
    h1 = client.execute(H_TWR_SQL, [계좌, 오늘]).rows
    return 원화계좌, bm.perf(계열, tuple(h0[0]) if h0 else None, tuple(h1[0]) if h1 else None)  # type: ignore[arg-type]


def run(
    client: Any, market: str, now: datetime, *, session: Any = None, sleep: Any = time.sleep, clock: Any = None,
    frozen: set[int] | None = None, slot: Slot = MAIN,
) -> dict[str, Any]:
    """하루 한 번(시장마다). 돌려주는 것은 실행 기록(`step_log.broker`)에 넣을 건수와 까닭.

    `clock` 은 보낸 시각(시험용). `slot` = 어느 모의 계좌(첫째 — 챔피언, 둘째 — 도전자, 25.1515)
    """
    clock = clock or (lambda: datetime.now(UTC))
    if market not in ("KR", "US"):
        return {"off": f"모르는 시장 {market}"}
    if not kv.configured(keys=slot.keys):
        빈칸 = kv.missing(keys=slot.keys)
        if len(빈칸) < 3:
            # 일부만 들어온 것은 연결하려다 이름을 틀린 것이다 — 조용히 꺼지면 모른다(2026-10-16 시험 주문에서
            # 앱 시크릿 이름이 달라 하루를 꺼진 채 지날 뻔했다, 25.1491·25.1492). 일일 배치가 알림을 보낸다
            return {"off": f"{slot.label} 키 일부만 있음", "missing": 빈칸}
        return {"off": f"{slot.label} 키 없음"}
    if slot == MAIN:
        베낌 = MIRROR_ACCOUNT
    else:
        # 도전자 — 고르지 않았거나 챔피언과 같으면 꺼짐. 바꾸면 그 전 도전자가 산 종목은 다음 실행의 정리 주문이 판다
        # (정리는 "이 증권사 칸에서 산 적 있고 지금 베끼는 계좌에 없는 종목", 15.2)
        베낌 = challenger_account(client) or ""
        if not 베낌:
            return {"off": "도전자 계좌를 고르지 않음(/kis)"}
        if 베낌 == MIRROR_ACCOUNT:
            return {"off": f"도전자 {베낌} 가 챔피언과 같음"}
        # 첫째와 같은 모의계좌면 두 베끼기의 잔고·체결 조회가 섞여 정리 주문이 남의 종목을 판다 — 설정 실수로 알린다
        if kv.account(keys=kv.CHALLENGER) == kv.account(keys=kv.MAIN):
            return {"off": f"{slot.label} 계좌가 첫째와 같음", "missing": ["KIS_VTS2_ACCOUNT(첫째와 같은 계좌)"]}
    if not client.execute(ACCOUNT_SQL, [베낌]).rows:
        return {"off": f"베낄 계좌 {베낌} 없음"}
    미국 = market == "US"
    증권사 = slot.broker_us if 미국 else slot.broker
    지금 = now.isoformat()
    현지 = now.astimezone(cal.market_tz(market))
    오늘 = 현지.date()
    sess = session or kv.Session(client, now, keys=slot.keys)
    기록: dict[str, Any] = {}

    # 1. 맞추기 — 어제까지 낸 하루짜리 주문은 끝났다. 체결 조회는 3개월 안만 되므로 그보다 오래된 줄은 모름으로 닫는다
    한계 = (오늘 - timedelta(days=kv.CCLD_MAX_DAYS)).isoformat()
    client.execute(OLD_SQL, [지금, 증권사, 한계])
    남은 = client.execute(PENDING_SQL, [증권사, 오늘.isoformat()]).dicts()
    if 남은:
        시작 = min(str(r["trade_date"]) for r in 남은).replace("-", "")
        끝 = (오늘 - timedelta(days=1)).strftime("%Y%m%d")
        체결, 다받음 = (sess.executions_us if 미국 else sess.executions)(시작, 끝)
        결과 = bm.reconcile(남은, 체결, complete=다받음, missing=bm.MISSING_US if 미국 else bm.MISSING)
        client.batch([(RECON_SQL, [o.status, o.order_no, o.filled, o.avg_price, o.message, 지금, o.row_id])
                      for o in 결과])  # fmt: skip
        기록["reconciled"] = dict(Counter(o.status for o in 결과))
        if not 다받음:
            기록["reconcile_truncated"] = True

    # 2. 잔고 — 내부 장부와 대 본다
    보유, 합 = sess.balance_us() if 미국 else sess.balance()
    코드표 = _ids(client, {h.code for h in 보유}, market)
    증권 = {코드표[h.code]: (h.quantity, h.sellable) for h in 보유 if h.code in 코드표}
    내부 = {int(r["stock_id"]): int(r["q"]) for r in client.execute(INTERNAL_QTY_SQL, [베낌, market]).dicts()
            if int(r["q"] or 0) > 0}  # fmt: skip
    # 어긋남은 **베낀 적 있는 종목**과 증권사 보유만 — 키를 넣기 전에 산 가상 보유는 "연결 전 보유" 로
    # 따로 센다(감사 [권장] 3). 같은 날 다시 돌면 시가에 찬 오늘 매수가 증권사엔 있고 가상 장부엔 다음 배치에야
    # 들어온다 — 그 수량을 뺀다(감사 [권장] 2)
    산적0 = {int(r[0]) for r in client.execute(BOUGHT_SQL, [증권사]).rows}
    오늘산0 = {int(r[0]): int(r[1] or 0)
              for r in client.execute(BOUGHT_TODAY_SQL, [증권사, 오늘.isoformat()]).rows}  # fmt: skip
    증권수 = {k: v[0] - 오늘산0.get(k, 0) for k, v in 증권.items()}
    연결전 = sorted(sid for sid in 내부 if sid not in 산적0 and sid not in 증권)
    어긋남 = bm.holdings_diff({k: q for k, q in 증권수.items() if q > 0},
                            {k: q for k, q in 내부.items() if k not in 연결전})  # fmt: skip
    부터 = (오늘 - timedelta(days=bm.COMPARE_DAYS)).isoformat()
    비교 = bm.compare(client.execute(COMPARE_SQL, [증권사, 부터, 오늘.isoformat()]).dicts())
    # 체결 칸(15.9, 25.1514) — 곁다리: 실패해도 비교 요약은 그대로
    try:
        비교 |= bm.cells(client.execute(CELLS_SQL, [증권사, 오늘.isoformat()]).dicts())
    except Exception as exc:  # noqa: BLE001
        기록["cells_error"] = type(exc).__name__
    # 웹 보유내역·성과(docs/paper.md 15.7, 25.1496·25.1497). 시장 칸은 그 시장 통화(국내 원 — 현금은 총평가와
    # 같은 D+2, 미국 달러). 성과는 **국내 배치가 계좌 전체 원화 총액**으로 낸다 — 미국 매수 돈이 원화에서 자동
    # 환전으로 나가 시장별 총액 비는 수익률이 아니다. 국내 배치 시각(08:30 KST)이면 두 시장 모두 전날 종가라 시점이
    # 맞다
    계좌 = {"currency": "USD" if 미국 else "KRW", "cash": 합.get("cash") if 미국 else 합.get("cash_d2", 합.get("cash")),
          "holdings_value": 합.get("holdings_value"), "total": 합.get("total")}  # fmt: skip
    원화계좌: dict[str, Any] | None = None
    성과 = None
    if not 미국:
        # 곁다리 안의 곁다리 — 여기서 무엇이 깨져도 아래 주문 보내기는 돈다(25.1499, 검토 [권장] 5)
        try:
            원화계좌, 성과 = _account_perf(client, sess, 증권사, 오늘.isoformat(), 합, 베낌)
        except Exception as exc:  # noqa: BLE001
            기록["perf_error"] = type(exc).__name__
    보유칸 = [{"id": 코드표.get(h.code), "code": h.code, "qty": h.quantity, "avg": h.avg_price, "price": h.price,
             "value": h.value, "pnl": h.pnl} for h in 보유]  # fmt: skip
    client.execute(DAILY_SQL, [
        증권사, 오늘.isoformat(), 베낌, None if 미국 else 합.get("cash"), None if 미국 else 합.get("total"),
        json.dumps(보유칸), json.dumps({str(k): list(v) for k, v in 어긋남.items()}), slot.keys.source, 지금,
        json.dumps(비교 | {"account": 계좌, "account_krw": 원화계좌, "perf": 성과}, ensure_ascii=False),
    ])  # fmt: skip
    기록["compare"] = {"n": 비교["n"], "match_pct": 비교["match_pct"], "kept_pct": 비교["kept_pct"]}
    # 괴리 알림 (docs/paper.md 15.8, 25.1504·25.1507) — 켜진 칸을 설정 `kis_diverge` 에 증권사마다
    # {active, sent} 로 두고, 켜졌는데 **아직 보내지 않은** 칸만 알린다. 보냈다는 표시는 일일 배치가 알림을 보낸 뒤
    # `mark_diverge_sent` 로 — 그 전에 실행이 죽거나 발송이 실패하면 다음 실행이 다시 알린다(검토 1),
    # 같은 날 다시 돌려도 두 번 가지 않는다(검토 4)
    # 도전자 계좌는 괴리 알림을 내지 않는다 — 재는 자일 뿐, 알림은 챔피언(실전으로 갈 전략)만(25.1515)
    try:
        if slot != MAIN:
            raise _건너뜀
        상태 = db.get_setting(client, "kis_diverge", None)
        상태 = 상태 if isinstance(상태, dict) else {}
        내상태 = 상태.get(증권사) if isinstance(상태.get(증권사), dict) else {}
        켜짐 = bm.diverge_active(비교, set(내상태.get("active") or []))
        보냄 = {k: d for k, d in (내상태.get("sent") or {}).items() if k in 켜짐}
        새 = sorted(켜짐 - set(보냄))
        상태[증권사] = {"active": sorted(켜짐), "sent": 보냄}
        db.set_setting(client, "kis_diverge", 상태)
        if 새:
            기록["diverge"] = bm.diverge(비교, 새, market)
            기록["diverge_keys"] = 새
    except _건너뜀:
        pass
    except Exception as exc:  # noqa: BLE001 — 알림 곁다리. 주문 보내기는 그대로
        기록["diverge_error"] = type(exc).__name__
    기록["holdings"] = len(보유)
    if 연결전:
        기록["before_link"] = len(연결전)
    기록["diff"] = len(어긋남)
    if len(코드표) < len({h.code for h in 보유}):
        기록["unknown_codes"] = len({h.code for h in 보유}) - len(코드표)

    # 3. 보내기
    if not cal.is_session(market, 오늘):
        기록["send"] = "오늘은 장이 없다"
        return 기록 | {"calls": sess.calls}
    if 미국:
        막힘 = _us_window(now, 오늘)
        if 막힘:
            기록["send"] = 막힘
            return 기록 | {"calls": sess.calls}
    elif 현지.time() >= ORDER_CLOSE_KST:
        기록["send"] = "장 끝난 뒤 — 보내지 않음"
        return 기록 | {"calls": sess.calls}
    elif 현지.time() < ORDER_OPEN_KST:
        기다림 = datetime.combine(오늘, ORDER_OPEN_KST, tzinfo=현지.tzinfo) - 현지
        if 기다림 > WAIT_MAX:
            기록["send"] = "접수 시작 전 — 보내지 않음"
            return 기록 | {"calls": sess.calls}
        sleep(기다림.total_seconds() + 5)
    멈춤값 = db.get_setting(client, "paper_halt", None)
    멈춤 = bool(멈춤값.get("on")) if isinstance(멈춤값, dict) else bool(멈춤값)
    주문들 = client.execute(OPEN_ORDERS_SQL, [베낌, market, 오늘.isoformat()]).dicts()
    보냄 = client.execute(SENT_SQL, [증권사, 오늘.isoformat()]).dicts()
    이미 = {int(r["paper_order_id"]) for r in 보냄 if r["paper_order_id"] is not None}
    정리함 = {int(r["stock_id"]) for r in 보냄 if r["paper_order_id"] is None}
    산적 = {int(r[0]) for r in client.execute(BOUGHT_SQL, [증권사]).rows}
    종목 = _codes(client, {int(o["stock_id"]) for o in 주문들} | set(증권))
    # 미국은 KIS 거래소 코드를 모르는 종목(Cboe BZX·IEX 상장 등)을 내지 않는다 — 코드 없음으로 빠진다
    거래소 = {sid: kv.US_EXCHANGE.get(m) for sid, (_t, m) in 종목.items()}
    코드 = {sid: t for sid, (t, _m) in 종목.items() if not 미국 or 거래소.get(sid)}
    종가 = _closes(client, set(코드), 오늘.isoformat()) if 미국 else None
    오늘산 = {int(r[0]): int(r[1] or 0) for r in client.execute(BOUGHT_TODAY_SQL, [증권사, 오늘.isoformat()]).rows}
    낼것, 안냄 = bm.plan(주문들, 코드, 내부, 증권, 산적, already=이미, cleaned_today=정리함, halted=멈춤,
                       bought_today=오늘산, frozen=frozen, currency="USD" if 미국 else "KRW",
                       closes=종가)  # fmt: skip
    상태: Counter[str] = Counter()
    for it in 낼것:
        # 미국은 늘 지정가(시장가 대신 종가 ± 폭) — 내부 주문이 시장가였으면 `limit_price` 가 그 폭 지정가다
        client.execute(INSERT_SQL, [
            증권사, it.paper_order_id, 베낌, market, it.stock_id, it.code, it.side,
            "market" if it.price is None else "limit", it.price, it.quantity, 오늘.isoformat(), it.reason,
            slot.keys.source, 지금,
        ])  # fmt: skip
        내것 = -1 if it.paper_order_id is None else it.paper_order_id
        mine = client.execute(MINE_SQL, [증권사, 오늘.isoformat(), it.stock_id, it.side, 내것, 지금]).rows
        if not mine:
            상태["skipped_other_run"] += 1
            continue
        rid = int(mine[0][0])
        보낸시각 = clock().isoformat()
        try:
            if 미국:
                # 예약번호는 정규 주문번호가 아니다(개장 때 따로 생긴다) — 글로만 남기고 맞추기는 날·종목·방향·수량으로
                받음 = sess.order_us(it.side, it.code, str(거래소[it.stock_id]), it.quantity, float(it.price or 0))
                글 = f"예약번호 {받음.order_no}"
                client.execute(SENT_UPDATE_SQL, ["accepted", None, 보낸시각, 글, 보낸시각, rid])
            else:
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


class _건너뜀(Exception):  # noqa: N818 — 흐름 표시
    """도전자 계좌의 괴리 알림을 건너뛴다"""


def summary_lines(client: Any) -> list[str]:
    """주간 요약의 KIS모의투자 절 — 배치가 마지막으로 낸 비교 요약을 시장마다 읽기만 한다.

    0078·0080 전이거나 줄이 없으면(키를 넣기 전) 빈 목록
    """
    out: list[str] = []
    for 증권사, 이름 in ((BROKER, MIRROR_ACCOUNT), (BROKER_US, f"{MIRROR_ACCOUNT} 미국")):
        rows = client.execute(LATEST_DAILY_SQL, [증권사]).dicts()
        if not rows or not rows[0].get("summary_json"):
            continue
        r = rows[0]
        out += bm.compare_lines(json.loads(r["summary_json"]), len(json.loads(r.get("diff_json") or "{}")),
                                str(r["as_of"]), 이름)  # fmt: skip
    return out


def mark_diverge_sent(client: Any, market: str, keys: list[str], day: str) -> None:
    """괴리 알림을 보낸 칸에 보낸 날을 적는다 (25.1507).

    다음 실행은 꺼졌다 다시 켜질 때까지 그 칸을 다시 알리지 않는다
    """
    증권사 = BROKER_US if market == "US" else BROKER
    상태 = db.get_setting(client, "kis_diverge", None)
    상태 = 상태 if isinstance(상태, dict) else {}
    내상태 = 상태.get(증권사) if isinstance(상태.get(증권사), dict) else {"active": list(keys), "sent": {}}
    내상태["sent"] = {**(내상태.get("sent") or {}), **{k: day for k in keys}}
    상태[증권사] = 내상태
    db.set_setting(client, "kis_diverge", 상태)
