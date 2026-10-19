"""신호 종목(보유 아님)에도 기업행위 표시 — 웹이 겹친 관심 목표가를 거른다 (docs/intraday.md 2장, docs/infra.md 25.1551).
감사 재현: 3거래일 전 1:2 분할된 신호 종목에 `action_guard` 가 없어 분할 전 관심 목표가로 "도달" 이 날마다 나갔다."""

from __future__ import annotations

import json

import pytest

from batch.jobs import monitor_targets as job
from tests.test_portfolio_job import MemClient


def test_신호만_있는_종목도_분할이면_표시한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import db

    monkeypatch.setattr(job, "signal_date_current", lambda *_a: True)
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
              " VALUES (7, '000007', 'KOSPI', 'KR', '종목7', 'KRW', 'active', 't', 't', '000007.KS')")  # fmt: skip
    for i in range(20):
        종가 = 50000 if i >= 17 else 100000  # 3거래일 전 1:2 분할, 등락률 0
        c.execute("INSERT INTO prices (stock_id, date, close, change_pct, volume, currency, source, fetched_at)"
                  " VALUES (7, date('2026-08-28', ?), ?, 0, 100, 'KRW', 't', 't')", [f"+{i} days", 종가])  # fmt: skip
    c.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
              " tranche_plan, target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version,"
              " created_at) VALUES (7, '2026-09-16', 'short', 'x', 48000, 50000, 'KRW', '[]', 55000, 46000, 1, 'x', '{}', 1,"
              " 't')")  # fmt: skip
    assert job.run("KR") == 0
    r = c.execute("SELECT reasons, avg_volume_20d FROM monitor_targets WHERE stock_id = 7").fetchone()
    assert "action_guard" in json.loads(r[0]) and r[1] is None
