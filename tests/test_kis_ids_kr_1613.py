"""25.1611 교차검증 [필수] 2 (docs/paper.md 15장, docs/infra.md 25.1613).

지키는 것: 같은 티커가 두 행일 때 오늘 처음 산 새 행(아직 맞추지 않은 매수)도 "산 적" — 같은 날 다시 돌 때 KIS 잔고가
예전에 다 판 옛 행에 붙어 방금 산 주식을 정리로 팔지 않는다
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import broker_mirror as job
from batch.sources import kis_vts as kv
from tests import test_kis_vts_1475 as kr
from tests.test_kis_vts_1475 import c  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def test_같은_날_다시_돌면_오늘_산_새_행을_정리하지_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    x = c.conn.execute
    x("UPDATE paper_orders SET status = 'filled' WHERE id = 102")  # 매도는 치움
    x("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source, fetched_at,"
      " asset_type) VALUES (50, '005930', 'KOSDAQ', 'KR', 'x', '전자', 'KRW', 'excluded', 't', 't', 'stock')")
    # 옛 행(50)은 예전에 KIS 로 사고 다 팔았다
    for oid, side, d in ((400, "buy", "2026-10-08"), (401, "sell", "2026-10-13")):
        x("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency, decided_on,"
          " fill_session, reason, rule_version, status, created_at) VALUES (?, 'B', 'KR', 50, ?, 'market', 3, 'KRW', ?,"
          " ?, 'r', 1, 'filled', 't')", [oid, side, d, d])
        x("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
          " fee, tax, slippage, cash_krw, created_at) VALUES (?, 'B', 50, ?, ?, 9000, 3, 'KRW', 1, 0, 0, 0, 0, 't')",
          [oid, side, d])
        x("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
          " quantity, trade_date, reason, status, filled_qty, source, created_at) VALUES ('kis_vts', ?, 'B', 'KR', 50,"
          " '005930', ?, 'market', 3, ?, '베낌', 'filled', 3, 't', 't')", [oid, side, d])
    s = kr._Sess()
    job.run(c, "KR", kr._at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: kr._at("08:40"))
    assert [o[0] for o in s.orders] == ["buy", "buy"]
    다시 = kr._Sess(holdings=[kv.Holding("005930", 3, 3, 9_800.0)])  # 새 행 매수 3주가 시가에 찼다
    job.run(c, "KR", kr._at("09:35"), session=다시, sleep=lambda _x: None, clock=lambda: kr._at("09:35"))
    assert 다시.orders == []
    assert "50" not in json.loads(c.q("SELECT diff_json FROM paper_broker_daily")[0][0])
