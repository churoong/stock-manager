"""25.1607 교차검증 [필수]·[권고] 반영 (docs/paper.md 15장, docs/infra.md 25.1608).

지키는 것
- 같은 티커가 두 행이면 KIS 보유는 **이 증권사 칸에서 산 적 있는 행**에 붙는다 — H 가 이사 전(이제 상장 아님) 행을
  든 채 팔면 KIS 도 판다. 상장 여부만 보면 보유가 산 적 없는 상장 행에 붙어 영영 안 팔렸다
- 상한·예약 마감에 잘려 하나도 안 낸 정리는 "일부만 정리" 까닭을 남기지 않는다
- 미국에서 티커는 있는데 KIS 거래소 코드가 없으면 까닭이 그 사실을 말한다
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from tests.test_kis_vts_us_1485 import _kst, _UsSess, c  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def _두_행(c: Client, held: int, held_m: str, other: int, other_m: str) -> None:  # noqa: F811
    x = c.conn.execute
    x("UPDATE paper_orders SET status = 'filled'")  # 픽스처 매수는 치움
    for sid, m, st in ((held, held_m, "excluded"), (other, other_m, "active")):  # 2 는 픽스처 주문이 가리켜 지우지 않는다
        x("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source, fetched_at,"
          " asset_type) VALUES (?, 'AAPL', ?, 'US', 'x', '전자', 'USD', ?, 't', 't', 'stock')"
          " ON CONFLICT(id) DO UPDATE SET market = excluded.market, status = excluded.status", [sid, m, st])
        x("INSERT OR IGNORE INTO prices (stock_id, date, close, currency, source, fetched_at)"
          " VALUES (?, '2026-10-15', 201.0, 'USD', 't', 't')", [sid])
    # H 가 이사 전 행으로 3주 샀고 KIS 에도 베껴 찼다
    x("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
      " decided_on, fill_session, reason, rule_version, tranche, created_at, status) VALUES (300, 'B', 'US', ?, 'buy',"
      " 'limit', 200, 3, 'USD', '2026-10-13', '2026-10-14', 'r', 1, 1, 't', 'filled')", [held])
    x("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
      " fee, tax, slippage, cash_krw, created_at) VALUES (300, 'B', ?, 'buy', '2026-10-14', 200, 3, 'USD', 1400, 0, 0,"
      " 0, -840000, 't')", [held])
    x("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
      " limit_price, quantity, trade_date, reason, status, filled_qty, source, created_at) VALUES ('kis_vts_us', 300,"
      " 'B', 'US', ?, 'AAPL', 'buy', 'limit', 200, 3, '2026-10-14', '베낌', 'filled', 3, 't', 't')", [held])
    # 오늘 H 가 판다(시장가)
    x("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
      " decided_on, fill_session, reason, rule_version, tranche, created_at) VALUES (301, 'B', 'US', ?, 'sell',"
      " 'market', NULL, 3, 'USD', '2026-10-15', '2026-10-16', 'r', 1, 1, 't')", [held])


@pytest.mark.parametrize("held,held_m,other,other_m", [(30, "NYSE", 2, "NASDAQ"), (2, "NASDAQ", 30, "NYSE")])
def test_kis_보유는_산_적_있는_행에_붙는다(c: Client, held: int, held_m: str, other: int, other_m: str) -> None:  # noqa: F811
    _두_행(c, held, held_m, other, other_m)
    assert job._ids(c, {"AAPL"}, "US", "kis_vts_us") == {"AAPL": held}
    s = _UsSess(holdings=[kv.Holding("AAPL", 3, 3, 200.0)])
    기록 = job.run(c, "US", _kst("21:27"), session=s, clock=lambda: _kst("21:27"))
    assert [(o[0], o[1], o[3]) for o in s.orders] == [("sell", "AAPL", 3)]
    assert "not_sent" not in 기록
    assert json.loads(c.q("SELECT diff_json FROM paper_broker_daily")[0][0]) == {}
    # 다른 증권사 칸에서 산 것은 이 칸의 잣대가 아니다 — 상장 중인 행
    assert job._ids(c, {"AAPL"}, "US", "kis_vts_us_b") == {"AAPL": other}


def test_잘려서_하나도_안_낸_정리는_일부만_까닭이_없다() -> None:
    공통 = dict(internal_qty={}, bought_before={7}, already=set(), cleaned_today=set(), currency="KRW", halted=False)
    낼것, 안냄 = bm.plan([], {7: "X"}, broker={7: (3, 1)}, sent_today=40, **공통)  # type: ignore[arg-type]
    assert 낼것 == [] and 안냄 == [(None, 7, f"하루 주문 상한 {bm.MAX_ORDERS_PER_DAY}")]
    # 잘리지 않으면 그대로
    낼것, 안냄 = bm.plan([], {7: "X"}, broker={7: (3, 1)}, **공통)  # type: ignore[arg-type]
    assert [x.quantity for x in 낼것] == [1] and 안냄 == [(None, 7, bm.PARTIAL_CLEANUP)]
    # 예약 마감으로 잘린 것도 같은 식으로 지운다
    assert bm.without_partial(안냄, 낼것) == []


# 25.1611 — 25.1608 교차검증 [필수] 1: 찬 매수만 "산 적", 여러 행이면 잔고를 행마다 나눈다
def _새행_매수(c: Client, new: int, status: str, filled: int, internal: bool) -> None:  # noqa: F811
    x = c.conn.execute
    x("DELETE FROM paper_orders WHERE id = 301")  # 오늘 매도 없음 — H 는 옛 행을 그대로 든다
    x("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
      " decided_on, fill_session, reason, rule_version, tranche, created_at, status) VALUES (310, 'B', 'US', ?, 'buy',"
      " 'limit', 200, 2, 'USD', '2026-10-14', '2026-10-15', 'r', 1, 1, 't', ?)", [new, "filled" if internal else "expired"])
    x("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
      " limit_price, quantity, trade_date, reason, status, filled_qty, source, created_at) VALUES ('kis_vts_us', 310,"
      " 'B', 'US', ?, 'AAPL', 'buy', 'limit', 200, 2, '2026-10-15', '베낌', ?, ?, 't', 't')", [new, status, filled])
    if internal:
        x("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
          " fee, tax, slippage, cash_krw, created_at) VALUES (310, 'B', ?, 'buy', '2026-10-15', 200, 2, 'USD', 1400, 0, 0,"
          " 0, -560000, 't')", [new])


def _run(c: Client, qty: int) -> tuple:  # noqa: F811
    s = _UsSess(holdings=[kv.Holding("AAPL", qty, qty, 200.0)])
    기록 = job.run(c, "US", _kst("21:27"), session=s, clock=lambda: _kst("21:27"))
    diff = json.loads(c.q("SELECT diff_json FROM paper_broker_daily")[0][0])
    return s.orders, 기록, diff


def test_A_새행_매수가_안_찼는데_산적으로_세어_옛행_보유를_정리(c: Client) -> None:  # noqa: F811
    # H 는 옛 행(30, excluded) 3주를 든 채. 새 행(2) 베낀 매수는 안 찼다(unfilled) → KIS 는 AAPL 3주(옛 행 몫)
    _두_행(c, 30, "NYSE", 2, "NASDAQ")
    _새행_매수(c, 2, "unfilled", 0, internal=False)
    orders, 기록, diff = _run(c, 3)
    assert orders == []  # 기대: 아무것도 안 판다


def test_B_두_행_모두_들면_한_행에_몰려_정리(c: Client) -> None:  # noqa: F811
    # H: 옛 행 3주 + 새 행 2주 (둘 다 KIS 로 찼다) → KIS AAPL 5주
    _두_행(c, 30, "NYSE", 2, "NASDAQ")
    _새행_매수(c, 2, "filled", 2, internal=True)
    orders, 기록, diff = _run(c, 5)
    assert orders == [] and diff == {}


def test_C_둘다_상장중_둘다_산적_옛행_큰id(c: Client) -> None:  # noqa: F811
    # 옛 행에서 다 팔고 새 행으로 다시 샀다 — 옛 행 status 가 아직 active 면(옮김 직후·유니버스 실패) 큰 id(옛 행)가 이긴다
    _두_행(c, 30, "NYSE", 2, "NASDAQ")
    c.conn.execute("UPDATE stocks SET status = 'active' WHERE id = 30")
    c.conn.execute("DELETE FROM paper_orders WHERE id = 301")
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, reason, rule_version, tranche, created_at, status) VALUES (302,"
                   " 'B', 'US', 30, 'sell', 'market', NULL, 3, 'USD', '2026-10-14', '2026-10-14', 'r', 1, 1, 't', 'filled')")
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (302, 'B', 30, 'sell', '2026-10-14', 200,"
                   " 3, 'USD', 1400, 0, 0, 0, 840000, 't')")
    _새행_매수(c, 2, "filled", 2, internal=True)
    orders, 기록, diff = _run(c, 2)
    assert orders == [] and diff == {}


def test_D_안_찬_매수는_산_적이_아니다_정리는_찬_행의_거래소로(c: Client) -> None:  # noqa: F811
    # H 는 옛 행(30) 3주를 이미 팔았는데 KIS 에는 남았다 — 새 행(2) 베낀 매수는 안 찼다. 정리는 산 행(NYSE)으로
    _두_행(c, 30, "NYSE", 2, "NASDAQ")
    c.conn.execute("UPDATE paper_orders SET status = 'filled' WHERE id = 301")
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (301, 'B', 30, 'sell', '2026-10-15', 200,"
                   " 3, 'USD', 1400, 0, 0, 0, 840000, 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, reason, rule_version, tranche, created_at, status) VALUES (310,"
                   " 'B', 'US', 2, 'buy', 'limit', 200, 2, 'USD', '2026-10-14', '2026-10-15', 'r', 1, 1, 't', 'expired')")
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, limit_price, quantity, trade_date, reason, status, filled_qty, source, created_at)"
                   " VALUES ('kis_vts_us', 310, 'B', 'US', 2, 'AAPL', 'buy', 'limit', 200, 2, '2026-10-15', '베낌',"
                   " 'unfilled', 0, 't', 't')")  # fmt: skip
    orders, _기록, _diff = _run(c, 3)
    assert [(o[0], o[1], o[2], o[3]) for o in orders] == [("sell", "AAPL", "NYSE", 3)]
