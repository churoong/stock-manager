"""공시 반응 통계 — 시장 전체 주요 공시를 모으고 유형별 5거래일 초과수익을 계산한다
(docs/disclosure_reaction.md, docs/infra.md 25.996).

1. **모으기** — DART 목록을 회사를 정하지 않고 날짜마다(주요사항보고·거래소공시) 받아 `disclosures` 에
   넣는다(같은 접수번호는 한 번). `disclosure_reaction` 이 비었으면 지난 `BACKFILL_DAYS` 일, 아니면 지난 `DAILY_DAYS` 일
2. **계산** — 금요일이거나 표가 비었을 때만. 유형마다 (종목, 날짜) 한 번씩 `services/disclosure_reaction.car`

국내 수급 수집(`kis-flows.yml`) 끝에 부른다. DART 일일 한도(2만)를 세고(`record_and_guard`), 막히면 거기서 멈춘다.

실행
  python -m batch.jobs.disclosure_reaction [--days N] [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import disclosure_reaction as dr
from batch.sources import dart_disclosures as dd

JOB_NAME = "disclosure_reaction"
#: 처음 한 번 거슬러 모으는 날 수 — 1년(호출 약 1,500회, DART 일 한도 2만 안)
BACKFILL_DAYS = 365
#: 평소에 겹쳐 받는 날 수 — 며칠 빠져도 메워진다(같은 접수번호는 UNIQUE)
DAILY_DAYS = 7
#: 금요일에만 다시 계산한다
WEEKDAY = 4
INSERT = (
    "INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, url, is_material, source,"
    " fetched_at)"
    " SELECT s.id, ?, ?, ?, ?, ?, ?, ?, ? FROM stocks s WHERE s.country = 'KR' AND s.ticker = ?"
    " ON CONFLICT (receipt_no) DO NOTHING"
)
UPSERT = (
    "INSERT INTO disclosure_reaction (type, label, keywords, priority, n, mean_pct, median_pct, pos_pct, window_days,"
    " since, as_of, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def collect(client: TursoClient, today: date, days: int) -> dict:
    """지난 days 일의 시장 전체 주요 공시를 넣는다. 한도에 막히면 멈춘다."""
    stamp, calls, rows, err = db.now_iso(), 0, 0, None
    for back in range(days, -1, -1):
        day = (today - timedelta(days=back))
        if day.weekday() >= 5:
            continue
        하루 = 0
        for kind in dd.MARKET_KINDS:
            got, n, err = dd.fetch_market_day(day.strftime("%Y%m%d"), kind)
            calls += n
            하루 += n
            stmts = [
                (INSERT, [d.corp_code, d.receipt_no, d.title, d.disclosed_at,
                          dd.VIEW_URL.format(receipt_no=d.receipt_no), 1 if kind == "B" else 0, dd.SOURCE, stamp,
                          d.stock_code])
                for d in got
            ]  # fmt: skip
            for i in range(0, len(stmts), 500):
                client.batch(stmts[i : i + 500])
            rows += len(stmts)
            if err:
                break
        # 하루치마다 세고 **넘기 전에** 멈춘다 (CLAUDE.md 80% 경고·100% 중단, 25.117) — 첫 1년 모으기가 1,500회다
        if 하루 and db.record_and_guard(client, "dart_opendart", count=하루, limit_value=20_000) == "blocked":
            err = err or "DART 일일 한도 — 나머지 날은 다음 실행에"
        if err:
            break
    return {"days": days, "calls": calls, "rows": rows, "error": err}


def compute(client: TursoClient, today: date) -> dict:
    since = (today - timedelta(days=BACKFILL_DAYS + 30)).isoformat()
    events: dict[tuple[int, str, str], dr.Event] = {}
    for r in client.execute(
        "SELECT d.stock_id, s.market, d.disclosed_at, d.title FROM disclosures d JOIN stocks s ON s.id = d.stock_id"
        " WHERE s.country = 'KR' AND d.disclosed_at >= ?",
        [since],
    ).rows:
        kind = dr.classify(str(r[3]))
        if kind:
            idx = "KOSDAQ" if str(r[1]).upper() == "KOSDAQ" else "KOSPI"
            events.setdefault((int(r[0]), str(r[2]), kind), dr.Event(int(r[0]), idx, str(r[2]), kind))
    if not events:
        return {"events": 0}
    first = min(e.day for e in events.values())
    series: dict[int, tuple[list[str], list[float], list[float | None]]] = defaultdict(lambda: ([], [], []))
    for r in client.execute(
        "SELECT stock_id, date, close, adj_close FROM prices WHERE close > 0 AND date >= ?"
        " AND stock_id IN (SELECT DISTINCT stock_id FROM disclosures WHERE disclosed_at >= ? AND stock_id IS NOT NULL)"
        " ORDER BY stock_id, date",
        [(date.fromisoformat(first) - timedelta(days=10)).isoformat(), since],
    ).rows:
        d, c, a = series[int(r[0])]
        d.append(str(r[1]))
        c.append(float(r[2]))
        a.append(float(r[3]) / float(r[2]) if r[3] else None)
    index: dict[str, dict[str, float]] = defaultdict(dict)
    for r in client.execute(
        "SELECT index_code, date, close FROM index_prices WHERE index_code IN ('KOSPI', 'KOSDAQ') AND date >= ?",
        [first],
    ).rows:
        index[str(r[0])][str(r[1])] = float(r[2])
    values: dict[str, list[float]] = defaultdict(list)
    for e in events.values():
        s = series.get(e.stock_id)
        if s:
            x = dr.car(s[0], s[1], s[2], index[e.index_code], e.day)
            if x is not None:
                values[e.type].append(x)
    rows = dr.summarize(values)
    as_of = max((s[0][-1] for s in series.values() if s[0]), default=None)
    stamp = db.now_iso()
    client.batch([("DELETE FROM disclosure_reaction", [])] + [
        (UPSERT, [r["type"], r["label"], json.dumps(r["keywords"], ensure_ascii=False), i, r["n"], r["mean_pct"],
                  r["median_pct"], r["pos_pct"], dr.WINDOW_DAYS, first, as_of, stamp])
        for i, r in enumerate(rows)
    ])  # fmt: skip
    return {"events": len(events), "measured": sum(len(v) for v in values.values()), "since": first, "as_of": as_of,
            "ready": [r["type"] for r in rows if r["n"] >= dr.MIN_N]}  # fmt: skip


def run(days: int | None = None, force: bool = False) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = cal.user_today()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="KR", trade_date=today.isoformat())
        비었나 = client.execute("SELECT COUNT(*) FROM (SELECT 1 FROM disclosure_reaction LIMIT 1)").scalar() == 0
        모음 = collect(client, today, days or (BACKFILL_DAYS if 비었나 else DAILY_DAYS))
        # **1년 모으기가 중간에 멈췄으면 계산하지 않는다** (25.1006, 교차검증). 계산하면 표가 차서 다음 실행이
        # "비지 않았다" 로 보고 7일만 받는다 — 멈춘 날 뒤 몇 달이 영구히 빈다. 표를 비워 두면 다음 실행이 1년을
        # 다시 받는다(같은 접수번호는 한 번)
        if 비었나 and 모음.get("error") and not force:
            계산: dict = {"skipped": "1년 모으기가 끝나지 않음 — 다음 실행이 다시 받는다"}
        elif force or 비었나 or today.weekday() == WEEKDAY:
            계산 = compute(client, today)
        else:
            계산 = {"skipped": "금요일 아님"}
        status = "partial" if 모음.get("error") else "success"
        db.finish_batch_run(client, run_id, status=status, step_log={"collect": 모음, "compute": 계산},
                            error_text=모음.get("error"))  # fmt: skip
        print(f"공시 반응: 모음 {모음['rows']}건(호출 {모음['calls']}) · 계산 {계산}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    return run(args.days, args.force)


if __name__ == "__main__":
    sys.exit(guard(main))
