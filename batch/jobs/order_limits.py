"""절대 한도·가격 깃 그림자 계측의 읽기 (docs/paper.md 15.12, docs/infra.md 25.2284) — 읽기 전용."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from batch.services import order_limits as ol

#: 창 안의 KIS모의투자 주문 — 상태와 무관하게(거부된 것도 "보내려던 주문" 이다)
ORDERS_SQL = (
    "SELECT id, market, stock_id, side, order_type, limit_price, quantity, trade_date, reason, status"
    " FROM paper_broker_orders WHERE trade_date >= ? ORDER BY trade_date, id"
)
#: 그 종목들의 일봉 — 창 앞으로 `PRICE_LOOKBACK_DAYS` 더(앞 종가·평균 거래대금). 유일 색인 (stock_id, date)
PRICES_SQL = (
    "SELECT stock_id, date, close, volume, value FROM prices"
    " WHERE stock_id IN (SELECT value FROM json_each(?)) AND date >= ? AND date <= ? ORDER BY stock_id, date"
)


def run(client: Any, today: str) -> list[str]:
    since = (date.fromisoformat(today) - timedelta(days=ol.WINDOW_DAYS)).isoformat()
    orders = client.execute(ORDERS_SQL, [since]).dicts()
    ids = sorted({int(o["stock_id"]) for o in orders})
    bars: dict[int, list[dict[str, Any]]] = {}
    if ids:
        바닥 = (date.fromisoformat(since) - timedelta(days=ol.PRICE_LOOKBACK_DAYS)).isoformat()
        for r in client.execute(PRICES_SQL, [json.dumps(ids), 바닥, today]).dicts():
            bars.setdefault(int(r["stock_id"]), []).append(r)
    return ol.lines(ol.summarize(orders, bars), since, today)
