"""KIS모의투자 장중 손절·익절 (docs/paper.md 15.11, docs/infra.md 25.1768).

2026-10-23 사용자 지시: "배치가 오전에 한번만 돌아서 실제 장 중 변동성에는 대응을 못하는거 같은데" →
"보유 종목에 대한 제안은 진행해줘" → "아니야 이건 지금 바로 반영해줘" → "손절만이 아니라 익절도 같이 반영해야돼".

일일 배치를 장중에 더 돌리지 않는다(Turso 월 읽기·점수 입력 불변·KIS 초당 한도 — docs/handoff.md).
대신 **국내 정규장 동안 10분마다** 가볍게 돈다:
1. 아침 가상모의투자 단계가 베끼는 계좌(H)의 보유마다 적어 둔 **감시선**(`jobs/paper.guard_line` — 그 계좌의
   매도 판정식을 가격으로 바꾼 손절선·익절선)을 설정 `kis_guard_lines` 한 행으로 읽는다
2. KIS 모의 잔고 조회 한 번(보유·주문가능 수량·**현재가**)
3. 현재가가 손절선 이하이거나 익절선(추적손절·목표)에 닿은 종목을 **KIS 에서만** 시장가로 전량 판다 — 줄을 먼저
   `sending` 으로 적고 보낸다(`jobs/broker_mirror` 와 같은 두 번 주문 막기). 텔레그램으로 알린다

**가상모의투자 H 는 바꾸지 않는다** — H 는 지금처럼 아침에 일봉 종가로 판정한다(H 판 2 관문·11-03 판정과
섞지 않는다). 그래서 KIS모의투자는 "H + 장중 손절·익절" 이 되고, 같은 종목에서 H(일봉)와 KIS(장중)의 결과가
갈린다 — 그 차이가 장중 손절·익절이 나은지의 실측이다(모의계좌는 하나뿐이라 도전자로 견줄 수 없다, 15.10).
판 뒤 H 가 같은 종목을 팔면 KIS 엔 팔 수량이 없어 "증권사 모의에 팔 수량 없음" 으로 빠진다(`bm.plan`).
실계좌는 건드리지 않는다(모의 도메인만, `kis_vts`).

DB 읽기: 실행마다 멈춤 스위치·일일 배치 실행 중·감시선 설정 각 한 행, 오늘 낸 장중 매도 몇 행, 알림 표시 설정 몇 행
(기업행위 의심·정리 줄 겹침·순간 거부), 감시선이 없을 때만 일일 배치 끝남 한 행, 알릴 때만 종목 이름.
시간 밖·휴장일은 DB 를 열지 않는다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any

import requests

from batch import config
from batch.core import calendar as cal
from batch.core import db, markets
from batch.core.entry import guard
from batch.jobs import broker_mirror as bmj
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from batch.sources.kis import KisFailed

log = logging.getLogger("intraday_guard")
#: 실행 기록 이름 = 모듈 이름(주간 요약의 기대 주기 표가 워크플로의 `-m batch.jobs.<이름>` 으로 대 본다)
JOB_NAME = "intraday_guard"

#: 감시선을 담는 설정 키 — {시장: {as_of, account, rule, lines: {종목 id: 선}}}. 아침 가상모의투자 단계가 적는다
GUARD_KEY = "kis_guard_lines"
#: 감시선을 만드는 계좌 = KIS모의투자가 베끼는 계좌
GUARD_ACCOUNT = bmj.MIRROR_ACCOUNT
#: 국내 정규장 안에서 보는 시각(한국) — 시가 단일가(09:00) 직후와 장 마감 동시호가(15:20~) 앞은 뺀다. 동시호가 시간에는
#: 시장가가 마감 단일가로 가 "현재가 닿음" 과 체결가가 따로 논다. 달력을 못 읽을 때만 쓰는 기본값이다
WINDOW_KST = (dtime(9, 5), dtime(15, 15))
#: 실제 창 = 그날 개장 + 5분 ~ 마감 − 15분(달력 `session_open_utc`·`session_close_utc`) — 새해 첫 거래일 10:00 개장은
#: 달력이 주고, 수능일은 달력에 없어 `monitor_targets.KR_CSAT_DAYS` 로 한 시간 늦춘다(25.1774, 25.1773 교차검증 [권고]:
#: 고정 창이라 그 두 날 09:13~09:53 실행이 장 열리기 전 가격으로 판정해 시장가 매도를 낼 수 있었다). 반복 실행
#: (`scripts/kis_guard_loop.py`, 25.1796)이 이 창을 그대로 따라 수능일에도 16:15 까지 본다
OPEN_MARGIN = timedelta(minutes=5)
CLOSE_MARGIN = timedelta(minutes=15)
#: 일일 배치가 돌고 있으면 쉰다 — 같은 앱키의 초당 한도를 나눠 쓰면 배치 주문이 `EGW00201` 거부로 적힌다(웹 조회와 같은
#: 막기, `web/lib/kisVts.BATCH_RUNNING_WINDOW_MS`)
BATCH_RUNNING_MINUTES = 45
BATCH_RUNNING_SQL = (
    "SELECT 1 FROM batch_runs WHERE job_name IN ('daily_kr', 'daily_us') AND status = 'running' AND started_at >= ?"
    " LIMIT 1"
)
#: 오늘(한국 날짜) 국내 일일 배치가 끝났나 — 감시선이 없을 때 "기다림" 과 "멈춤" 을 가른다(25.1770).
#: **성공·부분만** 센다(25.1772, 25.1771 교차검증 [필수]: 실패·건너뜀까지 세자 08:27 이 한도·KrxNotYet 로 건너뛰고
#: 09:35 예비가 도는 날 거짓 "꺼짐" 이 되살아났다 — 예비 실행·dispatch 는 `has_successful_run`(성공·부분)이 거짓이면
#: 다시 돈다). 실패·건너뜀으로 끝내 선이 안 생기는 날은 아래 `WAIT_LIMIT_KST` 가 잡는다
DAILY_DONE_SQL = (
    "SELECT 1 FROM batch_runs WHERE job_name = 'daily_kr' AND status IN ('success', 'partial') AND started_at >= ?"
    " LIMIT 1"
)
#: 이 시각(한국)이 지나도 감시선이 없으면 배치 기록과 상관없이 멈춤으로 본다 — 배치가 기록조차 못 남긴 날(25.1771)
WAIT_LIMIT_KST = dtime(11, 0)
WAITING = "아침 국내 일일 배치 대기 — 끝나면 감시선이 생긴다"
#: 오늘 이미 장중 매도를 낸 종목 — 한 종목 하루 한 번(유일 색인 `idx_paper_broker_cleanup` 도 막는다). 다만
#: **순간 거부**(초당 한도·토큰)는 같은 줄로 다시 보낸다 — 색인이 둘째 줄을 막아 첫 시도가 한도에 걸리면 그날
#: 내내 손절이 안 나갔다(25.1769, 25.1768 교차검증 [권고])
GUARDED_TODAY_SQL = (
    "SELECT id, stock_id, status, message FROM paper_broker_orders WHERE broker = ? AND trade_date = ?"
    " AND side = 'sell' AND reason IN (?, ?)"
)
#: 다시 보내도 되는 거부 — KIS 초당 한도(`EGW00201`·원장 `EGW00215`, 25.1799)·토큰 만료. 잔고·가격 제한 같은 진짜 거부는
#: 그대로 둔다
TRANSIENT_CODES = (*kv.RATE_LIMIT_CODES, "EGW00123", "EGW00121")
#: 순간 거부 알림 — 다시 보내는 것은 다음 회차에도 선에 닿아 있을 때뿐이다(25.1774, 25.1773 교차검증 [권고]:
#: "다음 회차에 다시 보냄" 만 적어 반등한 날은 약속한 매도도 후속 알림도 없었다)
TRANSIENT_TEXT = "거부(초당 한도·토큰) — 다음 회차에 선에 아직 닿아 있으면 다시 보냄"
#: 다시 보낼 때 수량·사유도 지금 값으로(25.1770 — 처음 값이 남으면 다음 아침 맞추기가 거짓 `partial`·`unknown`)
RETRY_SQL = (
    "UPDATE paper_broker_orders SET status = 'sending', message = NULL, created_at = ?, quantity = ?, reason = ?"
    " WHERE id = ? AND status = 'rejected'"
)
#: 다시 보내기가 정말 이 실행 몫인가 — UPDATE 가 바꾼 행이 없으면(아침 맞추기 등이 이미 바꿈) 보내지 않는다(25.1772,
#: 25.1771 교차검증 [권고]: 조건을 빼도 시험이 통과했고, 바뀐 행 수를 보지 않아 조건이 있어도 주문은 나갔다)
RETRY_MINE_SQL = "SELECT id FROM paper_broker_orders WHERE id = ? AND status = 'sending' AND created_at = ?"
#: 종목마다 하루 한 번만 알리는 것들의 표시 — {date, ids}. 10분마다 같은 알림을 되풀이하지 않게. 표시는 **보낸 뒤**
#: `main` 이 적는다(25.1772 — 먼저 적으면 전송이 실패한 날 다시 안 간다)
#: 기업행위 의심으로 팔지 않은 종목(25.1771)
SUSPECT_KEY = "kis_guard_suspect_alerted"
#: 같은 날 아침 정리 매도 줄과 겹쳐 장중 매도를 못 낸 종목(25.1772, 25.1771 교차검증 [필수]) — 부분 유일 색인
#: `idx_paper_broker_cleanup`(broker, trade_date, stock_id, side; 증권사 주문 id 없음)이 둘째 줄을 막는다.
#: 색인에 사유를 넣는 마이그레이션(11월)까지는 막지 못하고 알린다
OTHER_ROW_KEY = "kis_guard_other_row_alerted"
#: 순간 거부(초당 한도·토큰)를 알린 종목 — 다시 보내는 동안 10분마다 같은 거부 알림이 가지 않게(25.1772)
TRANSIENT_KEY = "kis_guard_transient_alerted"
#: 감시선 계산이 일부 보유에서 깨진 날 한 번 알린 날짜(25.1774)
LINE_ERR_KEY = "kis_guard_line_error_alerted"
#: 판 뒤 텔레그램을 못 보낸 알림 글 — {date, text}. 판 종목은 다음 회차부터 "이미" 로 빠져 글이 다시 안 만들어지므로
#: 글을 남겨 두고 다음 회차가 앞에 붙여 다시 보낸다(25.1774, 25.1773 교차검증 [권고])
UNSENT_KEY = "kis_guard_unsent_alert"
#: 아침 감시선이 없는 날 한 번만 알린다 — {날짜} (25.1769, 교차검증 [권고]: 아침 단계가 실패하면 하루 종일 조용히
#: 꺼졌다)
NO_LINES_ALERT_KEY = "kis_guard_no_lines_alerted"
NAME_SQL = (
    "SELECT id, COALESCE(name_ko, name_en, ticker) AS name FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
)


@dataclass(frozen=True)
class Hit:
    """장중 감시선에 닿은 보유 하나"""

    stock_id: int
    code: str
    quantity: int
    price: float
    line: float
    kind: str  # stop · trail · target
    reason: str  # bm.REASON_GUARD_STOP · bm.REASON_GUARD_TAKE


KIND_TEXT = {"stop": "손절선", "trail": "익절선(고점 대비)", "target": "목표가", "suspect": "기준 종가"}


def save_lines(
    client: Any, market: str, today: date, account: str, rule: str, lines: dict[int, dict[str, Any]],
    errors: dict[str, int] | None = None,
) -> dict[str, int]:
    """아침 가상모의투자 단계가 부른다 — 그 시장의 감시선을 오늘 날짜로 덮어쓴다(다른 시장 칸은 그대로).
    건수만 돌려준다. `errors` = 감시선 계산 예외 {이름: 수} — 예외로 선이 하나도 안 섰으면 `dropped`(25.1773)"""
    빼기, 까닭 = action_today(client, sorted(lines), today) if market == "KR" else (set(), None)
    계산 = f"감시선 계산 오류({', '.join(sorted(errors))})" if errors else None
    lines = {sid: 선 for sid, 선 in lines.items() if sid not in 빼기}
    값 = db.get_setting(client, GUARD_KEY, None)
    값 = 값 if isinstance(값, dict) else {}
    값[market] = {"as_of": today.isoformat(), "account": account, "rule": rule,
                 "lines": {str(sid): 선 for sid, 선 in sorted(lines.items())},
                 # 일정을 몰라 선을 모두 뺀 날 — 장중 감시가 "감시선 없음" 으로 알리고 멈춤으로 남긴다(25.1772)
                 **({"dropped": 까닭} if 까닭 and 빼기 else {"dropped": 계산} if 계산 and not lines else {}),
                 # 일부만 깨진 날 — 나머지 선은 서고 장중 실행이 멈춤·알림(그 종목은 감시 밖, 25.1774)
                 **({"line_errors": sum(errors.values())} if errors and lines else {})}  # fmt: skip
    db.set_setting(client, GUARD_KEY, 값)
    return {"n": len(lines), **{k: sum(k in 선 for 선 in lines.values()) for k in ("stop", "trail", "target")},
            **({"action_day": len(빼기)} if 빼기 else {}), **({"action_day_error": 까닭} if 까닭 else {}),
            **({"line_errors": errors} if errors else {})}


def lines_saved(client: Any, today: date, account: str) -> bool:
    """오늘·이 계좌의 국내 감시선 칸이 적혀 있나(선이 0 개여도 적힌 것) — 같은 날 다시 돈 단계가 다시 적을지 가른다.
    **계산 오류로 빠진 선이 있으면 안 적힌 것**으로 본다 — 순간 오류였으면 다시 돌리기로 되살아나게(25.1781,
    25.1780 교차검증 [권고]: 모두 깨진 날 `dropped` 칸이 "적힘" 이라 다시 돌려도 계속 꺼졌다). 일정 **읽기 실패**도
    순간 오류일 수 있어 안 적힌 것(25.1785). 일정 묵음으로 뺀 날은 다시 해도 같아 적힌 것으로 둔다"""
    값 = db.get_setting(client, GUARD_KEY, None)
    칸 = 값.get("KR") if isinstance(값, dict) else None
    if not isinstance(칸, dict) or 칸.get("as_of") != today.isoformat() or 칸.get("account") != account:
        return False
    다시 = ("계산 오류", "읽기 실패")
    return not (칸.get("line_errors") or any(x in str(칸.get("dropped") or "") for x in 다시))


def action_today(client: Any, stock_ids: list[int], today: date) -> tuple[set[int], str | None]:
    """**오늘**이 무상·유상증자 권리락일인 종목 — 그날은 감시선을 만들지 않는다(25.1771, 25.1770 교차검증 [필수]:
    기계적 하락이 나는 바로 그날은 아침 배치가 아직 그 하락을 못 봐 `corporate_action_recent` 가 거르지 못했고, 권리락
    전 원가의 손절선에 조정된 현재가가 닿아 거짓 장중 손절이 실제 KIS 매도로 나갔다). 장중 알림의 25.1725 와 같은 일정
    (`kr_corp_events` 기준일 → 권리락일)이지만 "다음 장" 이 아니라 오늘로 본다(아침 단계가 개장 뒤에 돌 수 있다).
    못 읽으면 **모두** 뺀다(보수 — 그날 장중 감시가 꺼질 뿐) — (종목, 못 읽은 까닭).
    일정 표가 **비었거나 묵었어도** 모름이다(25.1772, 25.1771 교차검증 [권고]: 이 표는 실전 시세 키로 `kis-flows` 가
    채운다 — 장중 감시의 모의 키와 다르다. 실전 키가 빠지거나 수집이 멈추면 SQL 은 오류 없이 빈 결과를 줘 권리락일
    종목에도 선이 섰다)"""
    from batch.jobs import monitor_targets as mt

    if not stock_ids:
        return set(), None
    try:
        최근 = client.execute(CORP_FRESH_SQL).scalar()
        if not 최근 or str(최근)[:10] < (today - timedelta(days=CORP_STALE_DAYS)).isoformat():
            return set(stock_ids), f"기업행위 일정 묵음({str(최근)[:10] if 최근 else '비어 있음'})"
        끝 = (today + timedelta(days=mt.ACTION_DAY_LOOKAHEAD_DAYS)).isoformat()
        rows = client.execute(mt.ACTION_DAY_SQL, [today.isoformat(), 끝]).rows
        감시 = set(stock_ids)
        빼기 = {int(r[0]) for r in rows if int(r[0]) in 감시 and mt.ex_date(str(r[1])[:10]) == today}
        # 주식배당 락일도 — 배당 일정(`dividend`)에 주식배당 비율(`stk_divi_rate` > 0)로 온다. 5~10% 기계적 하락은 ±31%
        # 가드에 안 걸리고, 다음 아침 H 는 기업행위로 그 종목을 멈춰 KIS 만 판 채로 갈렸다
        # (25.1773, 25.1772 교차검증 [필수])
        for r in client.execute(STOCK_DIV_SQL, [today.isoformat(), 끝]).rows:
            if int(r[0]) in 감시 and stock_dividend(r[2]) and mt.ex_date(str(r[1])[:10]) == today:
                빼기.add(int(r[0]))
        return 빼기, None
    except Exception as exc:  # noqa: BLE001 — 모르면 그날 선을 만들지 않는다(거짓 손절보다 꺼짐이 낫다)
        if db.quota_reason(exc):
            raise
        return set(stock_ids), f"기업행위 일정 읽기 실패({type(exc).__name__})"  # 알림이 일정 쪽을 보게(25.1774)


#: 주식배당 일정 — 배당 일정 가운데 주식배당 비율이 있는 행(파이썬에서 거른다, `stock_dividend`). 창은 권리락과 같다
STOCK_DIV_SQL = (
    "SELECT s.id, e.record_date, e.detail FROM kr_corp_events e JOIN stocks s ON s.ticker = e.code"
    " WHERE e.kind = 'dividend' AND e.record_date > ? AND e.record_date <= ?"
)


def stock_dividend(detail: Any) -> bool:
    """예탁원 배당 일정의 주식배당 비율(`stk_divi_rate`)이 0 보다 큰가 — `daily.py`·`sell_flags` 와 같은 칸.
    못 읽으면 False"""
    import json

    try:
        d = json.loads(detail) if isinstance(detail, str) else detail
        return float(str((d or {}).get("stk_divi_rate") or 0).replace(",", "").strip()) > 0
    except (TypeError, ValueError, AttributeError):
        return False


#: 기업행위 일정(`kr_corp_events`)이 이 날수보다 묵으면 모름 — `kis-flows` 는 평일 18:17 KST 에 돈다. 금요일 수집 뒤
#: 월요일 아침이 사흘, 추석·설 연휴(닷새 안팎)를 넘기게 7일. 수집 창은 앞 45일(유상 120일)이라 일주일 묵은 일정도 그 안
#: 권리락일은 대개 담는다 — 그 사이 새로 공시된 일정만 빠진다 [확인필요: 무상증자 공시~권리락일 최소 간격]
CORP_STALE_DAYS = 7
CORP_FRESH_SQL = "SELECT MAX(fetched_at) FROM kr_corp_events"


def due(now: datetime, market: str = "KR") -> str | None:
    """지금 볼 때인가 — 아니면 까닭. DB 를 열기 전에 본다(시간 밖 예약 실행이 읽기를 쓰지 않게)"""
    현지 = now.astimezone(cal.market_tz(market))
    if not cal.is_session(market, 현지.date()):
        return "휴장일"
    창 = window(market, 현지.date())
    if 창 is None:
        if not WINDOW_KST[0] <= 현지.time() <= WINDOW_KST[1]:
            return "장중 감시 시간 밖"
    elif not 창[0] <= now <= 창[1]:
        return "장중 감시 시간 밖"
    return None


def window(market: str, day: date) -> tuple[datetime, datetime] | None:
    """그날 장중 감시 창(UTC) — 개장 + 5분 ~ 마감 − 15분. 수능일은 한 시간 늦춘다. 달력이 모르면 None"""
    from batch.jobs import monitor_targets as mt

    열림, 닫힘 = cal.session_open_utc(market, day), cal.session_close_utc(market, day)
    if 열림 is None or 닫힘 is None:
        return None
    if market == "KR" and day.isoformat() in mt.KR_CSAT_DAYS and 열림.astimezone(UTC).hour == 0:
        열림, 닫힘 = 열림 + mt.CSAT_SHIFT, 닫힘 + mt.CSAT_SHIFT
    return 열림 + OPEN_MARGIN, 닫힘 - CLOSE_MARGIN


def hits(holdings: list[kv.Holding], lines: dict[str, tuple[int, dict[str, Any]]]) -> list[Hit]:
    """감시선에 닿은 보유. 손절이 먼저(같은 날 둘 다면 손절로 판다). 현재가·주문가능 수량이 없으면 보지 않는다"""
    out: list[Hit] = []
    for h in holdings:
        짝 = lines.get(h.code)
        if 짝 is None or not h.price or h.price <= 0 or int(h.sellable or 0) <= 0:
            continue
        sid, 선 = 짝
        p = float(h.price)
        # 아침 선의 기준 종가 대비 하루 가격제한폭(±30%)을 넘는 현재가는 장중 움직임이 아니다 — 액면분할 재상장·병합
        # 첫날처럼 기준가 자체가 바뀐 날(25.1771, 25.1770 교차검증 [필수]. 웹 장중 감시의 ±31% 가드 25.597 과 같은 문턱)
        기준 = float(선.get("ref") or 0)
        if 기준 > 0 and abs(p / 기준 - 1) > markets.KR_DAILY_LIMIT + 0.01:
            out.append(Hit(sid, h.code, 0, p, 기준, "suspect", ""))
            continue
        # H 수량까지만 판다 — 사람이 그 모의계좌에서 손으로 산 주식까지 팔지 않게(정리 주문 원칙, paper.md 15.1 —
        # 25.1770, 25.1769 교차검증 [권고]). 오늘 KIS 에서만 찬 회차는 H 장부에 내일 들어오니 그날은 남는다(보수 쪽)
        수량 = min(int(h.sellable), int(선.get("qty") or h.sellable))
        if _닿음(p, 선.get("stop"), "아래"):
            out.append(Hit(sid, h.code, 수량, p, float(선["stop"]), "stop", bm.REASON_GUARD_STOP))
        elif _닿음(p, 선.get("trail"), "아래"):
            out.append(Hit(sid, h.code, 수량, p, float(선["trail"]), "trail", bm.REASON_GUARD_TAKE))
        elif _닿음(p, 선.get("target"), "위"):
            out.append(Hit(sid, h.code, 수량, p, float(선["target"]), "target", bm.REASON_GUARD_TAKE))
    return out


def _닿음(price: float, line: Any, 쪽: str) -> bool:
    """선에 닿았나 — 판정식(`sell_flags.evaluate`·`pp.trend_exit`)처럼 비를 소수 여섯째 자리에서 반올림해 견준다(경계가
    부동소수 찌꺼기로 빗나가지 않게, 25.1769 — 곱한 여유 `1 + 1e-9` 는 `<` 로 바꿔도 시험이 못 잡았다)"""
    if line is None or float(line) <= 0:
        return False
    비 = round(price / float(line) - 1, 6)
    return 비 <= 0 if 쪽 == "아래" else 비 >= 0


def note(hit: Hit) -> str:
    """주문 줄의 비고(웹 거래내역에 보인다) — 왜 팔았나. 현재가 ≤ 선(익절 목표는 ≥)"""
    부호 = "≥" if hit.kind == "target" else "≤"
    return f"장중 현재가 {hit.price:,.0f} {부호} {KIND_TEXT[hit.kind]} {hit.line:,.0f}"


def run(
    client: Any, now: datetime, *, session: Any = None, clock: Any = None, dry_run: bool = False,
) -> dict[str, Any]:
    """한 번 본다(국내). 돌려주는 것은 실행 기록(`step_log`)에 넣을 건수와 까닭 — 종목·수량·금액은 넣지 않는다"""
    clock = clock or (lambda: datetime.now(UTC))
    if not kv.configured(keys=kv.MAIN):
        return {"off": "KIS모의투자 키 없음"}
    멈춤값 = db.get_setting(client, "paper_halt", None)
    if bool(멈춤값.get("on")) if isinstance(멈춤값, dict) else bool(멈춤값):
        return {"off": "모의 매매 멈춤(/paper)"}
    오늘 = now.astimezone(cal.market_tz("KR")).date()
    # 일일 배치 실행 중 확인이 **감시선 확인보다 먼저** — 배치가 늦게 도는 날(08:27 이 한도로 건너뛰고 09:35 예비가
    # 도는 꼴) 감시선을 적기 전 실행이 "꺼짐" 을 알렸다(25.1770, 25.1769 교차검증 [필수])
    if client.execute(BATCH_RUNNING_SQL, [(now - timedelta(minutes=BATCH_RUNNING_MINUTES)).isoformat()]).rows:
        return {"skipped": "일일 배치 실행 중 — KIS 초당 한도를 나눠 쓰지 않게"}
    값 = db.get_setting(client, GUARD_KEY, None)
    칸 = (값 or {}).get("KR") if isinstance(값, dict) else None
    if not isinstance(칸, dict) or 칸.get("as_of") != 오늘.isoformat():
        # 오늘 국내 일일 배치가 아직 끝나지 않았으면 기다리는 것이다(정상 쉼, 알림 없음).
        # 끝났는데 감시선이 없을 때만 멈춤
        시작 = datetime.combine(오늘, dtime(0, 0), tzinfo=cal.market_tz("KR")).astimezone(UTC).isoformat()
        if (not client.execute(DAILY_DONE_SQL, [시작]).rows
                and now.astimezone(cal.market_tz("KR")).time() < WAIT_LIMIT_KST):  # fmt: skip
            return {"off": WAITING}
        return {"off": f"{NO_LINES} — 오늘 국내 일일 배치가 끝났거나 {WAIT_LIMIT_KST:%H:%M} 이 지났는데 감시선이 없음"}
    선들 = {str(v.get("code")): (int(sid), v) for sid, v in (칸.get("lines") or {}).items() if v.get("code")}
    if not 선들:
        if 칸.get("dropped"):  # 기업행위 일정을 몰라 아침에 선을 모두 뺐다 — 조용히 꺼지지 않게(25.1772)
            return {"off": f"{NO_LINES} — {칸['dropped']}"}
        return {"lines": 0}
    sess = session or kv.Session(client, now, keys=kv.MAIN)
    보유, _합 = sess.balance()
    기록: dict[str, Any] = {"lines": len(선들), "holdings": len(보유)}
    날표시: list[str] = []  # 보낸 뒤 `main` 이 오늘 날짜를 적는 하루 한 번 표시
    if 칸.get("line_errors"):
        기록["line_errors"] = int(칸["line_errors"])  # 일부 보유의 감시선 계산이 깨졌다 — 그 종목은 오늘 감시 밖
        if db.get_setting(client, LINE_ERR_KEY, None) != 오늘.isoformat():
            날표시.append(LINE_ERR_KEY)
    if getattr(sess, "balance_truncated", False):
        return 기록 | {"skipped": "잔고 조회가 쪽 상한에 닿아 잘림", "calls": sess.calls}
    오늘줄 = client.execute(GUARDED_TODAY_SQL, [bmj.BROKER, 오늘.isoformat(), bm.REASON_GUARD_STOP,
                                               bm.REASON_GUARD_TAKE]).dicts()  # fmt: skip
    다시 = {int(r["stock_id"]): int(r["id"]) for r in 오늘줄
            if r["status"] == "rejected" and any(c in str(r["message"] or "") for c in TRANSIENT_CODES)}  # fmt: skip
    이미 = {int(r["stock_id"]) for r in 오늘줄} - set(다시)
    닿음 = [x for x in hits(보유, 선들) if x.stock_id not in 이미]
    의심 = [x for x in 닿음 if x.kind == "suspect"]
    닿음 = [x for x in 닿음 if x.kind != "suspect"]
    표시: dict[str, list[int]] = {}  # 보낸 뒤 `main` 이 적는 하루 한 번 표시
    if 의심:
        기록["suspect"] = len(의심)  # 기업행위 의심 — 팔지 않는다(알림만, 종목마다 하루 한 번)
        의심 = [x for x in 의심 if x.stock_id not in alerted_ids(client, SUSPECT_KEY, 오늘)]
        표시[SUSPECT_KEY] = [x.stock_id for x in 의심]
    종류 = [x.kind for x in 닿음]
    기록["hits"] = {k: 종류.count(k) for k in ("stop", "trail", "target") if k in 종류}
    if dry_run:  # 확인 실행은 세기만 — 주문·알림·표시 없음(25.1772)
        return 기록 | {"dry_run": True, "calls": sess.calls}
    오류글 = (f"⚠️ KIS모의투자 장중 감시 — 오늘 감시선 계산 오류 {기록['line_errors']}건, 그 종목은 장중 손절·익절 밖"
             "(일일 배치 실행 기록 guard_lines 확인)") if 날표시 else ""  # fmt: skip
    if not 닿음:
        글 = [x for x in (suspect_text(client, 의심) if 의심 else "", 오류글) if x]
        if 글:
            기록["alert"], 기록["marks"], 기록["date_marks"] = "\n".join(글), 표시, 날표시
        return 기록 | {"dry_run": False, "calls": sess.calls}
    지금 = now.isoformat()
    상태: dict[str, int] = {}
    보낸: list[tuple[Hit, str]] = []
    겹침: list[Hit] = []
    순간본 = alerted_ids(client, TRANSIENT_KEY, 오늘)
    기록줄오류: Exception | None = None
    for x in 닿음:
        try:
            if x.stock_id in 다시:
                rid = 다시[x.stock_id]
                client.execute(RETRY_SQL, [지금, x.quantity, x.reason, rid])
                if not client.execute(RETRY_MINE_SQL, [rid, 지금]).rows:
                    상태["skipped_other_run"] = 상태.get("skipped_other_run", 0) + 1
                    continue
                상태["retried"] = 상태.get("retried", 0) + 1
            else:
                client.execute(bmj.INSERT_SQL, [
                    bmj.BROKER, None, GUARD_ACCOUNT, "KR", x.stock_id, x.code, "sell", "market", None, x.quantity,
                    오늘.isoformat(), x.reason, kv.MAIN.source, 지금,
                ])  # fmt: skip
                mine = client.execute(bmj.MINE_SQL, [bmj.BROKER, 오늘.isoformat(), x.stock_id, "sell", -1, 지금]).rows
                if not mine:
                    # 같은 날 아침 정리 매도 줄이 유일 색인을 차지했다 — 멈춤으로 남기고 종목마다 하루 한 번 알린다
                    상태["skipped_other_row"] = 상태.get("skipped_other_row", 0) + 1
                    겹침.append(x)
                    continue
                rid = int(mine[0][0])
        except Exception as exc:  # noqa: BLE001 — 줄을 못 적었으면 보내지 않는다(두 번 주문 막기가 줄에 기댄다)
            기록줄오류 = exc
            break
        보낸시각 = clock().isoformat()
        try:
            받음 = sess.order("sell", x.code, x.quantity, None)
            결과, 값 = "접수", ["accepted", 받음.order_no, 보낸시각, note(x), 보낸시각, rid]
            상태["accepted"] = 상태.get("accepted", 0) + 1
        except (kv.NoResponse, requests.RequestException) as exc:
            # 갔는지 모른다 — 다시 보내지 않는다(두 번 주문). 다음 아침 체결 조회로 맞춘다
            글 = str(exc)[:200] if isinstance(exc, kv.NoResponse) else f"응답 없음 ({type(exc).__name__})"
            결과, 값 = "응답 없음 — 내일 아침 맞춤", ["sending", None, 보낸시각, f"{note(x)} · {글}", None, rid]
            상태["no_response"] = 상태.get("no_response", 0) + 1
        except KisFailed as exc:
            순간 = any(c in str(exc) for c in TRANSIENT_CODES)
            결과 = TRANSIENT_TEXT if 순간 else f"거부 ({bm._까닭(str(exc))})"
            값 = ["rejected", None, 보낸시각, f"{note(x)} · {str(exc)[:150]}", 보낸시각, rid]
            상태["rejected"] = 상태.get("rejected", 0) + 1
            if 순간:  # 다시 보내는 동안 같은 거부 알림은 종목마다 하루 한 번
                표시.setdefault(TRANSIENT_KEY, []).append(x.stock_id)
                if x.stock_id in 순간본:
                    결과 = ""
        if 결과:
            보낸.append((x, 결과))  # 주문은 이미 나갔다 — 기록이 깨져도 알림은 간다(25.1769)
        try:
            client.execute(bmj.SENT_UPDATE_SQL, 값)
        except Exception as exc:  # noqa: BLE001 — 줄은 `sending` 으로 남아 다음 아침 체결 조회가 맞춘다
            기록줄오류 = exc
            break
    기록["sent"] = 상태
    겹침 = [x for x in 겹침 if x.stock_id not in alerted_ids(client, OTHER_ROW_KEY, 오늘)]
    if 겹침:
        표시[OTHER_ROW_KEY] = [x.stock_id for x in 겹침]
    글 = [alert_text(client, 보낸) if 보낸 else "", other_row_text(client, 겹침) if 겹침 else "",
          suspect_text(client, 의심) if 의심 else "", 오류글]  # fmt: skip
    if any(글):
        기록["alert"], 기록["marks"] = "\n".join(x for x in 글 if x), {k: v for k, v in 표시.items() if v}
        기록["date_marks"] = 날표시
    if 보낸:
        기록["sale_alert"] = 글[0]  # 못 보내면 이 글만 남긴다 — 다른 문단은 표시가 안 적혀 다음 회차가 다시 만든다
    if 기록줄오류 is not None:
        # 알림을 실어 올린다 — `main` 이 알림을 보낸 뒤 실패로 닫는다
        raise GuardRecordError(기록 | {"calls": sess.calls}) from 기록줄오류
    return 기록 | {"calls": sess.calls}


class GuardRecordError(RuntimeError):
    """주문 뒤 기록을 못 적었다 — 실행 기록(알림 글 포함)을 들고 올라간다"""

    def __init__(self, 기록: dict[str, Any]) -> None:
        super().__init__("장중 매도 기록 실패")
        self.record = 기록


def alerted_ids(client: Any, key: str, today: date) -> set[int]:
    """오늘 이미 알린 종목(종목마다 하루 한 번 알림의 표시 — {date, ids})"""
    본 = db.get_setting(client, key, None)
    if not isinstance(본, dict) or 본.get("date") != today.isoformat():
        return set()
    return {int(i) for i in 본.get("ids") or []}


def mark_ids(client: Any, now: datetime, marks: dict[str, list[int]]) -> None:
    """알림을 **보낸 뒤** 종목 표시를 더한다(25.1772)"""
    오늘 = now.astimezone(cal.market_tz("KR")).date()
    for key, ids in marks.items():
        if ids:
            모두 = sorted(alerted_ids(client, key, 오늘) | set(ids))
            db.set_setting(client, key, {"date": 오늘.isoformat(), "ids": 모두})


def other_row_text(client: Any, xs: list[Hit]) -> str:
    """같은 날 아침 정리 매도 줄과 겹쳐 장중 매도를 못 낸 종목(25.1772)"""
    이름 = _names(client, [x.stock_id for x in xs])
    줄 = [f"  · {이름.get(x.stock_id, x.code)} — {note(x)}" for x in xs]
    return "\n".join(["⚠️ KIS모의투자 장중 감시 — 오늘 아침 정리 매도 줄과 겹쳐 장중 손절·익절을 내지 못함"
                      "(같은 날 같은 종목 매도 한 줄 제약 — 11월 색인 고침 전까지)", *줄])  # fmt: skip


def suspect_text(client: Any, xs: list[Hit]) -> str:
    """기업행위 의심으로 팔지 않은 종목(종목마다 하루 한 번 — `run` 이 거른다)"""
    이름 = _names(client, [x.stock_id for x in xs])
    줄 = [f"  · {이름.get(x.stock_id, x.code)} 현재가 {x.price:,.0f} — 아침 기준 종가 {x.line:,.0f} 의 ±31% 밖"
          for x in xs]
    return "\n".join(["⚠️ KIS모의투자 장중 감시 — 기업행위 의심(분할·병합 재상장 등)으로 장중 매도를 내지 않음", *줄])


def _names(client: Any, ids: list[int]) -> dict[int, str]:
    import json

    try:
        return {int(r["id"]): str(r["name"]) for r in client.execute(NAME_SQL, [json.dumps(ids)]).dicts()}
    except Exception:  # noqa: BLE001 — 이름을 못 읽어도 코드로 알린다(주문은 이미 나갔다)
        return {}


def alert_text(client: Any, sent: list[tuple[Hit, str]]) -> str:
    """텔레그램 한 통 — 종목 이름·선·현재가·수량·결과. 비공개 대화방으로만 간다(공개 로그에는 찍지 않는다)"""
    이름 = _names(client, [x.stock_id for x, _ in sent])
    줄 = ["⚡ KIS모의투자 장중 매도 (H 의 손절·익절선을 장중에)"]
    for x, 결과 in sent:
        줄.append(f"  · {이름.get(x.stock_id, x.code)} {x.quantity:,}주 시장가 — {note(x)} → {결과}")
    줄.append("  가상모의투자 H 는 아침 일봉 판정 그대로입니다(docs/paper.md 15.11)")
    return "\n".join(줄)


#: 아침 감시선이 없어 꺼진 까닭의 머리 — 이것만 문제(건너뜀)로 세고 알린다
NO_LINES = "오늘 감시선 없음"


def run_status(기록: dict[str, Any]) -> str:
    """실행 기록 상태 — **진짜 멈춤만** `skipped`(주간 요약이 문제로 센다): 아침 감시선 없음·잘린 잔고.
    일일 배치 실행 중·키 없음·멈춤 스위치·보유 없음은 정상 쉼이라 `success`(25.1769, 교차검증 [권고] — 쉼까지
    건너뜀이면 주간 요약에 매주 "건너뜀 N" 이 떠 진짜 멈춤이 묻혔다). 정리 줄과 겹쳐 못 낸 종목도 멈춤이다.
    판 뒤 알림을 못 보냈으면 `partial` — 주문은 나갔는데 사람은 모른다(25.1772)"""
    # 남은 판 알림이 또 못 갔다 — 감시선 없음·잘림 회차라도 부분(25.1777)
    if 기록.get("alert_error") and 기록.get("unsent_pending"):
        return "partial"
    if str(기록.get("off", "")).startswith(NO_LINES) or "잘림" in str(기록.get("skipped", "")):  # 대기·실행 중은 정상
        return "skipped"
    # 판 뒤 알림 실패가 먼저 — 계산 오류·정리 줄 겹침이 있는 날에도 "주문은 나갔는데 사람은 모름" 이 묻히지 않게
    # (25.1775)
    if 기록.get("alert_error") and (set(기록.get("sent") or {}) & {"accepted", "no_response", "rejected"}
                                   or 기록.get("unsent_pending")):  # fmt: skip
        return "partial"
    if (기록.get("sent") or {}).get("skipped_other_row") or 기록.get("line_errors"):
        return "skipped"
    return "success"


def no_lines_alert(client: Any, now: datetime, why: str = "") -> str | None:
    """오늘 국내 일일 배치는 끝났는데 감시선이 없는 날 한 번 — 장중 손절·익절이 그날 꺼졌다(25.1769). 보냈다는
    표시는 **보낸 뒤** `mark_alerted` 로(25.1770 — 먼저 적으면 전송 실패에 그날 알림이 다시 안 갔다)"""
    오늘 = now.astimezone(cal.market_tz("KR")).date().isoformat()
    if db.get_setting(client, NO_LINES_ALERT_KEY, None) == 오늘:
        return None
    # 까닭을 싣는다 — 일정 묵음이면 일일 배치가 아니라 `kis-flows`·실전 시세 키를 봐야 한다(25.1773)
    까닭 = why.split(" — ", 1)[1] if " — " in why else ""
    if "기업행위 일정" in 까닭:
        볼곳 = f"{까닭} — kis-flows 실행·실전 시세 키 확인"
    elif "계산 오류" in 까닭:
        볼곳 = f"{까닭} — 일일 배치 실행 기록 guard_lines 확인"
    else:
        볼곳 = "아침 가상모의투자 단계가 감시선을 적지 못했습니다(일일 배치 확인)"
    return f"⚠️ KIS모의투자 장중 손절·익절 꺼짐 — {볼곳}. 그날 매도는 아침 일봉 판정만 따릅니다"


def kst_today(now: datetime) -> str:
    return now.astimezone(cal.market_tz("KR")).date().isoformat()


def unsent_raw(client: Any, *, repair: bool = False) -> dict[str, str] | None:
    """남겨 둔 못 보낸 장중 매도 알림 원문 — {date, text}. 없으면 None. 보낸 뒤에는 빈 칸(`{}`)으로 비운다 — 배치는
    설정 행을 지우지 않는다(`test_settings_keys`). `repair` 면 25.1774 가 남긴 `null` 행(매 회차 "설정을 읽지 못함"
    경고)을 빈 칸으로 고친다(25.1775 교차검증 [권고])"""
    못읽음: list[str] = []
    값 = db.get_setting(client, UNSENT_KEY, None, 못읽음=못읽음)
    if 못읽음 and repair:
        db.set_setting(client, UNSENT_KEY, {})
    return 값 if isinstance(값, dict) and 값.get("text") else None


def unsent_text(client: Any, now: datetime, *, repair: bool = False) -> str | None:
    """앞 회차(또는 지난 장 마지막 회차)에 못 보낸 장중 매도 알림 — 머리를 한 번 붙여 다시 보낸다. 지난 날 글도 버리지
    않는다(25.1775 — 마지막 회차에서 실패하면 다음 날 날짜가 달라 조용히 버려졌다). 머리는 "겹칠 수 있음" — 전송 여부
    모름(`TelegramUncertainError`)·일부만 감(`TelegramPartialError`)도 여기로 온다"""
    값 = unsent_raw(client, repair=repair)
    if 값 is None:
        return None
    언제 = "앞 회차에" if 값.get("date") == kst_today(now) else f"지난 장({값.get('date')})부터"
    return f"({언제} 보내지 못했거나 전송 여부를 모르는 장중 매도 알림 — 겹칠 수 있음)\n{값['text']}"


def mark_alerted(client: Any, now: datetime, key: str = NO_LINES_ALERT_KEY) -> None:
    db.set_setting(client, key, now.astimezone(cal.market_tz("KR")).date().isoformat())


#: 실행이 예외로 실패한 날 한 번 알린다 — 상태 화면은 성공 줄만 빼고 실패는 보이지만, 사람이 매일 보는 곳은 텔레그램이다
#: (25.1770, 25.1769 교차검증 [권고] — 토큰·코드 오류로 매 회차 실패해도 주간 요약에서만 드러났다)
FAIL_ALERT_KEY = "kis_guard_fail_alerted"


def main() -> int:
    parser = argparse.ArgumentParser(description="KIS모의투자 장중 손절·익절")
    parser.add_argument("--force", action="store_true", help="시간·휴장일 판단을 건너뜀(시험용 — 주문은 그대로 난다)")
    parser.add_argument("--dry-run", action="store_true", help="닿은 종목만 세고 주문·알림은 내지 않음")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    now = datetime.now(UTC)
    if not args.force and (까닭 := due(now)):
        log.info("건너뜀: %s", 까닭)  # DB 를 열지 않는다
        return 0
    from batch.core.client import TursoClient
    from batch.notify import telegram

    client = TursoClient()
    rid = db.start_batch_run(client, job_name=JOB_NAME, market="KR",
                             trade_date=now.astimezone(cal.market_tz("KR")).date().isoformat())  # fmt: skip
    try:
        try:
            기록 = run(client, now, dry_run=args.dry_run)
            실패: BaseException | None = None
        except GuardRecordError as exc:
            기록, 실패 = exc.record, exc.__cause__ or exc
        글, 표시 = 기록.pop("alert", None), 기록.pop("marks", None) or {}
        날표시 = 기록.pop("date_marks", None) or []
        감시선없음 = not 글 and str(기록.get("off", "")).startswith(NO_LINES)
        # 보내기 전 DB 읽기(감시선 없음 표시·못 보낸 글)는 깨져도 **이번 글은 보낸다** — 주문 직후 DB 가 잠깐 끊기면
        # 이 읽기가 먼저 던져 판 알림이 한 통도 안 갔다(25.1788, 결함 루프 — 25.1769 "기록이 깨져도 알림은 간다")
        못보냄 = None
        try:
            if 감시선없음 and not args.dry_run:  # 확인 실행은 알림·표시를 내지 않는다(25.1772)
                글 = no_lines_alert(client, now, str(기록.get("off", "")))
            못보냄 = unsent_text(client, now, repair=True) if not args.dry_run else None
        except Exception as exc:  # noqa: BLE001 — 알림이 먼저. 실패는 아래에서 실행 기록으로
            기록["db_error"] = type(exc).__name__
            실패 = 실패 or exc
        if 못보냄:
            글 = f"{못보냄}\n{글}" if 글 else 못보냄
        if 글:
            try:
                telegram.send(글)
                기록["alerted"] = True
            except Exception as exc:  # noqa: BLE001 — 주문은 이미 냈다. 알림만 빠진다
                기록["alert_error"] = type(exc).__name__
                if 못보냄:
                    기록["unsent_pending"] = True  # 앞 판 알림이 아직 사람에게 안 갔다 — 이 회차도 부분(25.1775)
                if 판글 := 기록.get("sale_alert"):
                    # 판 알림은 다음 회차가 다시 만들지 못한다 — **판 알림 원문만** 머리 없이 쌓아 다음 회차가 보낸다
                    # (25.1774·25.1775 — 글 전체를 남기면 머리·표시성 문단이 회차마다 겹쳐 쌓였다)
                    try:
                        앞 = unsent_raw(client)
                        # 날짜는 처음 못 보낸 날 그대로(머리), 문단마다 그날 날짜를 단다 — 지난 장 글과 오늘 매도가
                        # 한 머리 아래 서로의 날짜로 읽히지 않게(25.1776·25.1777 교차검증)
                        문단 = f"[{kst_today(now)}] {판글}"
                        새글 = f"{앞['text']}\n{문단}" if 앞 else 문단
                        db.set_setting(client, UNSENT_KEY, {"date": 앞["date"] if 앞 else kst_today(now), "text": 새글})
                        # 글이 남았으니 순간 거부는 "알린 것" 으로 친다 — 안 그러면 텔레그램이 끊긴 동안 같은 거부
                        # 문단이 회차마다 쌓였다(25.1777 교차검증 [권고])
                        mark_ids(client, now, {TRANSIENT_KEY: 표시.get(TRANSIENT_KEY, [])})
                    except Exception as e2:  # noqa: BLE001 — 텔레그램·DB 가 함께 끊겼다. 다음 아침 맞추기가 줄을 본다
                        기록["db_error"] = type(e2).__name__
                        실패 = 실패 or e2
            else:  # 표시는 보낸 뒤에만 — 실패한 날은 다음 회차가 다시 알린다
                try:
                    if 감시선없음:
                        mark_alerted(client, now)
                    for key in 날표시:
                        mark_alerted(client, now, key)
                    mark_ids(client, now, 표시)
                    if 못보냄:  # 빈 칸으로 — null 로 덮으면 매 회차 "설정을 읽지 못함" 경고가 났다(25.1775)
                        db.set_setting(client, UNSENT_KEY, {})
                except Exception as exc:  # noqa: BLE001 — 보냈다. 표시를 못 적으면 다음 회차가 한 번 더 알릴 뿐
                    기록["db_error"] = type(exc).__name__
                    실패 = 실패 or exc
        기록.pop("sale_alert", None)  # 글은 실행 기록(step_log)에 넣지 않는다 — 종목·수량
        if 실패 is not None:
            raise 실패
        db.finish_batch_run(client, rid, status=run_status(기록), step_log={"guard": 기록})
        log.info("장중 감시: %s", {k: v for k, v in 기록.items() if k in ("off", "skipped", "lines", "hits", "sent")})
        return 0
    except Exception as exc:
        # 실행 기록 닫기도 DB 라 깨질 수 있다 — 그 실패가 아래 실패 알림을 가리지 않게(25.1788). 한도는 실패로
        # 알리지 않는다(25.6) — 닫기가 같은 한도로 던져도 건너뜀으로 끝낸다(25.1791, 25.1788 교차검증 [참고]: 닫기
        # 실패를 삼키자 한도 회차가 "실행 실패" 알림으로 흘렀다)
        한도 = db.quota_reason(exc)
        try:
            if 한도:
                db.finish_batch_run(client, rid, status="skipped", error_text=한도)
            else:
                db.finish_batch_run(client, rid, status="failed", error_text=f"{type(exc).__name__}: {str(exc)[:300]}")
        except Exception:  # noqa: BLE001 — 한도면 다음 실행의 reap 이, 그 밖에는 `entry.guard` 의 `fail_open_runs` 가 닫는다
            log.warning("실행 기록을 닫지 못했습니다")
        if 한도:
            return 0
        try:
            오늘 = now.astimezone(cal.market_tz("KR")).date().isoformat()
            try:
                본날 = db.get_setting(client, FAIL_ALERT_KEY, None)
            except Exception:  # noqa: BLE001 — 표시를 못 읽으면 보낸다(두 통이 한 통도 없는 것보다 낫다)
                본날 = None
            if not args.dry_run and 본날 != 오늘:
                telegram.send(f"⚠️ KIS모의투자 장중 손절·익절 실행 실패 ({type(exc).__name__}) — 실행 기록·"
                              "Actions 로그 확인. 오늘 같은 실패는 다시 알리지 않습니다")  # fmt: skip
                mark_alerted(client, now, FAIL_ALERT_KEY)
        except Exception:  # noqa: BLE001 — 알림 실패는 원래 실패를 가리지 않는다(아래에서 원래 예외를 올린다)
            pass
        raise
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(guard(main))
