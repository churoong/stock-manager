"""미국 가격 백필 (docs/data-sources.md 4절 yfinance, docs/adjust.md).

미국 추천이 나오려면 모멘텀 127 거래일, 1년 성과 지표 200 거래일, 장기 밴드 250 거래일이 필요하다.
미국 전종목 수집은 2026-09-16 에 시작해 10 거래일뿐이라 과거를 한 번 채운다.

**일일 배치로 하면 안 되는 이유** (조사 워크플로 2026-09-17)
  - 30분 제한(daily-us.yml)
  - 전 종목을 메모리에 모았다가 한 요청으로 저장 → Turso 60초 제한
이 배치는 200종목 조각마다 받자마자 저장하고, 종목 구간(--start --count)으로 나눠 돌린다.

수정주가(adj_close)를 같은 시점에 한꺼번에 받으므로 조정 기준이 한 계열 안에서 맞는다.

실행
  python -m batch.jobs.backfill_us --lookback 1300 --start 0 --count 200    # 시범
  python -m batch.jobs.backfill_us --lookback 1300 --start 0 --count 1400
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.daily import _store_us_bars, _us_symbol_ids
from batch.sources import yfinance_src

log = logging.getLogger("backfill_us")

JOB_NAME = "backfill_us"


def plan_chunks(symbols: list[str], start: int, count: int | None, chunk: int) -> list[list[str]]:
    """정렬된 심볼에서 [start, start+count) 구간을 chunk 개씩 자른다."""
    selected = symbols[start : (start + count) if count else None]
    return [selected[i : i + chunk] for i in range(0, len(selected), chunk)]


# 후보만 받을 때의 거래대금 하한. 미국 유니버스 문턱과 같다(services/universe.UniverseFilters.usa).
# 이 문턱에 못 미치는 종목은 추천 후보가 될 수 없으므로 5년치를 받을 이유가 없다.
#
# 왜 필요한가 (2026-09-17): 전 종목 5년치는 약 610만 행이다. Turso 무료 플랜의 쓰기 한도는
# 월 1,000만 행이고(docs/infra.md 2절), 같은 달 국내 5년 백필이 이미 수백만 행을 썼다.
# 한도를 넘으면 쓰기가 막혀 일일 리포트까지 멈춘다. 200종목 시범은 21만 행, 초당 348행이었다.
DEFAULT_MIN_TURNOVER = 5_000_000


def candidate_symbols(client: TursoClient, min_turnover: float) -> set[str]:
    """최근 저장된 일봉의 평균 거래대금 추정이 하한 이상인 심볼."""
    rs = client.execute(
        "SELECT s.yahoo_symbol FROM prices p JOIN stocks s ON s.id = p.stock_id"
        " WHERE s.country = 'US' AND s.status = 'active' AND p.value IS NOT NULL"
        "   AND p.date >= (SELECT date(MAX(p2.date), '-30 days') FROM prices p2"
        "                  JOIN stocks s2 ON s2.id = p2.stock_id WHERE s2.country = 'US')"
        " GROUP BY s.yahoo_symbol HAVING AVG(p.value) >= ?",
        [min_turnover],
    )
    return {str(r[0]) for r in rs.rows}


def run(
    lookback: int, start: int, count: int | None, chunk: int, min_turnover: float | None = None,
    detect_drift: bool = False,
) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        targets = _us_symbol_ids(client, include_etf=True)  # 추천·보유 ETF 의 빠진 날도 메운다 (25.896)
        symbols = sorted(targets)
        if min_turnover:
            keep = candidate_symbols(client, min_turnover)
            symbols = [s for s in symbols if s in keep]
            log.info("거래대금 %s 이상 후보 %d종목만 받습니다", f"{min_turnover:,.0f}", len(symbols))
        trade_date = cal.previous_session("US").isoformat()
        groups = plan_chunks(symbols, start, count, chunk)
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="US", trade_date=trade_date)

        stored = 0
        tickers = 0
        failed_groups = 0
        빈조각 = 0
        blocked = False
        notes: list[str] = []
        started = time.monotonic()

        for index, group in enumerate(groups):
            t0 = time.monotonic()
            result = yfinance_src.fetch_daily_bars(group, lookback_days=lookback, chunk_size=len(group))
            fetched = time.monotonic() - t0
            db.record_api_call(client, "yfinance", count=1)
            if not result.ok:
                failed_groups += 1
                notes.append(f"조각 {index + 1}: {result.error}")
                # **조각마다 따로 부르므로 잇달아 빈 조각은 여기서 센다** (docs/infra.md 25.603, 교차검증).
                # `fetch_daily_bars` 의 "두 조각 잇달아 비면 멈춤" 은 한 번 부를 때의 조각끼리만 세어,
                # 백필은 차단 뒤에도 남은 조각을 모두 불렀다
                빈조각 = 빈조각 + 1 if not result.data else 0
                if result.limit_state == "blocked" or 빈조각 >= 2:
                    blocked = True
                    # 이 자리의 조각은 늘 비어 있다(`ok=bool(bars)`) — 차단만이 아니라 **어떤 실패든** 두 번 잇달면
                    # 멈춘다.
                    # 원인은 앞 줄의 `notes` 에 있다. "차단" 이라고 단정하지 않는다 (25.605, 교차검증)
                    log.warning("야후가 막혔거나 조각이 잇달아 실패해 %d/%d 조각에서 멈춥니다 — %s",
                                index + 1, len(groups), result.error)  # fmt: skip
                    break
                continue
            빈조각 = 0
            if result.error:
                notes.append(f"조각 {index + 1}: {result.error}")

            t1 = time.monotonic()
            # 전체 재수집이면 감지가 필요 없다(받는 것이 곧 새 기준). **짧은 구간만 메우는 복귀 백필은 켠다** (25.601) —
            # 쉬는 동안 분할·배당락이 난 종목은 메운 뒤 분할 전 행과 이어져 절벽이 영구히 남았다
            # 감지 실패는 기록에 남긴다 — 로그만 남고 그 종목 구간은 보류된 채 success 로 닫혔다 (25.603, 교차검증)
            감지말: list[str] = []
            rows, seen, _ = _store_us_bars(
                client, result.data, trade_date, targets, result.source, detect_drift=detect_drift, notes=감지말
            )
            notes.extend(f"조각 {index + 1}: {m}" for m in 감지말)
            if 감지말:
                failed_groups += 1
            saved = time.monotonic() - t1
            stored += rows
            tickers += seen
            log.info(
                "조각 %d/%d  받기 %.1f초  저장 %.1f초  %s행 (%.0f행/초)",
                index + 1, len(groups), fetched, saved, f"{rows:,}", rows / saved if saved else 0,
            )
            # **받은 것은 담았지만 막혔으면 멈춘다** (docs/infra.md 25.609, 교차검증). 25.606 부터 일부만 막힌 조각은
            # ok=True·blocked 로 온다 — `not result.ok` 안에서만 보면 막힌 뒤에도 남은 조각을 다 불렀다
            if result.limit_state == "blocked":
                blocked = True
                failed_groups += 1
                log.warning("야후 호출 제한으로 %d/%d 조각에서 멈춥니다 — %s", index + 1, len(groups), result.error)
                break

        elapsed = time.monotonic() - started
        step: dict[str, Any] = {
            "lookback": lookback,
            "min_turnover": min_turnover,
            "candidates": len(symbols),
            "range": [start, start + (count or len(symbols))],
            "chunks": len(groups),
            "stored_rows": stored,
            "tickers": tickers,
            "failed_chunks": failed_groups,
            "blocked": blocked,
            "elapsed_sec": round(elapsed),
            "notes": notes[:10],
        }
        status = "failed" if stored == 0 else ("partial" if failed_groups or blocked else "success")
        db.finish_batch_run(client, run_id, status=status, step_log=step)

        print(f"구간 {start}~{start + (count or len(symbols))} / 전체 {len(symbols)}종목, 조각 {len(groups)}개")
        print(f"저장 {stored:,}행, 종목 {tickers:,}, 실패 조각 {failed_groups}, 멈춤 {blocked}, {elapsed / 60:.1f}분")
        for note in notes[:10]:
            print(f"  주의: {note}")
        return 0 if stored and not blocked else 1
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="미국 가격 백필")
    parser.add_argument("--lookback", type=int, default=1300, help="받을 달력일 수 (기본 1300 달력일 ≈ 3.6년)")
    parser.add_argument("--start", type=int, default=0, help="정렬된 심볼 목록에서 시작 위치")
    parser.add_argument("--count", type=int, help="이번에 받을 종목 수 (비우면 끝까지)")
    parser.add_argument("--chunk", type=int, default=yfinance_src.CHUNK_SYMBOLS, help="한 요청의 심볼 수")
    parser.add_argument(
        "--detect-drift", action="store_true",
        help="짧은 구간만 메울 때 수정주가 어긋남(분할·배당락)을 찾아 재수집 대기열에 적는다 (docs/infra.md 25.601)",
    )
    parser.add_argument(
        "--min-turnover", type=float, default=None,
        help=f"평균 거래대금 하한(달러). 주면 후보만 받는다. 권장 {DEFAULT_MIN_TURNOVER:,}",
    )
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.lookback, args.start, args.count, args.chunk, args.min_turnover, detect_drift=args.detect_drift)


if __name__ == "__main__":
    sys.exit(guard(main))
