"""국내 수급 일별 수집 — 투자자별 순매수 · 공매도 · 신용잔고 (docs/data-sources.md 3.1, docs/infra.md 25.987).

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
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.sources import kis

log = logging.getLogger(__name__)
JOB_NAME = "kis_flows"
#: 공매도 조회 창 — 첫날은 이만큼 거슬러 채우고, 그 뒤로는 겹치는 날을 덮어쓴다(잠정치 정정)
SHORT_LOOKBACK_DAYS = 45
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

        def one(item: tuple[int, str]) -> tuple[int, dict[str, dict]]:
            sid, code = item
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
        for i in range(0, len(statements), 500):
            client.batch(statements[i : i + 500])
        db.record_api_call(client, "kis_openapi", count=qc.calls + int(issued))
        step = {"stocks": len(stocks), "rows": len(statements), "calls": qc.calls, "failed": len(failed),
                "failed_sample": dict(list(failed.items())[:10])}  # fmt: skip
        # 절반 넘게 못 받았으면 실패로 남긴다 — 조금 빠진 것은 다음 날 겹쳐 받으며 메워진다(30일 창)
        status = "failed" if len(failed) > len(stocks) * 3 / 2 else ("partial" if failed else "success")
        db.finish_batch_run(client, run_id, status=status, step_log=step,
                            error_text=f"못 받은 호출 {len(failed)}개" if failed else None)  # fmt: skip
        print(f"수급 수집: 종목 {len(stocks)} · 행 {len(statements)} · 호출 {qc.calls} · 실패 {len(failed)}")
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
