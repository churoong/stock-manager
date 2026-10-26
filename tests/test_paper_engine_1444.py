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
    # 종목 통화 원가도 같은 비율로 던다 — 평균 단가·종목 통화 수익(달러 종목의 환차 뺀 수익)이 이 값을 쓴다
    # (25.1743 돌연변이 점검 `positions_cost_on_sell` 이 살아남았다 — 원화 원가만 보고 있었다)
    assert 보유[7].cost == pytest.approx(100_015 * 0.6)
    assert 보유[7].first_buy_date == "2026-10-16"
    assert 보유[7].horizon == "short" and 보유[7].score_at_buy == 71.0
    실현 = pp.realized(체결)
    assert 실현[0]["pnl_krw"] == round(체결[1].cash_krw - 100_015 * 0.4)


def test_sell_more_than_held_is_an_error() -> None:
    with pytest.raises(ValueError):
        pp.positions([pp.make_fill(_주문("sell", None), 10_000, "2026-10-16", 1.0, 비용)])
    # 들고 있는 것보다 많이 — 보유 없음과 다른 갈래(25.1743 돌연변이 점검 `positions_oversell` 이 살아남았다)
    with pytest.raises(ValueError, match="보유보다 많은"):
        pp.positions([pp.make_fill(_주문(qty=3), 10_000, "2026-10-16", 1.0, 비용),
                      pp.make_fill(_주문("sell", None, qty=5, oid=2), 10_000, "2026-10-17", 1.0, 비용)])  # fmt: skip


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
    assert v2.rows == [{"stock_id": 7, "quantity": 10, "currency": "KRW", "cost_krw": 100_015, "value_krw": 120_000,
                        "price_date": "2026-10-17", "horizon": "short", "avg_price": pytest.approx(10_001.5),
                        "close": 12_000, "first_buy_date": "2026-10-16", "unrealized_krw": 19_985,
                        "unrealized_pct": pytest.approx((120_000 / 100_015 - 1) * 100),
                        "unrealized_local_pct": pytest.approx((12_000 / 10_001.5 - 1) * 100), "unknown_why": None,
                        "weight_pct": pytest.approx(120_000 / v2.equity_krw * 100)}]  # fmt: skip
    # 기업행위로 멈춘 종목은 종가가 있어도 원가로 — 분할 날 종가 × 분할 전 수량은 거짓 손실이다
    v3 = pp.value(흐름, 체결, {7: (2_400, "2026-10-17")}, {}, frozen={7})
    assert v3.holdings_krw == 100_015 and v3.unknown == [{"stock_id": 7, "why": "기업행위(멈춤)"}]
    # 원가로 둔 줄은 까닭을 줄에도 — 화면이 "0원 · +0.0%" 대신 까닭을 보인다(25.1604)
    assert v3.rows[0]["unknown_why"] == "기업행위(멈춤)" and v3.rows[0]["unrealized_local_pct"] is None


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


def test_usd_sizing_matches_reservation_so_first_tranche_is_not_starved() -> None:
    """체결 때 남은 달러 회차를 환율 여유까지 빼므로 수량도 같은 잣대로 낸다 (25.1447, 교차검증 [필수] 2)."""
    # 검증 재현: 현금 100만 원·환율 1,400 에서 회차 금액이 현금 안에 거의 다 드는 날
    배분 = [{"stock_id": 9, "amount": 710, "tranches": [{"price_at_or_below": 10, "amount": 300},
                                                      {"price_at_or_below": 9.8, "amount": 250},
                                                      {"price_at_or_below": 9.6, "amount": 160}]}]  # fmt: skip
    주문, _ = pp.size_buys(배분, 1_000_000, 1_400.0, set(), "USD")
    os_ = [pp.Order(id=i, account_id="A", stock_id=9, side="buy", order_type="limit", limit_price=o["limit_price"],
                    quantity=o["quantity"], currency="USD", fill_session="d") for i, o in enumerate(주문, 1)]  # fmt: skip
    첫 = pp.make_fill(os_[0], 10, "d", 1_400.0, 비용)
    남은예약 = pp.reserved_krw(os_[1:], {"USD": 1_400.0})
    assert 1_000_000 + 첫.cash_krw - (남은예약 or 0) >= 0


def test_after_parses_both_timestamp_shapes() -> None:
    assert pp.after("2026-10-15T14:00:00.000Z", "2026-10-15T00:30:00+00:00")
    assert not pp.after("2026-10-15T00:29:59Z", "2026-10-15T00:30:00.000001+00:00")
    assert pp.after("2026-10-15 14:00:00", "2026-10-15T00:30:00+00:00")


def test_compare_real_uses_the_same_window() -> None:
    """실제 투자와 비교 (2026-10-15 사용자 지시 "실제 투자랑도 비교하는거 그냥 키자", 25.1448)."""
    실제 = [("2026-09-01", 1.0), ("2026-10-14", 1.10), ("2026-10-20", 1.21)]
    모의 = {"A": [("2026-10-15", 1.0), ("2026-10-21", 1.05)], "B": [("2026-10-15", 1.0), ("2026-10-21", 1.02)]}
    c = pp.compare_real(모의, 실제)
    # 기간은 늦게 시작한 쪽(10-15)부터 일찍 끝난 쪽(10-20)까지 — 실제는 10-15 이하 마지막(10-14, 1.10)에서 10-20(1.21)
    assert c is not None and (c["start"], c["end"]) == ("2026-10-14", "2026-10-20")
    assert c["real_pct"] == pytest.approx(10.0)
    assert c["paper_pct"] == {"A": pytest.approx(0.0), "B": pytest.approx(0.0)}  # 모의는 10-20 이하 마지막 = 10-15
    줄 = pp.compare_line(c)
    assert 줄 is not None and "실제 +10.0%" in 줄 and "A +0.0%" in 줄 and "보유분만" in 줄
    assert pp.compare_real(모의, []) is None and pp.compare_real({}, 실제) is None
    assert pp.compare_line(None) is None


def test_window_change_edges() -> None:
    rows = [("2026-10-01", 1.0), ("2026-10-05", 1.2)]
    assert pp.window_change(rows, "2026-09-01", "2026-10-31") == (pytest.approx(20.0), "2026-10-01", "2026-10-05")
    assert pp.window_change(rows, "2026-10-06", "2026-10-31") == (pytest.approx(0.0), "2026-10-05", "2026-10-05")
    assert pp.window_change(rows, "2026-10-02", "2026-09-30") is None


def test_decision_outcomes_and_due_windows() -> None:
    """결정 원장 성과 (25.1449) — 거래일 수가 찬 창만, 지수는 그 날짜 이하 마지막 값으로."""
    뒤 = [(f"2026-10-{d:02d}", 100 + d) for d in range(1, 26)]  # 25 거래일
    o = pp.outcomes(100.0, 뒤, 2_000.0, {"2026-10-05": 2_100.0, "2026-10-20": None})
    assert o["ret_5d"] == pytest.approx(5.0) and o["bench_5d"] == pytest.approx(5.0)
    assert o["ret_20d"] == pytest.approx(20.0) and o["bench_20d"] is None
    assert "ret_60d" not in o  # 60 거래일은 아직
    assert pp.outcomes(None, 뒤, 1.0, {}) == {}
    assert pp.due_horizons("2026-10-01", "2026-10-07", {}) == []
    assert pp.due_horizons("2026-10-01", "2026-10-08", {}) == [5]
    assert pp.due_horizons("2026-10-01", "2026-12-31", {"ret_5d": 1.0}) == [20, 60]


def test_attribution_splits_selection_entry_exit() -> None:
    """성과 분해 (25.1450) — 모두 20 거래일 뒤 지수 대비 초과(%p), 진입은 체결가 대비 그날 종가(%)."""
    def 결(kind: str, ret: float, bench: float, reason: str = "x") -> dict:
        return {"kind": kind, "reason": reason, "ret_20d": ret, "bench_20d": bench}

    결정 = [결("buy", 10, 2), 결("buy", -4, 2), 결("skip", 8, 2, "종목 비중 상한 도달"), 결("skip", 1, 2, "현금 부족"),
          결("missed", 12, 2), 결("sell", -6, -1), 결("hold_flag", 3, 1), {"kind": "buy", "ret_20d": None, "bench_20d": None}]
    a = pp.attribution(결정, [(10_000, 10_100), (9_800, 9_800), (100, None)])
    assert a["selection"] == {"n": 2, "avg": pytest.approx(1.0), "hit": pytest.approx(50.0)}
    assert a["skipped"]["avg"] == pytest.approx(-1.0)  # 보유 상한 도달(지난 승자)은 새 후보와 견주지 않는다
    assert a["skip_by_reason"]["종목 비중 상한 도달"]["avg"] == pytest.approx(6.0)
    assert a["entry"] == {"n": 2, "avg": pytest.approx(0.5)}
    assert a["missed"]["avg"] == pytest.approx(10.0)
    assert a["sold"]["avg"] == pytest.approx(-5.0) and a["held_flag"]["avg"] == pytest.approx(2.0)
    줄 = pp.attribution_lines("A", a)
    assert "주문 낸 새 후보 +1.0%p (n=2, 지수보다 오른 비율 50%, 참고)" in 줄[0]
    assert any("체결가가 그날 종가보다 평균 0.50% 쌌다 (n=2)" in x for x in 줄)
    assert pp.attribution_lines("A", None) == []
    빈 = pp.attribution([], [])
    assert pp.attribution_lines("B", 빈)[0].endswith("표본 없음 · 안 낸 새 후보 표본 없음")



def test_attribution_counts_episodes_not_days() -> None:
    """같은 종목이 이어지는 날마다 쌓여도 한 번 — 28일 지나 다시 나오면 새 표본 (25.1453, 교차검증 [필수] 4)."""
    줄 = [{"stock_id": 1, "decided_on": f"2026-09-{d:02d}", "kind": "buy", "ret_20d": 5, "bench_20d": 0} for d in range(1, 21)]
    줄.append({"stock_id": 1, "decided_on": "2026-10-20", "kind": "buy", "ret_20d": -5, "bench_20d": 0})
    줄.append({"stock_id": 2, "decided_on": "2026-09-02", "kind": "buy", "ret_20d": 1, "bench_20d": 0})
    a = pp.attribution(줄, [])
    assert a["selection"]["n"] == 3 and a["selection"]["avg"] == pytest.approx(1 / 3)
