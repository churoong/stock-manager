"""모의 투자 엔진 (docs/paper.md, docs/infra.md 25.1444) — 체결·장부·검산·시간가중·그림자 기준."""

from __future__ import annotations

import pytest

from batch.services import paper as pp

비용 = pp.Costs(buy_fee_pct=0.015, sell_fee_pct=0.015, sell_tax_pct=0.15, slippage_pct=0.10)


def _주문(side: str = "buy", limit: float | None = 10_000, qty: int = 10, oid: int = 1, sid: int = 7,
         cur: str = "KRW") -> pp.Order:  # fmt: skip
    return pp.Order(id=oid, account_id="A", stock_id=sid, side=side, order_type="limit" if side == "buy" else "market",
                    limit_price=limit, quantity=qty, currency=cur, fill_session="2026-10-16", horizon="short",
                    score_at_order=71.0, score_as_of="2026-10-15")  # fmt: skip


def _입금(*금액: float) -> list[pp.CashFlow]:
    return [pp.CashFlow(id=i + 1, flow_date=f"2026-10-{15 + i}", amount_krw=a, kind="deposit") for i, a in enumerate(금액)]


def test_limit_buy_fills_only_when_close_at_or_below() -> None:
    assert pp.judge(_주문(), pp.Bar("2026-10-16", 10_500, 10_000))[0] == 10_000  # 경계 포함
    assert pp.judge(_주문(), pp.Bar("2026-10-16", 9_000, 10_050))[0] is None  # 장중 저가로 찼어도 놓친다(보수)
    assert pp.judge(_주문(), None)[0] is None
    # 정지한 날(거래량 0·시가 0)은 종가가 전일 값이라 거짓으로 찬다 — 안 찬 것으로 (검증 [필수] 5)
    assert pp.judge(_주문(), pp.Bar("2026-10-16", 0, 9_000, 0))[0] is None
    assert pp.judge(_주문(), pp.Bar("2026-10-16", 9_500, 9_000, 0))[0] is None


def test_market_sell_uses_open_then_close() -> None:
    assert pp.judge(_주문("sell", None), pp.Bar("2026-10-16", 9_800, 9_900))[0] == 9_800
    assert pp.judge(_주문("sell", None), pp.Bar("2026-10-16", None, 9_900))[0] == 9_900
    # 장 열린 뒤 낸 주문은 시가가 주문보다 앞선 값 — 종가 (검증 [필수] 4)
    늦은 = pp.Order(**{**_주문("sell", None).__dict__, "fill_basis": "close"})
    assert pp.judge(늦은, pp.Bar("2026-10-16", 9_800, 9_900))[0] == 9_900


def test_fill_costs_are_adverse() -> None:
    매수 = pp.make_fill(_주문(), 10_000, "2026-10-16", 1.0, 비용)
    # 100,000 + 수수료 15 — 지정가는 그 값보다 비싸게 찰 수 없어 슬리피지 없음
    assert 매수.cash_krw == -100_015 and 매수.slippage == 0
    매도 = pp.make_fill(_주문("sell", None), 10_000, "2026-10-17", 1.0, 비용)
    # 100,000 − 100 − 15 − 150
    assert 매도.cash_krw == 99_735
    달러 = pp.make_fill(_주문(cur="USD", limit=100), 100, "2026-10-16", 1_400.0, 비용)
    assert 달러.cash_krw == round(-(1_000 + 0.15) * 1_400)


def test_ledger_cash_positions_and_realized() -> None:
    흐름 = _입금(1_000_000)
    체결 = [pp.make_fill(_주문(qty=10), 10_000, "2026-10-16", 1.0, 비용),
            pp.make_fill(_주문("sell", None, qty=4, oid=2), 11_000, "2026-10-17", 1.0, 비용)]  # fmt: skip
    현금 = pp.cash(흐름, 체결)
    assert 현금 == 1_000_000 - 100_015 + 체결[1].cash_krw
    보유 = pp.positions(체결)
    assert 보유[7].quantity == 6
    assert 보유[7].cost_krw == pytest.approx(100_015 * 0.6)
    assert 보유[7].first_buy_date == "2026-10-16"
    assert 보유[7].horizon == "short" and 보유[7].score_at_buy == 71.0
    실현 = pp.realized(체결)
    assert 실현[0]["pnl_krw"] == round(체결[1].cash_krw - 100_015 * 0.4)


def test_sell_more_than_held_is_an_error() -> None:
    with pytest.raises(ValueError):
        pp.positions([pp.make_fill(_주문("sell", None), 10_000, "2026-10-16", 1.0, 비용)])


def test_full_sell_resets_basis() -> None:
    체결 = [pp.make_fill(_주문(qty=5), 10_000, "2026-10-16", 1.0, 비용),
            pp.make_fill(_주문("sell", None, qty=5, oid=2), 10_000, "2026-10-17", 1.0, 비용),
            pp.make_fill(_주문(qty=3, oid=3), 9_000, "2026-10-20", 1.0, 비용)]  # fmt: skip
    p = pp.positions(체결)[7]
    assert p.quantity == 3 and p.first_buy_date == "2026-10-20"


def test_valuation_unknown_price_uses_cost_not_zero() -> None:
    흐름 = _입금(1_000_000)
    체결 = [pp.make_fill(_주문(), 10_000, "2026-10-16", 1.0, 비용)]
    v = pp.value(흐름, 체결, {}, {})
    assert v.holdings_krw == 100_015 and v.unknown == [{"stock_id": 7, "why": "종가 없음"}]
    v2 = pp.value(흐름, 체결, {7: (12_000, "2026-10-17")}, {})
    assert v2.equity_krw == 1_000_000 - 100_015 + 120_000
    assert v2.pnl_krw == v2.equity_krw - 1_000_000
    assert v2.pnl_pct == pytest.approx(v2.pnl_krw / 10_000)


def test_invested_counts_cancel_rows() -> None:
    흐름 = [*_입금(1_000_000, 500_000), pp.CashFlow(3, "2026-10-17", -500_000, "cancel", cancels_id=2)]
    assert pp.invested(흐름) == 1_000_000


def test_cancellable_rules() -> None:
    흐름 = [*_입금(1_000_000, 500_000), pp.CashFlow(3, "2026-10-17", -500_000, "cancel", cancels_id=2)]
    assert pp.cancellable(흐름, 2, {"A": 9e9}) == "이미 취소한 입금입니다"
    assert pp.cancellable(흐름, 3, {"A": 9e9}) == "취소 행은 다시 취소할 수 없습니다"
    assert pp.cancellable(흐름, 9, {"A": 9e9}) == "없는 입금입니다"
    assert "계좌 B" in str(pp.cancellable(흐름, 1, {"A": 2_000_000, "B": 10}))
    assert pp.cancellable(흐름, 1, {"A": 1_000_000, "B": 1_000_000}) is None


def test_reconcile_flags_tampered_ledger() -> None:
    assert pp.reconcile(None, 0, 0, 5)[0] is True
    assert pp.reconcile(1_000_000, 0, -100_015, 899_985)[0] is True
    ok, 글 = pp.reconcile(1_000_000, 0, -100_015, 1_000_000)  # 체결 행이 지워졌다
    assert ok is False and "장부 현금" in 글


def test_twr_ignores_deposit_timing() -> None:
    # 100만 → 110만(+10%) → 그날 100만 입금해 210만 → 231만(+10%) = 지수 1.21
    i1 = pp.twr_step(None, None, 1_000_000, 1_000_000)
    i2 = pp.twr_step(i1, 1_000_000, 1_100_000, 0)
    i3 = pp.twr_step(i2, 1_100_000, 2_100_000, 1_000_000)
    i4 = pp.twr_step(i3, 2_100_000, 2_310_000, 0)
    assert i4 == pytest.approx(1.21)


def test_shadow_benchmark_matches_flows_and_refuses_unknown() -> None:
    흐름 = _입금(1_000_000, 1_000_000)
    assert pp.shadow(흐름, {1: 2_500, 2: 2_000}, 2_500) == round(1_000_000 + 1_250_000)
    assert pp.shadow(흐름, {1: 2_500}, 2_500) is None  # 하나라도 모르면 내지 않는다
    # 취소는 원래 입금이 산 단위를 되돌린다 — 그날 값으로 팔지 않는다
    취소 = [*흐름, pp.CashFlow(3, "2026-10-20", -1_000_000, "cancel", cancels_id=2)]
    assert pp.shadow(취소, {1: 2_500, 2: 2_000, 3: 9_999}, 2_500) == 1_000_000


def test_sell_rules_twin_accounts() -> None:
    플래그 = {1: {"손절"}, 2: {"재무악화"}, 3: {"감성급락", "기간초과"}}
    assert pp.sells_for("all", 플래그) == {1: ["손절"], 2: ["재무악화"], 3: ["감성급락", "기간초과"]}
    assert pp.sells_for("core", 플래그) == {1: ["손절"], 3: ["기간초과"]}


def test_size_buys_integer_shares_within_cash_in_given_order() -> None:
    배분 = [
        {"stock_id": 1, "amount": 300_000, "tranches": [{"price_at_or_below": 50_000, "amount": 120_000},
                                                      {"price_at_or_below": 48_000, "amount": 90_000},
                                                      {"price_at_or_below": 46_000, "amount": 90_000}]},
        {"stock_id": 2, "amount": 100_000, "tranches": [{"price_at_or_below": 30_000, "amount": 40_000}]},
        {"stock_id": 3, "amount": 900_000, "tranches": [{"price_at_or_below": 1_000_000, "amount": 360_000}]},
    ]  # fmt: skip
    주문, 빠짐 = pp.size_buys(배분, 120_000, 1.0, sold_recently={2})
    # 순서는 2부가 낸 그대로 — 3 은 1주 100만 원이라 회차 금액이 1주에 못 미친다
    assert {"stock_id": 3, "tranche": 1, "why": "회차 금액 < 1주"} in 빠짐
    assert {"stock_id": 2, "why": "판 뒤 5거래일 안"} in 빠짐
    assert 주문[0] == {"stock_id": 1, "tranche": 1, "limit_price": 50_000, "quantity": 2}
    assert sum(o["quantity"] * o["limit_price"] * 1.01 for o in 주문) <= 120_000
    assert all(isinstance(o["quantity"], int) for o in 주문)
    assert any(b.get("why") == "현금 부족" for b in 빠짐)


def test_size_buys_usd_converts_cash() -> None:
    주문, _ = pp.size_buys([{"stock_id": 9, "amount": 1_000, "tranches": [{"price_at_or_below": 100, "amount": 400}]}],
                          1_400_000, 1_400.0, set())  # fmt: skip
    assert 주문 == [{"stock_id": 9, "tranche": 1, "limit_price": 100, "quantity": 4}]


def test_reserved_includes_buffers_and_refuses_unknown_fx() -> None:
    원화 = _주문(qty=10)
    달러 = _주문(cur="USD", limit=100, qty=4, oid=2)
    assert pp.reserved_krw([원화], {}) == round(100_000 * 1.01)
    assert pp.reserved_krw([원화, 달러], {"USD": 1_400.0}) == round(100_000 * 1.01 + 400 * 1.01 * 1_400 * 1.03)
    assert pp.reserved_krw([달러], {}) is None  # 모르는 예약을 0 으로 치면 같은 현금을 두 번 쓴다


def test_paper_modules_never_touch_real_ledgers_or_order_api() -> None:
    """모의 모듈은 실제 매매·보유·매도 플래그 표와 증권사 주문 API 를 건드리지 않는다 (docs/paper.md 0장)."""
    import re
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parent.parent / "batch"
    파일들 = [뿌리 / "services" / "paper.py", *(p for p in [뿌리 / "jobs" / "paper.py"] if p.exists())]
    금지 = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(trades|positions|sell_flags)\b|order-cash|order-credit"
                    r"|TTTC0802U|TTTC0801U|VTTC", re.I)  # fmt: skip
    for f in 파일들:
        글 = f.read_text(encoding="utf-8")
        assert not 금지.search(글), f"{f.name} 가 실제 장부·주문 API 를 건드린다"
        assert not re.search(r"\bFROM\s+(trades|positions)\b", 글, re.I), f"{f.name} 가 실제 매매·보유를 읽는다"
