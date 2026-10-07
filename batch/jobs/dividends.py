"""배당 수집 (DART alotMatter). 국내 유니버스 편입 종목만.

회사마다 최근 6해의 사업보고서를 해마다 하나씩 부른다(25.476 — 값이 그해 보고서 접수일에 알려져야 한다).
**지난해들 보고서는 이미 받았으면 건너뛴다**(25.478) — 평소 실행은 최근 보고서만 부르고, 처음 한 번만 여섯 해를 채운다.

실행
  python -m batch.jobs.dividends                 # 최근 사업연도 = 작년
  python -m batch.jobs.dividends --latest 2025   # 기준 연도 지정
  python -m batch.jobs.dividends --limit 20      # 시험 삼아 적게
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.sources import dart
from batch.sources import dart_dividends as dd

log = logging.getLogger("dividends")

JOB_NAME = "dividends"

# 호출 간격(초). DART 의 초당·분당 제한은 공개돼 있지 않다 [확인필요].
# 일 한도 20,000 에 평소 약 880회(최근 보고서만, 25.478)라 여유가 크므로 느긋하게 둔다. 처음 채울 때는
# 약 5,300회 × 0.25초 ≈ 22분 + 응답 시간 — 워크플로 제한을 넘으면 다음 실행이 받은 것을 건너뛰고 이어 간다
MIN_INTERVAL = 0.25

_COLS = (
    "stock_id, fiscal_year, report_year, receipt_no, as_of_date, cash_dividend_total, dps_common, dps_preferred,"
    " payout_ratio, payout_basis, yield_common, net_income_consolidated, eps_consolidated, face_value,"
    " source, fetched_at"
)
_CONFLICT = (
    "ON CONFLICT (stock_id, fiscal_year, report_year) DO UPDATE SET receipt_no = excluded.receipt_no,"
    " as_of_date = excluded.as_of_date, cash_dividend_total = excluded.cash_dividend_total,"
    " dps_common = excluded.dps_common, dps_preferred = excluded.dps_preferred,"
    " payout_ratio = excluded.payout_ratio, payout_basis = excluded.payout_basis,"
    " yield_common = excluded.yield_common, net_income_consolidated = excluded.net_income_consolidated,"
    " eps_consolidated = excluded.eps_consolidated, face_value = excluded.face_value,"
    " source = excluded.source, fetched_at = excluded.fetched_at"
)


#: 받을 사업보고서 수 — 해마다 하나씩 (docs/infra.md 25.476). 백테스트 창(5년) + 1
REPORTS_BACK = 6


def report_years(latest: int) -> list[int]:
    """최근 `REPORTS_BACK` 해의 사업보고서 **하나씩** (docs/infra.md 25.476, 2회차 검증 1).

    예전에는 최근 보고서와 3년 전 보고서 둘만 받았다(각각 3년치를 실어 6개 연도 **값**은 덮는다).
    그런데 값이 **언제 알려졌나**(`as_of_date`)는 그것을 실은 보고서의 접수일이다 — FY2023·2024 값은
    2026-03 에 접수된 2025 보고서에만 있어, 백테스트 기준일 2024-03~2026-03 에는 FY2022(2023-03 접수)가
    최신 배당으로 쓰였다(배당수익률이 1~2년 묵음). 해마다 받으면 각 해의 값이 그해 보고서 접수일에 알려진다.
    호출은 회사당 1 → 6번(유니버스 약 880 × 6 ≈ 5,300, DART 하루 한도 안)이고 해에 한 번 돈다
    """
    return [latest - k for k in range(REPORTS_BACK)]


def to_row(stock_id: int, year: dd.DividendYear, fetched: str) -> tuple:
    return (
        stock_id, year.fiscal_year, year.report_year, year.receipt_no, year.as_of_date,
        year.cash_dividend_total, year.dps_common, year.dps_preferred,
        year.payout_ratio, year.payout_basis, year.yield_common,
        year.net_income_consolidated, year.eps_consolidated, year.face_value,
        dd.SOURCE, fetched,
    )


#: 이만큼 연속으로 실패하면 멈춘다 — 한 종목의 일시적 실패가 아니라 키·점검 같은 전체 문제다 (docs/infra.md 25.319).
#: **배당만 20 이다** (25.609, 교차검증 — 25.607 에서 5 로 줄였다가 되돌림). 배당은 한 해 며칠만 돈다(dividends.yml)
#: — 짧은 DART 장애(시간 초과 5번)로 멈추면 한 달을 잃는다. 끝까지 불러도 한도(20,000)의 약 9% 라 더 부르는 쪽이 싸다
MAX_CONSECUTIVE_FAILURES = 20


def targets(client: TursoClient) -> list[tuple[int, str]]:
    rs = client.execute(
        "SELECT s.id, s.dart_corp_code FROM stocks s JOIN universe_members u ON u.stock_id = s.id"
        " WHERE s.country = 'KR' AND s.dart_corp_code IS NOT NULL AND u.included = 1"
        f"   AND u.snapshot_date = {db.latest_snapshot_sql()} ORDER BY s.id",
        ["KR"],
    )
    return [(int(r[0]), str(r[1])) for r in rs.rows]


def run(latest: int | None = None, limit: int | None = None) -> int:
    from batch.jobs.etf import _bulk

    latest = latest or datetime.now(UTC).year - 1
    years = report_years(latest)
    client = TursoClient()
    try:
        db.apply_migrations(client)
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="KR", trade_date=str(latest))
        corps = targets(client)
        if limit:
            corps = corps[:limit]
        # **부르기 전에 본다** (docs/infra.md 25.388). 100사마다 세므로, 재무 수집이 이미 한도를 채운 날에도
        # 첫 확인까지 DART 를 100번 더 불렀다
        if db.usage_blocked_today(client, "dart_opendart"):
            db.finish_batch_run(client, run_id, status="skipped", error_text="DART 일일 한도가 이미 찼습니다")
            print("DART 일일 한도가 이미 찼습니다. 배당 수집을 건너뜁니다")
            return 0

        # **지난해들 보고서는 받은 것을 건너뛴다** (infra 25.478, 교차검증). 25.476 으로 호출이 회사당 6번이 되자
        # 처음 채우는 실행이 워크플로 제한(시간)을 넘을 수 있었고, 대상이 늘 처음부터 돌아 뒤쪽 회사는 영영 못 채웠다.
        # 최근 보고서는 늘 부른다(정정 반영). 지난 보고서의 정정은 드물어 받지 않는다 `[확인필요: 정정 빈도]`
        이미 = {
            (int(r[0]), int(r[1])) for r in client.execute(
                "SELECT DISTINCT stock_id, report_year FROM stock_dividends WHERE report_year < ?", [latest]
            ).rows
        }  # fmt: skip
        now = db.now_iso()
        rows: list[tuple] = []
        failures: list[str] = []
        no_data = 0
        calls = 0
        blocked = False
        멈춘까닭 = ""  # 한도인지 잇단 실패인지 — 예전에는 둘 다 "요청 제한" 으로 적혔다 (25.607)

        # **중간중간 센다** (2026-09-22, docs/infra.md 25.117). 예전에는 루프가 다 끝난
        # 뒤에 `count=calls` 로 한 번만 적었다 — 도중에 죽으면(워크플로 시간 초과·D1 한도)
        # 1,700여 회가 **통째로** DART 카운터에서 사라진다. 그날 남은 DART 작업이
        # "아직 여유 있다" 고 판단한다
        안_센것 = 0
        연속실패 = 0
        성공 = 0
        for index, (stock_id, corp_code) in enumerate(corps):
            for year in years:
                if year != latest and (stock_id, year) in 이미:
                    continue
                if calls:
                    time.sleep(MIN_INTERVAL)
                result = dd.fetch_alot_matter(corp_code, year)
                calls += 1
                안_센것 += result.attempts  # 부르지 않은 실패(키 없음·오프라인)는 세지 않는다 (25.607)
                if not result.ok:
                    failures.append(f"{corp_code}/{year}: {result.error}")
                    연속실패 += 1
                    if result.limit_state == "blocked":
                        blocked = True
                        멈춘까닭 = f"DART 요청 제한: {result.error}"
                        break
                    # **연속으로 실패하면 멈춘다** (docs/infra.md 25.319). 키가 틀렸거나(010/011) DART 점검(800)이면
                    # 1,700여 회가 전부 실패하면서 그날 한도만 깎았다
                    if 연속실패 >= MAX_CONSECUTIVE_FAILURES:
                        blocked = True
                        멈춘까닭 = (f"연속 {연속실패}회 실패 — DART 키·점검 여부를 확인하세요"
                                    f" ({corp_code}/{year}: {result.error})")  # fmt: skip
                        break
                    continue
                연속실패 = 0
                성공 += 1
                if not result.data:
                    no_data += 1
                rows.extend(to_row(stock_id, item, now) for item in result.data)
            if blocked:
                log.warning("%s — %d/%d 사에서 멈춥니다", 멈춘까닭, index + 1, len(corps))
                break
            # 100사마다 카운터에 올리고 **그 수를 본다.** 바깥이 020 으로 거절할 때까지
            # 기다리는 것은 "넘고 나서 아는 것" 이다 (CLAUDE.md: 100%에서 중단)
            if 안_센것 >= 100:
                상태 = db.record_and_guard(
                    client, "dart_opendart", count=안_센것, limit_value=dart.DAILY_LIMIT, label="DART"
                )
                안_센것 = 0
                if 상태 == "blocked":
                    blocked = True
                    멈춘까닭 = "DART 일일 한도(우리 카운터)에 도달해 멈춥니다"
                    log.warning("DART 카운터가 한도에 닿아 %d/%d 사에서 멈춥니다", index + 1, len(corps))
                    break
            # 중간 저장. 끊겨도 받은 만큼은 남긴다
            if len(rows) >= 2000:
                _bulk(client, "stock_dividends", _COLS, rows, _CONFLICT)
                rows = []
            if (index + 1) % 100 == 0:
                log.info("배당 %d/%d 사", index + 1, len(corps))

        _bulk(client, "stock_dividends", _COLS, rows, _CONFLICT)
        if 안_센것:
            db.record_and_guard(
                client, "dart_opendart", count=안_센것, limit_value=dart.DAILY_LIMIT, label="DART"
            )

        saved = client.execute(
            "SELECT COUNT(*), COUNT(DISTINCT stock_id), SUM(cash_dividend_total > 0) FROM stock_dividends"
        ).rows[0]
        step: dict[str, Any] = {
            "report_years": years,
            "companies": len(corps),
            "calls": calls,
            "no_data_reports": no_data,
            "failures": len(failures),
            # 멈춘 까닭을 **맨 앞에** — 예전에는 failures 끝에 붙어 앞 5건만 남기는 기록에서 잘렸다 (25.607)
            "stopped": 멈춘까닭 or None,
            "failure_samples": failures[:5],
            "blocked": blocked,
            "table_rows": saved[0],
            "table_companies": saved[1],
            "rows_with_cash_dividend": saved[2],
        }
        # **하나도 못 받았으면 실패다** (docs/infra.md 25.319).
        # 예전에는 전부 실패해도 partial·종료 0 이라 워크플로가 초록이었다
        전부실패 = calls > 0 and 성공 == 0
        status = "failed" if 전부실패 else ("partial" if (failures or blocked) else "success")
        원인 = 멈춘까닭 or (f"{len(failures)}건 실패 — 예: {failures[0]}" if failures else "")
        db.finish_batch_run(client, run_id, status=status, step_log={**step, "succeeded_calls": 성공},
                            error_text=원인 or None)  # fmt: skip

        print(f"보고서 {years}, 대상 {len(corps)}사, 호출 {calls}회, 데이터 없음 {no_data}, 실패 {len(failures)}")
        print(f"표 누적: {saved[0]}행 / {saved[1]}사 / 현금배당 있는 행 {saved[2]}")
        if 멈춘까닭:
            print(f"  멈춤: {멈춘까닭}")
        for sample in failures[:5]:
            print(f"  실패 {sample}")
        return 1 if (blocked or 전부실패) else 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="DART 배당 수집")
    parser.add_argument("--latest", type=int, help="최근 사업연도 (기본: 작년)")
    parser.add_argument("--limit", type=int, help="앞에서부터 몇 사만 (시험용)")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.latest, args.limit)


if __name__ == "__main__":
    sys.exit(guard(main))
