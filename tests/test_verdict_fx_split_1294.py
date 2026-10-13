"""종목 분석 판정 머리의 평가손익이 해외 종목은 주가·환차·원화 합계로 나뉜다 (docs/infra.md 25.1294, 손익 감사 #1)."""

from __future__ import annotations

from batch.services import verdict as vd

# 10주 $100 · 환율 1,300 에 사서 지금 $90 · 1,495 — 주가 −10%, 원화 합계 +3.5%
POS = {"quantity": 10, "price_date": "2026-10-12", "unrealized_pnl": -100.0, "cost": 1000.0,
       "unrealized_pnl_krw": 45500.0, "cost_krw": 1300000.0, "unrealized_fx_pnl_krw": 175500.0}  # fmt: skip


def _머리(pos: dict | None, currency: str) -> str:
    return vd.build(vd.Inputs(name="x", ticker="X", currency=currency, position=vd.position_of(pos)))["headline"]


def test_해외_보유는_주가와_환차를_나눈다() -> None:
    h = _머리(POS, "USD")
    assert "주가 -10.0%" in h and "환차 +175,500원" in h and "원화 합계 +3.5%" in h
    assert "평가손익률 +3.5%" not in h


def test_원화_종목은_예전처럼() -> None:
    kr = {"quantity": 1, "price_date": "2026-10-12", "unrealized_pnl": 3.25, "cost": 100.0,
          "unrealized_pnl_krw": 3.25, "cost_krw": 100.0, "unrealized_fx_pnl_krw": 0.0}  # fmt: skip
    assert "평가손익률 +3.2%" in _머리(kr, "KRW") or "평가손익률 +3.3%" in _머리(kr, "KRW")


def test_환율이_없어도_주가_손익률은_낸다() -> None:
    h = _머리({**POS, "unrealized_pnl_krw": None, "unrealized_fx_pnl_krw": None}, "USD")
    assert "주가 -10.0%" in h and "원화 합계" not in h
