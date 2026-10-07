"""위성 ETF 판정 배치 (docs/etf.md 10장).

**새로 받지 않는다.** 핵심 판정(batch/jobs/etf.py)이 저장한 가장 최근 프로필을 읽어 판정만 한다.
그래서 Actions 분과 야후·한국거래소 호출이 거의 들지 않는다. 월 1회 핵심 판정 뒤에 이어서 돈다.

실행
  python -m batch.jobs.etf_satellite
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from datetime import date
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.etf import _bulk, profile_from_row
from batch.services import etf as core
from batch.services import etf_accounts
from batch.services import etf_satellite as sat

log = logging.getLogger("etf_satellite")

JOB_NAME = "etf_satellite"

_COLS = (
    "etf_id, as_of_date, sat_group, sub_group, passed, excluded_reason, score, rank_in_group, group_size,"
    " rationale_text, rationale_data, calc_version, created_at"
)
_CONFLICT = (
    "ON CONFLICT (etf_id, as_of_date, calc_version) DO UPDATE SET sat_group = excluded.sat_group,"
    " sub_group = excluded.sub_group, passed = excluded.passed, excluded_reason = excluded.excluded_reason,"
    " score = excluded.score, rank_in_group = excluded.rank_in_group, group_size = excluded.group_size,"
    " rationale_text = excluded.rationale_text, rationale_data = excluded.rationale_data,"
    " created_at = excluded.created_at"
)


def kr_input_from_row(row: dict[str, Any]) -> core.KrEtfInput:
    """저장된 국내 프로필 한 행을 판정 입력으로. category 열에 기초지수 이름이 있다(batch/jobs/etf.py)."""
    return core.KrEtfInput(
        symbol=str(row["symbol"]),
        name=str(row["etf_name"]),
        index_name=str(row.get("category") or ""),
        net_assets=row.get("total_assets"),
        avg_turnover=row.get("turnover_est"),
        premium_abs_avg=row.get("premium_abs_avg"),
        days_observed=int(row.get("days_observed") or 0),
        listed_3y_ago=bool(row.get("listed_3y_ago")),
        # 괴리율을 낸 날 수 (25.841, 0042). 옛 행은 NULL — 근거표가 예전처럼 days_observed 로 적는다
        premium_days=None if row.get("premium_days") is None else int(row["premium_days"]),
    )


def to_row(etf_id: int, as_of: str, ev: core.Evaluation, now: str) -> tuple:
    data = {
        "criteria": ev.criteria,
        "warnings": sat.warnings_for(ev),
        "component_scores": ev.component_scores,
        "weights": core.renormalized_weights(core.AVAILABLE_BY_COUNTRY[ev.country]) if ev.passed else None,
        # 계좌별 가능 여부 (docs/etf.md 11.1, 25.966). 위성은 순위 없이 가능 여부만
        "accounts": etf_accounts.accounts_for(ev.country, ev.bucket, satellite=True) if ev.passed else None,
    }
    return (
        etf_id,
        as_of,
        ev.bucket,
        sat.sub_group_of(ev),
        1 if ev.passed else 0,
        ev.excluded_reason,
        ev.score,
        ev.rank_in_category,
        ev.category_size,
        sat.rationale_text(ev),
        json.dumps(data, ensure_ascii=False, default=str),
        sat.CALC_VERSION,
        now,
    )


def _latest_profiles(client: TursoClient, country: str) -> tuple[str | None, list[dict[str, Any]]]:
    as_of = client.execute(
        "SELECT MAX(pf.as_of_date) FROM etf_profiles pf JOIN etfs e ON e.id = pf.etf_id WHERE e.country = ?",
        [country],
    ).scalar()
    if not as_of:
        return None, []
    rows = client.execute(
        "SELECT e.symbol, e.yahoo_symbol, e.name AS etf_name, pf.* FROM etf_profiles pf"
        " JOIN etfs e ON e.id = pf.etf_id WHERE e.country = ? AND pf.as_of_date = ?",
        [country, as_of],
    ).dicts()
    return str(as_of), rows


def run() -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        now = db.now_iso()
        summary: dict[str, Any] = {}
        total_rows = 0

        for country in ("US", "KR"):
            as_of, rows = _latest_profiles(client, country)
            if not as_of:
                # **기록은 남긴다** (docs/infra.md 25.608, 감사 — 25.114 와 같은 모양). 예전에는 기록 없이 넘어가
                # /status 가 "예약이 안 불렸다" 와 구별하지 못했다
                run_id = db.start_batch_run(client, job_name=JOB_NAME, market=country, trade_date=None)
                db.finish_batch_run(
                    client, run_id, status="skipped", error_text="저장된 프로필이 없습니다 — 핵심 판정을 먼저 돌리세요"
                )
                print(f"[{country}] 저장된 프로필이 없습니다. 핵심 판정(batch.jobs.etf)을 먼저 돌리세요")
                continue
            run_id = db.start_batch_run(client, job_name=JOB_NAME, market=country, trade_date=as_of)

            evaluations: list[tuple[int, core.Evaluation]] = []
            as_of_day = date.fromisoformat(as_of)
            for row in rows:
                fetched = str(row.get("fetched_at") or as_of)[:10]
                if country == "US":
                    ev = sat.evaluate_us(
                        str(row["yahoo_symbol"]), str(row["etf_name"]), profile_from_row(row), as_of_day, fetched
                    )
                else:
                    ev = sat.evaluate_kr(kr_input_from_row(row), as_of)
                if ev is not None:
                    evaluations.append((int(row["etf_id"]), ev))

            unverifiable = sat.score([ev for _, ev in evaluations])
            out = [to_row(etf_id, as_of, ev, now) for etf_id, ev in evaluations]
            # 같은 기준일·판의 옛 판정을 지우고 쓴다 (25.758, ETF 감사) — 규칙이 바뀌어 범위 밖이 된 ETF 의 옛 "통과"
            # 행이 남았다
            지우기 = [(
                "DELETE FROM etf_satellite_picks WHERE as_of_date = ? AND calc_version = ?"
                " AND etf_id IN (SELECT id FROM etfs WHERE country = ?)",
                [as_of, sat.CALC_VERSION, country],
            )]
            _bulk(client, "etf_satellite_picks", _COLS, out, _CONFLICT, before=지우기)
            total_rows += len(out)

            passed = Counter(ev.bucket for _, ev in evaluations if ev.passed)
            scoped = Counter(ev.bucket for _, ev in evaluations)
            reasons = Counter(
                (ev.excluded_reason or "").split(" (")[0] for _, ev in evaluations if not ev.passed
            )
            step = {
                "as_of": as_of,
                "profiles": len(rows),
                "in_scope": dict(scoped),
                "passed": dict(passed),
                "excluded_reasons": dict(reasons.most_common(15)),
                "calc_version": sat.CALC_VERSION,
                # 근거표를 못 만들어 뺀 것. **0 이어야 정상이다** (docs/infra.md 25.174)
                "unverifiable": unverifiable,
            }
            summary[country] = step
            # 근거표를 못 만들어 뺀 것이 있으면 success 가 아니다 — "0 이어야 정상" 인 수를 success 로 덮었다 (25.608)
            db.finish_batch_run(
                client, run_id, status="partial" if unverifiable else "success", step_log=step,
                error_text=f"근거표를 만들지 못해 {len(unverifiable)}개를 뺐습니다" if unverifiable else None,
            )

            if unverifiable:
                print(f"  주의: 근거표를 만들지 못해 {len(unverifiable)}개를 추천에서 뺐습니다 (docs/infra.md 25.174)")
            print(f"[{country}] 기준일 {as_of}, 프로필 {len(rows):,}개 → 위성 범위 {len(evaluations)}개")
            for group in sat.GROUP_ORDER:
                print(f"  {group}: 통과 {passed.get(group, 0)} / 범위 {scoped.get(group, 0)}")
            for name, count in reasons.most_common(8):
                print(f"  제외 {count:4d}  {name}")

        print(f"저장 {total_rows:,}행")
        return 0
    finally:
        client.close()


def main() -> int:
    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    return run()


if __name__ == "__main__":
    sys.exit(guard(main))
