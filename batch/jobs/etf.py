"""ETF 장기 적립 판정 배치.

규칙은 docs/etf.md 8장(미국)·9장(국내), 계산은 batch/services/etf.py 에 있다.
이 파일은 받아서 넘기고 저장하는 일만 한다.

미국 흐름 (etf.md 8.1)
  1. 나스닥 심볼 디렉터리에서 ETF 목록을 받아 etfs 에 저장한다
  2. 이름으로 레버리지·인버스를 먼저 뺀다
  3. 야후 일봉을 조각으로 받아 거래대금 추정이 문턱 미만인 것을 뺀다
  4. 남은 것만 종목별로 프로필을 받는다. 가장 비싼 단계라 마지막에 둔다
  5. 판정·점수·추천 종목 겹침을 계산해 저장한다

국내 흐름 (etf.md 9.1)
  한국거래소 ETF 일별매매정보를 최근 20 거래일 + 3년 전 하루, 모두 21회 부른다.
  하루치 전체 ETF 가 한 번에 오므로 종목 수와 무관하게 호출 수가 같다.

실행
  python -m batch.jobs.etf --market US
  python -m batch.jobs.etf --market US --max-profiles 50   # 시험 삼아 적게
  python -m batch.jobs.etf --market US --only VOO,SPY,BND  # 일봉 거르기를 건너뛰고 이 종목만 (저장하지 않는다, 25.352)
  python -m batch.jobs.etf --market US --rejudge           # 받지 않고 저장된 프로필로 다시 판정
  python -m batch.jobs.etf --market KR
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import criteria, etf_accounts
from batch.services import etf as svc
from batch.sources import krx, nasdaq_symbols, yahoo_fund
from batch.sources.yfinance_src import DailyBar, chunk_count, estimate_turnover, fetch_daily_bars

log = logging.getLogger("etf")

JOB_NAME = "etf"

# 3단계에서 받을 일봉 일수. 20 거래일 평균을 내려면 달력으로 한 달이 필요하다.
BAR_LOOKBACK_DAYS = 30
TURNOVER_DAYS = 20

# 4단계 상한. 종목당 약 1.5초라 1,200개면 30분이다. workflow 제한 시간(90분) 안에
# 일봉 단계와 함께 끝나도록 잡았다. 거래대금이 큰 순으로 자르므로 잘려 나가는 것은
# 문턱을 겨우 넘긴 작은 ETF 들이다. 실제로 몇 개가 남는지는 첫 실행에서 본다 [확인필요].
MAX_PROFILES = 1200

_ETF_COLS = "symbol, country, name, exchange, yahoo_symbol, status, source, fetched_at"

_PROFILE_COLS = (
    "etf_id, as_of_date, category, family, expense_ratio, total_assets, currency, inception_date,"
    " avg_volume, prev_close, turnover_est, holdings_json, stock_position, bond_position, source, fetched_at"
)

_PICK_COLS = (
    "etf_id, as_of_date, bucket, passed, excluded_reason, score, rank_in_category, category_size,"
    " rationale_text, rationale_data, calc_version, created_at"
)


# ----------------------------------------------------------------------
# 순수 도우미 (테스트 대상)
# ----------------------------------------------------------------------


def avg_turnover_by_symbol(bars: list[DailyBar], days: int = TURNOVER_DAYS) -> dict[str, float]:
    """심볼별 최근 N 거래일 평균 거래대금 추정.

    거래일이 N 에 못 미쳐도 평균을 낸다. 여기는 **거르기 전 단계**라 너그럽게
    두고, 최종 판정은 프로필의 평균 거래량으로 다시 한다(etf.md 8.2 유동성).
    """
    by_symbol: dict[str, list[tuple[str, int]]] = {}
    for bar in bars:
        value = estimate_turnover(bar.close, bar.volume)
        if value is None:
            continue
        by_symbol.setdefault(bar.ticker, []).append((bar.date, value))

    out: dict[str, float] = {}
    for symbol, rows in by_symbol.items():
        recent = sorted(rows, reverse=True)[:days]
        if recent:
            out[symbol] = sum(v for _, v in recent) / len(recent)
    return out


def select_for_profile(
    turnover: dict[str, float], minimum: float = svc.MIN_TURNOVER_USD, limit: int = MAX_PROFILES
) -> list[str]:
    """프로필을 받을 심볼. 문턱 이상을 거래대금 큰 순으로 limit 개까지."""
    passing = [(value, symbol) for symbol, value in turnover.items() if value >= minimum]
    passing.sort(key=lambda item: (-item[0], item[1]))
    return [symbol for _, symbol in passing[:limit]]


def profile_row(etf_id: int, as_of: str, profile: yahoo_fund.FundProfile, fetched: str) -> tuple:
    holdings = [{"symbol": h.symbol, "name": h.name, "pct": h.pct} for h in profile.holdings]
    return (
        etf_id,
        as_of,
        profile.category,
        profile.family,
        profile.expense_ratio,
        profile.total_assets,
        profile.currency,
        profile.inception_date,
        profile.avg_volume,
        profile.prev_close,
        profile.turnover_est,
        json.dumps(holdings, ensure_ascii=False),
        profile.stock_position,
        profile.bond_position,
        yahoo_fund.SOURCE,
        fetched,
    )


def _taxes(client: TursoClient) -> dict | None:
    """계좌별 근거(docs/etf.md 11.1)가 인용할 세율. 행이 없거나 깨졌으면 None — 근거에 "미입력" 으로 나간다.
    깨진 행은 `get_setting` 이 로그로 말한다. 그 밖의 읽기 실패는 판정의 다른 읽기와 같이 실행을 멈춘다."""
    v = db.get_setting(client, "taxes", None)
    return v if isinstance(v, dict) else None


def pick_row(
    etf_id: int, as_of: str, ev: svc.Evaluation, overlap: dict, now: str, taxes: dict | None = None
) -> tuple:
    data = {
        "criteria": ev.criteria,
        "category": ev.category,
        "country": ev.country,
        "component_scores": ev.component_scores,
        "weights": svc.renormalized_weights(svc.AVAILABLE_BY_COUNTRY[ev.country]) if ev.passed else None,
        "overlap": overlap,
        # 계좌별 가능 여부·우선순위 (docs/etf.md 11.1, 25.966). 통과한 것만
        "accounts": (
            etf_accounts.accounts_for(ev.country, ev.bucket, satellite=False, taxes=taxes) if ev.passed else None
        ),
    }
    return (
        etf_id,
        as_of,
        ev.bucket,
        1 if ev.passed else 0,
        ev.excluded_reason,
        ev.score,
        ev.rank_in_category,
        ev.category_size,
        svc.rationale_text(ev),
        json.dumps(data, ensure_ascii=False, default=str),
        svc.CALC_VERSION,
        now,
    )


# ----------------------------------------------------------------------
# 저장
# ----------------------------------------------------------------------


def _bulk(
    client: TursoClient,
    table: str,
    columns: str,
    rows: list[tuple],
    conflict: str,
    before: list[tuple[str, list[Any]]] | None = None,
) -> int:
    """여러 행을 한 문장에. 열 개수는 목록에서 직접 센다(signals.store 와 같은 방식).

    `before` 는 같은 묶음(batch) 앞에 붙일 문장 — 같은 기준일의 옛 판정을 지우는 데 쓴다 (25.758).
    쓸 행이 없어도 `before` 는 보낸다 — 규칙이 바뀌어 범위가 0개가 된 날 옛 행이 남았다 (25.760, 교차검증)."""
    if not rows:
        if before:
            client.batch(list(before))
        return 0
    count = db.column_count(columns)
    for row in rows:
        if len(row) != count:
            raise ValueError(
                f"{table}: 값 {len(row)}개인데 열은 {count}개입니다. 열을 바꾸면 값 묶음도 함께 고쳐야 합니다"
            )
    placeholder = "(" + ", ".join(["?"] * count) + ")"
    per_statement = max(1, 20_000 // count)
    statements: list[tuple[str, list[Any]]] = list(before or [])
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append(
            (f"INSERT INTO {table} ({columns}) VALUES " + ", ".join([placeholder] * len(chunk)) + " " + conflict, args)
        )
    client.batch(statements)
    return len(rows)


def store_master(client: TursoClient, symbols: list[nasdaq_symbols.UsSymbol], fetched: str) -> dict[str, int]:
    rows = [
        (s.symbol, "US", s.name, s.exchange, s.yahoo_symbol, "active", nasdaq_symbols.SOURCE, fetched)
        for s in symbols
    ]
    _bulk(
        client, "etfs", _ETF_COLS, rows,
        "ON CONFLICT (symbol, country) DO UPDATE SET name = excluded.name, exchange = excluded.exchange,"
        " yahoo_symbol = excluded.yahoo_symbol, source = excluded.source, fetched_at = excluded.fetched_at",
    )
    rs = client.execute("SELECT yahoo_symbol, id FROM etfs WHERE country = 'US'")
    return {str(row[0]): int(row[1]) for row in rs.rows}


def load_recommended_us(client: TursoClient) -> tuple[dict[str, str], str | None]:
    """가장 최근 미국 신호의 종목. {티커: 기간}. 없으면 빈 사전."""
    # 마지막으로 계산한 날 — 그날 0건이면 옛 겹침을 보였다 (25.827, 교차검증. 리포트·스트레스·적립과 같은 잣대)
    as_of = db.last_signal_calc_date(client, "US", None)
    if not as_of:
        return {}, None
    rs = client.execute(
        "SELECT s.ticker, sg.horizon FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        " WHERE s.country = 'US' AND sg.as_of_date = ? AND s.status = 'active'"  # 추천 화면과 같은 상태 조건 (25.824)
        # 그 나라·그 기준일의 가장 새 판만 — 새 판이 걸러 낸 종목의 옛 판 행이 섞이지 않게 (infra 25.423·25.464).
        # 바깥 행을 가리키지 않는다(25.428)
        "   AND sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id"
        "     WHERE s3.country = ? AND c.as_of_date = ?)",
        [as_of, "US", as_of],
    )
    tickers: dict[str, str] = {}
    for row in rs.rows:
        tickers.setdefault(str(row[0]), str(row[1]))
    return svc.normalize_recommended(tickers), str(as_of)


# ----------------------------------------------------------------------
# 국내 도우미 (테스트 대상)
# ----------------------------------------------------------------------

_PROFILE_COLS_KR = _PROFILE_COLS + ", premium_abs_avg, days_observed, listed_3y_ago, premium_days"

KR_DAYS = 20  # 거래대금·괴리율 평균을 낼 거래일 수

_PROFILE_CONFLICT = (
    "ON CONFLICT (etf_id, as_of_date) DO UPDATE SET category = excluded.category,"
    " family = excluded.family, expense_ratio = excluded.expense_ratio,"
    " total_assets = excluded.total_assets, inception_date = excluded.inception_date,"
    " avg_volume = excluded.avg_volume, prev_close = excluded.prev_close,"
    " turnover_est = excluded.turnover_est, holdings_json = excluded.holdings_json,"
    " stock_position = excluded.stock_position, bond_position = excluded.bond_position,"
    " fetched_at = excluded.fetched_at"
)
_PROFILE_CONFLICT_KR = (
    _PROFILE_CONFLICT
    + ", premium_abs_avg = excluded.premium_abs_avg, days_observed = excluded.days_observed,"
    " listed_3y_ago = excluded.listed_3y_ago, premium_days = excluded.premium_days"
)
_PICK_CONFLICT = (
    "ON CONFLICT (etf_id, as_of_date, calc_version) DO UPDATE SET bucket = excluded.bucket,"
    " passed = excluded.passed, excluded_reason = excluded.excluded_reason, score = excluded.score,"
    " rank_in_category = excluded.rank_in_category, category_size = excluded.category_size,"
    " rationale_text = excluded.rationale_text, rationale_data = excluded.rationale_data,"
    " created_at = excluded.created_at"
)


def kr_sessions(end: date, count: int = KR_DAYS) -> tuple[list[date], date]:
    """end 이하의 최근 거래일 count 개(오래된 순)와, 3년 전 같은 무렵의 거래일.

    3년 전 날짜가 2월 29일이면 28일로 옮긴다.
    """
    import pandas as pd

    from batch.core import calendar as kcal

    cal = kcal.exchange_calendar("KR")  # 손으로 더한 휴장일 포함 (25.449)
    last = cal.date_to_session(pd.Timestamp(end), direction="previous")
    window = cal.sessions_in_range(last - pd.Timedelta(days=count * 2 + 15), last)
    recent = [ts.date() for ts in window[-count:]]

    try:
        back = end.replace(year=end.year - 3)
    except ValueError:
        back = end.replace(year=end.year - 3, day=28)
    three_years = cal.date_to_session(pd.Timestamp(back), direction="previous").date()
    return recent, three_years


def aggregate_kr(days: list[list[krx.KrxEtfRow]], old_codes: set[str]) -> list[svc.KrEtfInput]:
    """여러 날의 응답을 ETF 별 판정 입력으로 모은다.

    이름·기초지수·순자산은 **가장 최근 날**의 값을 쓴다. 거래대금과 괴리율은 있는 날의
    평균이다. 없는 날을 0 으로 채우지 않는다 — 그러면 평균이 거짓으로 작아진다.
    """
    latest: dict[str, krx.KrxEtfRow] = {}
    turnovers: dict[str, list[int]] = {}
    premiums: dict[str, list[float]] = {}

    # **가장 최근 날에 없는 ETF 는 뺀다** (docs/infra.md 25.544, 감사 재현). 상장폐지·거래정지로 마지막 며칠 응답에서
    # 사라진 ETF 가 앞 16일 값으로 통과하고 "같은 지수 1위" 까지 됐다 — 기준일은 마지막 날, 종가는 없음으로 저장됐다
    최근 = {row.isu_cd for row in days[-1] if row.isu_cd} if days else set()
    for rows in days:  # 오래된 날부터
        for row in rows:
            if not row.isu_cd:
                continue
            latest[row.isu_cd] = row
            if row.value is not None:
                turnovers.setdefault(row.isu_cd, []).append(row.value)
            premium = row.premium
            if premium is not None:
                premiums.setdefault(row.isu_cd, []).append(abs(premium))

    out: list[svc.KrEtfInput] = []
    for code, row in latest.items():
        if 최근 and code not in 최근:
            continue
        values = turnovers.get(code, [])
        prem = premiums.get(code, [])
        out.append(
            svc.KrEtfInput(
                symbol=code,
                name=row.isu_nm,
                index_name=row.index_name,
                net_assets=float(row.net_assets) if row.net_assets is not None else None,
                avg_turnover=sum(values) / len(values) if values else None,
                premium_abs_avg=sum(prem) / len(prem) if prem else None,
                days_observed=len(values),
                premium_days=len(prem),
                listed_3y_ago=code in old_codes,
            )
        )
    return out


def kr_profile_row(etf_id: int, as_of: str, inp: svc.KrEtfInput, latest_close: float | None, fetched: str) -> tuple:
    """국내 프로필. 보수·설정일·보유종목은 자료가 없어 NULL 이다(0 으로 채우지 않는다)."""
    return (
        etf_id,
        as_of,
        inp.index_name or None,  # category 열에 기초지수 이름
        None,
        None,
        inp.net_assets,
        "KRW",
        None,
        None,
        latest_close,
        inp.avg_turnover,  # 실제 거래대금 평균. 미국처럼 추정이 아니다
        None,
        None,
        None,
        krx.SOURCE,
        fetched,
        inp.premium_abs_avg,
        inp.days_observed,
        1 if inp.listed_3y_ago else 0,
        # 괴리율을 낸 날 수 — 위성 판정이 저장된 프로필에서 읽는다 (25.841, 0042)
        inp.premium_days,
    )


def profile_from_row(row: dict[str, Any]) -> yahoo_fund.FundProfile:
    """저장된 etf_profiles 한 행을 FundProfile 로 되돌린다. 재판정에 쓴다."""
    holdings = [
        yahoo_fund.Holding(str(h.get("symbol") or ""), str(h.get("name") or ""), float(h.get("pct") or 0))
        for h in json.loads(row.get("holdings_json") or "[]")
    ]
    turnover = row.get("turnover_est")
    prev_close = row.get("prev_close")
    avg_volume = row.get("avg_volume")
    if avg_volume is None and turnover is not None and prev_close:
        avg_volume = float(turnover) / float(prev_close)
    return yahoo_fund.FundProfile(
        symbol=str(row["yahoo_symbol"]),
        category=row.get("category"),
        family=row.get("family"),
        expense_ratio=row.get("expense_ratio"),
        total_assets=row.get("total_assets"),
        currency=str(row.get("currency") or "USD"),
        inception_date=row.get("inception_date"),
        avg_volume=avg_volume,
        prev_close=prev_close,
        stock_position=row.get("stock_position"),
        bond_position=row.get("bond_position"),
        holdings=holdings,
    )


# ----------------------------------------------------------------------
# 실행
# ----------------------------------------------------------------------


def _store_and_report(
    client: TursoClient,
    run_id: int,
    market: str,
    as_of: str,
    evaluations: list[svc.Evaluation],
    pick_rows: list[tuple],
    notes: list[str],
    step_log: dict[str, Any],
    partial: bool,
    store: bool = True,
) -> int:
    # **시험 실행(`--only`)은 판정을 저장하지 않는다** (docs/infra.md 25.352). 웹은 시장별 가장 새 기준일만 읽으므로
    # 몇 종목만 판정해 오늘 날짜로 넣으면 미국 탭 전체가 그 부분집합으로 바뀐다("같은 분류에서 조건을 통과한
    # 유일한 ETF"). 결과는 아래에 찍어 보여 주기만 한다
    if store:
        # **온전한 실행이면 같은 기준일·판의 옛 판정을 지우고 쓴다** (docs/infra.md 25.758, ETF 감사). upsert 만 하면
        # 이번에 판정하지 않은 ETF 의 옛 행("3개 중 1위")이 새 행("2개 중 1위")과 한 분류에 함께 떴다. 부분 실행은
        # 지우지 않는다 — 빠진 것은 판정을 못 한 것이지 범위 밖이 된 것이 아니다
        지우기 = [] if partial else [(
            "DELETE FROM etf_picks WHERE as_of_date = ? AND calc_version = ?"
            " AND etf_id IN (SELECT id FROM etfs WHERE country = ?)",
            [as_of, svc.CALC_VERSION, market],
        )]
        _bulk(client, "etf_picks", _PICK_COLS, pick_rows, _PICK_CONFLICT, before=지우기)
    elif not any("저장하지 않았습니다" in n for n in notes):
        notes.append("시험 실행(--only)이라 판정·프로필을 저장하지 않았습니다 — 화면은 그대로입니다")

    passed = [ev for ev in evaluations if ev.passed]
    by_bucket = Counter(ev.bucket for ev in passed)
    # 근거표를 못 만들어 추천에서 뺀 것 (CLAUDE.md 절대 규칙, docs/infra.md 25.174).
    # **0 이어야 정상이다.** 조용히 넘기지 않고 실행 기록과 화면 양쪽에 남긴다
    unverifiable = [ev.symbol for ev in evaluations if ev.excluded_reason == criteria.NO_CRITERIA_REASON]
    # 허용 목록을 다듬을 근거. 분류 이름이 문서와 맞는지 대조한다(etf.md 8.2·9.2).
    categories = Counter(ev.category or "(없음)" for ev in evaluations)
    reasons = Counter((ev.excluded_reason or "").split(" (")[0] for ev in evaluations if not ev.passed)

    step_log.update(
        {
            "evaluated": len(evaluations),
            "passed": len(passed),
            "by_bucket": dict(by_bucket),
            "categories_top": dict(categories.most_common(40)),
            "excluded_reasons": dict(reasons.most_common(20)),
            "calc_version": svc.CALC_VERSION,
            "notes": notes[:10],
            "unverifiable": unverifiable,
        }
    )
    db.finish_batch_run(client, run_id, status="partial" if partial else "success", step_log=step_log)

    if unverifiable:
        print(f"  주의: 근거표를 만들지 못해 {len(unverifiable)}개를 추천에서 뺐습니다 (docs/infra.md 25.174)")
    print(f"[{market}] 기준일 {as_of}, 판정 {len(evaluations):,}개, 통과 {len(passed)}개")
    print(f"  계산 버전 {svc.CALC_VERSION}")
    for bucket in svc.BUCKET_ORDER:
        if by_bucket.get(bucket):
            print(f"  {bucket}: {by_bucket[bucket]}개")
    print("분류 상위:")
    for name, count in categories.most_common(25):
        print(f"  {count:4d}  {name}")
    print("제외 사유:")
    for name, count in reasons.most_common(10):
        print(f"  {count:4d}  {name}")
    for note in notes:
        print(f"  주의: {note}")
    return 0


def run_us(max_profiles: int = MAX_PROFILES, as_of: str | None = None, only: list[str] | None = None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        as_of = as_of or datetime.now(UTC).date().isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="US", trade_date=as_of)
        notes: list[str] = []

        # 1. 목록
        master = nasdaq_symbols.fetch_symbols()
        db.record_api_call(client, "nasdaqtrader_symdir")
        if not master.ok:
            db.finish_batch_run(client, run_id, status="failed", error_text=master.error)
            print(f"ETF 목록을 받지 못했습니다: {master.error}")
            return 1
        etfs = [s for s in master.data if s.is_etf and not s.is_test]
        now = db.now_iso()
        ids = store_master(client, etfs, now)
        names = {s.yahoo_symbol: s.name for s in etfs}

        # 2. 이름으로 거르기
        named_ok = [s.yahoo_symbol for s in etfs if not svc.name_looks_leveraged(s.name)]

        # 3. 일봉으로 거르기. --only 면 건너뛴다.
        # 시험 실행마다 일봉 25조각(약 10분)을 다시 받지 않으려고 둔 옵션이다.
        bars_error = ""
        일봉_막힘 = False
        if only:
            turnover: dict[str, float] = {}
            targets = [s for s in only if s in names]
            notes.append(f"--only 로 일봉 거르기를 건너뛰었습니다 ({len(targets)}종목)")
        else:
            bars = fetch_daily_bars(named_ok, lookback_days=BAR_LOOKBACK_DAYS)
            # 일봉도 야후다. 프로필(`yahoo_fund`)과 다른 이름으로 센다 (docs/infra.md 25.200)
            db.record_api_call(client, "yfinance", count=chunk_count(len(named_ok)))
            bars_error = bars.error
            일봉_막힘 = bars.limit_state == "blocked"
            if bars.error:
                notes.append(f"일봉: {bars.error}")
            turnover = avg_turnover_by_symbol(bars.data or [])
            targets = select_for_profile(turnover, limit=max_profiles)

        # **일봉이 막혔으면 프로필을 부르지 않는다** (docs/infra.md 25.611, 교차검증). 25.608 부터 이 판은 어차피
        # 저장하지 않는데, 그대로 프로필 최대 1,200회(약 30분)를 더 불러 버렸다 — 막힌 야후를 더 두드리는 셈이다
        if 일봉_막힘 and not turnover:
            # 첫 조각부터 전부 막혔으면 **실패다** (25.614, 교차검증) — 25.611 전에는 프로필 0개로 failed·종료 1
            # 이었는데 조기 종료가 partial·종료 0 으로 바꿔 워크플로가 초록이 됐다
            db.finish_batch_run(
                client, run_id, status="failed", error_text="; ".join(notes) or "일봉을 하나도 받지 못했습니다"
            )
            print("일봉을 하나도 받지 못했습니다 (야후 차단)")
            return 1
        if 일봉_막힘:
            notes.insert(0, "야후 차단으로 일부만 받아 이번 판정을 저장하지 않았습니다 — 화면은 지난 판정 그대로입니다")
            return _store_and_report(
                client, run_id, "US", as_of, [], [], notes,
                {"etfs_listed": len(etfs), "after_name_filter": len(named_ok), "with_turnover": len(turnover)},
                True, store=False,
            )  # fmt: skip

        # 4. 프로필
        profiles, failures, blocked = yahoo_fund.fetch_profiles(targets)
        db.record_api_call(client, yahoo_fund.SOURCE, count=len(targets))
        if failures:
            notes.append(f"프로필 실패 {len(failures)}개 (예: {'; '.join(failures[:3])})")
        if blocked:
            notes.append("야후 차단으로 프로필 수집을 중간에 멈췄습니다")
        if not profiles:
            db.finish_batch_run(client, run_id, status="failed", error_text="; ".join(notes) or "프로필 0개")
            print("프로필을 하나도 받지 못했습니다")
            for note in notes:
                print(f"  {note}")
            return 1

        profile_rows = [
            profile_row(ids[symbol], as_of, profile, now) for symbol, profile in profiles.items() if symbol in ids
        ]
        # 프로필도 저장하지 않는다 — 재판정(`--rejudge`)이 가장 새 프로필 날짜를 읽어
        # 부분집합으로 다시 판정한다 (25.352)
        # 차단으로 일부만 받은 날도 저장하지 않는다 — 재판정이 그 부분집합을 가장 새 프로필로 읽는다 (25.580)
        # **일봉이 막혀 일부 조각만 받았거나, 프로필 수를 줄인 시험 실행도 부분집합이다** (docs/infra.md 25.608, 감사).
        # 25.580 은 프로필 단계 차단만 봤다 — 일봉이 막히면 받은 조각의 거래대금으로만 대상을 골랐고,
        # `--max-profiles N` 이면 앞 N개만 받았다. 그 부분집합이 이달의 가장 새 판정이 됐고 위성도 그것을 읽었다
        # 실제로 잘렸을 때만 — 문턱을 넘은 ETF 가 N개보다 적으면 줄인 입력이 아무것도 자르지 않았다 (25.611, 교차검증)
        줄인_시험 = max_profiles < MAX_PROFILES and len(targets) >= max_profiles
        부분판 = blocked or 일봉_막힘 or 줄인_시험
        if not only and not 부분판:
            _bulk(client, "etf_profiles", _PROFILE_COLS, profile_rows, _PROFILE_CONFLICT)

        # 5. 판정
        evaluations = [
            svc.evaluate(symbol, names.get(symbol, symbol), profiles[symbol], date.fromisoformat(as_of), now[:10])
            for symbol in targets
            if symbol in profiles
        ]
        pick_rows = _judge_us(client, ids, as_of, evaluations, now)
        step_log: dict[str, Any] = {
            "etfs_listed": len(etfs),
            "after_name_filter": len(named_ok),
            # 이름으로 뺀 것은 판정·저장을 안 해 "왜 TQQQ 가 없나" 에 답이 없었다 — 기호를 남긴다 (25.718, 감사).
            # 판정 행을 만들면 D1 쓰기가 수백 행 늘어 실행 기록에만 둔다
            # **자르지 않는다** (25.722, 교차검증) — 알파벳순 300개로 자르자 TQQQ·SQQQ 가 잘려 "왜 없나" 에
            # 답하지 못했다. 수백 개 × 약 9바이트라 실행 기록 한 행에 충분히 들어간다(D1 행 한도 2MB)
            "name_excluded": sorted(s.yahoo_symbol for s in etfs if svc.name_looks_leveraged(s.name)),
            "with_turnover": len(turnover),
            "profile_targets": len(targets),
            "profiles": len(profiles),
        }
        print(f"ETF 목록 {len(etfs):,}개 → 이름 거르기 {len(named_ok):,} → 거래대금 문턱 통과 {len(targets):,}")
        partial = bool(부분판 or failures or bars_error or only)
        # **야후가 중간에 막으면 판정을 저장하지 않는다** (25.580, 감사).
        # 받은 일부만으로 분류 안 순위를 매겨 "N개 중 1위"·"유일한 ETF" 가 되고,
        # 못 받은 ETF 는 탈락 목록에도 없이 사라진 판이 한 달 동안 미국 탭을 덮었다
        if 부분판 and not only:
            까닭 = ("야후 차단으로 일부만 받아" if blocked or 일봉_막힘
                   else f"프로필을 {max_profiles}개로 줄인 시험 실행이라")  # fmt: skip
            notes.insert(0, f"{까닭} 이번 판정을 저장하지 않았습니다 — 화면은 지난 판정 그대로입니다")
        return _store_and_report(
            client, run_id, "US", as_of, evaluations, pick_rows, notes, step_log, partial,
            store=not only and not 부분판,
        )  # fmt: skip
    finally:
        client.close()


def _judge_us(
    client: TursoClient, ids: dict[str, int], as_of: str, evaluations: list[svc.Evaluation], now: str
) -> list[tuple]:
    svc.score_within_categories(evaluations)
    recommended, signals_as_of = load_recommended_us(client)
    taxes = _taxes(client)
    return [
        pick_row(ids[ev.symbol], as_of, ev, svc.overlap(ev.profile, recommended, signals_as_of), now, taxes)
        for ev in evaluations
        if ev.symbol in ids
    ]


def rejudge_us() -> int:
    """받지 않고, 가장 최근에 저장한 미국 프로필로 다시 판정한다.

    규칙(허용 목록·문턱)만 바꿨을 때 40분짜리 수집을 다시 돌리지 않으려고 둔다.
    기준일은 프로필을 받은 날이다. 판정은 CALC_VERSION 으로 구분돼 옛 판정을 덮지 않는다.
    """
    client = TursoClient()
    try:
        db.apply_migrations(client)
        as_of = client.execute(
            "SELECT MAX(pf.as_of_date) FROM etf_profiles pf JOIN etfs e ON e.id = pf.etf_id WHERE e.country = 'US'"
        ).scalar()
        if not as_of:
            print("저장된 미국 프로필이 없습니다. --rejudge 없이 먼저 수집하세요")
            return 1
        as_of = str(as_of)
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="US", trade_date=as_of)
        rows = client.execute(
            "SELECT e.yahoo_symbol, e.name AS etf_name, pf.* FROM etf_profiles pf JOIN etfs e ON e.id = pf.etf_id"
            " WHERE e.country = 'US' AND pf.as_of_date = ?",
            [as_of],
        ).dicts()
        ids = {str(r["yahoo_symbol"]): int(r["etf_id"]) for r in rows}
        now = db.now_iso()
        as_of_day = date.fromisoformat(as_of)
        evaluations = [
            svc.evaluate(
                str(r["yahoo_symbol"]), str(r["etf_name"]), profile_from_row(r), as_of_day, str(r["fetched_at"])[:10]
            )
            for r in rows
        ]
        pick_rows = _judge_us(client, ids, as_of, evaluations, now)
        notes = [f"재판정: {as_of} 에 받은 프로필 {len(rows)}개를 다시 판정했습니다 (새로 받지 않음)"]
        return _store_and_report(client, run_id, "US", as_of, evaluations, pick_rows, notes, {"rejudge": True}, False)
    finally:
        client.close()


def run_kr(as_of: str | None = None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        end = date.fromisoformat(as_of) if as_of else datetime.now(UTC).date() - timedelta(days=1)
        recent, three_years = kr_sessions(end)
        notes: list[str] = []

        # **받기 전에 기록을 연다** (2026-09-22, docs/infra.md 25.114).
        # 25.95 에서 "한 날도 못 받은" 경우만 기록을 남기게 고쳤는데, 받는 도중에 **예외로**
        # 죽으면(응답 모양이 바뀌거나 D1 한도에 걸리거나) 여전히 행이 하나도 안 남았다.
        # 그러면 `/status` 와 무응답 감시가 "예약이 안 불렸다" 와 **똑같이** 본다 — 월 1회
        # 작업이라 한 달 뒤에나 안다. 미국 경로는 처음부터 이렇게 하고 있었다.
        # 실제 기준일은 받아 봐야 알므로 일단 **겨눈 날**로 열고 나중에 고친다
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="KR", trade_date=end.isoformat())

        days: list[list[krx.KrxEtfRow]] = []
        latest_day = ""
        for session in recent:
            result = krx.fetch_etf_daily(session.strftime("%Y%m%d"))
            db.record_api_call(
                client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80
            )
            if not result.ok:
                notes.append(f"{session} 실패: {result.error}")
                if result.limit_state == "blocked":
                    break
                continue
            if not result.data:
                # 휴장일이 아닌데 비었으면 아직 집계 전이다(data-sources 2절). 그날만 뺀다
                notes.append(f"{session} 응답이 비었습니다")
                continue
            days.append(result.data)
            latest_day = session.isoformat()

        if not days:
            # **실패도 기록한다** (2026-09-21, docs/infra.md 25.95). 기록은 위에서 이미 열었다
            db.finish_batch_run(
                client, run_id, status="failed",
                error_text="; ".join(notes) or "ETF 일별매매정보를 한 날도 받지 못했습니다",
            )  # fmt: skip
            print("ETF 일별매매정보를 한 날도 받지 못했습니다")
            for note in notes:
                print(f"  {note}")
            return 1

        old = krx.fetch_etf_daily(three_years.strftime("%Y%m%d"))
        db.record_api_call(client, "krx_openapi", count=old.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80)
        if not old.ok or not old.data:
            # 3년 전 목록이 없으면 운용 이력을 판정할 수 없다. 통과시키지 않는다
            notes.append(f"3년 전({three_years}) 목록을 받지 못해 운용 이력을 판정할 수 없습니다")
        old_codes = {row.isu_cd for row in (old.data or [])}

        # 받아 봤으니 이제 진짜 기준일을 안다
        db.set_run_trade_date(client, run_id, latest_day)
        now = db.now_iso()
        inputs = aggregate_kr(days, old_codes)
        latest_rows = {row.isu_cd: row for row in days[-1]}

        # **믿을 수 있는 판인가** (docs/infra.md 25.580·25.583).
        # 3년 전 목록이 없거나 받은 날이 `MIN_KR_DAYS` 미만이면 모든 ETF 가
        # "운용 3년 미만"·"확인할 수 없는 값: 거래대금" 으로 떨어진다. 그런 판은 판정도 **프로필도** 저장하지 않는다.
        # 프로필을 저장하면 같은 워크플로의 위성 판정(`etf_satellite`)이 가장 새 프로필을 읽어
        # 국내 위성 전부를 떨어뜨리고 `success` 로 닫았다 (25.583, 교차검증)
        믿을_판 = bool(old_codes) and len(days) >= svc.MIN_KR_DAYS
        ids = store_master_kr(client, inputs, now)
        profile_rows = [
            kr_profile_row(
                ids[inp.symbol], latest_day, inp,
                latest_rows[inp.symbol].close if inp.symbol in latest_rows else None, now,
            )
            for inp in inputs
            if inp.symbol in ids
        ]
        if 믿을_판:
            _bulk(client, "etf_profiles", _PROFILE_COLS_KR, profile_rows, _PROFILE_CONFLICT_KR)

        evaluations = [svc.evaluate_kr(inp, latest_day) for inp in inputs]
        svc.score_within_categories(evaluations)
        taxes = _taxes(client)
        pick_rows = [
            pick_row(ids[ev.symbol], latest_day, ev, svc.overlap_kr(), now, taxes)
            for ev in evaluations
            if ev.symbol in ids
        ]
        step_log: dict[str, Any] = {
            "sessions": [d.isoformat() for d in recent],
            "days_received": len(days),
            "three_years": three_years.isoformat(),
        }
        partial = len(days) < svc.MIN_KR_DAYS or not old_codes
        # **3년 전 목록이 없으면 판정을 저장하지 않는다** (docs/infra.md 25.580, 감사).
        # 모든 국내 ETF 가 "운용 3년 미만" 으로 떨어진 판이 가장 새 기준일이 되어,
        # 다음 달까지 국내 탭이 "통과 0개" 였다(KODEX 200 이 "운용 3년 미만")
        # 저장하지 않았다는 말은 **맨 앞에** 둔다 — 화면은 앞 몇 개만 싣는다 (25.583)
        if not 믿을_판:
            notes.insert(
                0,
                "입력이 모자라(3년 전 목록 또는 거래일 수) 이번 판정·프로필을 저장하지 않았습니다"
                " — 화면은 지난 판정 그대로입니다",
            )  # fmt: skip
        return _store_and_report(
            client, run_id, "KR", latest_day, evaluations, pick_rows, notes, step_log, partial, store=믿을_판
        )  # fmt: skip
    finally:
        client.close()


def store_master_kr(client: TursoClient, inputs: list[svc.KrEtfInput], fetched: str) -> dict[str, int]:
    rows = [(inp.symbol, "KR", inp.name, "KRX", f"{inp.symbol}.KS", "active", krx.SOURCE, fetched) for inp in inputs]
    _bulk(
        client, "etfs", _ETF_COLS, rows,
        "ON CONFLICT (symbol, country) DO UPDATE SET name = excluded.name, source = excluded.source,"
        " fetched_at = excluded.fetched_at",
    )
    rs = client.execute("SELECT symbol, id FROM etfs WHERE country = 'KR'")
    return {str(row[0]): int(row[1]) for row in rs.rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="ETF 장기 적립 판정")
    parser.add_argument("--market", default="US", choices=["US", "KR", "us", "kr"])
    parser.add_argument("--max-profiles", type=int, default=MAX_PROFILES, help="미국: 프로필을 받을 최대 개수")
    parser.add_argument("--as-of", dest="as_of", help="기준일 YYYY-MM-DD")
    parser.add_argument("--only", help="미국: 쉼표로 구분한 심볼만 판정 (일봉 거르기 생략, 시험용)")
    parser.add_argument("--rejudge", action="store_true", help="미국: 받지 않고 저장된 프로필로 다시 판정")
    args = parser.parse_args()
    only = [s.strip().upper() for s in args.only.split(",") if s.strip()] if args.only else None

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    if args.market.upper() == "KR":
        return run_kr(args.as_of)
    if args.rejudge:
        return rejudge_us()
    return run_us(args.max_profiles, args.as_of, only)


if __name__ == "__main__":
    sys.exit(guard(main))
