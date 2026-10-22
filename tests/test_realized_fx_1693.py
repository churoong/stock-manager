"""가상모의투자 실현손익의 환차 몫 (docs/paper.md 6장, docs/infra.md 25.1693).

2026-10-22 사용자 지시 "모의투자에 실현손익이나 기간실현손익을 보는게 없네? 이것도 당연히 있어야지" — CLAUDE.md "해외 종목은
환차손익을 주가 손익과 분리해서 표시". 실현손익 = 주가 몫 + 환차 몫, 환차 몫 = 현지 평균 원가 × (매도 환율 − 매수 평균 환율).
"""

from __future__ import annotations

from batch.jobs import paper as job
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client


def _f(oid: int, side: str, d: str, price: float, qty: int, fx: float, fee: float, cur: str = "USD",
       sid: int = 2) -> pp.Fill:  # fmt: skip
    금액 = price * qty
    현지 = -(금액 + fee) if side == "buy" else 금액 - fee
    return pp.Fill(order_id=oid, account_id="A", stock_id=sid, side=side, fill_date=d, price=price, quantity=qty,
                   currency=cur, fx_rate=fx, fee=fee, tax=0.0, slippage=0.0, cash_krw=현지 * fx)  # fmt: skip


def test_손으로_계산한_환차_몫() -> None:
    # 10주 $100 (수수료 $1) 환율 1,300 → 원가 $1,001 = 1,301,300원. 10주 $110 (수수료 $1.1) 환율 1,400 → 1,538,460원
    # 실현 237,160 = 주가 (1,098.9 − 1,001) × 1,400 = 137,060 + 환차 1,001 × (1,400 − 1,300) = 100,100
    r = pp.realized([_f(1, "buy", "2026-10-01", 100, 10, 1300, 1.0), _f(2, "sell", "2026-10-05", 110, 10, 1400, 1.1)])
    assert r == [{"stock_id": 2, "fill_date": "2026-10-05", "order_id": 2, "pnl_krw": 237_160.0, "cost_krw": 1_301_300.0,
                  "fx_krw": 100_100.0, "price_krw": 137_060.0}]  # fmt: skip


def test_두_번_사서_반만_팔면_평균_원가와_평균_환율() -> None:
    # $100×10 @1,300, $120×10 @1,200 → 현지 원가 $2,200, 원화 2,740,000 → 반(10주) 팔 때 현지 $1,100·원화 1,370,000
    r = pp.realized([_f(1, "buy", "2026-10-01", 100, 10, 1300, 0.0), _f(2, "buy", "2026-10-02", 120, 10, 1200, 0.0),
                     _f(3, "sell", "2026-10-05", 110, 10, 1250, 0.0)])  # fmt: skip
    # 대금 $1,100 @1,250 = 1,375,000 → 실현 5,000 = 주가 (1,100 − 1,100) × 1,250 = 0 + 환차 1,100 × 1,250 − 1,370,000
    assert (r[0]["pnl_krw"], r[0]["price_krw"], r[0]["fx_krw"]) == (5_000.0, 0.0, 5_000.0)


def test_원화_종목의_환차는_0() -> None:
    r = pp.realized([_f(1, "buy", "2026-10-01", 10_000, 3, 1.0, 15.0, "KRW", 1),
                     _f(2, "sell", "2026-10-05", 11_000, 3, 1.0, 16.0, "KRW", 1)])  # fmt: skip
    assert r[0]["fx_krw"] == 0.0 and r[0]["price_krw"] == r[0]["pnl_krw"] == 33_000 - 16 - 30_015


def test_열이_생기기_전_매도는_배치가_한_번_채운다() -> None:
    c = Client()
    for oid, side, d in ((1, "buy", "2026-10-01"), (2, "sell", "2026-10-05")):
        c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                       " decided_on, fill_session, reason, rule_version, created_at) VALUES (?, 'A', 'US', 2, ?,"
                       " 'market', 10, 'USD', ?, ?, 'x', 1, 't')", [oid, side, d, d])  # fmt: skip
    체결 = [_f(1, "buy", "2026-10-01", 100, 10, 1300, 1.0), _f(2, "sell", "2026-10-05", 110, 10, 1400, 1.1)]
    for f, 실현 in zip(체결, (None, 237_160.0), strict=True):
        c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity,"
                       " currency, fx_rate, fee, tax, slippage, cash_krw, created_at, realized_krw) VALUES (?, ?, ?, ?, ?,"
                       " ?, ?, ?, ?, ?, 0, 0, ?, 't', ?)", [f.order_id, f.account_id, f.stock_id, f.side, f.fill_date,
                                                           f.price, f.quantity, f.currency, f.fx_rate, f.fee, f.cash_krw,
                                                           실현])  # fmt: skip
    assert job.fill_realized_fx(c, 체결) == 1
    assert c.q("SELECT realized_fx_krw FROM paper_fills WHERE order_id = 2") == [(100_100.0,)]
    assert job.fill_realized_fx(c, 체결) == 0  # 다 채우면 남은 것 없음


def test_저장된_실현손익과_다시_센_것이_다르면_환차를_적지_않는다() -> None:
    """25.1695 (교차검증 [권고] 2) — 25.1620 전 체결은 다른 원가로 실현손익이 적혔을 수 있다. 그 위에 환차를 적으면 주가 몫이
    틀린다 — 적지 않고 화면에 "환차 모름" 으로 남긴다."""
    c = Client()
    for oid, side, d in ((1, "buy", "2026-10-01"), (2, "sell", "2026-10-05")):
        c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                       " decided_on, fill_session, reason, rule_version, created_at) VALUES (?, 'A', 'US', 2, ?,"
                       " 'market', 10, 'USD', ?, ?, 'x', 1, 't')", [oid, side, d, d])  # fmt: skip
    체결 = [_f(1, "buy", "2026-10-01", 100, 10, 1300, 1.0), _f(2, "sell", "2026-10-05", 110, 10, 1400, 1.1)]
    for f, 실현 in zip(체결, (None, 230_000.0), strict=True):  # 저장된 실현이 다시 센 237,160 과 다르다
        c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity,"
                       " currency, fx_rate, fee, tax, slippage, cash_krw, created_at, realized_krw) VALUES (?, ?, ?, ?, ?,"
                       " ?, ?, ?, ?, ?, 0, 0, ?, 't', ?)", [f.order_id, f.account_id, f.stock_id, f.side, f.fill_date,
                                                           f.price, f.quantity, f.currency, f.fx_rate, f.fee, f.cash_krw,
                                                           실현])  # fmt: skip
    assert job.fill_realized_fx(c, 체결) == 0
    assert c.q("SELECT realized_fx_krw FROM paper_fills WHERE order_id = 2") == [(None,)]
