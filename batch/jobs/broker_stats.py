"""증권사 적중률 성적표 계산 (docs/brokers.md, docs/infra.md 25.995).

`kr_opinions` × `prices`(국내) × `index_prices`(코스피·코스닥)로 `services/broker_stats.compute` 를 돌려
`broker_stats` 를 통째로 바꾼다.
국내 수급 수집(`kis-flows.yml`)이 끝에 부른다 — 금요일이거나 표가 비었을 때만(읽기 ≈ 시세 25만 행, 주 1회면 충분하다).

실행
  python -m batch.jobs.broker_stats [--force]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import broker_stats as bs

JOB_NAME = "broker_stats"
#: 시세를 읽는 앞쪽 여유 — 가장 이른 의견보다 이만큼 앞부터(기준가는 발표 다음 거래일이라 하루면 되지만
#: 연휴를 덮는다)
LEAD_DAYS = 10
#: 요일(월=0) — 금요일에만 다시 계산한다
WEEKDAY = 4
UPSERT = (
    "INSERT INTO broker_stats (broker, as_of, n_opinions, n_target, avg_upside_pct, n_fwd, avg_excess_pct, hit_pct,"
    " n_touch, touch_pct, skipped_action, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def should_run(client: TursoClient, today: date, force: bool) -> bool:
    if force or today.weekday() == WEEKDAY:
        return True
    return client.execute("SELECT COUNT(*) FROM (SELECT 1 FROM broker_stats LIMIT 1)").scalar() == 0


def load(
    client: TursoClient,
) -> tuple[list[bs.Opinion], dict[int, list[bs.Bar]], dict[str, list[bs.Bar]], str | None]:
    ops = [
        bs.Opinion(int(r[0]), "KOSDAQ" if str(r[1]).upper() == "KOSDAQ" else "KOSPI", str(r[2]), str(r[3]),
                   float(r[4]) if r[4] is not None else None)
        for r in client.execute(
            "SELECT o.stock_id, s.market, o.date, o.broker, o.target_price FROM kr_opinions o"
            " JOIN stocks s ON s.id = o.stock_id ORDER BY o.date"
        ).rows
    ]  # fmt: skip
    if not ops:
        return [], {}, {}, None
    since = (date.fromisoformat(ops[0].date) - timedelta(days=LEAD_DAYS)).isoformat()
    bars: dict[int, list[bs.Bar]] = defaultdict(list)
    for r in client.execute(
        "SELECT p.stock_id, p.date, p.close, p.high, p.adj_close FROM prices p"
        " WHERE p.stock_id IN (SELECT DISTINCT stock_id FROM kr_opinions) AND p.date >= ? AND p.close > 0"
        " ORDER BY p.stock_id, p.date",
        [since],
    ).rows:
        close = float(r[2])
        bars[int(r[0])].append(bs.Bar(str(r[1]), close, float(r[3]) if r[3] else None,
                                      float(r[4]) / close if r[4] else None))  # fmt: skip
    index: dict[str, list[bs.Bar]] = defaultdict(list)
    for r in client.execute(
        "SELECT index_code, date, close FROM index_prices WHERE index_code IN ('KOSPI', 'KOSDAQ') AND date >= ?"
        " ORDER BY index_code, date",
        [since],
    ).rows:
        index[str(r[0])].append(bs.Bar(str(r[1]), float(r[2]), None, None))
    as_of = max((b[-1].date for b in bars.values() if b), default=None)
    return ops, dict(bars), dict(index), as_of


def run(force: bool = False) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = cal.user_today()
        if not should_run(client, today, force):
            print("증권사 성적표: 금요일이 아니라 건너뜀")
            return 0
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="KR", trade_date=today.isoformat())
        ops, bars, index, as_of = load(client)
        rows = bs.compute(ops, bars, index)
        stamp = db.now_iso()
        client.batch([("DELETE FROM broker_stats", [])] + [
            (UPSERT, [r["broker"], as_of or today.isoformat(), r["n_opinions"], r["n_target"], r["avg_upside_pct"],
                      r["n_fwd"], r["avg_excess_pct"], r["hit_pct"], r["n_touch"], r["touch_pct"], r["skipped_action"],
                      stamp])
            for r in rows
        ])  # fmt: skip
        익은 = sum(1 for r in rows if (r["n_fwd"] or 0) >= bs.MIN_N)
        step = {"opinions": len(ops), "brokers": len(rows), "brokers_ready": 익은, "as_of": as_of}
        db.finish_batch_run(client, run_id, status="success", step_log=step)
        print(f"증권사 성적표: 의견 {len(ops)} · 증권사 {len(rows)} (표본 {bs.MIN_N}건 이상 {익은})")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    return run(parser.parse_args().force)


if __name__ == "__main__":
    sys.exit(guard(main))
