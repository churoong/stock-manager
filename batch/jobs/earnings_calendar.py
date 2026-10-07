"""실적 일정 → earnings_calendar (docs/portfolio.md 5장, docs/stock_detail.md 2장).

두 겹이다. **야후 예정일이 있으면 그것을 쓰고, 없는 종목만 법정 제출 기한으로 추정한다.**

  1. 야후 (2026-09-18 실측): `Ticker.calendar` 의 `Earnings Date`. 국내·미국 모두 나온다
     (삼성전자 2026-10-28, SK하이닉스 10-27, 애플 10-30). 날짜가 하나면 확정으로,
     둘이면 야후도 모르는 **구간**이라 추정으로 적는다 (사용자 결정 2026-09-18)
  2. 법정 제출 기한 추정: 분기 결산일 + 45일, 연간 + 90일. "늦어도 이날까지" 이고
     실제 발표는 보통 더 이르다. 야후가 날짜를 주지 않은 종목에만 쓴다

대상은 화면에서 열어 볼 종목: 유니버스 편입 + 보유 + 관심. 주 1회 (야후 호출이 종목당 1회).

실행
  python -m batch.jobs.earnings_calendar
  python -m batch.jobs.earnings_calendar --limit 20     # 시험
  python -m batch.jobs.earnings_calendar --no-yahoo     # 추정만 (야후가 막혔을 때)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.portfolio import fiscal_year_ends
from batch.services import portfolio as pf
from batch.sources import yfinance_src

log = logging.getLogger("earnings_calendar")

JOB_NAME = "earnings_calendar"
SOURCE = "estimate:filing_deadline"
NOTE = "법정 제출 기한 추정 (실제 발표는 보통 더 이르다)"
SOURCE_YAHOO = "yfinance:calendar"
NOTE_YAHOO = "야후 예정일"
EVENTS_PER_STOCK = 4  # 앞으로 1년치
YAHOO_EVENT = "실적발표"
# 429 가 잇따르면 그만둔다. 남은 종목은 다음 주에 받는다 — 막힌 채로 계속 두드리지 않는다
MAX_RATE_LIMIT_HITS = 5
RATE_LIMIT_BACKOFF = 30.0

_COLS = "stock_id, event_type, scheduled_date, is_confirmed, note, source, fetched_at"

TARGETS_SQL = (
    "SELECT DISTINCT s.id, s.yahoo_symbol FROM stocks s WHERE s.status = 'active' AND ("
    "  s.id IN (SELECT stock_id FROM universe_members WHERE included = 1"
    f"            AND snapshot_date = {db.latest_snapshot_sql()})"
    "  OR s.id IN (SELECT stock_id FROM positions WHERE quantity > 0)"
    "  OR s.id IN (SELECT stock_id FROM watchlist)"
    ") ORDER BY s.id"
)


def pick_targets(client: TursoClient, country: str) -> list[tuple[int, str | None]]:
    """(stock_id, 야후 심볼). 심볼이 없으면 추정만 받는다."""
    rs = client.execute(TARGETS_SQL, [country])
    return [(int(r[0]), None if r[1] is None else str(r[1])) for r in rs.rows]


def yahoo_rows(stock_id: int, dates: list[str], today: str, now: str) -> list[tuple]:
    """야후 예정일 → 행. 지난 날짜는 버린다.

    날짜가 하나면 확정(is_confirmed = 1), 둘 이상이면 야후가 구간만 아는 것이라 추정으로 적고
    구간을 note 에 남긴다. 어느 쪽이든 어디서 온 값인지 화면이 알 수 있어야 한다.
    """
    upcoming = [d for d in dates if d >= today]
    if not upcoming:
        return []
    confirmed = len(dates) == 1
    note = NOTE_YAHOO if confirmed else f"{NOTE_YAHOO} 구간 추정 ({dates[0]}~{dates[-1]})"
    return [(stock_id, YAHOO_EVENT, upcoming[0], 1 if confirmed else 0, note, SOURCE_YAHOO, now)]


def rows_for(stock_id: int, fiscal_year_end: str, today: date, now: str) -> list[tuple]:
    """한 종목의 앞으로 EVENTS_PER_STOCK 개. 결산일이 이상하면 빈 목록(값을 지어내지 않는다)."""
    try:
        deadlines = pf.upcoming_deadlines(fiscal_year_end, today, EVENTS_PER_STOCK)
    except ValueError:
        return []
    return [
        (stock_id, f"실적발표({kind})", deadline, 0, NOTE, SOURCE, now)
        for kind, deadline, _d_day in deadlines
    ]


def collect_yahoo(
    targets: list[tuple[int, str | None]], today: str, now: str, answered: set[int] | None = None
) -> tuple[list[tuple], dict[str, int]]:
    """야후에서 예정일을 받는다. 막히면 그만두고 받은 것만 돌려준다.

    `answered` 를 주면 **야후가 답한 종목**(날짜가 없었어도)을 담는다.
    저장이 그 종목들의 옛 야후 일정만 지운다 (25.252).
    """
    rows: list[tuple] = []
    counts = {"asked": 0, "with_date": 0, "failed": 0, "rate_limited": 0}
    hits = 0
    for index, (stock_id, symbol) in enumerate(targets):
        if not symbol:
            continue
        if index:
            time.sleep(yfinance_src.EARNINGS_INTERVAL)
        counts["asked"] += 1
        result = yfinance_src.fetch_earnings_dates(symbol)
        if not result.ok:
            counts["failed"] += 1
            if result.rate_limited:
                hits += 1
                counts["rate_limited"] += 1
                if hits >= MAX_RATE_LIMIT_HITS:
                    log.warning("야후 429 가 %d번 — 예정일 수집을 그만둡니다", hits)
                    break
                time.sleep(RATE_LIMIT_BACKOFF)
            continue
        if answered is not None:
            answered.add(stock_id)
        new = yahoo_rows(stock_id, list(result.data or []), today, now)
        if new:
            counts["with_date"] += 1
            rows.extend(new)
    return rows, counts


def 이미_있는_야후(client: TursoClient, ids: list[int], today: str) -> set[int]:
    """앞으로의 야후 일정이 이미 있는 종목 (docs/infra.md 25.252)."""
    나온: set[int] = set()
    묶음 = db.in_chunk(reserve=2)
    for i in range(0, len(ids), 묶음):
        조각 = ids[i : i + 묶음]
        rs = client.execute(
            "SELECT DISTINCT stock_id FROM earnings_calendar WHERE source = ? AND scheduled_date >= ?"
            f" AND stock_id IN ({', '.join('?' * len(조각))})",
            [SOURCE_YAHOO, today, *조각],
        )
        나온.update(int(r[0]) for r in rs.rows)
    return 나온


def store(
    client: TursoClient,
    rows: list[tuple],
    today: str,
    *,
    yahoo_ids: set[int] | list[int],
    estimate_ids: set[int] | list[int],
) -> int:
    """이 배치가 쓰는 두 출처의 앞으로 일정을 지우고 다시 넣는다 — **이번에 다룬 종목만** (docs/infra.md 25.252).

    다른 출처의 확정 일정이 언젠가 들어오면 그것은 건드리지 않는다.

    2026-09-26 까지 지우기는 종목을 가리지 않았다. 야후 429 로 중간에 멈추면(`MAX_RATE_LIMIT_HITS`)
    아직 묻지 않은 종목의 **확정 일정이 지워져** 추정으로 바뀌거나 사라졌고,
    `--limit N` 시험 실행은 N 밖의 모든 종목 일정을 지웠다.
    - `yahoo_ids`: 야후가 **답한** 종목. 그 종목의 옛 야후 일정만 지운다(날짜가 빠졌으면 빠진 것이 맞다)
    - `estimate_ids`: 이번에 대상이었던 종목. 그 종목의 옛 추정만 지운다
    """
    statements: list[tuple[str, list[Any]]] = []
    묶음 = db.in_chunk(reserve=2)
    for 출처, 번호들 in ((SOURCE_YAHOO, sorted(yahoo_ids)), (SOURCE, sorted(estimate_ids))):
        for i in range(0, len(번호들), 묶음):
            조각 = 번호들[i : i + 묶음]
            statements.append((
                "DELETE FROM earnings_calendar WHERE source = ? AND scheduled_date >= ?"
                f" AND stock_id IN ({', '.join('?' * len(조각))})",
                [출처, today, *조각],
            ))  # fmt: skip
    if rows:
        holes = "(" + ", ".join(["?"] * db.column_count(_COLS)) + ")"
        for start in range(0, len(rows), 200):
            chunk = rows[start : start + 200]
            args: list[Any] = []
            for row in chunk:
                args.extend(row)
            statements.append(
                (f"INSERT INTO earnings_calendar ({_COLS}) VALUES " + ", ".join([holes] * len(chunk)), args)
            )
    client.batch(statements)
    return len(rows)


def run(limit: int | None = None, use_yahoo: bool = True) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = datetime.now(UTC).date()
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market=None, trade_date=today.isoformat()
        )
        try:
            targets: list[tuple[int, str | None]] = []
            for country in ("KR", "US"):
                targets.extend(pick_targets(client, country))
            targets = sorted(set(targets))[: limit or None]
            now = db.now_iso()

            rows: list[tuple] = []
            counts = {"asked": 0, "with_date": 0, "failed": 0, "rate_limited": 0}
            답한: set[int] = set()
            if use_yahoo:
                rows, counts = collect_yahoo(targets, today.isoformat(), now, 답한)
                # **센다** (docs/infra.md 25.200). 종목마다 한 번이라 야후를 가장 많이 부르는 작업인데
                # 2026-09-26 까지 카운터에 한 번도 안 올렸다 — 상태 화면의 야후 사용량이 그만큼 작게 보였다
                db.record_api_call(client, "yfinance", count=counts["asked"])

            # 야후가 날짜를 준 종목은 추정하지 않는다. 같은 화면에 두 날짜가 뜨면 어느 쪽을 믿을지 알 수 없다.
            # **이번에 못 물은 종목의 남아 있는 야후 일정도 그대로 두고 추정하지 않는다** (25.252)
            대상번호 = [stock_id for stock_id, _symbol in targets]
            남은_야후 = 이미_있는_야후(client, 대상번호, today.isoformat()) - 답한
            known = {row[0] for row in rows} | 남은_야후
            rest = [stock_id for stock_id, _symbol in targets if stock_id not in known]
            ends = fiscal_year_ends(client, sorted(rest))
            for stock_id, (fy_end, _filed) in ends.items():
                rows.extend(rows_for(stock_id, fy_end, today, now))
            stored = store(client, rows, today.isoformat(), yahoo_ids=답한, estimate_ids=대상번호)
        except Exception as exc:
            db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
            raise

        step = {
            "targets": len(targets),
            "yahoo": counts,
            "estimated_stocks": len(ends),
            "rows": stored,
        }
        status = "partial" if counts["rate_limited"] >= MAX_RATE_LIMIT_HITS else "success"
        db.finish_batch_run(client, run_id, status=status, step_log=step)
        print(
            f"실적 일정: 대상 {len(targets)}종목 · 야후 예정일 {counts['with_date']}"
            f"(호출 {counts['asked']}, 실패 {counts['failed']}) · 법정 기한 추정 {len(ends)}종목 · {stored}행"
        )
        if counts["rate_limited"]:
            print(f"  주의: 야후 429 {counts['rate_limited']}건. 다음 실행에서 나머지를 받습니다")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="실적 일정 수집·추정")
    parser.add_argument("--limit", type=int, help="앞에서부터 몇 종목만 (시험용)")
    parser.add_argument("--no-yahoo", action="store_true", help="야후를 부르지 않고 추정만")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.limit, not args.no_yahoo)


if __name__ == "__main__":
    sys.exit(guard(main))
