"""국내 수급 일별 수집 — 투자자별 순매수 · 공매도 · 신용잔고 · 증권사 투자의견 · 기업행위 일정
(docs/data-sources.md 3.1, docs/infra.md 25.987·25.988).

KIS 는 투자자별·신용을 **최근 30일만**, 공매도를 약 100일 준다(2026-10-07 실측).
과거로 거슬러 받을 수 없으니 매일 쌓는다.
지금은 **모으기만** 한다 — 팩터·신호로 쓰려면 계산식을 docs/factors.md 에 먼저 적고(문서 없는 팩터 금지), 쌓인 이력으로
백테스트한 뒤 켠다(CLAUDE.md 발굴 루프).

대상: 국내 유니버스 편입 종목(약 870) + 보유·관심 종목. 종목마다 3회 호출, 초당 15건 → 약 3~4분.
장 마감 뒤(18시 무렵) 돈다 — 장중의 투자자별 값은 잠정치다.

실행
  python -m batch.jobs.kis_flows [--limit N]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.sources import kis

log = logging.getLogger(__name__)
JOB_NAME = "kis_flows"
#: 공매도 조회 창 — 첫날은 이만큼 거슬러 채우고, 그 뒤로는 겹치는 날을 덮어쓴다(잠정치 정정)
SHORT_LOOKBACK_DAYS = 45
#: 투자의견 조회 창 — 표가 비었으면(첫 실행) 1년, 아니면 겹치게 60일 (25.988)
OPINION_FIRST_DAYS = 365
OPINION_DAYS = 60
#: 기업행위 일정 창 — 지난 7일(정정분)부터 앞으로 45일 (25.994). 한 번에 100행까지라 배당은 지난30~앞90일이면 잘렸다
#: (실측 100행). 리포트는 앞 10일만 쓴다(`daily.CORP_EVENT_DAYS`)
EVENT_BACK_DAYS = 7
EVENT_AHEAD_DAYS = 45
#: 유상증자는 KIS 가 **청약 시작일**(`sub_term_ft`)로 거른다 — 2026-10-07 실측: 기준일 08-31 행이 청약 10-06 이라
#: 09-30~ 창에 걸렸다. 청약은 기준일 뒤 약 5주라, 앞 10일 안 기준일을 놓치지 않게 앞 창을 넉넉히 둔다 (25.1008)
#: `[확인필요: 명세의 거르는 날짜]`
EVENT_AHEAD_DAYS_BY_KIND = {"rights": 120}
HELD_KR_TICKERS = (
    "SELECT s.ticker FROM positions p JOIN stocks s ON s.id = p.stock_id WHERE p.quantity > 0 AND s.country = 'KR'"
)
#: 한 종류가 이만큼 오면 잘렸을 수 있다 — 실행 기록에 남긴다
EVENT_PAGE_ROWS = 100
OPINION_UPSERT = (
    "INSERT INTO kr_opinions (stock_id, date, broker, opinion, opinion_code, prev_opinion_code, target_price,"
    " source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (stock_id, date, broker) DO UPDATE SET opinion = excluded.opinion,"
    " opinion_code = excluded.opinion_code, prev_opinion_code = excluded.prev_opinion_code,"
    " target_price = excluded.target_price, source = excluded.source, fetched_at = excluded.fetched_at"
)
EVENT_UPSERT = (
    "INSERT INTO kr_corp_events (code, kind, record_date, name, detail, source, fetched_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (code, kind, record_date) DO UPDATE SET name = excluded.name, detail = excluded.detail,"
    " source = excluded.source, fetched_at = excluded.fetched_at"
)
#: 스레드 수. 초당 한도는 `kis.QuoteClient` 의 문이 지킨다
WORKERS = 8
COLUMNS = (
    "frgn_net_qty", "orgn_net_qty", "prsn_net_qty", "frgn_net_amt", "orgn_net_amt", "prsn_net_amt",
    "short_qty", "short_vol_pct", "credit_rmnd_qty", "credit_rmnd_pct",
)  # fmt: skip


def targets(client: TursoClient) -> list[tuple[int, str]]:
    """(stock_id, 6자리 코드). 최신 국내 유니버스 편입 + 보유 + 관심."""
    rs = client.execute(
        "SELECT DISTINCT s.id, s.ticker FROM stocks s"
        " WHERE s.country = 'KR' AND s.status <> 'delisted' AND (s.id IN ("
        "   SELECT um.stock_id FROM universe_members um WHERE um.included = 1 AND um.snapshot_date = ("
        "     SELECT MAX(u2.snapshot_date) FROM universe_members u2 JOIN stocks s2 ON s2.id = u2.stock_id"
        "     WHERE s2.country = 'KR'))"
        "  OR s.id IN (SELECT stock_id FROM positions WHERE quantity > 0)"
        "  OR s.id IN (SELECT stock_id FROM watchlist))"
        " ORDER BY s.id"
    )
    return [(int(r[0]), str(r[1])) for r in rs.rows if len(str(r[1])) == 6]


def merge(investor: dict[str, dict], short: dict[str, dict], credit: dict[str, dict]) -> dict[str, dict]:
    """날짜 → 칸. 한 출처가 비어도 다른 출처의 칸은 남긴다(못 받은 칸은 None — 0 이 아니다)."""
    out: dict[str, dict] = {}
    for part in (investor, short, credit):
        for day, cols in part.items():
            out.setdefault(day, {}).update(cols)
    return out


#: 받은 칸만 덮는다 — 이번에 못 받은 칸(None)은 `COALESCE` 로 있던 값을 둔다. 질의는 고정이다(동적 SQL 을 늘리지 않는다)
UPSERT = (
    "INSERT INTO kr_flows (stock_id, date, frgn_net_qty, orgn_net_qty, prsn_net_qty, frgn_net_amt, orgn_net_amt,"
    " prsn_net_amt, short_qty, short_vol_pct, credit_rmnd_qty, credit_rmnd_pct, source, fetched_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (stock_id, date) DO UPDATE SET"
    " frgn_net_qty = COALESCE(excluded.frgn_net_qty, kr_flows.frgn_net_qty),"
    " orgn_net_qty = COALESCE(excluded.orgn_net_qty, kr_flows.orgn_net_qty),"
    " prsn_net_qty = COALESCE(excluded.prsn_net_qty, kr_flows.prsn_net_qty),"
    " frgn_net_amt = COALESCE(excluded.frgn_net_amt, kr_flows.frgn_net_amt),"
    " orgn_net_amt = COALESCE(excluded.orgn_net_amt, kr_flows.orgn_net_amt),"
    " prsn_net_amt = COALESCE(excluded.prsn_net_amt, kr_flows.prsn_net_amt),"
    " short_qty = COALESCE(excluded.short_qty, kr_flows.short_qty),"
    " short_vol_pct = COALESCE(excluded.short_vol_pct, kr_flows.short_vol_pct),"
    " credit_rmnd_qty = COALESCE(excluded.credit_rmnd_qty, kr_flows.credit_rmnd_qty),"
    " credit_rmnd_pct = COALESCE(excluded.credit_rmnd_pct, kr_flows.credit_rmnd_pct),"
    " source = excluded.source, fetched_at = excluded.fetched_at"
)


def upserts(stock_id: int, rows: dict[str, dict], fetched_at: str) -> list[tuple[str, list]]:
    """받은 칸만 덮는다 — 다른 날 받은 칸(예: 어제 받은 신용)을 None 으로 지우지 않는다."""
    return [
        (UPSERT, [stock_id, day, *(cols.get(c) for c in COLUMNS), kis.SOURCE, fetched_at])
        for day, cols in sorted(rows.items())
        if any(cols.get(c) is not None for c in COLUMNS)
    ]


#: 장중 경로의 "잡음" 표시와 같은 앞머리 (`web/app/api/cron/intraday/route.ts` `CLAIM_PREFIX`, 25.1086). 배치가 넣은
#: 행을 보내기 전에 장중 경로의 밀린 알림 발송(`flushOnce`)이 같은 NULL 행을 잡아 보내면 두 번 갔다. 잡은 채로 넣고,
#: 보내면 시각으로, 못 보내면 NULL 로 되돌린다. 배치가 도중에 죽으면 경로가 10분 뒤(묵은 잡음) 다시 보낸다
CLAIM_PREFIX = "claim:"

AFTER_HOURS_INSERT = (
    "INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at, sent_at)"
    " VALUES (?, 'KR', ?, ?, ?, ?, ?, ?) ON CONFLICT (stock_id, trigger_type, trade_date) DO NOTHING"
)


def _after_hours(client: TursoClient, qc: kis.QuoteClient, today, failed: dict[str, str]) -> dict:
    """보유 종목의 시간외 단일가가 문턱(장중 급등락과 같은 설정) 이상 움직였으면 알림을 남기고 보낸다 (25.992).

    하루 한 번은 `alerts` 의 UNIQUE 가 지킨다 — 손으로 다시 돌려도 두 번 보내지 않는다.
    로그에는 건수만 찍는다(공개 저장소)."""
    from batch.notify import telegram
    from batch.services import after_hours as ah

    보유 = [(int(r[0]), str(r[1]), str(r[2])) for r in client.execute(
        "SELECT p.stock_id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) FROM positions p"
        " JOIN stocks s ON s.id = p.stock_id WHERE p.quantity > 0 AND s.country = 'KR'"
    ).rows if len(str(r[1])) == 6]  # fmt: skip
    if not 보유:
        return {"holdings": 0}
    # **그날 시간외가 끝난 거래일로 적고, 조용시간이면 보내지 않는다** (25.1012). 예약이 7시간 늦게(01:25 KST) 돌아
    # 다음 날 날짜로 새벽에 보낼 뻔했다. 조용시간에는 저장만 하고 웹 장중 경로가 해제 뒤 첫 호출에 묶어 보낸다
    지금 = datetime.now(UTC)
    day = ah.session_day(지금)
    if day is None:
        return {"holdings": len(보유), "skipped": "시간외 단일가 진행 중(16:00~18:00)"}
    조용, 해제뒤 = ah.quiet_state(
        client.execute("SELECT value FROM settings WHERE key = 'quiet_hours'").scalar(), 지금
    )
    raw = client.execute("SELECT value FROM settings WHERE key = 'alert_thresholds'").scalar()
    문턱 = ah.spike_pct(raw)
    시세: dict[str, dict | None] = {}
    for _, code, _ in 보유:
        try:
            시세[code] = qc.after_hours(code)
        except (kis.KisFailed, OSError) as exc:
            failed[f"{code}:시간외"] = str(exc)
    걸림 = ah.hits(보유, 시세, 문턱)
    stamp = db.now_iso()
    잡음 = f"{CLAIM_PREFIX}{stamp}"
    새것 = []
    for h in 걸림:
        rs = client.execute(AFTER_HOURS_INSERT, [h["stock_id"], day.isoformat(), ah.TRIGGER, h["message"],
                                                 json.dumps(h["data"], ensure_ascii=False), stamp,
                                                 ah.QUIET_SKIPPED if (조용 and not 해제뒤) else 잡음])  # fmt: skip
        if rs.affected_rows:
            새것.append(h)
    보냄 = 0
    if 새것 and not 조용:
        머리 = f"시간외 알림 {len(새것)}건 ({day.isoformat()} 시간외 단일가 마감)"
        글 = "\n".join([머리, *(f"· {h['message']}" for h in 새것)])
        try:
            telegram.send(글)
            보냄 = len(새것)
            client.execute(
                "UPDATE alerts SET sent_at = ? WHERE trigger_type = ? AND trade_date = ? AND sent_at = ?"
                " AND stock_id IN (SELECT value FROM json_each(?))",
                [stamp, ah.TRIGGER, day.isoformat(), 잡음, json.dumps([h["stock_id"] for h in 새것])],
            )
        except Exception as exc:  # noqa: BLE001 — 못 보내면 알림 센터에는 남는다
            failed["시간외:발송"] = type(exc).__name__
            # 잡음을 풀어 장중 경로가 다음 호출에 보내게 한다(예전처럼 sent_at 비움)
            with contextlib.suppress(Exception):
                client.execute(
                    "UPDATE alerts SET sent_at = NULL WHERE trigger_type = ? AND trade_date = ? AND sent_at = ?",
                    [ah.TRIGGER, day.isoformat(), 잡음],
                )
    elif 새것:
        # 조용시간 + "해제 뒤 보내기" 면 잡음으로 넣었으니 풀어 둔다 — 장중 경로가 조용시간이 끝나면 보낸다(예전처럼
        # NULL). 조용시간 + 버리기면 QUIET_SKIPPED 로 넣어 이 UPDATE 는 아무 행도 안 건드린다
        client.execute("UPDATE alerts SET sent_at = NULL WHERE trigger_type = ? AND trade_date = ? AND sent_at = ?",
                       [ah.TRIGGER, day.isoformat(), 잡음])  # fmt: skip
    return {"holdings": len(보유), "day": day.isoformat(), "quiet": 조용, "threshold_pct": 문턱, "hits": len(걸림),
            "new": len(새것), "sent": 보냄}


def collect_events(client: TursoClient, qc: kis.QuoteClient, today: date, failed: dict[str, str]) -> list[dict]:
    """기업행위 일정 네 종류. 시장 전체를 받고, 100행에서 잘렸거나 실패한 종류는 보유 국내 종목마다 다시 받는다
    (25.1008)."""
    행사: list[dict] = []
    시작 = today - timedelta(days=EVENT_BACK_DAYS)
    잘림: list[str] = []
    for kind in kis.EVENT_APIS:
        끝 = today + timedelta(days=EVENT_AHEAD_DAYS_BY_KIND.get(kind, EVENT_AHEAD_DAYS))
        try:
            받은 = qc.events(kind, 시작, 끝)
            if len(받은) >= EVENT_PAGE_ROWS:
                failed[f"일정:{kind}"] = f"{len(받은)}행 — 잘렸을 수 있음"
                잘림.append(kind)
            행사 += 받은
        except (kis.KisFailed, OSError) as exc:
            failed[f"일정:{kind}"] = str(exc)
            잘림.append(kind)
    # **잘린 종류는 보유 국내 종목마다 다시 받는다** (25.1008). 분기말·연말 배당 기준일은 하루에 수백 건이라
    # 시장 전체가 100행에서 잘리고, 리포트가 싣는 보유 종목 배당 기준일이 빠졌다(10-07 실측: 배당 100행에서 잘림)
    보유코드 = [str(r[0]) for r in client.execute(HELD_KR_TICKERS).rows] if 잘림 else []
    for kind in 잘림:
        끝 = today + timedelta(days=EVENT_AHEAD_DAYS_BY_KIND.get(kind, EVENT_AHEAD_DAYS))
        for code in 보유코드:
            try:
                행사 += qc.events(kind, 시작, 끝, code)
            except (kis.KisFailed, OSError) as exc:
                failed[f"일정:{kind}:{code}"] = str(exc)
    return 행사


def run(limit: int | None = None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = cal.user_today()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="KR", trade_date=today.isoformat())
        if not kis.configured():
            db.finish_batch_run(client, run_id, status="skipped", error_text="KIS_APP_KEY·KIS_APP_SECRET 없음")
            return 0
        token, issued = kis.access_token(client)
        qc = kis.QuoteClient(token)
        stocks = targets(client)[: limit or None]
        failed: dict[str, str] = {}
        첫의견 = client.execute("SELECT COUNT(*) FROM (SELECT 1 FROM kr_opinions LIMIT 1)").scalar() == 0
        의견창 = today - timedelta(days=OPINION_FIRST_DAYS if 첫의견 else OPINION_DAYS)
        의견: dict[int, list[dict]] = {}

        def one(item: tuple[int, str]) -> tuple[int, dict[str, dict]]:
            sid, code = item
            try:
                의견[sid] = qc.opinions(code, 의견창, today)
            except (kis.KisFailed, OSError) as exc:
                failed[f"{code}:의견"] = str(exc)
            parts = []
            for name, call in (("투자자", lambda: qc.investor(code)),
                               ("공매도", lambda: qc.short(code, today - timedelta(days=SHORT_LOOKBACK_DAYS), today)),
                               ("신용", lambda: qc.credit(code, today))):  # fmt: skip
                try:
                    parts.append(call())
                except (kis.KisFailed, OSError) as exc:
                    failed[f"{code}:{name}"] = str(exc)
                    parts.append({})
            return sid, merge(*parts)

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(one, stocks))
        stamp = db.now_iso()
        statements = [s for sid, rows in results for s in upserts(sid, rows, stamp)]
        의견행 = [
            (OPINION_UPSERT, [sid, o["date"], o["broker"], o["opinion"], o["opinion_code"], o["prev_opinion_code"],
                              o["target_price"], kis.SOURCE, stamp])
            for sid, ops in 의견.items() for o in ops
        ]  # fmt: skip
        행사 = collect_events(client, qc, today, failed)
        행사행 = [
            (EVENT_UPSERT, [e["code"], e["kind"], e["record_date"], e["name"],
                            json.dumps(e["detail"], ensure_ascii=False), kis.SOURCE, stamp])
            for e in 행사
        ]  # fmt: skip
        statements += 의견행 + 행사행
        for i in range(0, len(statements), 500):
            client.batch(statements[i : i + 500])
        # 보유 종목 시간외 단일가 (docs/intraday.md 1.2, 25.992) — 곁다리. 실패해도 수집 결과는 그대로 남긴다
        시간외 = _after_hours(client, qc, today, failed) if cal.is_session("KR", today) else {"skipped": "휴장일"}
        db.record_api_call(client, "kis_openapi", count=qc.calls + int(issued))
        step = {"stocks": len(stocks), "rows": len(statements), "opinions": len(의견행), "events": len(행사행),
                "opinion_since": 의견창.isoformat(), "after_hours": 시간외, "calls": qc.calls, "failed": len(failed),
                "failed_sample": dict(list(failed.items())[:10])}  # fmt: skip
        # 절반 넘게 못 받았으면 실패로 남긴다 — 조금 빠진 것은 다음 날 겹쳐 받으며 메워진다(30일 창)
        status = "failed" if len(failed) > len(stocks) * 4 / 2 else ("partial" if failed else "success")
        db.finish_batch_run(client, run_id, status=status, step_log=step,
                            error_text=f"못 받은 호출 {len(failed)}개" if failed else None)  # fmt: skip
        print(f"수급 수집: 종목 {len(stocks)} · 행 {len(statements)} (의견 {len(의견행)} · 일정 {len(행사행)})"
              f" · 호출 {qc.calls} · 실패 {len(failed)}")
        return 1 if status == "failed" else 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    return run(args.limit)


if __name__ == "__main__":
    sys.exit(guard(main))
