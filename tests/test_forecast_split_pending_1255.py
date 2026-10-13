"""미국 분할이 재수집을 기다리는 동안 예측 성적표에 −75% 로 채점하지 않는다 (docs/infra.md 25.1255, 미국 시세 감사 재현)."""

from __future__ import annotations

import json

from batch.jobs import verdicts
from tests.test_report_picks import SqliteClient


def _db() -> SqliteClient:
    c = SqliteClient()
    k = c.conn
    k.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
              " VALUES (1, 'XYZ', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'XYZ')")  # fmt: skip
    for d, close in (("2026-07-09", 400.0), ("2026-10-09", 101.0)):  # 옛 행은 아직 분할 전
        k.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                  " VALUES (1, ?, ?, ?, 'USD', 'yfinance', 't')", [d, close, close])  # fmt: skip
    k.execute("INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
              " VALUES (1, '2026-10-06T12:00:00+00:00', '2026-09-28', 1.0, 1.0)")  # fmt: skip
    models = {"capm": {"3": {"exp": 0.03, "lo68": -0.10, "hi68": 0.16, "lo90": -0.20, "hi90": 0.27}}}
    k.execute("INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
              " VALUES ('2026-07-09', 1, 'US', 100.0, ?, 't')", [json.dumps(models)])  # fmt: skip
    return c


def test_분할_대기_중이면_기다렸다가_재수집_뒤에_채점한다() -> None:
    c = _db()
    prior: dict = {}
    state: dict = {"logged": ["2026-07-09"]}
    w: list[str] = []
    verdicts.evaluate_due(c, "US", "2026-10-09", {1: {"close": 101.0}}, prior, state, w)
    assert 1 not in prior  # 채점하지 않았다
    assert {x[2] for x in state["pending"]} == {1}  # 달마다 한 줄
    # 토요일 재수집이 옛 행을 고치고 대기열을 닫았다
    c.conn.execute("UPDATE prices SET close = 100, adj_close = 100 WHERE date = '2026-07-09'")
    c.conn.execute("UPDATE adjust_refresh_queue SET done_at = 'x'")
    verdicts.evaluate_due(c, "US", "2026-10-12", {1: {"close": 101.0}}, prior, state, w)
    cell = prior[1]["capm"]["3"]
    assert cell[2] == 1 and cell[-1] < 0.05  # 68% 범위 안, 오차 작음
    assert state["pending"] == []
