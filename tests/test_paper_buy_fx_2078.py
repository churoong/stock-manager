"""미국 매수 주문의 환율 방향 — 현금 검사·위험 점검 금액·열린 매수의 상한 몫 (25.2078, 25.2073 교차검증 [권고]).

25.2073 의 고정 환율 1,300 일치 판은 명목 자본이 커서(투자 비중 0.6 이하) 현금 모자람·위험 점검 막음 자리를 지나지 않는다 —
`buy_orders` 의 `cost_krw`·`cash_krw` 의 × 환율, `size_buys` 의 현금 ÷ 환율을 빼도 지났다. 그 자리를 손 사례로 묶는다:

- 위험 점검 금액(`cost_krw`)은 원화 — 자본 대비 비중이 배분 비중(10%) 그대로다. × 환율을 빼면 1/1,300 로 읽혀 막을 것을 못 막는다
- 현금 검사 금액(`cash_krw`)도 원화 — 예약과 같은 잣대(수수료·환율 여유)
- 현금이 빠듯한 날 `size_buys` 는 원화 현금을 달러로 바꿔 그 안에서만 산다(÷ 환율 빼면 1,300 배를 산다, 두 번이면 0주)
- 열린 달러 매수는 원화로 센 뒤 ÷ 환율로 달러 보유처럼 섹터 상한에 들어간다

`buy_orders` 의 `남은예약 / 환율`(보유 목록에 못 넣은 예약)은 이 시험 밖이다 — `reserved_by_stock` 이 매수 주문을 모두 종목별로
세므로 남은 몫은 반올림 차(주문 수 × 0.5원 이하)다(이름 붙은 죽은 갈래). `rationale.reserved` 는 기록 칸뿐이다.
"""

from __future__ import annotations

import pytest

from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client

환율 = 1300.0
여유 = (1 + pp.FEE_BUFFER) * (1 + pp.FX_BUFFER)  # 달러 주문의 예약 잣대


@pytest.fixture(autouse=True)
def _고른대로(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)


def _신호(sid: int = 1) -> report_picks.SignalRow:
    return report_picks.SignalRow(
        stock_id=sid, ticker=f"T{sid}", name=f"종목{sid}", market="NASDAQ", horizon="short", signal_type="단기",
        currency="USD", buy_zone_low=96.0, buy_zone_high=100.0, suggested_weight_pct=10.0, suggested_amount=None,
        size_reduction=1.0,
        tranche_plan=[{"step": 1, "ratio": 0.4, "price": 100.0, "amount": None},
                      {"step": 2, "ratio": 0.3, "price": 98.0, "amount": None},
                      {"step": 3, "ratio": 0.3, "price": 96.0, "amount": None}],
        as_of_date="2026-10-14", total_score=72.0, sector="기술", close=100.0, close_date="2026-10-14",
    )  # fmt: skip


def _캐시(*sids: int) -> dict[int, dict]:
    return {s: {"id": s, "ticker": f"T{s}", "name": f"종목{s}", "sector": "기술", "country": "US", "currency": "USD",
                "asset_type": "stock"} for s in sids}  # fmt: skip


def _평가(자본_krw: float, 현금_krw: float) -> pp.Valuation:
    return pp.Valuation(invested_krw=자본_krw, cash_krw=현금_krw, holdings_krw=자본_krw - 현금_krw, equity_krw=자본_krw,
                        pnl_krw=0, pnl_pct=0, rows=[])  # fmt: skip


def _사기(v: pp.Valuation, 신호: list, 설정: dict | None = None, open_buys: list | None = None, sids=(1,)):
    설정 = 설정 or {"max_weight_per_sector": 30.0, "max_weight_per_stock": 10.0, "min_order_amount": 0}
    return job.buy_orders(Client(accounts=("B",)), market="US", account="B", account_fills=[], valuation=v,
                          open_buys=open_buys or [], signal_rows=신호, settings=설정, price_series={},
                          series_unknown={}, fx_rate=환율, stop_ids=set(), sold_recently=set(),
                          stock_cache=_캐시(*sids))  # fmt: skip


def test_위험_점검_금액은_원화로_배분_비중_그대로() -> None:
    """자본 13억 원(= 100만 달러), 현금 넉넉 — 비중 10% 배분의 위험 점검 금액 합은 원화 자본의 약 10%(여유 곱)"""
    v = _평가(1.3e9, 1.3e9)
    주문, _ = _사기(v, [_신호()])
    assert 주문 and all(o["currency"] == "USD" for o in 주문)
    for o in 주문:
        assert o["cost_krw"] == pytest.approx(o["limit_price"] * o["quantity"] * 환율 * 여유)
        assert o["cash_krw"] == pytest.approx(o["cost_krw"])  # 지정가는 틈 여유 없음 — 같은 잣대
    비중 = sum(o["cost_krw"] for o in 주문) / v.equity_krw
    assert 0.09 < 비중 / 여유 <= 0.10 + 1e-9, 비중  # 정수 주 내림만큼 덜 — × 환율이 빠지면 1/1300


def test_현금이_빠듯하면_원화_현금을_달러로_바꿔_그_안에서만_산다() -> None:
    """현금 130만 원(= 1,000 달러) — 배분은 10만 달러지만 현금 안에서만. 1주 더 사면 현금을 넘는다"""
    v = _평가(1.3e9, 1.3e6)
    주문, 기록 = _사기(v, [_신호()])
    쓴 = sum(o["cash_krw"] for o in 주문)
    assert 0 < 쓴 <= v.cash_krw + 1e-6, (쓴, 기록)
    assert 쓴 + 96.0 * 환율 * 여유 > v.cash_krw  # 가장 싼 회차(96달러) 1주를 더 사면 현금을 넘는다 — 남김 없이 썼다
    assert sum(o["quantity"] for o in 주문) <= 1_000 / (96.0 * 여유)  # ÷ 환율이 빠지면 1,300배를 산다


def test_현금이_없으면_현금_부족으로_빠진다() -> None:
    v = _평가(1.3e9, 50.0 * 환율)  # 50 달러 — 100달러 1주도 못 산다
    주문, 기록 = _사기(v, [_신호()])
    assert 주문 == []
    assert {x["why"] for x in 기록["skipped"]} <= {"현금 부족", "투자 여력 부족"} and 기록["skipped"]


@pytest.mark.parametrize(("상한", "산다"), [(11.1, True), (11.0, False)])
def test_열린_달러_매수는_원화로_센_뒤_달러_보유처럼_섹터_상한에_든다(상한: float, 산다: bool) -> None:
    """종목 1 에 100달러 × 100주 열린 매수 — 원화로 약 1,352만 원 묶음, ÷ 환율로 약 10,403 달러 = 자본 100만 달러의 1.04%.
    같은 섹터 종목 3(비중 10%)은 섹터 상한 11.1% 면 들고 11.0% 면 통째로 빠진다(섹터 상한은 깎지 않고 뺀다).
    열린 몫의 ÷ 환율이 빠지면 섹터가 이미 1,300 배로 차 11.1% 에도 빠지고, 두 번 들어가면 11.0% 에도 산다"""
    v = _평가(1.3e9, 1.3e9)
    열린 = pp.Order(id=9, account_id="B", stock_id=1, side="buy", order_type="limit", limit_price=100.0, quantity=100,
                   currency="USD", fill_session="2026-10-15")  # fmt: skip
    assert pp.reserved_by_stock([열린], {"USD": 환율}) == {1: pytest.approx(100.0 * 100 * 여유 * 환율, abs=1.0)}
    설정 = {"max_weight_per_sector": 상한, "max_weight_per_stock": 10.0, "min_order_amount": 0}
    주문, 기록 = _사기(v, [_신호(1), _신호(3)], 설정, [열린], sids=(1, 3))
    결정 = {d["stock_id"]: d for d in 기록["_decisions"]}
    assert 결정[1]["reason"] == job.OPEN_BUY_SKIP_REASON
    if 산다:
        assert 결정[3]["kind"] == "buy" and {o["stock_id"] for o in 주문} == {3}, 결정[3]
    else:
        assert 결정[3]["reason"] == "섹터 상한 도달" and 주문 == []
        assert 결정[3]["detail"]["note"].startswith("기술 이미 1.0% + 이 종목 10.0%"), 결정[3]["detail"]["note"]


def test_열린_달러_매수가_묶은_현금은_새_매수에_쓰지_않는다() -> None:
    """25.2082 (25.2078 교차검증 [권고]) — 현금 = 열린 매수 예약(종목 9, 100달러 × 100주 ≈ 1,352만 원) + 1,000달러어치 원화.
    새 주문의 현금 검사 금액 합은 (현금 − 예약) 안이다. `size_buys` 에 넘기는 `cash_krw − 예약` 의 예약 빼기가 빠지면 묶인
    현금을 한 번 더 써 다음 날 체결 단계의 빚 막기가 회차를 '현금 부족' 으로 취소한다(장부·결정 원장이 실제 배분과 갈림)"""
    열린 = pp.Order(id=9, account_id="B", stock_id=9, side="buy", order_type="limit", limit_price=100.0, quantity=100,
                   currency="USD", fill_session="2026-10-15")  # fmt: skip
    예약 = pp.reserved_krw([열린], {"USD": 환율})
    assert 예약 == pytest.approx(100.0 * 100 * 여유 * 환율, abs=1.0)
    v = _평가(1.3e9, 예약 + 1_000 * 환율)
    주문, 기록 = _사기(v, [_신호()], open_buys=[열린], sids=(1, 9))
    쓴 = sum(o["cash_krw"] for o in 주문)
    assert 0 < 쓴 <= v.cash_krw - 예약 + 1e-6, (쓴, 기록)
    assert 쓴 + 96.0 * 환율 * 여유 > v.cash_krw - 예약  # 남김 없이 — 예약 밖 현금은 다 썼다
