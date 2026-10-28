"""가상 S(스윙)·T(당일 단타) — 미국 경로 (docs/paper.md 16.3.1·16.3.2, docs/infra.md 25.1898).

25.1890 교차검증 [권고]: S·T 는 국내·미국 두 배치에서 매매하는데(현금은 원화 하나, 미국 체결은 USDKRW 로 바꿈) 시험은 모두
`market="KR"` 였다. 여기서는 **0092 마이그레이션 행 그대로**(개설 2026-10-29) 미국 배치(`run_step(market="US")`)를 돌려
주문 → 체결 → 원화 현금 → 평가 행 검산까지 돈의 길을 본다.

미국 배치는 한국 저녁(미국 장 전)에 돈다 — 판정일 = 앞 미국 장, 주문 장 = 다음 미국 장, 실행 시계의 국내 날짜 = 그 다음
미국 장의 날짜(25.1626 의 `돌림` 과 같은 꼴). 시각은 정규장 시작(UTC 13:30·14:30) 전인 UTC 12:00.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _신호, 설정

#: 종목 2 = AAPL(미국·달러, tests/test_paper_job_1445.Client)
미국종목 = 2
입금 = 10_000_000  # 100만 원이면 비중 10% = 10만 원 ≈ 71달러라 50달러 종목을 1주밖에 못 산다
환율표 = {"2026-10-28": 1400.0, "2026-10-29": 1400.0, "2026-10-30": 1400.0, "2026-11-02": 1410.0, "2026-11-03": 1410.0}


@pytest.fixture(autouse=True)
def _고정(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


def _계좌() -> Client:
    """마이그레이션이 연 S·T 행 그대로(개설 10-29) — 행을 손으로 넣지 않는다"""
    c = Client(accounts=("S", "T"))
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', ?, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')", [입금])  # fmt: skip
    for d, r in 환율표.items():
        c.conn.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')",
                       [d, r])  # fmt: skip
    return c


def _시세(c: Client, day: str, open_: float, close: float) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 1000, 'USD', 't', 't')",
        [미국종목, day, open_, max(open_, close), min(open_, close), close])  # fmt: skip


def _미국신호() -> report_picks.SignalRow:
    return dataclasses.replace(
        _신호(), stock_id=미국종목, ticker="AAPL", name="종목2", market="NASDAQ", currency="USD", buy_zone_low=48,
        buy_zone_high=50, tranche_plan=[{"step": 1, "ratio": 0.4, "price": 50, "amount": None},
                                        {"step": 2, "ratio": 0.3, "price": 49, "amount": None},
                                        {"step": 3, "ratio": 0.3, "price": 48, "amount": None}],
        as_of_date="2026-10-28", close=50, close_date="2026-10-28",
    )  # fmt: skip


def _미국(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, signals: bool = False) -> dict:
    """미국 배치 한 번 — 판정일 `trade_date`(미국 장), 주문은 다음 미국 장. 실행 시계는 그 다음 장 날짜의 한국 저녁"""
    다음 = cal.next_session("US", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    now = datetime.fromisoformat(f"{다음}T12:00:00+00:00")
    assert now < cal.session_open_utc("US", 다음)  # 장 전 실행 — 주문 기준 시가
    _줄, 기록 = job.run_step(
        c, market="US", trade_date=trade_date, late=False, signal_rows=[_미국신호()] if signals else [],
        settings=설정, price_series={}, series_unknown={}, allocate=signals, now=now,
    )  # fmt: skip
    return 기록


def _현금(price: float, qty: int, side: str, day: str) -> float:
    """엔진과 같은 비용 정의처로 낸 원화 현금 영향 — 수수료는 `_수수료`(쪽마다), 슬리피지·세금은 `costs_for`"""
    비용 = job.costs_for("US", None, None, day)
    매수율, 매도율 = job._수수료("US", None)
    assert (비용.buy_fee_pct, 비용.sell_fee_pct) == (매수율, 매도율)
    총 = price * qty
    if side == "buy":
        return -round(총 * (1 + 비용.slippage_pct / 100 + 매수율 / 100) * 환율표[day])
    return round(총 * (1 - 비용.slippage_pct / 100 - 매도율 / 100 - 비용.sell_tax_pct / 100) * 환율표[day])


def _검산(c: Client, aid: str, as_of: str) -> tuple:
    return c.q("SELECT recon_ok, recon_note, cash_krw FROM paper_daily WHERE account_id = ? AND as_of = ?",
               [aid, as_of])[0]  # fmt: skip


def test_ST_는_개설일_전_미국_장에_매매하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1898 (25.1890 교차검증 [권고] — S·T 미국 경로 시험 없음). 개설일 막이(25.1891)가 미국 배치에서도 선다 —
    주문 장 10-28(개설 전날)이면 평가·매매하지 않고, 주문 장 10-29 부터 산다"""
    c = _계좌()
    기록 = _미국(c, monkeypatch, "2026-10-27", signals=True)
    assert 기록["accounts"] == {"S": {"not_open_until": "2026-10-29"}, "T": {"not_open_until": "2026-10-29"}}
    assert c.q("SELECT COUNT(*) FROM paper_orders") == [(0,)] and c.q("SELECT COUNT(*) FROM paper_daily") == [(0,)]
    _시세(c, "2026-10-28", 50, 50)
    _미국(c, monkeypatch, "2026-10-28", signals=True)
    assert sorted(c.q("SELECT DISTINCT account_id FROM paper_orders")) == [("S",), ("T",)]


def test_T_는_미국에서도_시가에_사고_같은_장_종가에_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1898 (25.1890 교차검증 [권고] — S·T 미국 경로 시험 없음). 짝 매도(종가 판정·회차 0·`단타종가`)가 달러로 나가고,
    다음 실행에서 둘 다 같은 날 찬다. 현금 변화 = (종가−시가)×수량×환율 − 쪽마다 수수료·슬리피지, 하루 끝 보유 0, 검산 맞음"""
    c = _계좌()
    _시세(c, "2026-10-28", 50, 50)
    _미국(c, monkeypatch, "2026-10-28", signals=True)
    주문 = c.q("SELECT market, stock_id, side, order_type, currency, fill_session, fill_basis, tranche, reason, quantity,"
               " rationale_json FROM paper_orders WHERE account_id = 'T' ORDER BY side")  # fmt: skip
    assert [r[:9] for r in 주문] == [
        ("US", 미국종목, "buy", "market", "USD", "2026-10-29", "open", 1, "2부 배분"),
        ("US", 미국종목, "sell", "market", "USD", "2026-10-29", "close", 0, pp.DAY_CLOSE_CODE)]  # fmt: skip
    수량 = 주문[0][9]
    assert 수량 > 0 and 주문[1][9] == 수량  # 산 만큼 판다
    assert json.loads(주문[1][10])["rule"] == "day_v1"

    시가, 종가 = 50.0, 51.0
    _시세(c, "2026-10-29", 시가, 종가)
    기록 = _미국(c, monkeypatch, "2026-10-29")
    체결 = c.q("SELECT side, price, quantity, currency, fx_rate, fx_date, fill_date, cash_krw FROM paper_fills"
               " WHERE account_id = 'T' ORDER BY id")  # fmt: skip
    assert [r[:7] for r in 체결] == [
        ("buy", 시가, 수량, "USD", 1400.0, "2026-10-29", "2026-10-29"),
        ("sell", 종가, 수량, "USD", 1400.0, "2026-10-29", "2026-10-29")]  # fmt: skip
    # 쪽마다 원화 현금 — 엔진 비용 정의처로 다시 낸 값과 같다(환율을 빼먹으면 1,400 배 어긋난다)
    assert [r[7] for r in 체결] == [_현금(시가, 수량, "buy", "2026-10-29"), _현금(종가, 수량, "sell", "2026-10-29")]
    비용 = job.costs_for("US", None, None, "2026-10-29")
    수수료 = (시가 * (비용.buy_fee_pct + 비용.slippage_pct) + 종가 * (비용.sell_fee_pct + 비용.slippage_pct)) / 100
    변화 = sum(r[7] for r in 체결)
    assert 변화 == pytest.approx((종가 - 시가) * 수량 * 1400 - 수수료 * 수량 * 1400, abs=1)
    assert c.q("SELECT realized_krw FROM paper_fills WHERE account_id = 'T' AND side = 'sell'") == [(변화,)]
    # 하루 끝 보유 0 — 안전망 매도(단타잔여)도 없다
    fills, _ = job._fills(c)
    assert pp.positions([f for f in fills if f.account_id == "T"]) == {}
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T' AND reason = ?", [pp.DAY_LEFT_CODE]) == [(0,)]
    assert c.q("SELECT status FROM paper_orders WHERE account_id = 'T'") == [("filled",), ("filled",)]
    # 평가 행(한국 날짜 10-30): 현금 = 입금 + 변화, 보유 없음, 검산 맞음 — S 도
    assert 기록["accounts"]["T"]["recon_ok"] is True and 기록["accounts"]["S"]["recon_ok"] is True
    assert _검산(c, "T", "2026-10-30") == (1, "맞음", 입금 + 변화)
    assert _검산(c, "S", "2026-10-30")[:2] == (1, "맞음")
    assert c.q("SELECT positions_json FROM paper_daily WHERE account_id = 'T' AND as_of = '2026-10-30'") == [("[]",)]


def test_S_는_미국에서_시가에_사고_종가_손절선에_다음_장_시가로_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1898 (25.1890 교차검증 [권고] — S·T 미국 경로 시험 없음). 진입 시가 50달러 → 둘째 날 종가 48(≤ 48.5 = −3%)
    → 다음 미국 장(주말 건너 11-02) 시가 시장가 `스윙손절`. 매도는 그 장 환율(1,410)로, 환차 몫이 따로, 검산 맞음"""
    c = _계좌()
    _시세(c, "2026-10-28", 50, 50)
    _미국(c, monkeypatch, "2026-10-28", signals=True)
    assert c.q("SELECT market, side, order_type, currency, fill_session, fill_basis, tranche FROM paper_orders"
               " WHERE account_id = 'S'") == [("US", "buy", "market", "USD", "2026-10-29", "open", 1)]  # fmt: skip
    _시세(c, "2026-10-29", 50, 51)  # 첫날 종가 +2% — 선 안
    _미국(c, monkeypatch, "2026-10-29")
    수량, 매수현금 = c.q("SELECT quantity, cash_krw FROM paper_fills WHERE account_id = 'S' AND side = 'buy'")[0]
    assert c.q("SELECT price, currency, fx_rate FROM paper_fills WHERE account_id = 'S'") == [(50.0, "USD", 1400.0)]
    assert 매수현금 == _현금(50.0, 수량, "buy", "2026-10-29")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S' AND side = 'sell'") == [(0,)]

    _시세(c, "2026-10-30", 49, 48)  # 종가 48 ≤ 50 × (1 − 3%)
    _미국(c, monkeypatch, "2026-10-30")
    매도 = c.q("SELECT market, quantity, currency, fill_session, fill_basis, reason, rationale_json FROM paper_orders"
               " WHERE account_id = 'S' AND side = 'sell'")  # fmt: skip
    assert [r[:6] for r in 매도] == [("US", 수량, "USD", "2026-11-02", "open", pp.SWING_CODES["stop"])]
    assert json.loads(매도[0][6])["sell_rule"] == pp.SWING_RULE
    assert _검산(c, "S", "2026-11-02")[:2] == (1, "맞음")

    _시세(c, "2026-11-02", 47.5, 48)
    기록 = _미국(c, monkeypatch, "2026-11-02")
    판것 = c.q("SELECT price, fx_rate, fill_date, cash_krw, realized_krw, realized_fx_krw FROM paper_fills"
               " WHERE account_id = 'S' AND side = 'sell'")[0]  # fmt: skip
    assert 판것[:3] == (47.5, 1410.0, "2026-11-02")
    assert 판것[3] == _현금(47.5, 수량, "sell", "2026-11-02")
    assert 판것[4] == 판것[3] + 매수현금 < 0  # 실현손익 = 매도 현금 − 매수 원가(원)
    # 환차 몫 = 달러 원가 × 환율 변화(1,400 → 1,410) — 주가 손익과 나눈다(CLAUDE.md)
    assert 판것[5] == pytest.approx(-매수현금 / 1400 * 10, abs=1)
    fills, _ = job._fills(c)
    assert pp.positions([f for f in fills if f.account_id == "S"]) == {}
    assert 기록["accounts"]["S"]["recon_ok"] is True
    assert _검산(c, "S", "2026-11-03") == (1, "맞음", 입금 + 매수현금 + 판것[3])
