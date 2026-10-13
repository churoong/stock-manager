"""다음 분할 매수 차수는 1차를 산 기간의 계획을 잇는다 (docs/infra.md 25.1262, 신호 감사 재현)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.jobs import monitor_targets as mt
from tests.test_report_picks import SqliteClient

계획 = {"short": [102_000, 100_000, 98_000], "long": [103_000, 84_100, 65_100]}


def _db(기간: str | None) -> SqliteClient:
    c = SqliteClient()
    k = c.conn
    k.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'X', 'KRW', 'active', 't', 't')")  # fmt: skip
    for hz, 값 in 계획.items():
        plan = [{"step": i + 1, "ratio": 1 / 3, "price": p} for i, p in enumerate(값)]
        k.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
                  " tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at)"
                  " VALUES (1, '2026-10-05', ?, 't', ?, ?, 'KRW', ?, 1, '', '{}', 1, 't')",
                  [hz, 값[2], 값[0], json.dumps(plan)])  # fmt: skip
    k.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
              " horizon, created_at, updated_at)"
              " VALUES (1, 'buy', '2026-10-06', 103000, 10, 'KRW', 1, 'none', ?, 't', 't')", [기간])  # fmt: skip
    return c


@pytest.mark.parametrize(("기간", "다음"), [("long", 84_100.0), ("short", 100_000.0), (None, 100_000.0)])
def test_산_기간의_계획(기간: str | None, 다음: float) -> None:
    assert mt.next_tranche(_db(기간), 1, date(2026, 10, 7)) == (다음, 2, "2026-10-05")
