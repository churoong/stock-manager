"""25.1614 교차검증 [필수]·[권고] 1 (docs/paper.md 15장, docs/infra.md 25.1615).

지키는 것
- 국내 같은 날 다시 돌 때, 이긴 행 아닌(옛) 행으로 오늘 산 회차가 시가에 찬 몫은 그 행에 붙는다 — 이긴 행으로 넘어가
  정리로 팔리지 않는다
- 주문가능이 모자란 날, 오늘 베낄 매도가 걸린 행이 주문가능을 먼저 받는다(옛 행 매도도 막히지 않는다)
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import broker_mirror as job
from batch.sources import kis_vts as kv
from tests import test_kis_vts_1475 as kr
from tests.test_kis_ids_1608 import _두_행
from tests.test_kis_ids_1614 import _bo, _fill, _run
from tests.test_kis_vts_us_1485 import c  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def test_옛_행_매도가_걸리면_주문가능을_먼저(c: Client) -> None:  # noqa: F811
    # H: 옛 행 3 + 새 행 2, KIS 5주 중 주문가능 3. 오늘 H 가 옛 행 3주를 판다
    _두_행(c, 30, "NYSE", 2, "NASDAQ")  # 301 = 옛 행 매도 3
    _fill(c, 310, 2, "buy", 2, "2026-10-15")
    _bo(c, 2, "buy", "filled", 2, 2, "2026-10-15", 310)
    assert _run(c, 5, sellable=3)[0] == [("sell", "NYSE", 3)]


@pytest.fixture()
def k(monkeypatch: pytest.MonkeyPatch) -> Client:
    return kr.c.__wrapped__(monkeypatch)


def test_국내_같은_날_다시_돌면_옛_행_오늘_매수를_정리하지_않는다(k: Client) -> None:
    c = k  # noqa: F811
    x = c.conn.execute
    x("UPDATE paper_orders SET status = 'filled' WHERE id IN (102, 103)")
    # 새 행(50, active) — H·KIS 4주. 옛 행 1(005930 픽스처, excluded) — H·KIS 2주. 오늘 옛 행 회차 101(3주)을 낸다
    x("UPDATE stocks SET status = 'excluded' WHERE id = 1")
    x("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source, fetched_at,"
      " asset_type) VALUES (50, '005930', 'KOSDAQ', 'KR', 'x', '전자', 'KRW', 'active', 't', 't', 'stock')")
    for oid, sid, q in ((400, 50, 4), (401, 1, 2)):
        x("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency, decided_on,"
          " fill_session, reason, rule_version, status, created_at) VALUES (?, 'B', 'KR', ?, 'buy', 'market', ?, 'KRW',"
          " '2026-10-08', '2026-10-08', 'r', 1, 'filled', 't')", [oid, sid, q])
        x("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
          " fee, tax, slippage, cash_krw, created_at) VALUES (?, 'B', ?, 'buy', '2026-10-08', 9000, ?, 'KRW', 1, 0, 0, 0,"
          " 0, 't')", [oid, sid, q])
        x("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
          " quantity, trade_date, reason, status, filled_qty, source, created_at) VALUES ('kis_vts', ?, 'B', 'KR', ?,"
          " '005930', 'buy', 'market', ?, '2026-10-08', '베낌', 'filled', ?, 't', 't')", [oid, sid, q, q])
    assert job._ids_all(c, {"005930"}, "KR", "kis_vts") == {"005930": [1, 50]}
    s = kr._Sess(holdings=[kv.Holding("005930", 6, 6, 9_800.0)])
    job.run(c, "KR", kr._at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: kr._at("08:40"))
    다시 = kr._Sess(holdings=[kv.Holding("005930", 9, 9, 9_800.0)])  # 옛 행 회차 3주가 시가에 찼다
    job.run(c, "KR", kr._at("09:35"), session=다시, sleep=lambda _x: None, clock=lambda: kr._at("09:35"))
    assert 다시.orders == []
    assert json.loads(c.q("SELECT diff_json FROM paper_broker_daily ORDER BY rowid DESC LIMIT 1")[0][0]) == {}
