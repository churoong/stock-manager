"""미국 주식수·시가총액·상장일 갱신 (docs/factors.md 9.3, docs/data-sources.md 14절).

미국 유니버스가 비는 이유 두 가지를 메운다.
  - market_cap 이 없어 시총 하한에서 전부 탈락
  - listed_date 가 없어 "상장 1년 이상" 판정 불가

시가총액 = SEC 주식수 × DB 최신 종가. 야후 종목별 시총 호출(월 230~314분)을 쓰지 않는다.
상장일 = 가격 이력의 관측 첫 거래일. 실제 상장일이 아니므로 5년 백필 뒤에 돌려야 뜻이 있다.

실행
  python -m batch.jobs.us_shares                        # 거래대금 후보만 (기본)
  python -m batch.jobs.us_shares --limit 20             # 시험
  python -m batch.jobs.us_shares --min-turnover 0       # 전 종목
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.backfill_us import DEFAULT_MIN_TURNOVER, candidate_symbols
from batch.jobs.daily import _us_symbol_ids
from batch.services import universe as uni
from batch.sources import sec_edgar

log = logging.getLogger("us_shares")

JOB_NAME = "us_shares"
#: 후보 문턱 여유 — 후보 창(30 달력일 평균)과 유니버스 창(20 거래일)이 달라 문턱 근처가 갈린다 (25.530).
#: 30 달력일은 약 21~22 거래일이라 창 차이는 거래일 1~2일치(약 5%)다(25.537 교차검증 — 처음 적은 "20/30" 은 달력일과
#: 거래일을 섞은 틀린 근거였다). 절반은 넉넉한 여유다 — 넓힌 만큼 SEC 호출만 는다
CANDIDATE_MARGIN = 0.5
BATCH_STATEMENTS = 200

# 종가와 날짜는 같은 행에서 온다. 백필 뒤 prices 는 (stock_id, date) 인덱스로 종목별 조회가 싸다.
_MARKET_CAP_SQL = (
    "UPDATE stocks SET listed_shares = ?,"
    " market_cap = ? * (SELECT close FROM prices WHERE stock_id = ? AND close IS NOT NULL ORDER BY date DESC LIMIT 1),"
    " market_cap_date = (SELECT MAX(date) FROM prices WHERE stock_id = ? AND close IS NOT NULL)"
    " WHERE id = ?"
)
# **앞으로만 당긴다. 비우지 않는다** (docs/infra.md 25.166).
#
# 예전에는 `SET listed_date = (SELECT MIN(date) …)` 하나였다. 관측 첫 거래일은 실제
# 상장일의 **상한**일 뿐이라(그날 값이 있었으니 그날 이전에 상장했다) 더 이른 값을
# 만나면 당기는 것이 맞지만, **늦추거나 비우는 것은 언제나 틀리다.**
#   - 가격이 한 행도 없으면 `MIN` 이 NULL 이라 멀쩡하던 값을 지웠다
#   - 이력이 얕으면(복구 직후 등) 모든 종목이 "며칠 전 상장" 이 됐다
_LISTED_DATE_SQL = (
    "UPDATE stocks SET listed_date ="
    "  MIN(COALESCE(listed_date, '9999-12-31'), (SELECT MIN(date) FROM prices WHERE stock_id = ?))"
    " WHERE id = ? AND (SELECT MIN(date) FROM prices WHERE stock_id = ?) IS NOT NULL"
)

#: 미국 가격 이력이 이보다 얕으면 `listed_date` 를 **아예 손대지 않는다**.
#: 정의처는 `services/universe.DEFAULT_MIN_LISTED_DAYS` 다 — 유니버스가 "상장 1년 미만" 을
#: 거르는 바로 그 값이라, 그보다 얕은 이력으로 첫 관측일을 적으면 **전 종목이 그 사유로
#: 빠진다.** 설명문에 "5년 백필 뒤에 돌려야 뜻이 있다" 고 적어 두었지만 **사람만 읽었다**
MIN_HISTORY_DAYS = uni.DEFAULT_MIN_LISTED_DAYS


def history_span_days(client: TursoClient, today: date) -> int | None:
    """미국 가격 이력이 며칠치인가. 한 행도 없으면 `None`."""
    rs = client.execute(
        "SELECT MIN(p.date) FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = 'US'"
    )
    값 = rs.scalar()
    if not 값:
        return None
    try:
        return (today - date.fromisoformat(str(값)[:10])).days
    except ValueError:
        return None


def may_set_listed_date(span_days: int | None, min_days: int = MIN_HISTORY_DAYS) -> bool:
    """이력이 그만큼 깊을 때만 첫 관측일을 적는다. 모르면 적지 않는다."""
    return span_days is not None and span_days >= min_days


def plan_statements(
    ids: dict[str, int],
    ciks: dict[str, str],
    shares: dict[str, sec_edgar.Shares],
    set_listed_date: bool = True,
    split_since: dict[int, str] | None = None,
    split_skipped: list[str] | None = None,
) -> tuple[list[tuple[str, list[Any]]], list[str]]:
    """심볼별 UPDATE 문. 주식수가 없는 심볼은 상장일만 갱신하고 이름을 돌려준다.

    `set_listed_date` 가 거짓이면 상장일은 건드리지 않는다 (docs/infra.md 25.166).

    **주식수 기준일 뒤에 분할·병합이 감지된 종목은 시총을 다시 내지 않는다** (docs/infra.md 25.524, 감사).
    SEC 주식수는 마지막 10-Q 의 값(분할 전)이고 야후 종가는 분할 뒤 값이라, 1:10 병합이면 시총이 10배로 부풀어
    하한 근처 부실 종목이 편입됐다(정분할이면 반대로 빠진다). 다음 정기보고서까지 옛 시총을 그대로 둔다 — 날짜가
    묵으면 유니버스가 묵은 시총을 따로 말한다(25.362).
    `split_since` 는 종목 → 분할 감지일(`adjust_drift.SPLIT_LOG_KEY` 기록, 25.535)
    """
    statements: list[tuple[str, list[Any]]] = []
    missing: list[str] = []
    split_since = split_since or {}
    for symbol, stock_id in sorted(ids.items()):
        if set_listed_date:
            statements.append((_LISTED_DATE_SQL, [stock_id, stock_id, stock_id]))
        found = shares.get(ciks.get(symbol, ""))
        if found is None:
            missing.append(symbol)
            continue
        감지 = split_since.get(stock_id)
        if 감지 and found.as_of and 감지[:10] > found.as_of[:10]:
            if split_skipped is not None:
                split_skipped.append(symbol)
            continue
        statements.append((_MARKET_CAP_SQL, [found.value, found.value, stock_id, stock_id, stock_id]))
    return statements, missing


def split_detections(client: TursoClient) -> dict[int, str]:
    """종목 → 마지막 분할 감지 시각 (`adjust_drift.SPLIT_LOG_KEY`, docs/infra.md 25.535).

    25.524 는 재수집 대기열의 표시를 읽었는데, 그 표시는 재수집 순서용이라 배당 감지와 뒤엉켜 새 분할을 놓쳤다.
    """
    try:
        기록 = db.get_setting(client, "us_split_detections", {}) or {}
    except Exception as exc:  # noqa: BLE001 — 표가 없으면 분할을 모른다(예전과 같다)
        if not db.표가_없나(exc):
            log.warning("분할 감지 기록을 읽지 못했습니다: %s", exc)
        return {}
    out: dict[int, str] = {}
    for k, v in 기록.items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out


def concept_counts(shares: dict[str, sec_edgar.Shares]) -> dict[str, int]:
    """어느 개념에서 주식수를 얻었는지. 가중평균 비중이 크면 시총이 시점 값에서 멀어진다."""
    out: dict[str, int] = {}
    for found in shares.values():
        out[found.concept] = out.get(found.concept, 0) + 1
    return out


def run(min_turnover: float | None, limit: int | None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        ids = _us_symbol_ids(client)
        if min_turnover:
            # **문턱의 절반으로 넓혀 고른다** (docs/infra.md 25.530, 감사 재현). 후보는 "최근 30 달력일 행의 평균" 이고
            # 유니버스는 "시장의 최근 20 거래일" 이라, 거래대금이 막 오른 종목(최근 20일 520만달러, 그 전 100만달러)은
            # 유니버스 문턱은 넘는데 후보에서 빠져 시총·상장일이 채워지지 않아 '데이터없음' 이 됐다.
            # 넓힌 만큼 SEC 호출이 늘지만 한도(초당 10회)만 있고 하루 한도는 없다
            keep = candidate_symbols(client, min_turnover * CANDIDATE_MARGIN)
            ids = {s: i for s, i in ids.items() if s in keep}
        if limit:
            ids = dict(sorted(ids.items())[:limit])

        trade_date = cal.previous_session("US").isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="US", trade_date=trade_date)

        sec = sec_edgar.SecClient()
        ticker_map = sec.ticker_map()
        ciks = {s: ticker_map[s] for s in ids if s in ticker_map}
        # 클래스주 여러 개가 한 CIK 를 쓴다(GOOGL·GOOG). 한 번만 부른다.
        unique_ciks = sorted(set(ciks.values()))
        log.info("대상 %d종목, SEC 매핑 %d, CIK %d", len(ids), len(ciks), len(unique_ciks))

        result = sec_edgar.fetch_shares(sec, unique_ciks)
        shares: dict[str, sec_edgar.Shares] = result.data or {}
        db.record_api_call(client, "sec_edgar", count=sec.calls)

        span = history_span_days(client, date.fromisoformat(trade_date))
        상장일적기 = may_set_listed_date(span)
        if not 상장일적기:
            log.warning(
                "미국 가격 이력이 %s일치라 상장일을 손대지 않습니다 (필요 %d일)", span, MIN_HISTORY_DAYS
            )
        분할_건너뜀: list[str] = []
        statements, missing = plan_statements(
            ids, ciks, shares, set_listed_date=상장일적기,
            split_since=split_detections(client), split_skipped=분할_건너뜀,
        )
        for start in range(0, len(statements), BATCH_STATEMENTS):
            client.batch(statements[start : start + BATCH_STATEMENTS])

        with_cap = len(ids) - len(missing)
        step: dict[str, Any] = {
            "targets": len(ids),
            "mapped": len(ciks),
            "unmapped": sorted(set(ids) - set(ciks))[:30],
            "ciks": len(unique_ciks),
            "shares_found": len(shares),
            "market_cap_updated": with_cap - len(분할_건너뜀),
            # 주식수 기준일 뒤 분할이 감지돼 시총을 다시 내지 않은 종목 (25.524)
            **({"split_skipped": 분할_건너뜀[:30]} if 분할_건너뜀 else {}),
            "missing_sample": missing[:30],
            "sec_calls": sec.calls,
            "by_concept": concept_counts(shares),
            "blocked": result.limit_state == "blocked",
            "error": result.error,
            # 상장일을 왜 안 적었는지 남긴다. 조용히 넘어가면 "왜 유니버스가 비지" 를 못 푼다
            "history_span_days": span,
            "listed_date_updated": 상장일적기,
        }
        # 못 받은 회사가 있으면(`warn`) partial — 옛 시총은 그대로 두지만 드러낸다 (25.603)
        status = "failed" if not shares else ("partial" if result.limit_state in ("blocked", "warn") else "success")
        db.finish_batch_run(client, run_id, status=status, step_log=step)

        print(f"대상 {len(ids)}종목 / SEC 매핑 {len(ciks)} / 주식수 {len(shares)} CIK / 시총 갱신 {with_cap}종목")
        print(f"개념별 {concept_counts(shares)}")
        print(f"SEC 호출 {sec.calls}회, 주식수 없음 {len(missing)}종목 (예: {', '.join(missing[:10])})")
        if not 상장일적기:
            print(
                f"  주의: 미국 가격 이력이 {span}일치라 상장일을 적지 않았습니다"
                f" (필요 {MIN_HISTORY_DAYS}일). 얕은 이력으로 적으면 전 종목이"
                " '상장 1년 미만' 으로 유니버스에서 빠집니다 — 먼저 백필을 돌리세요"
            )
        if result.error:
            print(f"  주의: {result.error}")
        return 0 if status != "failed" else 1
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="미국 주식수·시가총액·상장일 갱신")
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
