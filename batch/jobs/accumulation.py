"""장기 적립 종목 판정 배치 (국내 v1·미국 v1, docs/accumulation.md).

읽기만 해서 계산한다. 새로 받는 것이 없다(재무·배당·유니버스는 각자 배치가 채운다).

실행
  python -m batch.jobs.accumulation              # 국내
  python -m batch.jobs.accumulation --market US  # 미국
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import accumulation as acc
from batch.sources import dart

log = logging.getLogger("accumulation")

JOB_NAME = "accumulation"
#: 정의처는 `batch/sources/dart.ANNUAL_REPORT_CODE` 하나다 (docs/infra.md 25.143)
ANNUAL = dart.ANNUAL_REPORT_CODE

_COLS = (
    "stock_id, as_of_date, fiscal_year_to, passed, first_failed_gate, excluded_reason, score, rank_in_group,"
    " group_size, long_signal_on, thresholds_json, rationale_text, rationale_data, calc_version, created_at"
)
_CONFLICT = (
    "ON CONFLICT (stock_id, as_of_date, calc_version) DO UPDATE SET fiscal_year_to = excluded.fiscal_year_to,"
    " passed = excluded.passed, first_failed_gate = excluded.first_failed_gate,"
    " excluded_reason = excluded.excluded_reason, score = excluded.score, rank_in_group = excluded.rank_in_group,"
    " group_size = excluded.group_size, long_signal_on = excluded.long_signal_on,"
    " thresholds_json = excluded.thresholds_json, rationale_text = excluded.rationale_text,"
    " rationale_data = excluded.rationale_data, created_at = excluded.created_at"
)


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def 기준_사업연도(client: TursoClient, country: str) -> int | None:
    """가장 많은 회사의 최신 사업연도(최빈값, 같으면 큰 쪽).

    국내도 예전에는 `MAX(fiscal_year)` 였다 (docs/infra.md 25.632, 감사). 결산월이 12월이 아닌 회사
    (3·6월 결산) 하나가 새 사업연도 보고서를 내면 기준 연도가 한 해 올라가, 12월 결산 대다수가 그해
    사업보고서가 없어 5개년(G3)에서 몇 달 동안 한꺼번에 떨어졌다. 미국과 같은 최빈값으로 맞춘다.
    """
    value = client.execute(
        "SELECT mx FROM (SELECT MAX(f.fiscal_year) AS mx FROM financials f JOIN stocks s ON s.id = f.stock_id"
        "   WHERE s.country = ? AND f.report_code = ? AND f.consolidated = 1 GROUP BY f.stock_id)"
        " GROUP BY mx ORDER BY COUNT(*) DESC, mx DESC LIMIT 1",
        [country, ANNUAL],
    ).scalar()
    return int(value) if value else None


def load_inputs(client: TursoClient, country: str = "KR") -> tuple[list[acc.StockInput], int | None]:
    """유니버스 편입 종목과 재무·배당. 두 번째 값은 기준 회계연도.

    두 나라 모두 **가장 많은 회사의 최신 연도**(최빈값)다 — 국내도 25.632 전에는 최신 연도(MAX)였다.
    """
    universe = client.execute(
        "SELECT s.id, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.market, s.listed_date,"
        "       u.market_cap, u.avg_turnover_20d, u.snapshot_date"
        " FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
        " WHERE s.country = ? AND u.included = 1"
        f"   AND u.snapshot_date = {db.latest_snapshot_sql()}",
        [country, country],
    ).dicts()
    inputs = {
        int(r["id"]): acc.StockInput(
            stock_id=int(r["id"]),
            ticker=str(r["ticker"]),
            name=acc.display_name(str(r["name"])),
            market=str(r["market"]),
            listed_date=r["listed_date"],
            market_cap=_f(r["market_cap"]),
            cap_rank=None,
            snapshot_date=str(r["snapshot_date"]) if r.get("snapshot_date") else None,
        )
        for r in universe
    }
    turnover = {int(r["id"]): _f(r["avg_turnover_20d"]) for r in universe}
    if not inputs:
        return [], None

    latest_fy = 기준_사업연도(client, country)
    if not latest_fy:
        return list(inputs.values()), None
    # 미국은 종목마다 창이 달라(years_for) 10년 이력 판정(G2)까지 넉넉히 읽는다
    first_year = int(latest_fy) - (acc.MIN_LISTED_YEARS if country == "US" else acc.YEARS - 1)
    last_year = int(latest_fy) + (1 if country == "US" else 0)

    latest_receipt: dict[int, tuple[int, str]] = {}
    for r in client.execute(
        "SELECT f.stock_id, f.fiscal_year, f.report_date, f.revenue, f.operating_income, f.net_income,"
        "       f.total_equity, f.total_liabilities, f.retained_earnings, f.pretax_income, f.receipt_no"
        " FROM financials f JOIN stocks s ON s.id = f.stock_id"
        " WHERE s.country = ? AND f.report_code = ? AND f.consolidated = 1 AND f.fiscal_year BETWEEN ? AND ?",
        [country, ANNUAL, first_year, last_year],
    ).dicts():
        target = inputs.get(int(r["stock_id"]))
        if target is None:
            continue
        target.filing_years.add(int(r["fiscal_year"]))
        year = int(r["fiscal_year"])
        if year >= latest_receipt.get(target.stock_id, (0, ""))[0]:
            latest_receipt[target.stock_id] = (year, str(r["receipt_no"]))
        target.years[int(r["fiscal_year"])] = acc.FinYear(
            fiscal_year=int(r["fiscal_year"]),
            report_date=r["report_date"],
            revenue=_f(r["revenue"]),
            operating_income=_f(r["operating_income"]),
            net_income=_f(r["net_income"]),
            total_equity=_f(r["total_equity"]),
            total_liabilities=_f(r["total_liabilities"]),
            retained_earnings=_f(r["retained_earnings"]),
            pretax_income=_f(r["pretax_income"]),
        )

    # 같은 사업연도를 여러 보고서가 싣는다. 가장 나중 보고서의 값을 쓴다(오늘 시점 판정이다).
    for r in client.execute(
        "SELECT d.stock_id, d.fiscal_year, d.as_of_date, d.cash_dividend_total, d.dps_common, d.payout_ratio"
        " FROM stock_dividends d"
        " JOIN (SELECT stock_id, fiscal_year, MAX(report_year) AS ry FROM stock_dividends"
        "       WHERE fiscal_year BETWEEN ? AND ? GROUP BY stock_id, fiscal_year) m"
        "   ON m.stock_id = d.stock_id AND m.fiscal_year = d.fiscal_year AND m.ry = d.report_year",
        [first_year, last_year],
    ).dicts():
        target = inputs.get(int(r["stock_id"]))
        if target is None:
            continue
        target.dividends[int(r["fiscal_year"])] = acc.DivYear(
            fiscal_year=int(r["fiscal_year"]),
            as_of_date=r["as_of_date"],
            cash_dividend_total=_f(r["cash_dividend_total"]),
            dps_common=_f(r["dps_common"]),
            payout_ratio=_f(r["payout_ratio"]),
        )
    kept = list(inputs.values())
    if country == "US":
        # 주식 종류 중복을 먼저 빼고 시총 순위를 매긴다. 한 회사가 순위 두 자리를 차지하지 않게
        company_of = {sid: receipt for sid, (_y, receipt) in latest_receipt.items()}
        kept, dropped = acc.dedupe_share_classes(kept, company_of, turnover)
        if dropped:
            log.info("주식 종류 중복 %d개 뺌: %s", len(dropped), sorted(dropped.values())[:10])
    ranked = sorted(kept, key=lambda i: -(i.market_cap or 0))
    for position, inp in enumerate(ranked, start=1):
        if inp.market_cap:
            inp.cap_rank = position
    return kept, int(latest_fy)


def long_signal_ids(client: TursoClient, country: str = "KR") -> set[int]:
    # **마지막으로 계산한 날**로 — 그날 걸린 신호가 0건이면 MAX 는 더 옛날 날짜라, 한 달 동안 저장되는 "같은 날 장기
    # 신호" 표시가
    # 옛 신호를 지금 것처럼 말했다 (25.824, 감사. 리포트·스트레스와 같은 잣대 `db.last_signal_calc_date`, 25.337·25.359)
    as_of = db.last_signal_calc_date(client, country, None)
    if not as_of:
        return set()
    rs = client.execute(
        "SELECT sg.stock_id FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        " WHERE s.country = ? AND sg.horizon = 'long' AND sg.as_of_date = ? AND s.status = 'active'"
        # 그 나라·그 기준일의 가장 새 판만 — 새 판이 걸러 낸 종목의 옛 판 행이 섞이지 않게 (infra 25.423·25.464).
        # 바깥 행을 가리키지 않는다(25.428)
        "   AND sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id"
        "     WHERE s3.country = ? AND c.as_of_date = ?)",
        [country, as_of, country, as_of],
    )
    return {int(r[0]) for r in rs.rows}


def to_row(j: acc.Judgement, as_of: str, latest_fy: int, thresholds: dict, long_on: bool, now: str) -> tuple:
    data = {"criteria": j.criteria, "component_scores": j.component_scores, "metrics": j.metrics}
    return (
        j.stock_id,
        as_of,
        latest_fy,
        1 if j.passed else 0,
        j.first_failed_gate,
        j.excluded_reason,
        j.score,
        j.rank,
        j.group_size,
        1 if long_on else 0,
        json.dumps(thresholds, ensure_ascii=False, default=str),
        acc.rationale_text(j),
        json.dumps(data, ensure_ascii=False, default=str),
        acc.CALC_VERSION,
        now,
    )


def sweep_stale(client: TursoClient, country: str, as_of: str, keep: set[int]) -> int:
    """이번에 판정하지 않은 그 나라·그 날 행을 지운다 (docs/infra.md 17절·25.107).

    유니버스에서 빠졌거나 주식 종류가 겹쳐 빠진 종목의 지난 행이 그대로 남으면,
    화면은 **오늘 판정된 것처럼** 보여 준다(`as_of_date` 가 오늘이니까).

    **`NOT IN (?, ?, …)` 으로 한 번에 지우지 않는다.** 판정 종목이 수백~수천이라
    파라미터가 그만큼 붙는데 D1 은 질의당 100개까지다(`core/d1.MAX_PARAMS`).
    `core/d1.split_for_params` 는 `IN` 목록을 **일부러 안 쪼갠다** — 쪼개면 뜻이
    달라져 조용히 반쪽만 도는 것이 가장 나쁘기 때문이다. 그래서 여기서 나눈다.

    먼저 **무엇이 있는지 읽고**(파라미터 셋) 뺄 것을 파이썬에서 고른다. 보통은
    지울 것이 없어 쓰기가 **0번**이다 — D1 하루 쓰기 예산에도 이쪽이 낫다.
    """
    있는것 = {
        int(r[0])
        for r in client.execute(
            "SELECT stock_id FROM stock_accum_picks"
            " WHERE as_of_date = ? AND calc_version = ?"
            "   AND stock_id IN (SELECT id FROM stocks WHERE country = ?)",
            [as_of, acc.CALC_VERSION, country],
        ).rows
    }
    지울것 = sorted(있는것 - keep)
    if not 지울것:
        return 0
    size = db.in_chunk(reserve=2)  # 기준일·판 몫
    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(지울것), size):
        묶음 = 지울것[start : start + size]
        statements.append((
            "DELETE FROM stock_accum_picks WHERE as_of_date = ? AND calc_version = ?"
            f" AND stock_id IN ({', '.join(['?'] * len(묶음))})",
            [as_of, acc.CALC_VERSION, *묶음],
        ))  # fmt: skip
    client.batch(statements)
    return len(지울것)


def run(country: str = "KR") -> int:
    from batch.jobs.etf import _bulk

    rules = acc.RULES[country]
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = datetime.now(UTC).date()
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market=country, trade_date=today.isoformat()
        )

        inputs, latest_fy = load_inputs(client, country)
        if not inputs or latest_fy is None:
            db.finish_batch_run(client, run_id, status="failed", error_text="유니버스 또는 재무가 비어 있습니다")
            print("유니버스 또는 재무가 비어 있습니다")
            return 1

        years = acc.window(latest_fy)
        window_of = {i.stock_id: acc.years_for(i, latest_fy, rules) for i in inputs}
        thresholds = acc.measure_thresholds(inputs, years, rules, latest_fy if country == "US" else None)
        judgements = [acc.evaluate(i, window_of[i.stock_id], thresholds, today, rules) for i in inputs]
        unverifiable = acc.rank(judgements)
        long_on = long_signal_ids(client, country)

        now = db.now_iso()
        rows = [
            to_row(j, today.isoformat(), window_of[j.stock_id][-1], thresholds, j.stock_id in long_on, now)
            for j in judgements
        ]
        _bulk(client, "stock_accum_picks", _COLS, rows, _CONFLICT)
        버린것 = sweep_stale(
            client, country, today.isoformat(), {j.stock_id for j in judgements}
        )

        # 퍼널: 게이트마다 몇 개가 처음 걸렸나. docs/accumulation.md 2장 퍼널과 대조한다
        first_fail = Counter(j.first_failed_gate for j in judgements if not j.passed)
        passed = [j for j in judgements if j.passed]
        with_dividends = sum(1 for i in inputs if i.dividends)
        step = {
            "universe": len(inputs),
            "fiscal_years": years,
            "thresholds": thresholds,
            "first_failed_gate": dict(sorted(first_fail.items())),
            # 근거표를 못 만들어 뺀 것. **0 이어야 정상이다** (docs/infra.md 25.174)
            "unverifiable": unverifiable,
            "passed": len(passed),
            "by_market": dict(Counter(j.market for j in passed)),
            "stocks_with_dividend_rows": with_dividends,
            "long_signal_overlap": sum(1 for j in passed if j.stock_id in long_on),
            "swept": 버린것,
        }
        db.finish_batch_run(client, run_id, status="success", step_log=step)

        print(f"유니버스 {len(inputs)}종목, 사업연도 {years[0]}~{years[-1]}, 배당 자료 있는 종목 {with_dividends}")
        print(f"G8 중앙값 {thresholds['roe_median']} (n={thresholds['roe_n']})")
        remaining = len(inputs)
        for gate in ("G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10"):
            remaining -= first_fail.get(gate, 0)
            print(f"  {gate} 뒤 {remaining}종목 (여기서 {first_fail.get(gate, 0)} 제외)")
        if unverifiable:
            print(f"  주의: 근거표를 만들지 못해 {len(unverifiable)}종목을 추천에서 뺐습니다 (docs/infra.md 25.174)")
        print(f"통과 {len(passed)}종목 {dict(Counter(j.market for j in passed))}")
        for j in sorted(passed, key=lambda x: x.rank or 0)[:10]:
            print(f"  {j.rank:3d}. {j.name} ({j.ticker}) 점수 {j.score}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="장기 적립 종목 판정")
    parser.add_argument("--market", choices=["KR", "US"], default="KR")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.market)


if __name__ == "__main__":
    sys.exit(guard(main))
