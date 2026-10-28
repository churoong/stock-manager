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
from typing import Any

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
#: **종목마다** 첫 365일 의견 수집 (37회차 C 전제 ②, docs/factors.md 12.40, docs/infra.md 25.1676) — 예전 "첫 수집" 은
#: 의견 표 전체 기준이라 새로 편입된 종목·실패·잘린 종목은 60일만 받아 목표가 개정(기준창 t−365~t−90)을 덮지 못했다.
#: 이날부터 연다 — 10월은 Turso 0, 11-01·02 백업·ETF, 11-03 판정 실행이라 그 뒤(쓰기 몇만 행, 한 번)
OPINION_FULL_FROM = date(2026, 11, 4)
#: 한 실행에 365일로 받을 종목 상한 — 첫날 약 930종목이 한꺼번에 몰리지 않게(나흘에 나눔). 실행 시간·쓰기 행을
#: 묶는 값이다 `[확인필요: KIS 응답 시간 실측]`
OPINION_FULL_PER_RUN = 300
#: 365일 창이 잘리면(응답 100행) 반씩 쪼개는 깊이 상한 — 365일 → 약 23일(4번, 종목당 최대 31호출). 그래도 100행이면
#: 잘림으로 남긴다(다음 실행이 다시 365일)
OPINION_SPLIT_DEPTH = 4
#: 이어 받음 상태 — 마지막 기록 한 행(25.1681). 상태는 기록마다 통째로 다시 적힌다(`continuous_ids`·`continuous_carry`).
#: **`started_at` 으로 정렬한다**(25.1684, 교차검증 [권고]) — `ORDER BY id` 는 `(job_name)` 으로 찾은 뒤 그 작업 행을
#: 전부 읽어 임시 정렬해 하루 한 행씩 늘었다. `idx_batch_runs_job (job_name, started_at DESC)` 를 타면 첫 일치에서
#: 멈춘다(로컬 1년치 가짜 표 VM 5,867 → 28 단계). 이 작업은 동시 실행 그룹으로 하나씩 돌아 순서는 같다
OPINION_STATE_SQL = (
    "SELECT trade_date, step_log FROM batch_runs WHERE job_name = ? AND step_log LIKE '%\"continuous_ids\"%'"
    " ORDER BY started_at DESC LIMIT 1"
)
#: 기업행위 일정 창 — 지난 7일(정정분)부터 앞으로 45일 (25.994). 한 번에 100행까지라 배당은 지난30~앞90일이면 잘렸다
#: (실측 100행). 리포트는 앞 10일만 쓴다(`daily.CORP_EVENT_DAYS`)
EVENT_BACK_DAYS = 7
EVENT_AHEAD_DAYS = 45
#: 유상증자는 KIS 가 **청약 시작일**(`sub_term_ft`)로 거른다 — 2026-10-07 실측: 기준일 08-31 행이 청약 10-06 이라
#: 09-30~ 창에 걸렸다. 청약은 기준일 뒤 약 5주라, 앞 10일 안 기준일을 놓치지 않게 앞 창을 넉넉히 둔다 (25.1008)
#: `[확인필요: 명세의 거르는 날짜]`
EVENT_AHEAD_DAYS_BY_KIND = {"rights": 120}
#: 잘린 일정 종류를 종목마다 다시 받는 대상 — 사용자 실보유 + **KIS모의투자가 베끼는 계좌(H)의 국내 보유**(25.1773,
#: 25.1772 교차검증 [필수]: 연말·분기말 배당 목록이 100행에서 잘리면 H 만 든 종목의 주식배당 일정이 빠져 장중 감시가
#: 락일을 못 걸렀다). 계좌 이름은 `jobs/broker_mirror.MIRROR_ACCOUNT` 와 같다(시험이 대조)
HELD_KR_TICKERS = (
    "SELECT s.ticker FROM positions p JOIN stocks s ON s.id = p.stock_id WHERE p.quantity > 0 AND s.country = 'KR'"
    " UNION SELECT s.ticker FROM paper_fills f JOIN stocks s ON s.id = f.stock_id"
    " WHERE f.account_id = 'H' AND s.country = 'KR'"
    " GROUP BY s.ticker HAVING SUM(CASE WHEN f.side = 'buy' THEN f.quantity ELSE -f.quantity END) > 0"
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
        "     SELECT u2.snapshot_date FROM universe_members u2 CROSS JOIN stocks s2 ON s2.id = u2.stock_id"
        "     WHERE s2.country = 'KR' ORDER BY u2.snapshot_date DESC LIMIT 1))"
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


SENT_UPDATE = (
    "UPDATE alerts SET sent_at = ? WHERE trigger_type = ? AND trade_date = ? AND sent_at = ?"
    " AND stock_id IN (SELECT value FROM json_each(?))"
)


def _발송_기록(client: TursoClient, args: list) -> bool:
    """보낸 시간외 알림의 `sent_at` 을 적는다 — 실패하면 새 연결로 한 번 더 (25.1222, 25.1219 와 같은 까닭)."""
    try:
        client.execute(SENT_UPDATE, args)
        return True
    except Exception as exc:  # noqa: BLE001 — 이미 보냈다
        log.warning("시간외 알림 발송 시각을 적지 못했습니다 — 새 연결로 한 번 더: %s", type(exc).__name__)
    try:
        with TursoClient() as 새것:
            새것.execute(SENT_UPDATE, args)
        return True
    except Exception:  # noqa: BLE001 — 새 연결로도 못 적으면 부르는 쪽이 실행 기록에 남긴다(잡음은 유지)
        return False


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
    # 그 거래일이 KST 어제보다 오래면 보지 않는다 (25.1846) — 휴장일 실행이 며칠 전 시간외를 뒤늦게 알리지 않게.
    # 금요일 실행이 자정을 넘겨 토요일 새벽에 돌면 어제(금) 것이라 본다(예전 바깥 `is_session(오늘)` 이 건너뛰었다)
    if (지금.astimezone(ah.KST).date() - day).days > 1:
        return {"holdings": len(보유), "skipped": f"휴장일 — 마지막 시간외({day.isoformat()})가 하루보다 오래됨"}
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
        except Exception as exc:  # noqa: BLE001 — 못 보내면 알림 센터에는 남는다
            failed["시간외:발송"] = type(exc).__name__
            # 잡음을 풀어 장중 경로가 다음 호출에 보내게 한다(예전처럼 sent_at 비움)
            with contextlib.suppress(Exception):
                client.execute(
                    "UPDATE alerts SET sent_at = NULL WHERE trigger_type = ? AND trade_date = ? AND sent_at = ?",
                    [ah.TRIGGER, day.isoformat(), 잡음],
                )
        else:
            보냄 = len(새것)
            # **보낸 뒤 기록 실패는 잡음을 풀지 않는다** (docs/infra.md 25.1222, 감사 재현). 예전에는 발송과 기록이 한
            # `try` 라 기록이 실패하면 위 갈래로 가 `sent_at = NULL` 로 풀었고, 장중 경로가 같은 알림을 다시 보냈다
            if not _발송_기록(client, [stamp, ah.TRIGGER, day.isoformat(), 잡음,
                                      json.dumps([h["stock_id"] for h in 새것])]):  # fmt: skip
                failed["시간외:발송기록"] = "보냈지만 sent_at 을 적지 못함(잡음 유지)"
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


def opinion_coverage(
    targets: list[int], raw_rows: dict[int, int], full: set[int] | None = None,
) -> dict[str, Any]:
    """의견 호출 결과 — 받은 종목(`covered_ids`, 0건 응답도 받은 것), 못 받은 종목(`failed_ids`), 응답 원래 행이
    `kis.OPINION_PAGE_ROWS` 이상이라 창 앞쪽이 잘렸을 수 있는 종목(`truncated_ids`). 모두 종목 번호 오름차순.
    `full` 을 주면 365일 창으로 받아 잘리지 않은 종목(`full_ids`, 25.1676)도."""
    받음 = sorted(raw_rows)
    잘림 = sorted(sid for sid, n in raw_rows.items() if n >= kis.OPINION_PAGE_ROWS)
    out: dict[str, Any] = {"covered_ids": 받음, "failed_ids": sorted(set(targets) - set(raw_rows)),
                           "truncated_ids": 잘림, "page_rows": kis.OPINION_PAGE_ROWS}  # fmt: skip
    if full is not None:
        out["full_ids"] = sorted(s for s in full if s in raw_rows and s not in set(잘림))
    return out


def load_state(rows: list) -> dict[int, date]:  # noqa: ANN001 — DB 행
    """마지막 기록 → 종목마다 **이어 받은 마지막 날**. 그 실행에서 이어 받은 종목(`continuous_ids`)은 그 실행 날,
    받지 않았지만 아직 이어진 종목은 `continuous_carry` 의 날. 읽지 못하면 빈 상태 — 365일을 한 번 더 받을 뿐 덜 받지
    않는다."""
    for r in rows[:1]:
        try:
            날 = date.fromisoformat(str(r[0])[:10])
            c = json.loads(r[1] or "{}").get("opinion_coverage") or {}
            상태 = {int(sid): 날 for sid in c.get("continuous_ids") or []}
            상태.update({int(sid): date.fromisoformat(str(d)) for sid, d in (c.get("continuous_carry") or {}).items()})
            return 상태
        except (ValueError, AttributeError, TypeError):
            return {}
    return {}


def advance(state: dict[int, date], day: date, coverage: dict[str, Any]) -> dict[int, date]:
    """이번 실행을 더한 이어 받음 (25.1676·25.1681, 37회차 C 전제 ② — 계좌 X 의 "덮음" 재료).

    365일로 받고 잘리지 않은 종목(`full_ids`)은 오늘로 새로 잇는다. 그 밖에 받고 잘리지 않은 종목은 마지막 날과
    `OPINION_DAYS` 안이면(이번 창이 그 날에 닿음 — 양끝 포함이라 하루 보수적) 오늘로 잇는다. 못 받았거나 잘렸거나
    이번 대상이 아니었던 종목(손 `--limit`·유니버스에서 잠시 빠짐)은 마지막 날 그대로 — 틈이 `OPINION_DAYS` 안이면
    다음 실행의 창이 메운다. 마지막 날이 `OPINION_DAYS` 넘게 묵은 종목은 뺀다(365일로 다시 받아야 한다)."""
    새 = dict(state)
    전 = set(coverage.get("full_ids") or [])
    잘림 = set(coverage.get("truncated_ids") or [])
    for sid in coverage.get("covered_ids") or []:
        if sid in 전 or (sid not in 잘림 and sid in state and (day - state[sid]).days <= OPINION_DAYS):
            새[sid] = day
    return {sid: d for sid, d in 새.items() if (day - d).days <= OPINION_DAYS}


def state_fields(state: dict[int, date], day: date, coverage: dict[str, Any]) -> dict[str, Any]:
    """기록에 적을 상태 — 이번에 이어 받은 종목(`continuous_ids` — 계좌 X 의 덮음: **이 실행에서** 받았고 잘리지 않았고
    365일 뒤 끊김 없음)과 아직 이어진 나머지(`continuous_carry`, 종목 → 마지막 날). 같은 날 두 번 돌면 앞 실행이 받은
    종목도 날짜가 오늘이라, 이번 실행의 받음·잘림으로 한 번 더 거른다(25.1684, 교차검증 [권고] — 12.40 C 의 정의)."""
    이번 = set(coverage.get("covered_ids") or []) - set(coverage.get("truncated_ids") or [])
    return {"continuous_ids": sorted(sid for sid, d in state.items() if d == day and sid in 이번),
            "continuous_carry": {str(sid): d.isoformat() for sid, d in sorted(state.items())
                                 if not (d == day and sid in 이번)}}  # fmt: skip


def full_fetch_ids(
    stock_ids: list[int], state: dict[int, date], today: date, cap: int = OPINION_FULL_PER_RUN,
) -> set[int]:
    """이번 실행에 365일 창으로 받을 종목 — `OPINION_FULL_FROM` 부터, 이어 오지 않은 종목(새 편입·실패·잘림이 이어져
    마지막 날이 묵음)을 종목 번호 순으로 `cap` 개까지. 나머지는 다음 실행."""
    if today < OPINION_FULL_FROM:
        return set()
    return set([sid for sid in stock_ids if sid not in state or (today - state[sid]).days > OPINION_DAYS][:cap])


def opinions_split(
    qc: kis.QuoteClient, code: str, since: date, until: date, depth: int = 0,
) -> tuple[list[dict], int]:
    """창이 잘리면(원래 행 ≥ `kis.OPINION_PAGE_ROWS`) 반으로 쪼개 다시 받는다 — `OPINION_SPLIT_DEPTH` 까지.
    돌려주는 행 수는 조각들 가운데 **가장 큰** 원래 행 수(하나라도 100행이면 잘림으로 남는다)."""
    읽음, n = qc.opinions_with_count(code, since, until)
    if n < kis.OPINION_PAGE_ROWS or depth >= OPINION_SPLIT_DEPTH or (until - since).days < 2:
        return 읽음, n
    중간 = since + timedelta(days=(until - since).days // 2)
    a, na = opinions_split(qc, code, since, 중간, depth + 1)
    b, nb = opinions_split(qc, code, 중간 + timedelta(days=1), until, depth + 1)
    return a + b, max(na, nb)


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
        전창 = today - timedelta(days=OPINION_FIRST_DAYS)
        # 종목마다 첫 365일 수집 (25.1676·25.1681) — 표가 비었으면(첫 실행) 전 종목이 이미 365일
        상태 = {} if 첫의견 else load_state(client.execute(OPINION_STATE_SQL, [JOB_NAME]).rows)
        전부 = {sid for sid, _c in stocks} if 첫의견 else full_fetch_ids([sid for sid, _c in stocks], 상태, today)
        의견: dict[int, list[dict]] = {}
        의견원행: dict[int, int] = {}  # 받은 종목 → 응답의 원래 행 수(잘림 판정, 25.1647)

        def one(item: tuple[int, str]) -> tuple[int, dict[str, dict]]:
            sid, code = item
            try:
                # 60일 창도 잘리면 쪼갠다(25.1681 — 안 쪼개면 그 종목은 하루 걸러 365일로 다시 받았다)
                의견[sid], 의견원행[sid] = opinions_split(qc, code, 전창 if sid in 전부 else 의견창, today)
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
        # 휴장 여부는 안에서 본다 — 바깥에서 오늘이 장인지로 거르면 금요일 실행이 GitHub 지연으로 토요일 01:25 KST
        # 에 돌 때 금요일 시간외 알림이 통째로 빠졌다(25.1846, 결함 루프 [참고] — 안의 `session_day`(25.1012)와 어긋남)
        시간외 = _after_hours(client, qc, today, failed)
        db.record_api_call(client, "kis_openapi", count=qc.calls + int(issued))
        step = {"stocks": len(stocks), "rows": len(statements), "opinions": len(의견행), "events": len(행사행),
                "opinion_since": 의견창.isoformat(), "after_hours": 시간외, "calls": qc.calls, "failed": len(failed),
                "failed_sample": dict(list(failed.items())[:10])}  # fmt: skip
        # 의견 호출이 덮은 종목·실패·잘림 (37회차 C 전제 ①, factors.md 12.40, 25.1647) — 목표가 IC·계좌가
        # "의견 없음(0)" 과 "모름(NULL)" 을 가르는 재료. 지금 이것이 없어 실패·잘린 종목의 빈 창을 의견 없음으로
        # 읽을 수밖에 없었다(14회차 12.16)
        덮음 = opinion_coverage([sid for sid, _c in stocks], 의견원행, 전부)
        덮음["full_since"] = 전창.isoformat()
        덮음.update(state_fields(advance(상태, today, 덮음), today, 덮음))
        step["opinion_coverage"] = 덮음
        # **출처마다** 절반 넘게 못 받았으면 실패로 남긴다 (docs/infra.md 25.1397, KIS 수급 감사) — 조금 빠진 것은
        # 다음 날 겹쳐 받으며 메워진다(30일 창). 예전 문턱은 네 호출 합의 절반이라 투자자별 하나가 전 종목에서
        # 실패해도(약 1/4) `partial` 이었고, 공매도 행이 그날 날짜를 계속 채워 신선도는 초록 — KIS 가 30일만 주므로
        # 한 달 지나면 영영 잃는다
        출처별 = failed_by_source(failed)
        step["failed_by_source"] = 출처별
        status = run_status(출처별, len(stocks), len(failed))
        db.finish_batch_run(client, run_id, status=status, step_log=step,
                            error_text=f"못 받은 호출 {len(failed)}개" if failed else None)  # fmt: skip
        print(f"수급 수집: 종목 {len(stocks)} · 행 {len(statements)} (의견 {len(의견행)} · 일정 {len(행사행)})"
              f" · 호출 {qc.calls} · 실패 {len(failed)}")
        return 1 if status == "failed" else 0
    finally:
        client.close()


#: 종목마다 부르는 출처 — 실패 키 `"{코드}:{출처}"` 의 뒷부분 (25.1397)
PER_STOCK_SOURCES = ("의견", "투자자", "공매도", "신용")


def failed_by_source(failed: dict[str, str]) -> dict[str, int]:
    """실패 키를 출처별로 센다. 종목에 딸리지 않은 실패(일정·시간외)는 `기타`"""
    out: dict[str, int] = {}
    for key in failed:
        출처 = key.rsplit(":", 1)[-1] if ":" in key else ""
        출처 = 출처 if 출처 in PER_STOCK_SOURCES else "기타"
        out[출처] = out.get(출처, 0) + 1
    return out


def run_status(by_source: dict[str, int], n_stocks: int, n_failed: int) -> str:
    """종목별 출처 하나라도 절반 넘게 실패하면 `failed`, 조금이라도 실패하면 `partial` (25.1397)"""
    if n_stocks and any(by_source.get(k, 0) > n_stocks / 2 for k in PER_STOCK_SOURCES):
        return "failed"
    return "partial" if n_failed else "success"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    return run(args.limit)


if __name__ == "__main__":
    sys.exit(guard(main))
