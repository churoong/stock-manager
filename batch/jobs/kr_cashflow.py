"""국내 영업현금흐름 채우기 — 기존 `accrual` IC 를 국내에서도 판정할 수 있게.

(docs/factors.md 12.21 19회차 A1, docs/infra.md 25.1317)

국내 재무는 주요계정 묶음 API(`fnlttMultiAcnt`)로 받아 현금흐름표가 없다 — 그래서 발생액(순이익 − 영업현금흐름)이
국내는 늘 "판정 불가" 였다. 사업보고서 스냅샷마다 단일회사 전체 재무제표(`fnlttSinglAcntAll`)를 한 번 불러
영업현금흐름 **합계**를 `financial_snapshots.payload.operating_cash_flow` 에 **그 열쇠가 없는 행에만** 더한다
(미국 `us_financials` 와 같은 방식).

검증 둘의 조건(12.21 표):
- **접수번호가 정확히 같을 때만** 붙인다 — 응답의 `rcept_no` 가 스냅샷과 다르면(정정본) 건너뛴다. 붙이면 미래 정보다
- **자기정합** — 같은 응답의 순이익·총자산이 스냅샷 값과 같을 때만(같은 판의 숫자인지 확인)
- 합계 행만(`dart.OCF_ACCOUNT_IDS`), 당기 값만. 금융업은 부르지 않는다(발생액에서 어차피 뺀다).
  폐지 종목도 부른다(생존편향)
- 하루 `MAX_CALLS_PER_RUN` 회까지, `record_and_guard` 로 80% 경고·100% 멈춤
- **`START_ON` 전에는 아무것도 하지 않는다** — 11-03 국내 기본 백테스트가 반쯤 찬 자료로 판정하지 않게.
  남은 스냅샷이 0 이 되면 `DONE_KEY` 에 그날을 적고, 백테스트는 그 기록이 있을 때만 국내 영업현금흐름을 쓴다
  (`backtest.load_snapshots` — 검증 B 조건 ④)
- 주간 재무 수집 뒤에 돌아 새 사업보고서도 채운다(검증 A 조건 ⑦)

탐침: `python -m batch.jobs.kr_cashflow --probe <dart 고유번호> <사업연도>` — 쓰지 않고 응답의 칸만 찍는다.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import sectors
from batch.sources import dart

log = logging.getLogger(__name__)

JOB_NAME = "kr_cashflow"
#: 이날(사용자 날짜) 전에는 돌지 않는다 — 11-03 국내 기본 백테스트(17:11 UTC) 다음 날
START_ON = date(2026, 11, 4)
#: 한 실행의 DART 호출 상한 — 일 한도 2만의 4분의 1. 나머지는 주간 재무 수집·공시가 쓴다
MAX_CALLS_PER_RUN = 5000
#: 채우기를 마친 날(사용자 날짜) — 백테스트가 이 기록이 있을 때만 국내 영업현금흐름을 쓴다
DONE_KEY = "kr_cfo_fill_done"

TODO_SQL = (
    "SELECT f.stock_id, f.receipt_no, f.fiscal_year, f.consolidated, f.payload, s.dart_corp_code, s.sector_code"
    " FROM financial_snapshots f JOIN stocks s ON s.id = f.stock_id"
    " WHERE s.country = 'KR' AND f.report_code = ? AND s.dart_corp_code IS NOT NULL"
    " AND json_type(f.payload, '$.operating_cash_flow') IS NULL"
    " ORDER BY f.fiscal_year DESC, f.stock_id"
)
#: 못 채운 것도 다시 부르지 않게 null 로 적는다(열쇠는 생기고 값은 없다 — "불렀는데 없었다")
FILL_SQL = (
    "UPDATE financial_snapshots SET payload = json_set(payload, '$.operating_cash_flow', ?)"
    " WHERE stock_id = ? AND receipt_no = ? AND consolidated = ?"
    " AND json_type(payload, '$.operating_cash_flow') IS NULL"
)


def matched_value(snapshot_receipt: str, payload: dict, parsed: dict) -> tuple[int | None, str]:
    """(붙일 값, 까닭). 값이 None 이면 그 스냅샷에는 null 을 적는다(다시 부르지 않는다)."""
    if parsed.get("receipt_no") != snapshot_receipt:
        return None, "접수번호 다름(정정본이거나 판 모름)"
    for key in ("net_income", "total_assets"):
        if payload.get(key) is not None and parsed.get(key) is not None and int(payload[key]) != int(parsed[key]):
            return None, f"자기정합 실패({key})"
    if parsed.get("operating_cash_flow") is None:
        return None, "영업현금흐름 합계 행 없음"
    return int(parsed["operating_cash_flow"]), "채움"


def run(today: date | None = None, max_calls: int = MAX_CALLS_PER_RUN) -> int:
    today = today or cal.user_today()
    if today < START_ON:
        print(f"국내 영업현금흐름: {START_ON} 전이라 돌지 않습니다"
              " (11-03 국내 기본 백테스트를 반쯤 찬 자료로 판정하지 않게)")
        return 0
    client = TursoClient()
    run_id = db.start_batch_run(client, job_name=JOB_NAME, market="KR", trade_date=today.isoformat())
    셈: dict[str, int] = {}
    호출 = 0
    try:
        rows = client.execute(TODO_SQL, [dart.ANNUAL_REPORT_CODE]).dicts()
        남음 = 0
        for r in rows:
            if sectors.is_financial(r.get("sector_code")):
                continue
            if 호출 >= max_calls:
                남음 += 1
                continue
            if db.record_and_guard(client, "dart_opendart", limit_value=dart.DAILY_LIMIT) == "blocked":
                남음 += 1
                셈["한도"] = 셈.get("한도", 0) + 1
                break
            호출 += 1
            res = dart.fetch_full_statement(str(r["dart_corp_code"]), int(r["fiscal_year"]), dart.ANNUAL_REPORT_CODE,
                                            bool(r["consolidated"]))  # fmt: skip
            if not res.ok:
                셈["실패"] = 셈.get("실패", 0) + 1
                continue  # 적지 않는다 — 다음 실행이 다시 부른다
            try:
                payload = json.loads(r["payload"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            값, 까닭 = matched_value(str(r["receipt_no"]), payload, dart.parse_full_statement(res.data or []))
            셈[까닭] = 셈.get(까닭, 0) + 1
            client.execute(FILL_SQL, [값, r["stock_id"], r["receipt_no"], r["consolidated"]])
        끝 = 남음 == 0 and not 셈.get("실패")
        if 끝 and not db.get_setting(client, DONE_KEY):
            db.set_setting(client, DONE_KEY, today.isoformat())
        db.finish_batch_run(client, run_id, status="success",
                            step_log={"calls": 호출, "outcomes": 셈, "remaining": 남음, "done": 끝})  # fmt: skip
        print(f"국내 영업현금흐름: 호출 {호출}, {셈}, 남음 {남음}{' — 채우기 끝' if 끝 else ''}")
        return 0
    except Exception as exc:
        db.finish_batch_run(client, run_id, status="failed", error_text=str(exc)[:500])
        raise
    finally:
        client.close()


def probe(corp_code: str, fiscal_year: int) -> int:
    """쓰지 않고 응답의 칸만 찍는다 — `rcept_no` 가 행마다 오는지, 합계 account_id 가 무엇인지 (검증 A 조건 ⑨)."""
    for consolidated in (True, False):
        res = dart.fetch_full_statement(corp_code, fiscal_year, dart.ANNUAL_REPORT_CODE, consolidated)
        rows = res.data or []
        cf = [r for r in rows if r.get("sj_div") == "CF"][:12]
        print(json.dumps({"consolidated": consolidated, "ok": res.ok, "error": res.error, "rows": len(rows),
                          "rcept_no": sorted({str(r.get("rcept_no")) for r in rows})[:3],
                          "parsed": dart.parse_full_statement(rows),
                          "cf_head": [(r.get("account_id"), r.get("account_nm")) for r in cf]},
                         ensure_ascii=False))  # fmt: skip
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="국내 영업현금흐름 채우기 (docs/infra.md 25.1317)")
    ap.add_argument("--probe", nargs=2, metavar=("CORP_CODE", "YEAR"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    if args.probe:
        return probe(args.probe[0], int(args.probe[1]))
    return run()


if __name__ == "__main__":
    sys.exit(guard(main))
