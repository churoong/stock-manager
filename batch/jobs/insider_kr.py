"""국내 내부자 매매 수집 (docs/data-sources.md 16.1, 엔진 강화 D).

유니버스 편입 종목의 임원·주요주주 소유상황보고를 insider_trades 에 쌓는다.
집계와 근거표는 batch/services/insider.py 가 이 표만 읽는다. **팩터에는 넣지 않는다**(docs/signals.md 8장).

주 1회 도는 이유: 보고가 회사당 몇 건이라 매일 볼 것이 없고, 회사마다 1MB 응답을 받아야 한다
(기간 파라미터가 없다, 16.1 실측). 유니버스 879사 × 1회 = 879회로 DART 일 한도(2만) 안이다.

**받은 것을 다 넣지 않는다.** 응답은 최근 2년치인데 LOOKBACK_DAYS 안의 접수분만 저장한다.
2년치를 전부 넣으면 첫 실행에 수십만 행이 되고, 집계 창은 90일이라 쓰지도 않는다(Turso 쓰기 예산).

실행
  python -m batch.jobs.insider_kr                  # 유니버스 전체
  python -m batch.jobs.insider_kr --limit 5        # 시험
  python -m batch.jobs.insider_kr --lookback 365
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.financials import target_corps
from batch.sources import dart, dart_insider

log = logging.getLogger("insider_kr")

JOB_NAME = "insider_kr"
# 집계 창(services/insider.WINDOW_DAYS)은 90일이다. 두 배로 받아 두면 주 1회 실행이
# 한두 번 걸러도 창이 비지 않고, 늦게 접수된 보고도 들어온다.
LOOKBACK_DAYS = 180
# 업종 채우기와 같은 간격. DART 초당 제한은 공개돼 있지 않다 [확인필요]
DART_INTERVAL = 0.25

_COLS = (
    "stock_id, filed_date, trade_date, insider, role, action, shares, price, currency,"
    " source, receipt_no, fetched_at"
)


def to_row(stock_id: int, report: dart_insider.InsiderReport, now: str) -> tuple:
    """보고서 → insider_trades 한 행. 거래일과 단가는 이 API 에 없다(문서 16.1)."""
    return (
        stock_id,
        report.filed_date,
        None,
        report.insider,
        report.role,
        report.action,
        abs(report.shares_delta),
        None,
        "KRW",
        dart_insider.SOURCE,
        report.receipt_no,
        now,
    )


def store(client: TursoClient, rows: list[tuple]) -> int:
    """같은 보고서를 두 번 넣지 않는다(UNIQUE). 이미 있으면 건너뛴다."""
    if not rows:
        return 0
    width = db.column_count(_COLS)
    for row in rows:
        if len(row) != width:
            raise ValueError(f"값 {len(row)}개인데 열은 {width}개입니다. 열과 값 묶음을 함께 고치세요")
    placeholder = "(" + ", ".join(["?"] * width) + ")"
    per = max(1, 20_000 // width // 10)
    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), per):
        chunk = rows[start : start + per]
        statements.append((
            f"INSERT INTO insider_trades ({_COLS}) VALUES {', '.join([placeholder] * len(chunk))}"
            " ON CONFLICT (source, receipt_no, insider, action, shares) DO NOTHING",
            [v for row in chunk for v in row],
        ))  # fmt: skip
    for start in range(0, len(statements), 20):
        client.batch(statements[start : start + 20])
    return len(rows)


def run(limit: int | None = None, lookback_days: int = LOOKBACK_DAYS, as_of: str | None = None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        # 한국 날짜다 (25.605 — 25.204 와 같은 까닭). 수 03:20 KST 예약에서 UTC 날짜는 아직 화요일이었다
        as_of = as_of or cal.local_today("KR").isoformat()
        since = (date.fromisoformat(as_of) - timedelta(days=lookback_days)).isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="KR", trade_date=as_of)
        now = db.now_iso()

        corps = target_corps(client)[: limit or None]
        rows: list[tuple] = []
        counts = {"companies": len(corps), "with_reports": 0, "dropped_rows": 0, "failed": 0}
        error = ""
        잇단실패 = 0
        첫원인 = ""
        불린 = 0
        한도 = False
        for index, (corp_code, stock_id) in enumerate(corps):
            if index:
                time.sleep(DART_INTERVAL)
            result = dart_insider.fetch_reports(corp_code, since=since)
            불린 += 1
            # **2026-09-22 까지 이 호출을 한 번도 안 셌다** (docs/infra.md 25.117).
            # 주 1회 × 879사 = 879회가 DART 하루 한도(20,000)에서 조용히 빠져나갔다
            상태 = db.record_and_guard(client, "dart_opendart", count=result.attempts, limit_value=dart.DAILY_LIMIT,
                                       label="DART")  # fmt: skip
            if not result.ok:
                counts["failed"] += 1
                if result.limit_state == "blocked":
                    error = f"{index}/{len(corps)} 에서 DART 한도: {result.error}"
                    한도 = True
                    break
                log.warning("%s 실패: %s", corp_code, result.error)
                첫원인 = 첫원인 or f"{corp_code}: {result.error}"
                # **잇달면 멈춘다** (docs/infra.md 25.605, 감사). 키 오류(011)·점검(800)·5xx 면 879사를 다 불러
                # 한도와 워크플로 시간(40분)만 썼다 — 매달리면 저장 전에 잡이 죽어 받은 행까지 잃었다
                잇단실패 += 1
                if 잇단실패 >= dart.MAX_CONSECUTIVE_FAILURES:
                    # 원인은 첫 실패가 아니라 **지금 실패**다 (25.606, 교차검증)
                    error = (f"{index + 1}/{len(corps)} 에서 DART 실패 {잇단실패}번 잇달아 멈춤"
                             f" — {corp_code}: {result.error}")  # fmt: skip
                    break
            else:
                잇단실패 = 0
                reports, dropped = result.data
                counts["dropped_rows"] += dropped
                if reports:
                    counts["with_reports"] += 1
                    rows.extend(to_row(stock_id, r, now) for r in reports)
            # **받은 응답은 담고 나서 멈춘다** (docs/infra.md 25.386, 25.318 과 같은 모양). 예전에는 카운터가
            # 한도에 닿은 호출의 응답을 **받아 놓고 버렸다** — 호출은 이미 썼는데 그 회사의 보고가 빠졌다
            if 상태 == "blocked":
                error = f"{index + 1}/{len(corps)} 에서 DART 한도(우리 카운터)"
                한도 = True
                break

        written = store(client, rows)
        step = {**counts, "rows": written, "since": since, "lookback_days": lookback_days}
        # 실패가 있으면 **원인을 기록에 남긴다**, 한 곳도 못 받았으면 failed (25.605). 예전에는 전사 실패가
        # partial·error_text 없음으로 닫혀, 무응답 감시는 "돌았다" 로 보고 원인은 로그에만 있었다
        멈춤 = bool(error)  # 한도·잇단 실패로 도중에 멈췄다
        받은곳 = 불린 - counts["failed"]
        if counts["failed"] and not error:
            error = f"{counts['failed']}사 실패 — 예: {첫원인}"
        # 한도 멈춤은 고장이 아니다 — 예전처럼 partial (25.318)
        status = "failed" if 불린 and 받은곳 <= 0 and not 한도 else ("partial" if error else "success")
        db.finish_batch_run(client, run_id, status=status, step_log=step, error_text=error or None)
        print(f"내부자 보고 {written}행 (회사 {counts['companies']} 중 보고 있음 {counts['with_reports']},"
              f" 실패 {counts['failed']}, 버린 행 {counts['dropped_rows']}, {since} 이후)")  # fmt: skip
        return 1 if 멈춤 or status == "failed" else 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="국내 내부자 매매 수집")
    parser.add_argument("--limit", type=int, help="앞에서부터 몇 개만 (시험용)")
    parser.add_argument("--lookback", type=int, default=LOOKBACK_DAYS, help=f"며칠치 (기본 {LOOKBACK_DAYS})")
    parser.add_argument("--as-of", dest="as_of")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.limit, args.lookback, args.as_of)


if __name__ == "__main__":
    sys.exit(guard(main))
