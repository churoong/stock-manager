"""미국 연간 재무 수집 (SEC companyfacts, docs/data-sources.md 14.1·14.2).

국내 financials 와 같은 표·같은 열에 넣는다. 점수·신호 코드가 나라를 가리지 않고 그대로 읽는다.
  financials          연도마다 한 행 (report_code 11011, 연결 1). 정정 공시가 늦게 오면 덮는다
  financial_snapshots 공시(accn)마다 한 행, 덮지 않는다. as_of = filed 다음 거래일(14.1)

실행
  python -m batch.jobs.us_financials                    # 거래대금 후보만 (기본)
  python -m batch.jobs.us_financials --limit 20         # 시험
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.backfill_us import DEFAULT_MIN_TURNOVER, candidate_symbols
from batch.jobs.daily import _us_symbol_ids
from batch.jobs.dividends import _COLS as DIV_COLS
from batch.jobs.dividends import _CONFLICT as DIV_CONFLICT
from batch.jobs.financials import _FIN_COLS as FIN_COLS
from batch.jobs.financials import _FIN_SQL, _SNAP_SQL, _bulk
from batch.jobs.financials import _SNAP_COLS as SNAP_COLS
from batch.sources import dart, sec_edgar
from batch.sources import sec_facts as sf

log = logging.getLogger("us_financials")

JOB_NAME = "us_financials"
#: 정의처는 `batch/sources/dart.ANNUAL_REPORT_CODE` 하나다 (docs/infra.md 25.143)
ANNUAL_REPORT_CODE = dart.ANNUAL_REPORT_CODE

# 몇 해치를 넣나. 점수는 최근 2년, 장기 적립은 5년, 밸류 밴드·백테스트는 길수록 좋다.
# 10년 × 후보 약 2,900종목 × 2표 ≈ 6만 행. Turso 월 쓰기 한도(1,000만 행)에 비해 작다.
YEARS = 10

# 이만큼 회사를 받으면 한 번 저장한다. 끊겨도 앞부분은 남는다.
FLUSH_EVERY = 200


def build_rows(
    stock_id: int,
    reports: list[sf.AnnualReport],
    *,
    min_year: int,
    as_of_for: Callable[[str], str],
    now: str,
) -> tuple[list[tuple], list[tuple]]:
    """financials 행(연도마다)과 financial_snapshots 행(공시마다)."""
    kept = [r for r in reports if r.fiscal_year >= min_year]
    fin_rows: list[tuple] = []
    for year, r in sorted(sf.latest_by_year(kept).items()):
        v = r.values
        fin_rows.append(
            (
                stock_id, year, ANNUAL_REPORT_CODE, "A", 1, r.filed, r.accn, "US-GAAP", "USD", "USD",
                v.get("current_assets"), v.get("noncurrent_assets"), v.get("total_assets"),
                v.get("current_liabilities"), v.get("noncurrent_liabilities"), v.get("total_liabilities"),
                v.get("capital_stock"), v.get("retained_earnings"), v.get("total_equity"),
                v.get("revenue"), v.get("operating_income"), v.get("pretax_income"), v.get("net_income"),
                v.get("comprehensive_income"), sf.SOURCE, now,
            )
        )  # fmt: skip
    snap_rows = [
        (
            stock_id,
            as_of_for(r.filed),
            r.accn,
            r.fiscal_year,
            ANNUAL_REPORT_CODE,
            1,
            json.dumps(
                {**r.values, "period_end": r.period_end, "form": r.form, "filed": r.filed, "concepts": r.concepts},
                ensure_ascii=False,
            ),
            sf.SOURCE,
            now,
        )
        for r in kept
    ]
    return fin_rows, snap_rows


#: 스냅샷을 만든 **뒤에** 더한 payload 열쇠 (docs/infra.md 25.444). 스냅샷은 덮지 않는다(`_SNAP_SQL` DO NOTHING)
#: — 그래서 이 열쇠가 생기기 전 공시의 payload 에는 값이 없다. **같은 공시(accn)에 실려 있던 값**이므로 더해도
#: 시점 규칙을 어기지 않는다. **열쇠가 아예 없는 행에만**(`json_type … IS NULL` — 값이 null 로 적힌 행과 구별한다,
#: 25.448) 그 열쇠 하나를 더하고 나머지는 건드리지 않는다
ADDED_PAYLOAD_KEYS = ("operating_cash_flow", "shares_outstanding", "shares_basic", "shares_basic_prev", "gross_profit")

#: 문자열을 이어 붙이지 않는다 — `tests/test_sql_schema.py` 가 질의를 스키마에 대 볼 수 있게 열쇠마다 문장을 둔다
_FILL_SQL = {
    "operating_cash_flow": (
        "UPDATE financial_snapshots SET payload = json_set(payload, '$.operating_cash_flow', ?)"
        " WHERE stock_id = ? AND receipt_no = ? AND consolidated = 1 AND source = ?"
        " AND json_type(payload, '$.operating_cash_flow') IS NULL"
    ),
    "shares_outstanding": (
        "UPDATE financial_snapshots SET payload = json_set(payload, '$.shares_outstanding', ?)"
        " WHERE stock_id = ? AND receipt_no = ? AND consolidated = 1 AND source = ?"
        " AND json_type(payload, '$.shares_outstanding') IS NULL"
    ),
    "shares_basic": (
        "UPDATE financial_snapshots SET payload = json_set(payload, '$.shares_basic', ?)"
        " WHERE stock_id = ? AND receipt_no = ? AND consolidated = 1 AND source = ?"
        " AND json_type(payload, '$.shares_basic') IS NULL"
    ),
    "shares_basic_prev": (
        "UPDATE financial_snapshots SET payload = json_set(payload, '$.shares_basic_prev', ?)"
        " WHERE stock_id = ? AND receipt_no = ? AND consolidated = 1 AND source = ?"
        " AND json_type(payload, '$.shares_basic_prev') IS NULL"
    ),
    # 매출총이익 (3회차 E, 25.740)
    "gross_profit": (
        "UPDATE financial_snapshots SET payload = json_set(payload, '$.gross_profit', ?)"
        " WHERE stock_id = ? AND receipt_no = ? AND consolidated = 1 AND source = ?"
        " AND json_type(payload, '$.gross_profit') IS NULL"
    ),
}


def payload_fill_statements(stock_id: int, reports: list[sf.AnnualReport]) -> list[tuple[str, list[Any]]]:
    """이미 있는 스냅샷 payload 에 **나중에 더한 열쇠**를 채우는 문장 (25.444). 값이 없는 공시는 건너뛴다."""
    out: list[tuple[str, list[Any]]] = []
    for r in reports:
        for key in ADDED_PAYLOAD_KEYS:
            value = r.values.get(key)
            if value is not None:
                out.append((_FILL_SQL[key], [value, stock_id, r.accn, sf.SOURCE]))
    return out


def build_dividend_rows(
    stock_id: int,
    reports: list[sf.AnnualReport],
    *,
    min_year: int,
    as_of_for: Callable[[str], str],
    now: str,
) -> list[tuple]:
    """stock_dividends 행. 배당 태그가 하나라도 있는 연도만 넣는다. 없으면 무배당으로 본다(docs/accumulation.md 7장).

    국내와 같은 표를 쓰되 금액 단위가 **달러**다(stocks.currency 로 구분). 보고서 연도 = 그 10-K 의 회계연도.
    """
    rows: list[tuple] = []
    for year, r in sorted(sf.latest_by_year([x for x in reports if x.fiscal_year >= min_year]).items()):
        paid, dps = r.values.get("dividends_paid"), r.values.get("dps_common")
        if paid is None and dps is None:
            continue
        net = r.values.get("net_income")
        payout = round(paid / net * 100, 1) if paid is not None and net and net > 0 else None
        rows.append(
            (
                stock_id, year, year, r.accn, as_of_for(r.filed), paid, dps, None,
                payout, "연결", None, net, None, None, sf.SOURCE, now,
            )
        )  # fmt: skip
    return rows


def run(min_turnover: float | None, limit: int | None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        ids = _us_symbol_ids(client)
        if min_turnover:
            keep = candidate_symbols(client, min_turnover)
            ids = {s: i for s, i in ids.items() if s in keep}
        if limit:
            ids = dict(sorted(ids.items())[:limit])

        today = datetime.now(UTC).date()
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market="US", trade_date=today.isoformat()
        )

        sec = sec_edgar.SecClient()
        ticker_map = sec.ticker_map()
        # 클래스주 여러 개가 한 CIK 를 쓴다(GOOGL·GOOG). 한 번 받아 두 종목에 같이 넣는다.
        by_cik: dict[str, list[int]] = {}
        for symbol, stock_id in ids.items():
            if symbol in ticker_map:
                by_cik.setdefault(ticker_map[symbol], []).append(stock_id)
        log.info("대상 %d종목, CIK %d", len(ids), len(by_cik))

        as_of_cache: dict[str, str] = {}

        def as_of_for(filed: str) -> str:
            if filed not in as_of_cache:
                as_of_cache[filed] = cal.next_session("US", date.fromisoformat(filed)).isoformat()
            return as_of_cache[filed]

        min_year = today.year - YEARS
        now = db.now_iso()
        fin_buf: list[tuple] = []
        snap_buf: list[tuple] = []
        div_buf: list[tuple] = []
        stats = dict.fromkeys(("no_facts", "no_annual", "companies", "fin_rows", "snap_rows", "div_rows", "failed"), 0)
        stats["ciks"] = len(by_cik)
        missing_revenue: list[str] = []
        blocked = ""

        fill_buf: list[tuple[str, list[Any]]] = []

        def flush() -> None:
            _bulk(client, "financials", FIN_COLS, _FIN_SQL, fin_buf)
            _bulk(client, "financial_snapshots", SNAP_COLS, _SNAP_SQL, snap_buf)
            _bulk(client, "stock_dividends", DIV_COLS, " " + DIV_CONFLICT, div_buf)
            stats["div_rows"] += len(div_buf)
            div_buf.clear()
            stats["fin_rows"] += len(fin_buf)
            stats["snap_rows"] += len(snap_buf)
            fin_buf.clear()
            snap_buf.clear()
            # 새 스냅샷을 넣은 **뒤에** 채운다 — 이번에 처음 넣은 행은 이미 값이 있어 조건에 안 걸린다
            for start in range(0, len(fill_buf), 200):
                client.batch(fill_buf[start : start + 200])
            stats["payload_fills"] = stats.get("payload_fills", 0) + len(fill_buf)
            fill_buf.clear()

        for index, (cik, stock_ids) in enumerate(sorted(by_cik.items())):
            try:
                payload = sec.company_facts(cik)
            except sec_edgar.SecBlocked as exc:
                blocked = f"{index}/{len(by_cik)} 에서 차단: {exc}"
                break
            except Exception as exc:  # noqa: BLE001 — 한 회사 실패로 전체를 멈추지 않는다
                log.warning("SEC companyfacts %s 실패: %s", cik, exc)
                # "없음" 과 가른다 — 못 받은 회사가 있으면 partial 로 닫는다 (25.601)
                stats["failed"] += 1
                continue
            if payload is None:
                stats["no_facts"] += 1
                continue
            reports = sf.parse_annual_reports(payload)
            if not reports:
                stats["no_annual"] += 1
                continue
            stats["companies"] += 1
            latest = max(reports, key=lambda r: r.fiscal_year)
            if latest.values.get("revenue") is None:
                missing_revenue.append(cik)
            for stock_id in stock_ids:
                fin, snap = build_rows(stock_id, reports, min_year=min_year, as_of_for=as_of_for, now=now)
                fin_buf.extend(fin)
                snap_buf.extend(snap)
                div_buf.extend(
                    build_dividend_rows(stock_id, reports, min_year=min_year, as_of_for=as_of_for, now=now)
                )
                fill_buf.extend(payload_fill_statements(stock_id, [r for r in reports if r.fiscal_year >= min_year]))
            if (index + 1) % FLUSH_EVERY == 0:
                flush()
                log.info("SEC 재무 %d/%d", index + 1, len(by_cik))
        flush()
        db.record_api_call(client, "sec_edgar", count=sec.calls)

        step: dict[str, Any] = {
            **stats,
            "targets": len(ids),
            "missing_revenue": len(missing_revenue),
            "missing_revenue_sample": missing_revenue[:20],
            "sec_calls": sec.calls,
            "blocked": blocked,
        }
        status = "failed" if not stats["companies"] else ("partial" if blocked or stats["failed"] else "success")
        db.finish_batch_run(client, run_id, status=status, step_log=step)

        print(f"대상 {len(ids)}종목 / CIK {len(by_cik)} / 재무 있는 회사 {stats['companies']}")
        print(
            f"financials {stats['fin_rows']}행, 스냅샷 {stats['snap_rows']}행,"
            f" 배당 {stats['div_rows']}행, SEC 호출 {sec.calls}회"
        )
        print(
            f"fact 없음 {stats['no_facts']}, 10-K 연간값 없음 {stats['no_annual']},"
            f" 최근 연도 매출 없음 {len(missing_revenue)}"
        )
        if blocked:
            print(f"  주의: {blocked}")
        return 0 if status != "failed" else 1
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="미국 연간 재무 수집 (SEC)")
    parser.add_argument(
        "--min-turnover",
        type=float,
        default=DEFAULT_MIN_TURNOVER,
        help=f"평균 거래대금 하한(달러). 0 이면 전 종목. 기본 {DEFAULT_MIN_TURNOVER:,}",
    )
    parser.add_argument("--limit", type=int, help="앞에서부터 몇 종목만 (시험용)")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.min_turnover or None, args.limit)


if __name__ == "__main__":
    sys.exit(guard(main))
