"""1부의 같은 발행사 두 종목 — 기록과 주석만 (docs/infra.md 25.1385, 25회차 P2)."""

from __future__ import annotations

from batch.services import report_picks as rp
from tests.test_report_picks import row


def _미국(sid: int, ticker: str, q: float, g: float, score: float) -> rp.SignalRow:
    r = row(sid, score=score)
    r.ticker, r.market, r.currency = ticker, "NASDAQ", "USD"
    r.factor_scores = {"quality": q, "growth": g, "value": 50.0}
    return r


def test_열쇠와_퀄리티_성장이_같을_때만_짝() -> None:
    goog, googl = _미국(1, "GOOG", 80.0, 70.0, 90.0), _미국(2, "GOOGL", 80.0, 70.0, 89.0)
    다른 = _미국(3, "GOOD", 60.0, 40.0, 88.0)  # 앞 4글자 같은 다른 회사 — 재무 점수가 다르다
    assert rp.same_issuer_pairs([goog, googl, 다른]) == [("GOOG", "GOOGL")]
    # 국내 보통·우선주(앞 5자리)도
    a, b = row(10, score=80.0), row(11, score=79.0)
    a.ticker, b.ticker = "005930", "005935"
    a.factor_scores = b.factor_scores = {"quality": 70.0, "growth": 60.0}
    assert rp.same_issuer_pairs([a, b]) == [("005930", "005935")]
    # 점수가 비면 짝으로 보지 않는다
    b.factor_scores = {"quality": None, "growth": 60.0}
    assert rp.same_issuer_pairs([a, b]) == []


def test_1부_끝에_한_줄_고른_종목은_그대로() -> None:
    goog, googl = _미국(1, "GOOG", 80.0, 70.0, 90.0), _미국(2, "GOOGL", 80.0, 70.0, 89.0)
    out = rp.compose([goog, googl], 10_000_000, "2026-09-16", currency="USD")
    assert "GOOG·GOOGL 는 같은 발행사로 보입니다" in out.text
    assert {r.stock_id for r in out.chosen} == {1, 2}
