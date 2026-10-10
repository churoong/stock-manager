"""재무 데이터 수집.

두 곳에 나눠 저장한다. 이유가 다르다.

  financials            지금 시점에서 본 최신 재무. 정정 공시가 오면 덮어쓴다.
                        화면과 팩터 계산이 본다
  financial_snapshots   그때 받은 값 그대로. 덮어쓰지 않고 쌓는다.
                        백테스트는 오직 이것만 본다

정정 공시가 나면 과거 판단을 소급해 바꿔서는 안 된다. 2026년 5월에 정정된
숫자를 2026년 4월의 투자 판단에 쓰면 그것도 미래를 보는 것이다.

실행
  python -m batch.jobs.financials --years 5
  python -m batch.jobs.financials --year 2025 --report 11011
  python -m batch.jobs.financials --years 3 --report 11011 --resume   # 며칠에 걸쳐 이어 받기
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.sources import dart

log = logging.getLogger("financials")

JOB_NAME = "financials"

# 호출 사이 간격. DART 가 초당 한도를 공개하지 않아 보수적으로 둔다.
CALL_INTERVAL_SECONDS = 0.3


def sync_corp_codes(client: TursoClient) -> tuple[int, list[str]]:
    """DART 고유번호를 종목에 이어 붙인다.

    DART 는 종목코드가 아니라 고유번호로 조회한다. 이 연결이 없으면
    재무를 한 건도 못 받는다.
    """
    result = dart.fetch_corp_codes()
    db.record_api_call(client, "dart_opendart", limit_value=dart.DAILY_LIMIT, warn_at_pct=80)

    if not result.ok:
        return 0, [f"고유번호 받기 실패: {result.error}"]

    now = db.now_iso()
    statements = [
        (
            "UPDATE stocks SET dart_corp_code = ?, fetched_at = ?"
            " WHERE ticker = ? AND country = 'KR'",
            [corp.corp_code, now, corp.stock_code],
        )
        for corp in result.data
    ]
    for i in range(0, len(statements), 300):
        client.batch(statements[i : i + 300])

    rs = client.execute(
        "SELECT COUNT(*) FROM stocks WHERE country = 'KR' AND dart_corp_code IS NOT NULL"
    )
    linked = int(rs.scalar() or 0)
    # 성공 요약은 경고가 아니다 — 여기서 찍고 빈 경고를 돌려준다 (25.604). 받기 실패만 경고로 올린다
    print(f"  상장사 {len(result.data)}건 중 {linked}종목에 고유번호를 이었습니다")
    return linked, []


def linked_count(client: TursoClient) -> int:
    """고유번호가 이미 이어진 국내 종목 수. 못 읽으면 0(=다시 잇는다)."""
    try:
        rs = client.execute(
            "SELECT COUNT(*) FROM stocks WHERE country = 'KR' AND dart_corp_code IS NOT NULL"
        )
        return int(rs.scalar() or 0)
    except Exception:  # noqa: BLE001 — 모르면 잇는 쪽이 안전하다
        return 0


def target_corps(
    client: TursoClient, universe_only: bool = True, only_ids: list[int] | None = None
) -> list[tuple[str, int]]:
    """수집 대상. (고유번호, stock_id) 목록.

    유니버스에 든 종목만 받는다. 제외된 종목의 재무는 쓸 일이 없고
    호출만 늘린다. `only_ids` 를 주면 그 종목만 — 유니버스 밖 종목 참고 분석(docs/analysis.md 8장, 25.1018)
    """
    if only_ids:
        rs = client.execute(
            "SELECT dart_corp_code, id FROM stocks WHERE country = 'KR' AND dart_corp_code IS NOT NULL"
            " AND id IN (SELECT value FROM json_each(?))",
            [json.dumps(sorted(only_ids))],
        )
        return [(str(r[0]), int(r[1])) for r in rs.rows]
    if universe_only:
        sql = (
            "SELECT s.dart_corp_code, s.id FROM stocks s"
            " JOIN universe_members u ON u.stock_id = s.id"
            " WHERE s.country = 'KR' AND s.dart_corp_code IS NOT NULL"
            "   AND u.included = 1"
            f"   AND u.snapshot_date = {db.latest_snapshot_sql()}"
        )
        args: list[str] = ["KR"]
    else:
        sql = (
            "SELECT dart_corp_code, id FROM stocks"
            " WHERE country = 'KR' AND dart_corp_code IS NOT NULL"
        )
        args = []
    rs = client.execute(sql, args)
    return [(str(row[0]), int(row[1])) for row in rs.rows]


def already_collected(client: TursoClient, fiscal_year: int, report_code: str) -> set[int]:
    """그 회계연도·보고서가 이미 들어와 있는 `stock_id` 들. 못 읽으면 빈 집합(=다시 받는다)."""
    try:
        rs = client.execute(
            "SELECT DISTINCT stock_id FROM financials WHERE fiscal_year = ? AND report_code = ?",
            [fiscal_year, report_code],
        )
        return {int(row[0]) for row in rs.rows}
    except Exception:  # noqa: BLE001 — 모르면 받는 쪽이 안전하다
        return set()


#: 한도에 닿아 멈췄다는 표지. 바깥 루프가 이 글자로 **모든** 기간을 멈춘다 (docs/infra.md 25.318)
LIMIT_REACHED = "DART 일일 한도에 도달했습니다"

#: 한도가 아닌 실패가 잇달아 멈췄다는 표지. 바깥 루프는 이것으로도 모든 기간을 멈춘다. 예전(25.604)에는
#: `LIMIT_REACHED` 를 빌려 써서 키 오류·점검 때 "한도에 도달" 이라는 틀린 원인이 남았다 (25.605, 교차검증 — 25.389 와
#: 같은 모양)
FAILURES_STOPPED = "DART 실패가 잇달아 멈췄습니다"

#: 한도가 아닌 DART 실패가 이만큼 잇달면 멈춘다 (25.604). 정의는 `dart.MAX_CONSECUTIVE_FAILURES`
MAX_CONSECUTIVE_FAILURES = dart.MAX_CONSECUTIVE_FAILURES


def _한도_찼나(client: TursoClient) -> bool:
    """오늘 DART 카운터가 이미 100% 인가. 못 읽으면 거짓(부른 뒤 세는 쪽이 다시 본다)."""
    try:
        rs = client.execute(
            "SELECT state FROM api_usage WHERE api_name = 'dart_opendart' AND window_type = 'day' AND window_start = ?",
            [datetime.now(UTC).strftime("%Y-%m-%d")],
        )
        rows = rs.dicts()
    except Exception:  # noqa: BLE001 — 표가 없거나 가짜 클라이언트
        return False
    return bool(rows) and rows[0].get("state") == "blocked"


class Streak:
    """기간을 건너 이어 세는 **잇단 실패 수** (docs/infra.md 25.1083).

    `collect_period` 가 기간마다 0 에서 다시 세서, 종목이 몇 개뿐이라 기간마다 호출이 한 번인 경로(참고 분석 — 유니버스
    밖 종목 하나)는 DART 가 점검·불통이어도 5번에 닿지 않고 23개 기간을 다 불렀다. 10-08 국내 배치에서 연결 실패
    (`Max retries exceeded`)가 기간마다 따로 나며 끝까지 돌았다 — 응답 없이 매달리는 장애면 120초 × 23번이다.
    """

    def __init__(self) -> None:
        self.n = 0


def collect_period(
    client: TursoClient,
    corps: list[tuple[str, int]],
    fiscal_year: int,
    report_code: str,
    *,
    resume: bool = False,
    streak: Streak | None = None,
) -> tuple[int, list[str]]:
    """한 회계연도·보고서 종류를 전 종목에 대해 수집한다.

    **`resume=True` 면 이미 들어온 종목은 건너뛴다.** D1 하루 쓰기 한도(10만 행) 안에서
    며칠에 걸쳐 채울 때 쓴다. 이것이 없으면 매일 전 종목을 처음부터 다시 받아 **같은 자리에서
    잘리고, 영원히 끝나지 않는다** — 재무가 따라잡기의 맨 앞으로 온 2026-09-20 에 실제로
    그렇게 될 뻔했다 (docs/infra.md 25.24).

    기본값이 False 인 이유: **정정 공시가 오면 덮어써야 한다.** 주 1회 도는 평소 실행은 전부
    다시 받아 정정을 반영하고, 따라잡기만 `--resume` 으로 이어 받는다.
    """
    by_corp = dict(corps)
    codes = list(by_corp.keys())
    warnings: list[str] = []
    stored = 0

    if resume:
        가진것 = already_collected(client, fiscal_year, report_code)
        남은것 = [code for code in codes if by_corp[code] not in 가진것]
        print(f"  {fiscal_year} {report_code}: 이미 {len(codes) - len(남은것)}종목 있음 → {len(남은것)}종목만 받습니다")
        codes = 남은것
        if not codes:
            return 0, warnings

    잇단 = streak or Streak()
    for start in range(0, len(codes), dart.MAX_CORPS_PER_CALL):
        # **부르기 전에 본다** (docs/infra.md 25.318). 예전에는 부른 뒤에야 셌다 — 이미 한도가 찼어도 한 번은 나갔다
        if _한도_찼나(client):
            warnings.append(LIMIT_REACHED)
            break
        chunk = codes[start : start + dart.MAX_CORPS_PER_CALL]
        result = dart.fetch_multi_financials(chunk, fiscal_year, report_code)
        usage = db.record_api_call(
            client, "dart_opendart", limit_value=dart.DAILY_LIMIT, warn_at_pct=80
        )
        if usage["state"] == "warn":
            log.warning(
                "DART 호출이 한도의 %d%% 를 넘었습니다 (%d/%d)",
                usage["warn_at_pct"], usage["call_count"], usage["limit_value"],
            )

        if not result.ok:
            warnings.append(f"{fiscal_year} {report_code}: {result.error}")
            if result.limit_state == "blocked":
                # DART 가 020(요청 제한)을 줬다. 문구에 "한도" 가 없어 바깥 루프가 못 알아봤다 — 같은 표지를 남긴다
                warnings.append(LIMIT_REACHED)
                break
            # **한도가 아닌 실패가 잇달면 멈춘다** (25.604, 감사). 키 사용 불가(011)·점검(800)이면 매 호출이
            # 실패하는데 전 기간을 다 불러
            # 한도만 썼다(배당 25.319·백필 25.387 은 이미 멈춘다). 같은 표지로 바깥 기간 루프도 끊는다
            잇단.n += 1
            if 잇단.n >= MAX_CONSECUTIVE_FAILURES:
                warnings.append(f"{FAILURES_STOPPED} ({잇단.n}번) — 키·점검을 확인하세요")
                break
            continue
        잇단.n = 0

        # 이 호출은 이미 나갔고 셌다. 한도에 닿았어도 **받은 것은 저장한다** — 예전에는 버렸다
        stored += _store(client, result.data, by_corp, result.source)
        if usage["state"] == "blocked":
            warnings.append(LIMIT_REACHED)
            break
        time.sleep(CALL_INTERVAL_SECONDS)

    return stored, warnings


def _store(
    client: TursoClient,
    items: list[dart.CompanyFinancials],
    by_corp: dict[str, int],
    source: str,
) -> int:
    """financials 와 financial_snapshots 양쪽에 넣는다."""
    now = db.now_iso()
    fin_rows: list[tuple] = []
    snap_rows: list[tuple] = []

    for item in items:
        stock_id = by_corp.get(item.corp_code)
        if stock_id is None:
            continue

        values = item.values
        # **단위는 통화를 따른다** (docs/infra.md 25.729, 감사) — "원" 을 박아 두어 USD 로 보고한 회사 행도 단위가
        # "원" 이었다
        단위 = "원" if item.currency == "KRW" else item.currency
        fin_rows.append(
            (
                stock_id, item.fiscal_year, item.report_code, item.period_type,
                1 if item.consolidated else 0, item.report_date, item.receipt_no,
                "K-IFRS", item.currency, 단위,
                values.get("current_assets"), values.get("noncurrent_assets"),
                values.get("total_assets"), values.get("current_liabilities"),
                values.get("noncurrent_liabilities"), values.get("total_liabilities"),
                values.get("capital_stock"), values.get("retained_earnings"),
                values.get("total_equity"), values.get("revenue"),
                values.get("operating_income"), values.get("pretax_income"),
                values.get("net_income"), values.get("comprehensive_income"),
                source, now,
            )
        )
        snap_rows.append(
            (
                stock_id, item.report_date, item.receipt_no, item.fiscal_year,
                item.report_code, 1 if item.consolidated else 0,
                # 스냅샷에도 통화·단위·회계기준·기간을 담는다 (25.729, 감사) — 백테스트는 스냅샷만 읽는데 금액
                # 열쇠뿐이라
                # 원화와 달러를 가를 수 없었다(CLAUDE.md "회계기준·통화·단위 명시"). 표 스키마는 그대로, payload 에
                # 더한다
                json.dumps(
                    {**values, "currency": item.currency, "unit": 단위, "accounting_standard": "K-IFRS",
                     "period_type": item.period_type},
                    ensure_ascii=False,
                ),
                source, now,
            )
        )

    _bulk(client, "financials", _FIN_COLS, _FIN_SQL, fin_rows)
    _bulk(client, "financial_snapshots", _SNAP_COLS, _SNAP_SQL, snap_rows)
    return len(fin_rows)


_FIN_COLS = (
    "stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
    " receipt_no, accounting_standard, currency, unit,"
    " current_assets, noncurrent_assets, total_assets, current_liabilities,"
    " noncurrent_liabilities, total_liabilities, capital_stock, retained_earnings,"
    " total_equity, revenue, operating_income, pretax_income, net_income,"
    " comprehensive_income, source, fetched_at"
)
_FIN_SQL = (
    " ON CONFLICT (stock_id, fiscal_year, report_code, consolidated) DO UPDATE SET"
    "   report_date = excluded.report_date, receipt_no = excluded.receipt_no,"
    "   total_assets = excluded.total_assets, total_equity = excluded.total_equity,"
    "   total_liabilities = excluded.total_liabilities, revenue = excluded.revenue,"
    "   operating_income = excluded.operating_income, net_income = excluded.net_income,"
    "   pretax_income = excluded.pretax_income,"
    "   comprehensive_income = excluded.comprehensive_income,"
    "   current_assets = excluded.current_assets,"
    "   noncurrent_assets = excluded.noncurrent_assets,"
    "   current_liabilities = excluded.current_liabilities,"
    "   noncurrent_liabilities = excluded.noncurrent_liabilities,"
    "   capital_stock = excluded.capital_stock,"
    "   retained_earnings = excluded.retained_earnings,"
    # 통화·단위·기준·기간·출처도 갱신한다 (25.729) — 예전에는 처음 넣은 값이 영영 남았다
    "   currency = excluded.currency, unit = excluded.unit, accounting_standard = excluded.accounting_standard,"
    "   period_type = excluded.period_type, source = excluded.source,"
    "   fetched_at = excluded.fetched_at"
)

_SNAP_COLS = (
    "stock_id, as_of_date, receipt_no, fiscal_year, report_code, consolidated,"
    " payload, source, fetched_at"
)
# 스냅샷은 덮어쓰지 않는다. 같은 접수번호가 다시 오면 그냥 둔다.
# 과거에 받은 값을 지금 값으로 바꾸면 시점 조회의 뜻이 사라진다.
_SNAP_SQL = " ON CONFLICT (stock_id, receipt_no, consolidated) DO NOTHING"


def column_count(columns: str) -> int:
    """열 목록 문자열에서 열 개수를 센다.

    손으로 세어 넘기게 두면 열을 하나 더할 때 같이 고치는 것을 잊는다.
    실제로 26개 열에 24를 넘겨 "24 values for 26 columns" 로 배치가 죽었다.
    """
    return len([c for c in columns.split(",") if c.strip()])


def _bulk(
    client: TursoClient, table: str, columns: str, conflict: str, rows: list[tuple]
) -> None:
    """여러 행을 한 문장에 넣는다. 열 개수는 목록에서 직접 센다."""
    if not rows:
        return

    param_count = column_count(columns)
    for row in rows:
        if len(row) != param_count:
            raise ValueError(
                f"{table}: 값 {len(row)}개인데 열은 {param_count}개입니다. "
                "열을 더하거나 뺄 때 값 묶음도 함께 고쳐야 합니다"
            )

    placeholder = "(" + ", ".join(["?"] * param_count) + ")"
    # SQLite 바인딩 변수 상한(32,766)에 걸리지 않게 여유를 둔다
    per_statement = max(1, 20_000 // param_count)

    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        sql = (
            f"INSERT INTO {table} ({columns}) VALUES "
            + ", ".join([placeholder] * len(chunk))
            + conflict
        )
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append((sql, args))
    client.batch(statements)


def run(
    plan: list[tuple[int, str]], *, universe_only: bool = True, resume: bool = False, only_ids: list[int] | None = None
) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market="KR", trade_date=None
        )

        warnings: list[str] = []
        # 고유번호 잇기는 3,990건을 UPDATE 한다 — D1 이 인덱스까지 세므로 하루 예산에서 적지 않다.
        # 이어 받는 중에는 이미 이어 놓았으므로 건너뛴다. 새로 상장한 종목은 주 1회 평소 실행이 잇는다
        # 몇 종목만 받을 때(참고 분석, 25.1018)도 고유번호 잇기를 건너뛴다 — 이미 이어져 있으면 4천 건 UPDATE 가 낭비다
        if (resume or only_ids) and linked_count(client) > 0:
            linked = 0
            print("고유번호는 이미 이어져 있습니다. 건너뜁니다 (쓰기 예산을 재무에 남긴다)")
        else:
            linked, w = sync_corp_codes(client)
            # 고유번호 연결 **요약은 경고가 아니다** (docs/infra.md 25.604, 감사). 경고에 넣어 문제없는 주간 실행이
            # 매번 partial("일부 성공")로 닫혀 진짜 일부 실패와 /status 에서 구별되지 않았다. 받기 실패만 경고다
            warnings += w

        corps = target_corps(client, universe_only, only_ids)
        if not corps:
            db.finish_batch_run(
                client, run_id, status="failed",
                error_text="대상 종목이 없습니다. 유니버스를 먼저 만드세요",
            )
            print("대상 종목이 없습니다. 유니버스를 먼저 만드세요")
            return 1

        print(f"대상 {len(corps)}종목, 고유번호 연결 {linked}종목")

        total = 0
        잇단 = Streak()  # 기간을 건너 이어 센다 (25.1083)
        for year, report in plan:
            stored, w = collect_period(client, corps, year, report, resume=resume, streak=잇단)
            total += stored
            warnings += w
            name = dart.REPORT_CODES.get(report, (report,))[0]
            print(f"  {year} {name}: {stored}건")
            # **한도면 모든 기간을 멈춘다** (docs/infra.md 25.318). 예전 `break` 는 보고서 루프만 끊어
            # 남은 연도마다 한 번씩 더 불렀다(매번 020)
            if LIMIT_REACHED in w or any(x.startswith(FAILURES_STOPPED) for x in w):
                break

        db.finish_batch_run(
            client, run_id,
            status="partial" if warnings else "success",
            step_log={"stored": total, "corps": len(corps), "warnings": warnings[:20]},
        )

        print(f"저장 {total}건")
        if warnings:
            print("경고")
            for warning in warnings[:8]:
                print(f"  - {warning}")
        return 0
    finally:
        client.close()


#: 분기·반기보고서의 법정 제출 기한(분기 말 + 45일, 자본시장법 160조) — 월·일. 기한이 지난 올해 보고서만 받는다.
#: 기한 전에 부르면 대부분 "자료 없음"이라 호출만 쓴다. 기한 전에 일찍 낸 회사는 기한까지 늦게 잡힌다 (25.455).
#: 기한이 토·일·공휴일이면 다음 영업일로 밀린다(민법 161조) — `deadline_of` (25.457, 교차검증)
QUARTER_DEADLINES: dict[str, tuple[int, int]] = {"11013": (5, 15), "11012": (8, 14), "11014": (11, 14)}


def deadline_of(year: int, report: str, is_business_day: Callable[[date], bool]) -> date:
    """그해 그 보고서의 실제 제출 기한. 법정 날짜가 영업일이 아니면 다음 영업일."""
    month, day = QUARTER_DEADLINES[report]
    d = date(year, month, day)
    for _ in range(10):  # 연휴가 길어도 열흘 안에 끝난다
        if is_business_day(d):
            return d
        d += timedelta(days=1)
    return d


def _krx_business_day(d: date) -> bool:
    """거래소 영업일로 공휴일을 대신한다. 달력이 그 날을 모르면(범위 밖) 주말만 본다."""
    try:
        return cal.is_session("KR", d)
    except Exception:  # noqa: BLE001 — 달력 범위 밖이면 주말 규칙으로 떨어진다(기한 판정만 쓴다)
        return d.weekday() < 5


def collection_plan(
    year: int | None, span: int, report: str | None, today: date,
    is_business_day: Callable[[date], bool] = _krx_business_day,
) -> list[tuple[int, str]]:  # fmt: skip
    """받을 (회계연도, 보고서) 목록, 받는 순서대로 (factors.md 11.4·12.1, infra 25.434·25.438·25.455·25.457).

    기본은 **올해의 분기·반기 보고서 중 실제 기한(`deadline_of`)이 지난 것**을 먼저, 그다음 **작년부터 거슬러
    `span` 해**의 `dart.REPORT_CODES` 전부(사업·반기·1분기·3분기)다. 올해 사업보고서는 12월 결산이면
    내년 3월에나 나오지만 3·6월 결산 회사 몫이 있어 7월부터 받는다(25.665).
    올해 분기를 앞에 두는 것은 분기 실적 서프라이즈(SUE)의 입력이 가장 새것이어야 하기 때문이다 — 하루 호출 한도에
    걸리면 가장 오래된 해가 밀린다. **`--year`·`--report` 를 주면 예전처럼 그 곱만 받는다** — 올해 분기를 더하는 것은
    기본 계획뿐이다(25.457, 교차검증: `--report 11012` 만 줘도 올해 반기가 끼었다).
    """
    reports = [report] if report else list(dart.REPORT_CODES)
    if year:
        return [(year, r) for r in reports]
    지난해들 = [(y, r) for y in range(today.year - 1, today.year - 1 - span, -1) for r in reports]
    if report:
        return 지난해들
    올해 = [
        (today.year, r) for r in reports
        if r in QUARTER_DEADLINES and today > deadline_of(today.year, r, is_business_day)
    ]  # fmt: skip
    # **7월부터는 올해 사업연도 사업보고서도 받는다** (docs/infra.md 25.665, 교차검증). 3월 결산은 6월 말, 6월 결산은
    # 9월 말에 낸다 — 예전에는 이듬해에야 받아, 그동안 최신이 한 해 묵은 사업보고서라 25.660 의 신선도 검사(457일)가
    # 9월 말부터 정상 회사를 "묵음" 으로 떨어뜨렸다. 12월 결산 회사는 자료가 없어 호출만 늘어난다(묶음 호출이라 적다)
    if today.month >= 7:
        올해.append((today.year, dart.ANNUAL_REPORT_CODE))
    return 올해 + 지난해들


def main() -> int:
    parser = argparse.ArgumentParser(description="재무 데이터 수집")
    parser.add_argument("--years", type=int, default=5, help="최근 몇 개 회계연도")
    parser.add_argument("--year", type=int, help="특정 연도만")
    parser.add_argument("--report", help="특정 보고서 코드만")
    parser.add_argument("--all-stocks", action="store_true", help="유니버스 밖도 포함")
    parser.add_argument(
        "--resume", action="store_true",
        help="이미 들어온 회계연도·종목은 건너뛴다. 하루 쓰기 한도 안에서 며칠에 걸쳐 채울 때 (infra 25.24)."
             " 평소 실행은 정정 공시를 반영해야 하므로 쓰지 않는다",
    )  # fmt: skip
    parser.add_argument(
        "--skip-if-ran-within", dest="skip_days", type=float,
        help="최근 이 일수 안에 성공했으면 건너뛴다 (D1 따라잡기, docs/infra.md 25.8)",
    )  # fmt: skip
    args = parser.parse_args()

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    if args.skip_days is not None:
        with TursoClient() as client:
            # partial 은 세지 않는다 — 빈 칸이 남았다. `--resume` 이라 다시 돌아도 빠진 것만 받는다 (25.320)
            if db.ran_within(client, JOB_NAME, args.skip_days, "KR", include_partial=False):
                print(f"최근 {args.skip_days:g}일 안에 돌았습니다. 건너뜁니다 (쓰기 예산을 시세에 남긴다)")
                return 0

    plan = collection_plan(args.year, args.years, args.report, datetime.now(UTC).date())

    print("받을 기간 " + ", ".join(f"{y}/{r}" for y, r in plan))
    return run(plan, universe_only=not args.all_stocks, resume=args.resume)


if __name__ == "__main__":
    sys.exit(guard(main))
