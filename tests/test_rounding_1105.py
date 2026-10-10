"""표시 반올림을 웹 `toFixed` 와 같게 (docs/infra.md 25.1105, 리포트 감사 재현).

파이썬 서식은 .5 를 짝수 쪽으로 보내 같은 종목이 텔레그램 팩터 줄 72·근거 문장 73·웹 73 으로 갈렸다."""

from __future__ import annotations

from batch.core.rounding import fixed, half_up
from batch.notify import report_sections as rs
from batch.services import holding_scores as hs
from batch.services import sell_flags as sf


def test_toFixed_와_같은_값() -> None:
    # JS: (72.5).toFixed(0)="73", (2.25).toFixed(1)="2.3", (-12.5).toFixed(0)="-13", (1.005).toFixed(2)="1.00"
    assert [fixed(72.5), fixed(2.25, 1), fixed(-12.5), fixed(1.005, 2)] == ["73", "2.3", "-13", "1.00"]
    assert fixed(-0.3, sign=True) == "+0" and fixed(12.5, sign=True) == "+13" and half_up(-0.4) == 0.0


def test_리포트_점수_줄이_근거_문장과_같다() -> None:
    assert rs._score(72.5) == "73" and rs._score(None) == "-"


def test_보유_점수와_매도_플래그_문장도_올린다() -> None:
    h = hs.HoldingScore(name="가", market="KOSPI", score_now=60.5, rank_now=None, as_of="2026-10-08",
                        score_at_trade=70.5, first_buy="2026-09-01")  # fmt: skip
    글 = hs.line(h)
    assert "지금 61점" in 글 and "매수 때 71점" in 글 and "→ -10점" in 글
    flags = sf.evaluate(sf.HoldingInput(stock_id=1, name="가", horizon="long", first_buy_date="2026-01-02",
                                        currency="KRW", cost=1.0, market_value=1.0, price_date="2026-10-08",
                                        score_at_buy=80.5, score_now=60.5, score_now_date="2026-10-08"),
                        __import__("datetime").date(2026, 10, 9))  # fmt: skip
    assert any("종합 점수 81→61점" in f.rationale_text for f in flags)
