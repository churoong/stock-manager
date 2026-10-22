"""놓친 지정가 회차의 까닭 글 — 백만 원 넘는 가격도 지수 표기 없이 (docs/infra.md 25.1640, 10-22 운영 점검)."""

from __future__ import annotations

from batch.services import paper as pp


def _주문(limit: float) -> pp.Order:
    return pp.Order(id=1, account_id="A", stock_id=1, side="buy", order_type="limit", limit_price=limit, quantity=1,
                    currency="KRW", fill_session="2026-10-21")  # fmt: skip


def test_큰_가격도_그대로_적는다() -> None:
    _가격, 까닭 = pp.judge(_주문(1_271_000), pp.Bar("2026-10-21", 1_300_000.0, 1_294_000.0, 1000, 0.5))
    assert 까닭 == "종가 1294000 > 회차 가격 1271000"


def test_작은_값은_예전_글_그대로() -> None:
    assert pp.judge(_주문(167_700), pp.Bar("2026-10-21", 170_000.0, 169_600.0, 1000, 0.5))[1] == "종가 169600 > 회차 가격 167700"
    assert pp.judge(_주문(43.74), pp.Bar("2026-10-21", 44.0, 43.85, 1000, 0.5))[1] == "종가 43.85 > 회차 가격 43.74"
