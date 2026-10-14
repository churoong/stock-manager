"""신호 0 인 날 지난 신호로 "매수 검토" 를 내지 않는다 (docs/infra.md 25.1336, 분석 화면 감사 #2).

신호 작업은 그날 행만 지우고 다시 넣어, 신호 0 인 날에는 `signals` 의 최신 날이 지난 날로 남는다. 판정표는 그날에도 남는다
— 마지막 계산일은 둘의 최신이다(웹 `LAST_CALC_DATE` 와 같다)."""

from __future__ import annotations

import json
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from tests.test_portfolio_job import MemClient


def _돌림(오늘_판정표: bool) -> str:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', '종목1', 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
              " weights_json, rank_in_market, calc_version, created_at) VALUES (1, '2026-10-14', 70, ?, 0, '{}', 1,"
              " 10, 't')", [json.dumps({"value": 50, "quality": 60})])  # fmt: skip
    # 10-07 에 단기 신호. 10-14 는 시장 전체 신호 0 — 판정표만 남았다
    c.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
              " tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at, target_price,"
              " stop_price) VALUES (1, '2026-10-07', 'short', 'buy', 100, 110, 'KRW', '[]', 0, 'r', '{}', 'v', 't',"
              " 120, 93)")  # fmt: skip
    날 = "2026-10-14" if 오늘_판정표 else "2026-10-07"
    c.execute("INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
              " calc_version, created_at) VALUES (1, ?, 'short', 0, 1, '[]', 1, 't')", [날])  # fmt: skip
    warnings: list[str] = []
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 15), warnings))  # type: ignore[arg-type]
    return c.execute("SELECT verdict FROM stock_verdicts WHERE stock_id = 1").fetchone()[0]


def test_오늘_판정표만_있으면_지난_신호를_쓰지_않는다() -> None:
    assert _돌림(오늘_판정표=True) != "consider_buy"  # 예전: consider_buy (10-07 신호)


def test_신호와_판정표가_같은_날이면_그대로() -> None:
    assert _돌림(오늘_판정표=False) == "consider_buy"
