"""미국 공시 수집 → disclosures (docs/data-sources.md 14.4). 주 1회.

SEC submissions 응답에 공시 목록이 함께 온다(이미 업종 코드를 그 응답에서 받고 있다). 국내와 같은
`disclosures` 표에 넣어 종목 상세의 "최근 공시" 가 나라와 상관없이 같은 모양으로 보이게 한다.

국내(`disclosures_kr`)와 다른 점
  - 응답이 크다(평균 160KB, JPM 4.6MB). 그래서 **지켜보는 종목만**, 주 1회다
  - 초당 10회 한도는 SecClient 가 스스로 지킨다
  - 고유번호가 아니라 CIK 로 부르고, 접수번호(accession)를 국내 receipt_no 자리에 넣는다

실행
  python -m batch.jobs.disclosures_us
  python -m batch.jobs.disclosures_us --max 200
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.sources import sec_edgar as sec

log = logging.getLogger("disclosures_us")

JOB_NAME = "disclosures_us"
SOURCE = "sec_submissions"
DEFAULT_MAX = 300  # 한 번에 부를 회사 수. 초당 10회라 300사면 30초 남짓
PER_STOCK = 10  # 종목당 최근 몇 건 (화면이 10건을 보여 준다)

_COLS = "stock_id, corp_code, receipt_no, title, disclosed_at, url, report_name, is_material, source, fetched_at"

# CIK 는 표에 없다. SEC 티커 목록(company_tickers.json)으로 그때그때 맞춘다 — sectors.py 와 같은 방식이다
TARGETS_SQL = (
    "SELECT DISTINCT s.id, s.yahoo_symbol FROM stocks s"
    " WHERE s.country = 'US' AND s.status = 'active' AND s.yahoo_symbol IS NOT NULL AND ("
    "   s.id IN (SELECT stock_id FROM monitor_targets WHERE market = 'US')"
    "   OR s.id IN (SELECT stock_id FROM watchlist)"
    "   OR s.id IN (SELECT stock_id FROM positions WHERE quantity > 0)"
    " ) ORDER BY s.id"
)


def pick_targets(client: TursoClient, limit: int) -> list[tuple[int, str]]:
    """(stock_id, 야후 심볼). 지켜보는 종목만."""
    rs = client.execute(TARGETS_SQL)
    return [(int(r[0]), str(r[1])) for r in rs.rows][:limit]


def with_cik(targets: list[tuple[int, str]], ticker_map: dict[str, str]) -> tuple[list[tuple[int, str]], list[str]]:
    """(stock_id, CIK) 와 목록에 없는 심볼. SEC 목록의 정확성은 SEC 가 보장하지 않는다(docs 14절)."""
    out: list[tuple[int, str]] = []
    missing: list[str] = []
    for stock_id, symbol in targets:
        cik = ticker_map.get(symbol)
        if cik:
            out.append((stock_id, cik))
        else:
            missing.append(symbol)
    return out, missing


def to_row(stock_id: int, filing: sec.Filing, now: str) -> tuple:
    return (
        stock_id, filing.cik, filing.accession, filing.title, filing.filed, filing.url, filing.form, 0, SOURCE, now,
    )


def store(client: TursoClient, rows: list[tuple]) -> int:
    """같은 접수번호는 건너뛴다(UNIQUE). 국내 disclosures_kr.store 와 같은 규칙이다."""
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


def collect(client: TursoClient, max_stocks: int = DEFAULT_MAX) -> tuple[int, int, list[str]]:
    """(대상 수, 저장 시도 행 수, 경고). 차단되면 받은 것까지 넣고 멈춘다."""
    targets = pick_targets(client, max_stocks)
    if not targets:
        return 0, 0, []
    try:
        sec_client = sec.SecClient()
        ticker_map = sec_client.ticker_map()
    except RuntimeError as exc:  # SEC_USER_AGENT 없음
        return len(targets), 0, [f"공시 수집 건너뜀: {exc}"]
    except Exception as exc:  # noqa: BLE001 — 티커 목록을 못 받으면 아무것도 못 한다
        return len(targets), 0, [f"SEC 티커 목록 실패: {exc}"]

    pairs, missing = with_cik(targets, ticker_map)
    now = db.now_iso()
    rows: list[tuple] = []
    warnings: list[str] = []
    if missing:
        warnings.append(f"SEC 티커 목록에 없는 심볼 {len(missing)}개: {', '.join(missing[:5])}")
    failed = 0
    for stock_id, cik in pairs:
        try:
            filings = sec_client.filings(cik, limit=PER_STOCK)
        except sec.SecBlocked as exc:
            warnings.append(f"SEC 차단으로 중단: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 — 한 회사 실패로 나머지를 버리지 않는다
            log.warning("SEC submissions %s 실패: %s", cik, exc)
            failed += 1
            continue
        rows.extend(to_row(stock_id, f, now) for f in filings)
    db.record_api_call(client, "sec_edgar", count=sec_client.calls)
    if failed:
        warnings.append(f"공시 수집: {failed}/{len(pairs)}사 실패")
    return len(targets), store(client, rows), warnings


def run(max_stocks: int = DEFAULT_MAX) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market="US", trade_date=datetime.now(UTC).date().isoformat()
        )
        try:
            targets, stored, warnings = collect(client, max_stocks)
        except Exception as exc:
            db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
            raise
        db.finish_batch_run(
            client, run_id, status="partial" if warnings else "success",
            step_log={"targets": targets, "rows": stored, "warnings": warnings},
        )
        print(f"미국 공시: 대상 {targets}사, 행 {stored}")
        for w in warnings:
            print(f"  주의: {w}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="미국 공시 수집")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help="한 번에 부를 회사 수")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.max)


if __name__ == "__main__":
    sys.exit(guard(main))
