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
from batch.core import db
from batch.core.entry import guard
from batch.jobs import broker_mirror as bmj
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from batch.sources.kis import KisFailed

log = logging.getLogger("intraday_guard")
JOB_NAME = "kis_guard"

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
#: 오늘 이미 장중 매도를 낸 종목 — 한 종목 하루 한 번(유일 색인 `idx_paper_broker_cleanup` 도 막는다)
GUARDED_TODAY_SQL = (
    "SELECT stock_id FROM paper_broker_orders WHERE broker = ? AND trade_date = ? AND side = 'sell'"
    " AND reason IN (?, ?)"
)
NAME_SQL = (
    "SELECT id, COALESCE(name_ko, name_en, ticker) AS name FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
)
#: 경계값(정확히 손절선)이 부동소수 찌꺼기로 빗나가지 않게 — 판정식의 소수 여섯째 자리 반올림과 같은 여유
EPS = 1e-9


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


KIND_TEXT = {"stop": "손절선", "trail": "익절선(고점 대비)", "target": "목표가"}


def save_lines(
    client: Any, market: str, today: date, account: str, rule: str, lines: dict[int, dict[str, Any]],
) -> dict[str, int]:
    """아침 가상모의투자 단계가 부른다 — 그 시장의 감시선을 오늘 날짜로 덮어쓴다(다른 시장 칸은 그대로).
    건수만 돌려준다"""
    값 = db.get_setting(client, GUARD_KEY, None)
    값 = 값 if isinstance(값, dict) else {}
    값[market] = {"as_of": today.isoformat(), "account": account, "rule": rule,
                 "lines": {str(sid): 선 for sid, 선 in sorted(lines.items())}}  # fmt: skip
    db.set_setting(client, GUARD_KEY, 값)
    return {"n": len(lines), **{k: sum(k in 선 for 선 in lines.values()) for k in ("stop", "trail", "target")}}


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
        if 선.get("stop") is not None and p <= float(선["stop"]) * (1 + EPS):
            out.append(Hit(sid, h.code, int(h.sellable), p, float(선["stop"]), "stop", bm.REASON_GUARD_STOP))
        elif 선.get("trail") is not None and p <= float(선["trail"]) * (1 + EPS):
            out.append(Hit(sid, h.code, int(h.sellable), p, float(선["trail"]), "trail", bm.REASON_GUARD_TAKE))
        elif 선.get("target") is not None and p >= float(선["target"]) * (1 - EPS):
            out.append(Hit(sid, h.code, int(h.sellable), p, float(선["target"]), "target", bm.REASON_GUARD_TAKE))
    return out


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
    값 = db.get_setting(client, GUARD_KEY, None)
    칸 = (값 or {}).get("KR") if isinstance(값, dict) else None
    if not isinstance(칸, dict) or 칸.get("as_of") != 오늘.isoformat():
        return {"off": "오늘 감시선 없음 — 아침 가상모의투자 단계가 아직 안 돌았거나 실패"}
    선들 = {str(v.get("code")): (int(sid), v) for sid, v in (칸.get("lines") or {}).items() if v.get("code")}
    if not 선들:
        return {"lines": 0}
    if client.execute(BATCH_RUNNING_SQL, [(now - timedelta(minutes=BATCH_RUNNING_MINUTES)).isoformat()]).rows:
        return {"skipped": "일일 배치 실행 중 — KIS 초당 한도를 나눠 쓰지 않게"}
    sess = session or kv.Session(client, now, keys=kv.MAIN)
    보유, _합 = sess.balance()
    기록: dict[str, Any] = {"lines": len(선들), "holdings": len(보유)}
    if getattr(sess, "balance_truncated", False):
        return 기록 | {"skipped": "잔고 조회가 쪽 상한에 닿아 잘림", "calls": sess.calls}
    이미 = {int(r[0]) for r in client.execute(
        GUARDED_TODAY_SQL, [bmj.BROKER, 오늘.isoformat(), bm.REASON_GUARD_STOP, bm.REASON_GUARD_TAKE]).rows}
    닿음 = [x for x in hits(보유, 선들) if x.stock_id not in 이미]
    종류 = [x.kind for x in 닿음]
    기록["hits"] = {k: 종류.count(k) for k in ("stop", "trail", "target") if k in 종류}
    if dry_run or not 닿음:
        return 기록 | {"dry_run": dry_run, "calls": sess.calls}
    지금 = now.isoformat()
    상태: dict[str, int] = {}
    보낸: list[tuple[Hit, str]] = []
    for x in 닿음:
        client.execute(bmj.INSERT_SQL, [
            bmj.BROKER, None, GUARD_ACCOUNT, "KR", x.stock_id, x.code, "sell", "market", None, x.quantity,
            오늘.isoformat(), x.reason, kv.MAIN.source, 지금,
        ])  # fmt: skip
        mine = client.execute(bmj.MINE_SQL, [bmj.BROKER, 오늘.isoformat(), x.stock_id, "sell", -1, 지금]).rows
        if not mine:
            상태["skipped_other_row"] = 상태.get("skipped_other_row", 0) + 1  # 같은 날 정리 매도가 이미 있다
            continue
        rid = int(mine[0][0])
        보낸시각 = clock().isoformat()
        try:
            받음 = sess.order("sell", x.code, x.quantity, None)
            client.execute(bmj.SENT_UPDATE_SQL, ["accepted", 받음.order_no, 보낸시각, note(x), 보낸시각, rid])
            상태["accepted"] = 상태.get("accepted", 0) + 1
            보낸.append((x, "접수"))
        except (kv.NoResponse, requests.RequestException) as exc:
            # 갔는지 모른다 — 다시 보내지 않는다(두 번 주문). 다음 아침 체결 조회로 맞춘다
            글 = str(exc)[:200] if isinstance(exc, kv.NoResponse) else f"응답 없음 ({type(exc).__name__})"
            client.execute(bmj.SENT_UPDATE_SQL, ["sending", None, 보낸시각, f"{note(x)} · {글}", None, rid])
            상태["no_response"] = 상태.get("no_response", 0) + 1
            보낸.append((x, "응답 없음 — 내일 아침 맞춤"))
        except KisFailed as exc:
            글 = f"{note(x)} · {str(exc)[:150]}"
            client.execute(bmj.SENT_UPDATE_SQL, ["rejected", None, 보낸시각, 글, 보낸시각, rid])
            상태["rejected"] = 상태.get("rejected", 0) + 1
            보낸.append((x, "거부"))
    기록["sent"] = 상태
    if 보낸:
        기록["alert"] = alert_text(client, 보낸)
    return 기록 | {"calls": sess.calls}


def alert_text(client: Any, sent: list[tuple[Hit, str]]) -> str:
    """텔레그램 한 통 — 종목 이름·선·현재가·수량·결과. 비공개 대화방으로만 간다(공개 로그에는 찍지 않는다)"""
    import json

    줄들 = client.execute(NAME_SQL, [json.dumps([x.stock_id for x, _ in sent])]).dicts()
    이름 = {int(r["id"]): str(r["name"]) for r in 줄들}
    줄 = ["⚡ KIS모의투자 장중 매도 (H 의 손절·익절선을 장중에)"]
    for x, 결과 in sent:
        줄.append(f"  · {이름.get(x.stock_id, x.code)} {x.quantity:,}주 시장가 — {note(x)} → {결과}")
    줄.append("  가상모의투자 H 는 아침 일봉 판정 그대로입니다(docs/paper.md 15.11)")
    return "\n".join(줄)


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
        기록 = run(client, now, dry_run=args.dry_run)
        글 = 기록.pop("alert", None)
        if 글:
            try:
                telegram.send(글, with_disclaimer=False)
                기록["alerted"] = True
            except Exception as exc:  # noqa: BLE001 — 주문은 이미 냈다. 알림만 빠진다
                기록["alert_error"] = type(exc).__name__
        상태 = "skipped" if ("off" in 기록 or "skipped" in 기록) else "success"
        db.finish_batch_run(client, rid, status=상태, step_log={"guard": 기록})
        log.info("장중 감시: %s", {k: v for k, v in 기록.items() if k in ("off", "skipped", "lines", "hits", "sent")})
        return 0
    except Exception as exc:
        if db.quota_reason(exc):
            db.finish_batch_run(client, rid, status="skipped", error_text=db.quota_reason(exc))
            return 0
        db.finish_batch_run(client, rid, status="failed", error_text=f"{type(exc).__name__}: {str(exc)[:300]}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(guard(main))
