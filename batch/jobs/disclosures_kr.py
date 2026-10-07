"""국내 공시 수집 → disclosures (docs/data-sources.md 1.2). 국내 일일 배치가 포트폴리오 갱신 뒤에 부른다.

대상은 **지켜보는 종목만**: 장중 감시 대상(보유·당일 신호) + 관심 종목. 회사당 호출 1회라 유니버스
전체(879사)를 매일 부르면 한 달에 2만 회 가까이 된다 — 한도(일 2만) 안이지만 필요가 없다.
전 종목이 필요하면 `--all` 로 손으로 돌린다.

실행
  python -m batch.jobs.disclosures_kr              # 최근 7일, 지켜보는 종목
  python -m batch.jobs.disclosures_kr --days 90 --all
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.sources import dart
from batch.sources import dart_disclosures as src

log = logging.getLogger("disclosures_kr")

JOB_NAME = "disclosures_kr"
DEFAULT_DAYS = 7  # 며칠 빠져도 메워지도록 한 주. 같은 접수번호는 UNIQUE 라 두 번 들어가지 않는다
CALL_GAP_SECONDS = 0.25  # 초당 제한이 공개돼 있지 않아 둔 간격 [확인필요]

TARGETS_SQL = (
    "SELECT DISTINCT s.id AS stock_id, s.dart_corp_code AS corp_code FROM stocks s"
    " WHERE s.country = 'KR' AND s.dart_corp_code IS NOT NULL AND ("
    "   s.id IN (SELECT stock_id FROM monitor_targets WHERE market = 'KR')"
    "   OR s.id IN (SELECT stock_id FROM watchlist)"
    " ) ORDER BY s.id"
)
ALL_SQL = (
    "SELECT s.id AS stock_id, s.dart_corp_code AS corp_code FROM stocks s JOIN universe_members u ON u.stock_id = s.id"
    " WHERE s.country = 'KR' AND s.dart_corp_code IS NOT NULL AND u.included = 1"
    f" AND u.snapshot_date = {db.latest_snapshot_sql()}"
    " ORDER BY s.id"
)


#: 실패 원인을 경고에 몇 개까지 적나 (25.605). 대개 모두 같은 원인이라 몇 개면 충분하다
대표_원인_수 = 3


def pick_targets(client: TursoClient, everyone: bool = False) -> list[tuple[int, str]]:
    rs = client.execute(ALL_SQL, ["KR"]) if everyone else client.execute(TARGETS_SQL)
    return [(int(r[0]), str(r[1])) for r in rs.rows]


def to_row(stock_id: int, d: src.Disclosure, source: str, now: str) -> tuple:
    return (stock_id, d.corp_code, d.receipt_no, d.title, d.disclosed_at, d.url, d.report_name, 0, source, now)


_COLS = "stock_id, corp_code, receipt_no, title, disclosed_at, url, report_name, is_material, source, fetched_at"


def store(client: TursoClient, rows: list[tuple]) -> int:
    """같은 접수번호는 건너뛴다(UNIQUE). 정정 공시는 접수번호가 다르므로 새 행이다."""
    if not rows:
        return 0
    placeholder = "(" + ", ".join(["?"] * db.column_count(_COLS)) + ")"
    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), 200):
        chunk = rows[start : start + 200]
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append((
            f"INSERT INTO disclosures ({_COLS}) VALUES " + ", ".join([placeholder] * len(chunk))
            + " ON CONFLICT (receipt_no) DO NOTHING",
            args,
        ))
    client.batch(statements)
    return len(rows)


def collect(
    client: TursoClient, days: int = DEFAULT_DAYS, everyone: bool = False
) -> tuple[int, int, list[str], list[int]]:
    """(대상 수, 저장 시도 행 수, 경고, **창을 다 받은 종목**). 한 회사가 실패해도 나머지는 받는다.

    창을 다 받은 종목 = 호출이 성공했고 쪽 상한에 잘리지 않은 종목 (docs/infra.md 25.487).
    자사주 공시 IC 가 "그 종목의 그 구간에 공시가 없었다" 를 말할 수 있는 것은 이 종목들뿐이다 —
    전 종목 수집 당시 대상이 아니었거나 실패·잘린 종목은 모른다."""
    targets = pick_targets(client, everyone)
    if not targets:
        return 0, 0, [], []
    # **한국 날짜다** (docs/infra.md 25.204). 국내 아침 배치(08:27 KST)에서 UTC 날짜는 아직 전날이라
    # 오늘 아침 접수된 공시가 하루 늦게 들어왔다. DART 의 접수일은 한국 날짜다
    today = cal.local_today("KR")
    bgn = (today - timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    now = db.now_iso()
    rows: list[tuple] = []
    warnings: list[str] = []
    failed = 0
    잇단실패 = 0
    원인: list[str] = []
    덮음: list[int] = []
    for i, (stock_id, corp_code) in enumerate(targets):
        if i:
            time.sleep(CALL_GAP_SECONDS)
        result = src.fetch_list(corp_code, bgn, end)
        # 세기만 하고 안 보고 있었다 (25.117). 바깥이 거절할 때까지 기다리면 "넘고 나서" 안다
        상태 = db.record_and_guard(client, "dart_opendart", count=result.attempts, limit_value=dart.DAILY_LIMIT,
                                   label="DART")  # 쪽을 넘기면 부른 만큼 센다 (25.477)  # fmt: skip
        if result.data:  # 실패해도 받은 쪽까지는 담는다 — 멈추기 전에 (25.477)
            rows.extend(to_row(stock_id, d, result.source, now) for d in result.data)
        if result.ok and not result.error:
            덮음.append(int(stock_id))
        elif result.ok:
            warnings.append(f"{corp_code}: {result.error}")
        if not result.ok:
            failed += 1
            if result.limit_state == "blocked":
                warnings.append(f"공시 수집 중단: {result.error}")
                break
            # **원인을 남기고, 잇달면 멈춘다** (docs/infra.md 25.605, 감사). 예전에는 "30/30사 실패" 만 남아
            # 키 오류(011)인지 점검(800)인지 몰랐고, 모든 호출이 실패해도 끝까지 불렀다 — 이 잡은 일일 배치 안에서
            # 발송보다 먼저 돈다
            if len(원인) < 대표_원인_수:
                원인.append(f"{corp_code}: {result.error}")
            잇단실패 += 1
            if 잇단실패 >= dart.MAX_CONSECUTIVE_FAILURES:
                # 멈추게 한 원인은 **지금 실패**다 — 앞 3건(대표 원인)과 다를 수 있다 (25.606, 교차검증)
                warnings.append(f"공시 수집 중단: DART 실패가 {잇단실패}번 잇달았습니다 — {corp_code}: {result.error}")
                break
        else:
            잇단실패 = 0
        # 받은 응답은 담고 나서 멈춘다 (docs/infra.md 25.386) — 한도에 닿은 호출의 공시를 버리지 않는다
        if 상태 == "blocked":
            warnings.append("공시 수집 중단: DART 일일 한도(우리 카운터)")
            break
    if failed:
        warnings.append(f"공시 수집: {failed}/{len(targets)}사 실패")
        warnings.extend(원인)
    stored = store(client, rows)
    return len(targets), stored, warnings, 덮음


def run(days: int = DEFAULT_DAYS, everyone: bool = False) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market="KR", trade_date=cal.local_today("KR").isoformat()
        )
        try:
            targets, stored, warnings, 덮음 = collect(client, days, everyone)
        except Exception as exc:
            db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
            raise
        db.finish_batch_run(
            client, run_id, status="partial" if warnings else "success",
            # 전 종목 수집이면 창을 다 받은 종목을 남긴다 — 자사주 공시 IC 가 종목마다 "덮었나" 를 본다 (25.487)
            step_log={"targets": targets, "rows": stored, "days": days, "all": everyone, "warnings": warnings[:50],
                      **({"covered_ids": 덮음} if everyone else {})},  # fmt: skip
        )
        print(f"공시 수집: 대상 {targets}사, 행 {stored} (최근 {days}일)")
        for w in warnings:
            print(f"  주의: {w}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="국내 공시 수집")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--all", action="store_true", help="지켜보는 종목이 아니라 유니버스 전체")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.days, args.all)


if __name__ == "__main__":
    sys.exit(guard(main))
