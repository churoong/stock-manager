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
#: 오늘 이 증권사 칸에 이미 적은 줄 수(거부 포함) — 하루 주문 상한을 실행이 아니라 하루로 센다(25.1567)
SENT_TODAY_SQL = "SELECT COUNT(*) FROM paper_broker_orders WHERE broker = ? AND trade_date = ?"
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
#: 이 증권사 칸이 이 계좌를 베끼기 시작한 날 — "연결 전 보유" 를 가르는 잣대(25.1564)
LINK_DATE_SQL = "SELECT MIN(trade_date) FROM paper_broker_orders WHERE broker = ? AND account_id = ?"
#: 그날 **전** 체결로 든 가상 수량(연결 때 이미 들고 있던 몫)
HELD_BEFORE_SQL = (
    "SELECT f.stock_id, SUM(CASE WHEN f.side = 'buy' THEN f.quantity ELSE -f.quantity END) AS q FROM paper_fills f"
    " JOIN stocks s ON s.id = f.stock_id WHERE f.account_id = ? AND s.country = ? AND f.fill_date < ?"
    " GROUP BY f.stock_id"
)
#: 종목마다 마지막으로 산 계좌(정리 줄의 계좌, 25.1517) — id 순이라 뒤 줄이 이긴다
BOUGHT_BY_SQL = (
    "SELECT stock_id, account_id FROM paper_broker_orders WHERE broker = ? AND side = 'buy' AND status <> 'rejected'"
    " ORDER BY id"
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
    " o.status AS i_status, o.close_note AS i_note, o.order_type AS i_order_type, o.fill_basis AS i_fill_basis,"
    " f.price AS i_price, f.fill_date AS i_fill_date FROM paper_broker_orders b"
    " LEFT JOIN paper_orders o ON o.id = b.paper_order_id LEFT JOIN paper_fills f ON f.order_id = b.paper_order_id"
    " WHERE b.broker = ? AND b.trade_date >= ? AND b.trade_date < ? AND b.account_id = ?"
)
#: 체결 칸 (docs/paper.md 15.9, 25.1514) — 베끼기 시작부터 누적. 내부 주문 유형·판정 잣대·장부 슬리피지(%)를 붙인다.
#: 증권사 주문 표의 order_type 은 미국 시장가를 limit 으로 적으므로 **내부** 주문 유형을 쓴다
CELLS_SQL = (
    "SELECT b.trade_date, b.side, b.status, b.filled_qty, b.avg_price, b.reason, o.status AS i_status,"
    " o.close_note AS i_note,"
    " o.order_type AS i_order_type, o.fill_basis AS i_fill_basis, f.price AS i_price, f.fill_date AS i_fill_date,"
    " CASE WHEN f.price > 0 AND f.quantity > 0 THEN f.slippage / (f.price * f.quantity) * 100 END AS i_slip_pct"
    " FROM paper_broker_orders b LEFT JOIN paper_orders o ON o.id = b.paper_order_id"
    " LEFT JOIN paper_fills f ON f.order_id = b.paper_order_id"
    " WHERE b.broker = ? AND b.trade_date < ? AND b.account_id = ?"
)
#: 성과 계열 — 국내 배치가 적는 **계좌 전체 원화 총액**(`summary_json.account_krw.total_krw`, 25.1497).
#: 오늘 줄은 따로 붙인다. **같은 기준(`basis`)의 줄만** 잇는다(25.1527) — 기준이 바뀐 날 총액이 뛰어 가짜 수익·낙폭이
#: 영구히 남는다
TOTALS_SQL = (
    "SELECT as_of, json_extract(summary_json, '$.account_krw.total_krw') AS total,"
    " json_extract(summary_json, '$.account_krw.assets_krw') AS assets,"
    " json_extract(summary_json, '$.account_krw.cash_krw') AS cash,"
    " json_extract(summary_json, '$.account_krw.usd_krw') AS fx"
    " FROM paper_broker_daily"
    " WHERE broker = ? AND as_of < ? AND account_id = ? AND json_extract(summary_json, '$.account_krw.basis') = ?"
    " ORDER BY as_of"
)
#: 미국 일일 배치가 끝난 한국 날짜 — 그날 가상 평가 행을 미국 종가로 덮었다(25.1517).
#: H 견주기의 끝 후보(25.1516·25.1543).
#: 운용 화면 대보기(`jobs/desk`)도 이것을 쓴다
US_DONE_SQL = (
    "SELECT DISTINCT date(started_at, '+9 hours') AS d FROM batch_runs WHERE job_name = ?"
    " AND status IN ('success', 'partial') AND started_at >= ?"
)
#: 계좌 전체 원화 총액의 기준 (docs/paper.md 15.7, docs/infra.md 25.1527). 1(25.1497~25.1526) = 국내 총평가 + 미국
#: 보유 외화평가 합·달러 예수금 × 고시환율 — 모의 계좌에서 틀렸다: 체결기준현재잔고 보유 줄은 수량·평가가 모두 0 이고,
#: 미국 매수 돈은 원화 예수금이 아니라 **따로 있는 해외 자산**(2026-10-16 탐침 tot_asst_amt 341,636,000)에서 나가
#: 미국 몫이 통째로 빠졌다.
#: 2 = 국내 총평가 + KIS 해외 총자산 원화(`output3.tot_asst_amt` = 해외 주식 평가 + 해외 현금 원화 환산)
TOTAL_BASIS = 2
#: 같은 기간 H 의 (시간가중 지수, 평가손익) — 시작일 이하 마지막 · 오늘 이하 마지막
H_TWR_SQL = (
    "SELECT twr_index, pnl_krw FROM paper_daily WHERE account_id = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1"
)
#: 주간 요약이 읽는 마지막 잔고 줄
LATEST_DAILY_SQL = (
    "SELECT as_of, account_id, diff_json, summary_json FROM paper_broker_daily WHERE broker = ?"
    " ORDER BY as_of DESC LIMIT 1"
)


CODES_SQL = "SELECT id, ticker, market FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
#: 같은 티커가 두 행이면(거래소를 옮긴 종목 — 유일 키는 (ticker, market)) **이 증권사 칸에서 산 적 있는 행**, 그다음
#: 상장 중인 쪽, 그다음 새 행 — 순서 없이 읽으면 옛(상장폐지) 행에 KIS 보유가 붙어 어긋남이 거짓으로 서고 H 가 팔아도
#: KIS 는 영영 팔지 않았다(25.1605, KIS 미국 감사 [권고] 1). 상장 여부만 보면 H 가 이사 전 행을 든 채일 때 같은 해가
#: 방향만 바꿔 남았다 — KIS 보유는 그 주식을 산 주문의 id 에 붙는 것이 맞다(25.1608, 25.1607 교차검증 [필수]).
#: "산 적" 은 **찼거나 아직 맞추지 않은(오늘 낸)** 매수(25.1613 — 찬 것만 보면 오늘 처음 산 새 행이 다음 날 맞추기
#: 전까지 지고, 같은 날 다시 돌 때 옛 행에 붙은 잔고를 정리로 팔았다. 안 찬 매수까지 보면 H 가 든 옛 행 주식을 팔았다,
#: 25.1611). 여러 행이면 잔고를 **이 증권사에서 그 행에 찬 순매수**(`NET_SQL`)만큼 나눈다(`bm.split_holding`)
IDS_SQL = (
    "SELECT s.id, s.ticker FROM stocks s WHERE s.country = ? AND s.ticker IN (SELECT value FROM json_each(?))"
    " ORDER BY EXISTS (SELECT 1 FROM paper_broker_orders o WHERE o.broker = ? AND o.stock_id = s.id"
    " AND o.side = 'buy' AND (o.filled_qty > 0 OR o.status IN ('sending', 'accepted'))), (s.status = 'active'), s.id"
)
#: 행마다 이 증권사 쪽 순보유 — 찬 매수 − 찬 매도, 아직 맞추지 않은 매수는 낸 수량으로(그날 시가에 찼다고 본다 —
#: `BOUGHT_TODAY_SQL` 과 같은 가정). 내부 보유로 나누면 KIS 가 산 적 없는 행(거부·상한)이 이긴 행의 잔고를
#: 가져가 베낀 매도가 막혔다(25.1613, 25.1611 교차검증 [필수] 1)
NET_SQL = (
    "SELECT stock_id, SUM(CASE WHEN side = 'buy' THEN (CASE WHEN filled_qty > 0 THEN filled_qty"
    " WHEN status IN ('sending', 'accepted') THEN quantity ELSE 0 END) ELSE -COALESCE(filled_qty, 0) END)"
    " FROM paper_broker_orders WHERE broker = ? AND stock_id IN (SELECT value FROM json_each(?)) GROUP BY stock_id"
)
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


def _ids_all(client: Any, codes: set[str], country: str, broker: str) -> dict[str, list[int]]:
    """티커마다 그 행들 — 뒤에 오는 행(찬 매수 있음·상장 중·새 id)이 이긴다(마지막)."""
    if not codes:
        return {}
    out: dict[str, list[int]] = {}
    for i, t in client.execute(IDS_SQL, [country, json.dumps(sorted(codes)), broker]).rows:
        out.setdefault(str(t), []).append(int(i))
    return out


def _ids(client: Any, codes: set[str], country: str, broker: str) -> dict[str, int]:
    return {t: ids[-1] for t, ids in _ids_all(client, codes, country, broker).items()}


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




def _account_perf(client: Any, sess: Any, 증권사: str, 오늘: str, 합: dict[str, Any], 계좌: str,
                  ) -> tuple[dict[str, Any], dict[str, Any] | None]:  # fmt: skip
    """국내 배치의 계좌 전체 원화 총액과 성과 (docs/paper.md 15.7, 25.1497·25.1527).

    총액 = 국내 총평가 + KIS 해외 총자산 원화(`tot_asst_amt` — 해외 주식 평가 + 해외 현금). 모의 계좌는 미국 매수
    돈이 원화 예수금이 아니라 따로 있는 해외 자산에서 나간다(25.1527 탐침 — 미국 매수 뒤 국내 D+2 그대로).
    한 조각이라도 모르면
    그날 총액은 모름. 계열은 같은 기준(`TOTAL_BASIS`)의 줄만 잇는다
    """
    try:
        us = kv.us_totals(sess.present_us())
    except (KisFailed, requests.RequestException):
        us = {"value_usd": None, "cash_usd": None, "usd_krw": None, "assets_krw": None}
    from batch.jobs.daily import job_name
    from batch.services import desk

    이전 = client.execute(TOTALS_SQL, [증권사, 오늘, 계좌, TOTAL_BASIS]).dicts()
    해외 = us.get("assets_krw")
    앞해외 = next((r["assets"] for r in reversed(이전) if r.get("assets") is not None), None)
    영해외 = 해외 is not None and float(해외) <= 0 and 앞해외 is not None and float(앞해외) > 0
    if 영해외:
        # 어제까지 해외 자산이 있던 계좌에 0 이 오면 응답 실패로 본다 — 그대로 더하면 총액이 국내만 남아 가짜 −40%·영구
        # MDD(25.1543 감사 3). 처음부터 해외 돈이 없는 계좌의 0 은 그대로 쓴다
        해외 = None
    총 = None if 합.get("total") is None or 해외 is None else float(합["total"]) + float(해외)
    원화계좌 = us | {"kr_total": 합.get("total"), "total_krw": 총, "basis": TOTAL_BASIS,
                   **({"assets_zero": True} if 영해외 else {})}  # fmt: skip
    계열 = [(str(r["as_of"]), r["total"]) for r in 이전] + [(오늘, 총)]
    p = bm.perf(계열)
    if not p:
        return 원화계좌, None
    # H 견주기 — 같은 창(시작 ~ 오늘보다 앞선 미국 배치 끝난 날), 쉬는 해외 현금 환율 몫 뺌 (25.1543 감사 1·2)
    줄 = {str(r["as_of"]): (r["total"], r.get("cash"), r.get("fx")) for r in 이전}
    줄[오늘] = (총, us.get("cash_krw"), us.get("usd_krw"))
    미국 = {str(r[0]) for r in client.execute(US_DONE_SQL, [job_name("US"), p["start"]]).rows}
    창 = desk.h_window(계열, 오늘, 미국)
    견줌: dict[str, Any] = {"h_from": None, "h_to": None, "kis_pnl_krw": None, "kis_cmp_pnl_krw": None,
                          "fx_cash_krw": None, "h_pnl_krw": None, "h_return_pct": None}  # fmt: skip
    if 창:
        s, e = 창[0][0], 창[1]
        h0 = client.execute(H_TWR_SQL, [계좌, s]).rows
        h1 = client.execute(H_TWR_SQL, [계좌, e]).rows
        견줌 = bm.h_compare(줄, s, e, tuple(h0[0]) if h0 else None, tuple(h1[0]) if h1 else None)  # type: ignore[arg-type]
    return 원화계좌, p | 견줌


def run(
    client: Any, market: str, now: datetime, *, session: Any = None, sleep: Any = time.sleep, clock: Any = None,
    frozen: set[int] | None = None, slot: Slot = MAIN, hold_send: str | None = None,
) -> dict[str, Any]:
    """하루 한 번(시장마다). 돌려주는 것은 실행 기록(`step_log.broker`)에 넣을 건수와 까닭.

    `clock` 은 보낸 시각(시험용). `slot` = 어느 모의 계좌(첫째 — 챔피언, 둘째 — 도전자, 25.1515).
    `hold_send` = 첫째가 보내지 못한 까닭 — 있으면 둘째도 같은 까닭으로 보내지 않는다
    (두 계좌의 주문 날이 어긋나지 않게, 25.1517)
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
    내부 = {int(r["stock_id"]): int(r["q"]) for r in client.execute(INTERNAL_QTY_SQL, [베낌, market]).dicts()
            if int(r["q"] or 0) > 0}  # fmt: skip
    코드표 = _ids_all(client, {h.code for h in 보유}, market, 증권사)
    여럿 = sorted(i for ids in 코드표.values() if len(ids) > 1 for i in ids)  # 한 행뿐이면 읽지 않는다
    순보유 = ({int(r[0]): int(r[1] or 0) for r in client.execute(NET_SQL, [증권사, json.dumps(여럿)]).rows}
             if 여럿 else {})  # fmt: skip
    # 같은 날 다시 돌면 시가에 찬 오늘 매수가 증권사엔 있고 가상 장부엔 다음 배치에야 들어온다(감사 [권장] 2)
    오늘산0 = {int(r[0]): int(r[1] or 0)
              for r in client.execute(BOUGHT_TODAY_SQL, [증권사, 오늘.isoformat()]).rows}  # fmt: skip
    # 이긴 행 아닌 행의 몫은 순보유와 내부 보유 가운데 작은 것 — 그 행의 매도가 KIS 에선 찼는데 기록이 0(`unknown`·
    # 잘린 맞추기)이거나 예전 판이 남긴 음수면, 순보유만으로는 H 가 든 이긴 행 주식을 정리로 팔거나 베낀 매도가 막혔다
    # (25.1614, 25.1613 교차검증 [권고] 1·2). 남는 몫은 이긴 행 — 정리는 그 행의 내부 보유를 넘는 만큼만 판다.
    # 국내는 내부 보유에 **오늘 낸 매수**를 더한다 — 같은 날 다시 돌 때 옛 행 회차가 시가에 찬 몫이 이긴 행으로 넘어가
    # 정리로 팔렸다(25.1615, 25.1614 교차검증 [필수]). 미국 예약은 개장 전이라 아직 차지 않았다
    몫 = {sid: min(max(n, 0), 내부.get(sid, 0) + (0 if 미국 else 오늘산0.get(sid, 0)))
         for sid, n in 순보유.items()}  # fmt: skip
    # 주문가능은 오늘 베낄 매도가 걸린 행부터(25.1615, 같은 교차검증 [권고] 1 — 이긴 행부터 고정하면 옛 행 매도가
    # 막혔다)
    파는행 = ({int(r["stock_id"]) for r in client.execute(OPEN_ORDERS_SQL, [베낌, market, 오늘.isoformat()]).dicts()
              if r["side"] == "sell"} if 여럿 else set())  # fmt: skip
    증권 = {sid: v for h in 보유 if h.code in 코드표
            for sid, v in bm.split_holding(h.quantity, h.sellable, 코드표[h.code], 몫, 파는행).items()}  # fmt: skip
    # 어긋남은 **베낀 적 있는 종목**과 증권사 보유만 — 키를 넣기 전에 산 가상 보유는 "연결 전 보유" 로
    # 따로 센다(감사 [권장] 3). 오늘 매수 수량을 뺀다(감사 [권장] 2)
    증권수 = {k: v[0] - 오늘산0.get(k, 0) for k, v in 증권.items()}
    # "연결 전 보유" = **베끼기를 시작한 날 전에 이미 들고 있던** 가상 종목(25.1564). 예전 "산 적 있는 종목(거부 제외)"
    # 은 연결 뒤 거부된 매수가 숨었고(25.1561), 고친 "베끼려 한 적 있는 종목(거부 포함)" 은 연결 전부터 든 종목의
    # 거부된 2·3회차가 그 종목 전체를 영구 어긋남으로 만들었다(25.1561 반박 [권고] B). 날로 가르면 둘 다 없다 —
    # 연결 뒤 산 가상 보유는 상한·코드 없음으로 보내지 못한 것까지 늘 대 본다(같은 반박 [참고] 4)
    연결일 = client.execute(LINK_DATE_SQL, [증권사, 베낌]).rows
    연결날 = str(연결일[0][0]) if 연결일 and 연결일[0][0] else None
    연결때 = ({int(r["stock_id"]) for r in client.execute(HELD_BEFORE_SQL, [베낌, market, 연결날]).dicts()
             if int(r["q"] or 0) > 0} if 연결날 else set(내부))  # fmt: skip
    연결전 = sorted(sid for sid in 내부 if sid in 연결때 and sid not in 증권)
    어긋남 = bm.holdings_diff({k: q for k, q in 증권수.items() if q > 0},
                            {k: q for k, q in 내부.items() if k not in 연결전})  # fmt: skip
    부터 = (오늘 - timedelta(days=bm.COMPARE_DAYS)).isoformat()
    # 비교·칸·성과는 **지금 베끼는 계좌의 줄만** — 도전자를 C 에서 D 로 바꾸면 C 주문이 D 의 창에 섞였다
    # (25.1517, 검토 6)
    비교 = bm.compare(client.execute(COMPARE_SQL, [증권사, 부터, 오늘.isoformat(), 베낌]).dicts())
    # 체결 칸(15.9, 25.1514) — 곁다리: 실패해도 비교 요약은 그대로
    try:
        비교 |= bm.cells(client.execute(CELLS_SQL, [증권사, 오늘.isoformat(), 베낌]).dicts())
    except Exception as exc:  # noqa: BLE001
        기록["cells_error"] = type(exc).__name__
    # 웹 보유내역·성과(docs/paper.md 15.7, 25.1496·25.1497). 시장 칸은 그 시장 통화(국내 원 — 현금은 총평가와
    # 같은 D+2, 미국 달러). 성과는 **국내 배치가 계좌 전체 원화 총액**(국내 총평가 + 해외 총자산, 25.1527)으로 낸다 —
    # 원화와 따로 있는 해외 자산이 한 계좌라 시장별 총액 비는 수익률이 아니다. 국내 배치 시각(08:30 KST)이면 두 시장
    # 모두 전날 종가라 시점이 맞다. H 견주기는 미국 배치까지 끝난 날로 창을 끊는다(25.1543)
    계좌 = {"currency": "USD" if 미국 else "KRW", "cash": 합.get("cash") if 미국 else 합.get("cash_d2", 합.get("cash")),
          "holdings_value": 합.get("holdings_value"), "total": 합.get("total")}  # fmt: skip
    if 미국:
        # 해외 현금·해외 총자산(원화 환산) — 달러 예수금 칸은 모의에서 늘 0 이라 그것만 보이면 "현금 0" 으로
        # 읽힌다(25.1527)
        계좌 |= {"cash_krw": 합.get("cash_krw"), "assets_krw": 합.get("assets_krw")}
    원화계좌: dict[str, Any] | None = None
    성과 = None
    if not 미국:
        # 곁다리 안의 곁다리 — 여기서 무엇이 깨져도 아래 주문 보내기는 돈다(25.1499, 검토 [권장] 5)
        try:
            원화계좌, 성과 = _account_perf(client, sess, 증권사, 오늘.isoformat(), 합, 베낌)
        except Exception as exc:  # noqa: BLE001
            기록["perf_error"] = type(exc).__name__
    보유칸 = [{"id": (코드표.get(h.code) or [None])[-1], "code": h.code, "qty": h.quantity, "avg": h.avg_price,
             "price": h.price, "value": h.value, "pnl": h.pnl} for h in 보유]  # fmt: skip
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
    if hold_send:
        기록["send"] = hold_send
        return 기록 | {"calls": sess.calls}
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
    낸수 = client.execute(SENT_TODAY_SQL, [증권사, 오늘.isoformat()]).rows
    낼것, 안냄 = bm.plan(주문들, 코드, 내부, 증권, 산적, already=이미, cleaned_today=정리함, halted=멈춤,
                       bought_today=오늘산, frozen=frozen, currency="USD" if 미국 else "KRW",
                       closes=종가, sent_today=int(낸수[0][0] or 0) if 낸수 else 0)  # fmt: skip
    # 미국에서 티커는 있는데 KIS 거래소 코드가 없어 뺀 종목은 "종목 코드 없음" 이 아니다 — 까닭을 바로 적는다
    # (25.1608, 25.1607 교차검증 [권고] 2. 내지 않는 처리 자체는 25.1485 그대로)
    if 미국:
        안냄 = [(oid, sid, r.replace("종목 코드 없음", f"KIS 미국 거래소 코드 없음({종목[sid][1]})", 1))
              if sid in 종목 and sid not in 코드 and r.startswith("종목 코드 없음") else (oid, sid, r)
              for oid, sid, r in 안냄]  # fmt: skip
    # 정리 주문은 그 종목을 **산 계좌**로 적는다 — 베끼는 계좌를 바꾼 뒤 정리 줄이 새 계좌의 주문처럼 보였다
    # (25.1517, 검토 6)
    산계좌 = {int(r[0]): str(r[1]) for r in client.execute(BOUGHT_BY_SQL, [증권사]).rows}
    상태: Counter[str] = Counter()
    for i, it in enumerate(낼것):
        if 미국 and (마감 := _us_window(clock(), 오늘)):
            # 보내는 사이 예약 접수 마감(개장 전 `US_RESV_CUTOFF`)을 넘겼다 — 단계 시작 때 한 번만 보면 마감 뒤에도
            # 나머지를 보냈다(25.1568, KIS 경로 감사 [참고] 4). 남은 것은 보내지 않고 까닭을 남긴다
            안냄.extend((x.paper_order_id, x.stock_id, 마감) for x in 낼것[i:])
            안냄 = bm.without_partial(안냄, 낼것[i:])
            낼것 = 낼것[:i]  # 기록의 `planned` 가 끊긴 줄까지 세어 `not_sent` 와 두 번 셌다(25.1569)
            break
        # 미국은 늘 지정가(시장가 대신 종가 ± 폭) — 내부 주문이 시장가였으면 `limit_price` 가 그 폭 지정가다
        client.execute(INSERT_SQL, [
            증권사, it.paper_order_id, 산계좌.get(it.stock_id, 베낌) if it.reason == bm.REASON_CLEANUP else 베낌,
            market, it.stock_id, it.code, it.side,
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
    # 둘째(도전자, 25.1515) 줄도 — 거부·모름·어긋남을 볼 곳이 텔레그램 실패 알림뿐이었다(25.1517, 검토 7)
    for 증권사, 이름 in ((BROKER, MIRROR_ACCOUNT), (BROKER_US, f"{MIRROR_ACCOUNT} 미국"),
                       (CHALLENGER.broker, "도전자"), (CHALLENGER.broker_us, "도전자 미국")):  # fmt: skip
        rows = client.execute(LATEST_DAILY_SQL, [증권사]).dicts()
        if not rows or not rows[0].get("summary_json"):
            continue
        r = rows[0]
        if 이름.startswith("도전자") and r.get("account_id"):
            이름 = f"도전자 {r['account_id']}" + (" 미국" if 이름.endswith("미국") else "")
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
