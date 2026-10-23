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

DB 읽기: 실행마다 설정 한 행·일일 배치 실행 중 확인 한 행·오늘 낸 장중 매도 몇 행. 시간 밖·휴장일은 DB 를 열지 않는다.
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
#: 시장가가 마감 단일가로 가 "현재가 닿음" 과 체결가가 따로 논다
WINDOW_KST = (dtime(9, 5), dtime(15, 15))
#: 일일 배치가 돌고 있으면 쉰다 — 같은 앱키의 초당 한도를 나눠 쓰면 배치 주문이 `EGW00201` 거부로 적힌다(웹 조회와 같은
#: 막기, `web/lib/kisVts.BATCH_RUNNING_WINDOW_MS`)
BATCH_RUNNING_MINUTES = 45
BATCH_RUNNING_SQL = (
    "SELECT 1 FROM batch_runs WHERE job_name IN ('daily_kr', 'daily_us') AND status = 'running' AND started_at >= ?"
    " LIMIT 1"
)
#: 오늘(한국 날짜) 국내 일일 배치가 끝났나 — 감시선이 없을 때 "기다림" 과 "멈춤" 을 가른다(25.1770)
DAILY_DONE_SQL = (
    "SELECT 1 FROM batch_runs WHERE job_name = 'daily_kr' AND status IN ('success', 'partial', 'failed', 'skipped')"
    " AND started_at >= ? LIMIT 1"
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
#: 다시 보내도 되는 거부 — KIS 초당 한도(`EGW00201`)·토큰 만료. 잔고·가격 제한 같은 진짜 거부는 그대로 둔다
TRANSIENT_CODES = (kv.RATE_LIMIT_CODE, "EGW00123", "EGW00121")
#: 다시 보낼 때 수량·사유도 지금 값으로(25.1770 — 처음 값이 남으면 다음 아침 맞추기가 거짓 `partial`·`unknown`)
RETRY_SQL = (
    "UPDATE paper_broker_orders SET status = 'sending', message = NULL, created_at = ?, quantity = ?, reason = ?"
    " WHERE id = ? AND status = 'rejected'"
)
#: 기업행위 의심으로 팔지 않은 종목을 알린 날·종목 — 10분마다 같은 알림을 되풀이하지 않게(25.1771)
SUSPECT_KEY = "kis_guard_suspect_alerted"
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
) -> dict[str, int]:
    """아침 가상모의투자 단계가 부른다 — 그 시장의 감시선을 오늘 날짜로 덮어쓴다(다른 시장 칸은 그대로).
    건수만 돌려준다"""
    빼기, 까닭 = action_today(client, sorted(lines), today) if market == "KR" else (set(), None)
    lines = {sid: 선 for sid, 선 in lines.items() if sid not in 빼기}
    값 = db.get_setting(client, GUARD_KEY, None)
    값 = 값 if isinstance(값, dict) else {}
    값[market] = {"as_of": today.isoformat(), "account": account, "rule": rule,
                 "lines": {str(sid): 선 for sid, 선 in sorted(lines.items())}}  # fmt: skip
    db.set_setting(client, GUARD_KEY, 값)
    return {"n": len(lines), **{k: sum(k in 선 for 선 in lines.values()) for k in ("stop", "trail", "target")},
            **({"action_day": len(빼기)} if 빼기 else {}), **({"action_day_error": 까닭} if 까닭 else {})}


def action_today(client: Any, stock_ids: list[int], today: date) -> tuple[set[int], str | None]:
    """**오늘**이 무상·유상증자 권리락일인 종목 — 그날은 감시선을 만들지 않는다(25.1771, 25.1770 교차검증 [필수]:
    기계적 하락이 나는 바로 그날은 아침 배치가 아직 그 하락을 못 봐 `corporate_action_recent` 가 거르지 못했고, 권리락
    전 원가의 손절선에 조정된 현재가가 닿아 거짓 장중 손절이 실제 KIS 매도로 나갔다). 장중 알림의 25.1725 와 같은 일정
    (`kr_corp_events` 기준일 → 권리락일)이지만 "다음 장" 이 아니라 오늘로 본다(아침 단계가 개장 뒤에 돌 수 있다).
    못 읽으면 **모두** 뺀다(보수 — 그날 장중 감시가 꺼질 뿐) — (종목, 못 읽은 까닭)"""
    from batch.jobs import monitor_targets as mt

    if not stock_ids:
        return set(), None
    try:
        끝 = (today + timedelta(days=mt.ACTION_DAY_LOOKAHEAD_DAYS)).isoformat()
        rows = client.execute(mt.ACTION_DAY_SQL, [today.isoformat(), 끝]).rows
        감시 = set(stock_ids)
        return {int(r[0]) for r in rows if int(r[0]) in 감시 and mt.ex_date(str(r[1])[:10]) == today}, None
    except Exception as exc:  # noqa: BLE001 — 모르면 그날 선을 만들지 않는다(거짓 손절보다 꺼짐이 낫다)
        if db.quota_reason(exc):
            raise
        return set(stock_ids), type(exc).__name__


def due(now: datetime, market: str = "KR") -> str | None:
    """지금 볼 때인가 — 아니면 까닭. DB 를 열기 전에 본다(시간 밖 예약 실행이 읽기를 쓰지 않게)"""
    현지 = now.astimezone(cal.market_tz(market))
    if not cal.is_session(market, 현지.date()):
        return "휴장일"
    if not WINDOW_KST[0] <= 현지.time() <= WINDOW_KST[1]:
        return "장중 감시 시간 밖"
    return None


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
        # 끝남 = 성공·부분뿐 아니라 실패·건너뜀(한도)도 — 그날은 감시선이 영영 안 생긴다(25.1771 교차검증 [권고])
        if (not client.execute(DAILY_DONE_SQL, [시작]).rows
                and now.astimezone(cal.market_tz("KR")).time() < WAIT_LIMIT_KST):  # fmt: skip
            return {"off": WAITING}
        return {"off": f"{NO_LINES} — 오늘 국내 일일 배치가 끝났거나 {WAIT_LIMIT_KST:%H:%M} 이 지났는데 감시선이 없음"}
    선들 = {str(v.get("code")): (int(sid), v) for sid, v in (칸.get("lines") or {}).items() if v.get("code")}
    if not 선들:
        return {"lines": 0}
    sess = session or kv.Session(client, now, keys=kv.MAIN)
    보유, _합 = sess.balance()
    기록: dict[str, Any] = {"lines": len(선들), "holdings": len(보유)}
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
    if 의심:
        기록["suspect"] = len(의심)  # 기업행위 의심 — 팔지 않는다(알림만, 종목마다 하루 한 번)
        본 = db.get_setting(client, SUSPECT_KEY, None)
        본 = set(본.get("ids") or []) if isinstance(본, dict) and 본.get("date") == 오늘.isoformat() else set()
        의심 = [x for x in 의심 if x.stock_id not in 본]
        if 의심 and not dry_run:
            db.set_setting(client, SUSPECT_KEY, {"date": 오늘.isoformat(),
                                                 "ids": sorted(본 | {x.stock_id for x in 의심})})  # fmt: skip
    종류 = [x.kind for x in 닿음]
    기록["hits"] = {k: 종류.count(k) for k in ("stop", "trail", "target") if k in 종류}
    if dry_run or not 닿음:
        if 의심 and not dry_run:
            기록["alert"] = suspect_text(client, 의심)
        return 기록 | {"dry_run": dry_run, "calls": sess.calls}
    지금 = now.isoformat()
    상태: dict[str, int] = {}
    보낸: list[tuple[Hit, str]] = []
    기록줄오류: Exception | None = None
    for x in 닿음:
        try:
            if x.stock_id in 다시:
                rid = 다시[x.stock_id]
                client.execute(RETRY_SQL, [지금, x.quantity, x.reason, rid])
                상태["retried"] = 상태.get("retried", 0) + 1
            else:
                client.execute(bmj.INSERT_SQL, [
                    bmj.BROKER, None, GUARD_ACCOUNT, "KR", x.stock_id, x.code, "sell", "market", None, x.quantity,
                    오늘.isoformat(), x.reason, kv.MAIN.source, 지금,
                ])  # fmt: skip
                mine = client.execute(bmj.MINE_SQL, [bmj.BROKER, 오늘.isoformat(), x.stock_id, "sell", -1, 지금]).rows
                if not mine:
                    상태["skipped_other_row"] = 상태.get("skipped_other_row", 0) + 1  # 같은 날 정리 매도가 이미 있다
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
            결과 = "거부(초당 한도·토큰) — 다음 회차에 다시 보냄" if 순간 else f"거부 ({bm._까닭(str(exc))})"
            값 = ["rejected", None, 보낸시각, f"{note(x)} · {str(exc)[:150]}", 보낸시각, rid]
            상태["rejected"] = 상태.get("rejected", 0) + 1
        보낸.append((x, 결과))  # 주문은 이미 나갔다 — 기록이 깨져도 알림은 간다(25.1769)
        try:
            client.execute(bmj.SENT_UPDATE_SQL, 값)
        except Exception as exc:  # noqa: BLE001 — 줄은 `sending` 으로 남아 다음 아침 체결 조회가 맞춘다
            기록줄오류 = exc
            break
    기록["sent"] = 상태
    if 보낸:
        기록["alert"] = alert_text(client, 보낸) + ("\n" + suspect_text(client, 의심) if 의심 else "")
    elif 의심:
        기록["alert"] = suspect_text(client, 의심)
    if 기록줄오류 is not None:
        # 알림을 실어 올린다 — `main` 이 알림을 보낸 뒤 실패로 닫는다
        raise GuardRecordError(기록 | {"calls": sess.calls}) from 기록줄오류
    return 기록 | {"calls": sess.calls}


class GuardRecordError(RuntimeError):
    """주문 뒤 기록을 못 적었다 — 실행 기록(알림 글 포함)을 들고 올라간다"""

    def __init__(self, 기록: dict[str, Any]) -> None:
        super().__init__("장중 매도 기록 실패")
        self.record = 기록


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
    건너뜀이면 주간 요약에 매주 "건너뜀 N" 이 떠 진짜 멈춤이 묻혔다)"""
    if str(기록.get("off", "")).startswith(NO_LINES) or "잘림" in str(기록.get("skipped", "")):  # 대기·실행 중은 정상
        return "skipped"
    return "success"


def no_lines_alert(client: Any, now: datetime) -> str | None:
    """오늘 국내 일일 배치는 끝났는데 감시선이 없는 날 한 번 — 장중 손절·익절이 그날 꺼졌다(25.1769). 보냈다는
    표시는 **보낸 뒤** `mark_alerted` 로(25.1770 — 먼저 적으면 전송 실패에 그날 알림이 다시 안 갔다)"""
    오늘 = now.astimezone(cal.market_tz("KR")).date().isoformat()
    if db.get_setting(client, NO_LINES_ALERT_KEY, None) == 오늘:
        return None
    return ("⚠️ KIS모의투자 장중 손절·익절 꺼짐 — 오늘 아침 가상모의투자 단계가 감시선을 적지 못했습니다"
            "(일일 배치 확인). 그날 매도는 아침 일봉 판정만 따릅니다")


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
        글 = 기록.pop("alert", None)
        감시선없음 = not 글 and str(기록.get("off", "")).startswith(NO_LINES)
        if 감시선없음:
            글 = no_lines_alert(client, now)
        if 글:
            try:
                telegram.send(글)
                기록["alerted"] = True
                if 감시선없음:
                    mark_alerted(client, now)
            except Exception as exc:  # noqa: BLE001 — 주문은 이미 냈다. 알림만 빠진다
                기록["alert_error"] = type(exc).__name__
        if 실패 is not None:
            raise 실패
        db.finish_batch_run(client, rid, status=run_status(기록), step_log={"guard": 기록})
        log.info("장중 감시: %s", {k: v for k, v in 기록.items() if k in ("off", "skipped", "lines", "hits", "sent")})
        return 0
    except Exception as exc:
        if db.quota_reason(exc):
            db.finish_batch_run(client, rid, status="skipped", error_text=db.quota_reason(exc))
            return 0
        db.finish_batch_run(client, rid, status="failed", error_text=f"{type(exc).__name__}: {str(exc)[:300]}")
        try:
            if db.get_setting(client, FAIL_ALERT_KEY, None) != now.astimezone(cal.market_tz("KR")).date().isoformat():
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
