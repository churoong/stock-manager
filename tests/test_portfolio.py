"""포트폴리오 계산 테스트 (docs/portfolio.md). 손으로 계산 가능한 고정 값으로 검증한다.

Step 12 완료 기준 (design.md): 환율이 다른 시점의 미국 매매에서 주가 손익과 환차손익이 분리되고
합계가 총손익과 일치한다.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta

import pytest

from batch.services import portfolio as pf

NO_COST = pf.CostRates(0, 0, 0)


def buy(tid: int, day: str, price: float, qty: float, fx: float = 1.0, **kw) -> pf.Trade:
    return pf.Trade(tid, 1, "buy", day, price, qty, "USD" if fx != 1.0 else "KRW", fx, **kw)


def sell(tid: int, day: str, price: float, qty: float, fx: float = 1.0, **kw) -> pf.Trade:
    return pf.Trade(tid, 1, "sell", day, price, qty, "USD" if fx != 1.0 else "KRW", fx, **kw)


class Test환차손익_분리:
    def test_미국_매매_주가손익과_환차손익의_합이_총손익(self) -> None:
        # 100달러에 10주(환율 1,300), 120달러에 10주 매도(환율 1,400). 수수료 없음
        trades = [
            buy(1, "2025-01-02", 100, 10, 1300.0, fee=0, tax=0),
            sell(2, "2026-01-02", 120, 10, 1400.0, fee=0, tax=0),
        ]
        lots, open_lots, warnings = pf.match_fifo(trades, NO_COST)
        assert not open_lots and not warnings
        lot = lots[0]
        assert lot.realized_pnl == pytest.approx(200.0)  # 달러
        assert lot.price_pnl_krw == pytest.approx(200 * 1300)  # 260,000원
        assert lot.fx_pnl_krw == pytest.approx(1200 * 100)  # 매도대금 1,200달러 × 100원 = 120,000원
        assert lot.realized_pnl_krw == pytest.approx(1200 * 1400 - 1000 * 1300)  # 380,000원
        assert lot.price_pnl_krw + lot.fx_pnl_krw == pytest.approx(lot.realized_pnl_krw)

    def test_주가는_손실_환율은_이익(self) -> None:
        trades = [
            buy(1, "2025-01-02", 100, 5, 1200.0, fee=1, tax=0),
            sell(2, "2025-06-02", 90, 5, 1450.0, fee=1, tax=0),
        ]
        lots, _, _ = pf.match_fifo(trades, NO_COST)
        lot = lots[0]
        assert lot.cost == pytest.approx(501)
        assert lot.proceeds == pytest.approx(449)
        assert lot.price_pnl_krw < 0 < lot.fx_pnl_krw
        assert lot.price_pnl_krw + lot.fx_pnl_krw == pytest.approx(lot.realized_pnl_krw)


class TestFIFO:
    def test_먼저_산_것부터_판다(self) -> None:
        trades = [buy(1, "2025-01-02", 10_000, 10), buy(2, "2025-02-03", 12_000, 10), sell(3, "2025-03-04", 15_000, 15)]
        lots, open_lots, _ = pf.match_fifo(trades, NO_COST)
        assert [(lot.buy_trade_id, lot.quantity) for lot in lots] == [(1, 10), (2, 5)]
        assert lots[0].realized_pnl == pytest.approx(50_000)
        assert lots[1].realized_pnl == pytest.approx(15_000)
        assert [(o.trade_id, o.quantity) for o in open_lots] == [(2, 5)]
        assert lots[0].holding_days == 61

    def test_보유보다_많이_팔면_보유분까지만_하고_경고(self) -> None:
        lots, open_lots, warnings = pf.match_fifo(
            [buy(1, "2025-01-02", 100, 3), sell(2, "2025-01-03", 110, 5)], NO_COST
        )
        assert sum(lot.quantity for lot in lots) == 3 and not open_lots
        assert "2주 많이" in warnings[0]

    def test_수수료_세금은_수량_비율로_나눈다(self) -> None:
        trades = [buy(1, "2025-01-02", 10_000, 10, fee=100), sell(2, "2025-01-03", 11_000, 4, fee=40, tax=80)]
        lots, open_lots, _ = pf.match_fifo(trades, NO_COST)
        assert lots[0].cost == pytest.approx(4 * 10_000 + 40)  # 매수 수수료 100 × 4/10
        assert lots[0].proceeds == pytest.approx(4 * 11_000 - 40 - 80)
        assert lots[0].fee_total == pytest.approx(80) and lots[0].tax_total == pytest.approx(80)
        assert open_lots[0].unit_fee == pytest.approx(10)

    def test_비운_수수료는_설정_비율로_추정하고_표시(self) -> None:
        rates = pf.CostRates(buy_fee_pct=0.015, sell_fee_pct=0.015, sell_tax_pct=0.18)
        fee, tax, estimated = pf.fee_and_tax(sell(1, "2025-01-02", 10_000, 100), rates)
        assert fee == pytest.approx(150) and tax == pytest.approx(1800) and estimated
        fee, tax, estimated = pf.fee_and_tax(sell(1, "2025-01-02", 10_000, 100, fee=100, tax=0), rates)
        assert (fee, tax, estimated) == (100, 0, False)

    def test_미국_매도는_세금_칸이_비어도_추정이_아니다(self) -> None:
        """미국은 매매세가 없어 0 이 확정값이다. 국내처럼 '모른다' 로 읽으면 미국 매도가 전부 '일부 추정' 이 된다 (docs/infra.md 25.285)."""
        rates = pf.CostRates(buy_fee_pct=0.25, sell_fee_pct=0.25, sell_tax_pct=None, has_sell_tax=False)
        fee, tax, estimated = pf.fee_and_tax(sell(1, "2025-01-02", 100, 10, 1400.0, fee=2.5), rates)
        assert (fee, tax, estimated) == (2.5, 0.0, False)


def test_잡이_미국_비용률을_매매세_없는_시장으로_만든다() -> None:
    """batch/jobs/portfolio 가 USD 에 has_sell_tax=False 를 넘기는가 — 서비스만 고치고 잡이 안 넘기면 소용없다."""
    from batch.jobs import portfolio as job

    rates, _ = job.cost_rates({}, {})
    assert rates["USD"].has_sell_tax is False and rates["KRW"].has_sell_tax is True


class Test보유와_평가:
    def test_평균단가_평균환율_평가손익_분리(self) -> None:
        trades = [buy(1, "2025-01-02", 100, 10, 1300.0, fee=0), buy(2, "2025-02-03", 110, 10, 1400.0, fee=0)]
        _, open_lots, _ = pf.match_fifo(trades, NO_COST)
        pos = pf.position_from(1, "USD", open_lots)
        assert pos is not None
        assert pos.avg_price == pytest.approx(105)
        assert pos.avg_fx == pytest.approx((1000 * 1300 + 1100 * 1400) / 2100)
        pf.valuate(pos, close=120, price_date="2026-09-16", fx_now=1390.0, fx_date="2026-09-16")
        assert pos.market_value_krw == pytest.approx(2400 * 1390)
        assert pos.unrealized_price_pnl_krw + pos.unrealized_fx_pnl_krw == pytest.approx(pos.unrealized_pnl_krw)

    def test_종가가_없으면_평가를_비운다(self) -> None:
        _, open_lots, _ = pf.match_fifo([buy(1, "2025-01-02", 100, 1)], NO_COST)
        pos = pf.position_from(1, "KRW", open_lots)
        assert pos is not None
        pf.valuate(pos, None, None, 1.0, None)
        assert pos.market_value_krw is None and pos.unrealized_pnl_krw is None

    def test_환율이_없어도_종목_통화_평가액은_낸다(self) -> None:
        """환율이 없으면 달러 평가액까지 비워 미국 보유의 손절 판정이 조용히 빠졌다 (docs/infra.md 25.1096, 감사)."""
        _, open_lots, _ = pf.match_fifo([buy(1, "2025-01-02", 100, 10, 1300.0, fee=0)], NO_COST)
        pos = pf.position_from(1, "USD", open_lots)
        assert pos is not None
        pf.valuate(pos, 60.0, "2026-09-16", None, None)
        assert pos.market_value == pytest.approx(600) and pos.unrealized_pnl == pytest.approx(600 - pos.cost)
        assert pos.market_value_krw is None and pos.unrealized_pnl_krw is None and pos.unrealized_fx_pnl_krw is None

    def test_다_팔면_보유_없음(self) -> None:
        _, open_lots, _ = pf.match_fifo([buy(1, "2025-01-02", 100, 2), sell(2, "2025-01-03", 100, 2)], NO_COST)
        assert pf.position_from(1, "KRW", open_lots) is None

    def test_업종_비중(self) -> None:
        a = pf.Position(1, 10, "KRW", 1, 1, 10, 10, "2025-01-01", None, market_value_krw=300.0, weight_pct=75.0)
        b = pf.Position(2, 10, "USD", 1, 1, 10, 10, "2025-01-01", None, market_value_krw=100.0, weight_pct=25.0)
        alloc = pf.allocation([a, b], {1: "반도체", 2: None})
        assert alloc["by_sector"] == {"반도체": 75.0, "업종 없음": 25.0}
        assert alloc["by_currency"] == {"KRW": 75.0, "USD": 25.0}


class Test시간가중수익률:
    def test_중간에_돈을_더_넣어도_수익률은_가격변화만(self) -> None:
        # 1일 100원에 10주, 2일 종가 110(+10%), 2일에 110원에 10주 더, 3일 종가 121(+10%)
        trades = [buy(1, "2025-01-01", 100, 10, fee=0), buy(2, "2025-01-02", 110, 10, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 110.0, "2025-01-03": 121.0}}
        rows, warnings = pf.value_series(trades, [], closes, {}, "2025-01-03", {"KRW": NO_COST})
        assert not warnings
        assert [r.value_krw for r in rows] == pytest.approx([1000, 2200, 2420])
        # 2일에 산 돈은 아침에 들어온 것으로 본다(25.396): 2200 / (1000 + 1100). 3일은 +10%
        assert [r.twr_index for r in rows] == pytest.approx([1.0, 2200 / 2100, 2200 / 2100 * 1.1])

    def test_배당은_수익으로_센다(self) -> None:
        trades = [buy(1, "2025-01-01", 100, 10, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}}
        receipts = [pf.DividendReceipt(1, "2025-01-02", 50.0, 1.0)]
        rows, _ = pf.value_series(trades, receipts, closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.05)

    def test_처음_산_날에_받은_배당은_지수에_넣지_않는다(self) -> None:
        """지급일 당일의 매수는 그 배당을 벌지 못했다 — 기록 전부터 든 주식의 배당이다 (docs/infra.md 25.932, 감사 재현)."""
        trades = [pf.Trade(1, 2, "buy", "2026-04-01", 1_000_000.0, 10, "KRW", 1.0, fee=0),  # 다른 종목 B
                  buy(2, "2026-04-15", 50_000, 10, fee=0)]  # 종목 1(A) 을 처음 산 날 = 지급일
        closes = {1: {"2026-04-15": 50_000.0}, 2: {"2026-04-01": 1_000_000.0, "2026-04-15": 1_000_000.0}}
        receipts = [pf.DividendReceipt(1, "2026-04-15", 400_000.0, 1.0)]
        assert pf.배당_붙일_날(receipts[0], trades) is None
        rows, warnings = pf.value_series(trades, receipts, closes, {}, "2026-04-15", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.0), "1.0381 이면 당일 매수가 번 배당으로 셌다"
        assert warnings

    def test_지급일에_판_몫의_배당은_그날에_붙인다(self) -> None:
        trades = [buy(1, "2025-01-01", 100, 10, fee=0), pf.Trade(2, 1, "sell", "2025-01-03", 100.0, 10, "KRW", 1.0, fee=0)]
        receipts = [pf.DividendReceipt(1, "2025-01-03", 50.0, 1.0)]
        assert pf.배당_붙일_날(receipts[0], trades) == "2025-01-03"

    def test_전량_매도_뒤_배당은_판_날에_붙인다(self) -> None:
        # 2일에 다 팔고 3일에 배당이 들어왔다 — 그 배당을 번 돈이 나간 2일에 붙인다 (25.578 은 경고만, 25.582)
        trades = [buy(1, "2025-01-01", 100, 10, fee=0), pf.Trade(2, 1, "sell", "2025-01-02", 100.0, 10, "KRW", 1.0, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0, "2025-01-03": 100.0}}
        receipts = [pf.DividendReceipt(1, "2025-01-03", 50.0, 1.0)]
        rows, warnings = pf.value_series(trades, receipts, closes, {}, "2025-01-03", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.05)
        assert not warnings

    def test_판_종목의_배당이_남은_종목의_수익이_되지_않는다(self) -> None:
        # A 1,000원어치를 사고 다 판 뒤 1원짜리 B 만 남았다. A 배당 50원이 B 위에 올라 지수가 51배가 됐다 (25.582,
        # 교차검증)
        trades = [
            buy(1, "2025-01-01", 100, 10, fee=0),
            pf.Trade(2, 1, "sell", "2025-01-02", 100.0, 10, "KRW", 1.0, fee=0),
            pf.Trade(3, 2, "buy", "2025-01-02", 1.0, 1, "KRW", 1.0, fee=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}, 2: {"2025-01-02": 1.0, "2025-01-03": 1.0}}
        receipts = [pf.DividendReceipt(1, "2025-01-03", 50.0, 1.0)]
        rows, _ = pf.value_series(trades, receipts, closes, {}, "2025-01-03", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.05), "51 이면 판 종목의 배당을 남은 1원의 수익으로 셌다"

    def test_배당락_뒤_거의_다_팔거나_다시_사도_지수가_부풀지_않는다(self) -> None:
        """지급일에 한 주만 남아도 지급일에 붙여 0.98 → 30.98 이 됐다 (docs/infra.md 25.828, 감사 재현)."""
        closes = {1: {"2025-12-01": 10_000.0, "2025-12-29": 9_800.0, "2026-01-10": 9_800.0, "2026-03-02": 9_800.0,
                      "2026-04-15": 9_800.0}}  # fmt: skip
        receipts = [pf.DividendReceipt(1, "2026-04-15", 300_000.0, 1.0)]
        팔기 = lambda n: pf.Trade(2, 1, "sell", "2026-01-10", 9_800.0, n, "KRW", 1.0, fee=0)  # noqa: E731
        다시사기 = pf.Trade(3, 1, "buy", "2026-03-02", 9_800.0, 1, "KRW", 1.0, fee=0)
        for trades in ([buy(1, "2025-12-01", 10_000, 1000, fee=0), 팔기(1000), 다시사기],
                       [buy(1, "2025-12-01", 10_000, 1000, fee=0), 팔기(999)]):
            rows, _ = pf.value_series(trades, receipts, closes, {}, "2026-04-15", {"KRW": NO_COST})
            assert rows[-1].twr_index == pytest.approx(1.01), "30.98 이면 배당을 1주 위에 붙였다"
            assert pf.배당_붙일_날(receipts[0], trades) == "2026-01-10"
        # 반만 판 정도면 예전처럼 지급일
        반 = [buy(1, "2025-12-01", 10_000, 1000, fee=0), 팔기(500)]
        assert pf.배당_붙일_날(receipts[0], 반) == "2026-04-15"

    def test_창보다_전에_산_몫과_오르내림과_하루_왕복(self) -> None:
        """교차검증이 살아남게 둔 변이들 (docs/infra.md 25.831) — 창 시작 보유·마지막 하락·하루 정점."""
        d = pf.DividendReceipt(1, "2026-04-15", 300_000.0, 1.0)
        팔기 = lambda i, day, n: pf.Trade(i, 1, "sell", day, 9_800.0, n, "KRW", 1.0, fee=0)  # noqa: E731
        사기 = lambda i, day, n: pf.Trade(i, 1, "buy", day, 9_800.0, n, "KRW", 1.0, fee=0)  # noqa: E731
        # 6개월보다 전(2024)에 산 1,000주에서 999주를 판다 — 창이 열릴 때의 보유가 "번 몫" 이다
        assert pf.배당_붙일_날(d, [사기(1, "2024-05-01", 1000), 팔기(2, "2026-01-10", 999)]) == "2026-01-10"
        # 내렸다(01-10) 올랐다(02-01) 다시 내렸다(03-05) — **마지막** 하락
        assert pf.배당_붙일_날(d, [사기(1, "2024-05-01", 1000), 팔기(2, "2026-01-10", 999), 사기(3, "2026-02-01", 999),
                                   팔기(4, "2026-03-05", 999)]) == "2026-03-05"  # fmt: skip
        # 10주를 들고 있다가 하루 안에 1,000주를 사고판 왕복은 정점이 아니다 — 지급일 그대로
        assert pf.배당_붙일_날(d, [사기(1, "2024-05-01", 10), 사기(2, "2026-03-02", 1000), 팔기(3, "2026-03-02", 1000)]) == "2026-04-15"
        # 창 경계: 창 시작(183일 전) 바로 전날의 매도는 창 밖
        assert pf.DIVIDEND_EARN_WINDOW_DAYS == 183
        assert pf.배당_붙일_날(d, [사기(1, "2024-05-01", 1000), 팔기(2, "2025-10-13", 999)]) == "2026-04-15"
        assert pf.배당_붙일_날(d, [사기(1, "2024-05-01", 1000), 팔기(2, "2025-10-14", 999)]) == "2025-10-14"

    def test_한_번도_들지_않은_종목의_배당은_여전히_경고한다(self) -> None:
        trades = [buy(1, "2025-01-02", 100, 10, fee=0)]
        closes = {1: {"2025-01-02": 100.0}}
        receipts = [pf.DividendReceipt(9, "2025-01-01", 50.0, 1.0)]
        _, warnings = pf.value_series(trades, receipts, closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert any("매수 기록이 없는 종목의 배당 1건(2025-01-01" in w for w in warnings)

    def test_다른_종목을_들고_있는_날에도_한_번도_들지_않은_종목의_배당은_지수에_넣지_않는다(self) -> None:
        """1,000원 보유에 산 적 없는 종목의 배당 500원이 지수를 1.0 → 1.5 로 만들었다 (docs/infra.md 25.616, 감사)."""
        trades = [buy(1, "2025-01-01", 100, 10, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}}
        receipts = [pf.DividendReceipt(9, "2025-01-02", 500.0, 1.0)]
        rows, warnings = pf.value_series(trades, receipts, closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.0)
        assert any("매수 기록이 없는 종목의 배당" in w for w in warnings)

    def test_보유_중_배당은_경고하지_않는다(self) -> None:
        trades = [buy(1, "2025-01-01", 100, 10, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}}
        receipts = [pf.DividendReceipt(1, "2025-01-02", 50.0, 1.0)]
        _, warnings = pf.value_series(trades, receipts, closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert not warnings

    def test_미국은_그날_환율로_평가(self) -> None:
        trades = [pf.Trade(1, 1, "buy", "2025-01-01", 10.0, 1, "USD", 1000.0, fee=0)]
        closes = {1: {"2025-01-01": 10.0, "2025-01-02": 10.0}}
        fx = {"USD": {"2025-01-01": 1000.0, "2025-01-02": 1100.0}}
        rows, _ = pf.value_series(trades, [], closes, fx, "2025-01-02", {"USD": NO_COST})
        assert rows[-1].value_krw == pytest.approx(11_000)
        assert rows[-1].twr_index == pytest.approx(1.1)


    def test_첫_매수일에_종가가_없어도_지수가_0_에_갇히지_않는다(self) -> None:
        """장중에 입력하고 바로 재계산 — 그날 종가가 아직 없다. 예전에는 지수 0 → 영영 0 (docs/infra.md 25.328)."""
        trades = [buy(1, "2025-01-02", 100, 10, fee=0)]
        closes = {1: {"2025-01-03": 110.0}}  # 첫날 종가 없음
        rows, warnings = pf.value_series(trades, [], closes, {}, "2025-01-03", {"KRW": NO_COST})
        assert [r.twr_index for r in rows] == pytest.approx([1.0, 1.1])
        assert warnings  # 체결가로 평가했다고 말한다


    def test_적게_들고_있다가_크게_산_날_지수가_무너지지_않는다(self) -> None:
        """100만원 보유 중 1천만원을 사고 새 종목이 −3%. 예전 식은 −30%, 더 크면 음수까지 (docs/infra.md 25.396)."""
        trades = [buy(1, "2025-01-01", 100, 10_000, fee=0), pf.Trade(2, 2, "buy", "2025-01-02", 1000, 10_000, "KRW", 1.0, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}, 2: {"2025-01-02": 970.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        # (100만 + 970만) / (100만 + 1000만) = −2.7%
        assert rows[-1].twr_index == pytest.approx(10_700_000 / 11_000_000)

    def test_작은_보유에_큰_매수가_얹혀도_지수는_음수가_아니다(self) -> None:
        trades = [buy(1, "2025-01-01", 1000, 1, fee=0), pf.Trade(2, 2, "buy", "2025-01-02", 100_000, 1, "KRW", 1.0, fee=0)]
        closes = {1: {"2025-01-01": 1000.0, "2025-01-02": 1000.0}, 2: {"2025-01-02": 95_000.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(96_000 / 101_000)

    def test_판_돈은_저녁에_나간다(self) -> None:
        # 10주 보유(100), 2일 종가 110 에 5주를 110 에 판다 → 그날 수익률은 +10% 그대로
        trades = [buy(1, "2025-01-01", 100, 10, fee=0), sell(2, "2025-01-02", 110, 5, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 110.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.1)


    def test_같은_날_팔고_산_교체는_오간_돈이_아니다(self) -> None:
        """A 가 +10% 인 날 전량 팔아 B 를 사고 B 는 보합 — 실제 +10%. 25.396 식은 +4.76% (docs/infra.md 25.416)."""
        trades = [
            buy(1, "2025-01-01", 100, 10, fee=0),
            sell(2, "2025-01-02", 110, 10, fee=0, tax=0),
            pf.Trade(3, 2, "buy", "2025-01-02", 110, 10, "KRW", 1.0, fee=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 110.0}, 2: {"2025-01-02": 110.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.1)

    def test_그날_산_몫을_그날_팔면_새_돈이_들어오고_나간_것이다(self) -> None:
        """1,000원 보유 중 B 를 1천만원에 사서 같은 날 1,100만원에 판다 — 실제 +9.99%. 예전 식은 1001배 (25.780)."""
        trades = [
            buy(1, "2025-01-01", 100, 10, fee=0),
            pf.Trade(2, 2, "buy", "2025-01-02", 1000, 10_000, "KRW", 1.0, fee=0),
            pf.Trade(3, 2, "sell", "2025-01-02", 1100, 10_000, "KRW", 1.0, fee=0, tax=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}, 2: {"2025-01-02": 1100.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx((1_000 + 11_000_000) / (1_000 + 10_000_000))

    def test_그날_산_몫의_당일_손실도_제_크기다(self) -> None:
        """100만원 보유에 1천만원을 사서 같은 날 −10% 에 판다 — 실제 −9.09%. 예전 식은 −50% (25.780)."""
        trades = [
            buy(1, "2025-01-01", 100, 10_000, fee=0),
            pf.Trade(2, 2, "buy", "2025-01-02", 1000, 10_000, "KRW", 1.0, fee=0),
            pf.Trade(3, 2, "sell", "2025-01-02", 900, 10_000, "KRW", 1.0, fee=0, tax=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}, 2: {"2025-01-02": 900.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx((1_000_000 + 9_000_000) / (1_000_000 + 10_000_000))

    def test_전날_몫을_팔아_산_것을_그날_또_팔아도_교체는_교체다(self) -> None:
        """A(전날 10주, +10%)를 팔아 B 를 사고 B 를 같은 날 +10% 에 판다 — 실제 1.1 × 1.1 이 아니라 그날 하루 +21%."""
        trades = [
            buy(1, "2025-01-01", 100, 10, fee=0),
            sell(2, "2025-01-02", 110, 10, fee=0, tax=0),
            pf.Trade(3, 2, "buy", "2025-01-02", 110, 10, "KRW", 1.0, fee=0),
            pf.Trade(4, 2, "sell", "2025-01-02", 121, 10, "KRW", 1.0, fee=0, tax=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 110.0}, 2: {"2025-01-02": 121.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        # 전날 1,000 → 저녁에 1,210 이 나갔다(평가 0). 들어온 새 돈은 없다
        assert rows[-1].twr_index == pytest.approx(1.21)

    def test_왕복한_돈으로_같은_날_다른_종목을_사도_새_돈을_두_번_세지_않는다(self) -> None:
        """A 100만 보유. B 를 1천만에 사서 1,100만에 팔고 그 돈으로 C 를 1,100만에 산다(보합) — 실제 12/11 (25.782, 교차검증)."""
        trades = [
            buy(1, "2025-01-01", 100, 10_000, fee=0),
            pf.Trade(2, 2, "buy", "2025-01-02", 1000, 10_000, "KRW", 1.0, fee=0),
            pf.Trade(3, 2, "sell", "2025-01-02", 1100, 10_000, "KRW", 1.0, fee=0, tax=0),
            pf.Trade(4, 3, "buy", "2025-01-02", 1100, 10_000, "KRW", 1.0, fee=0),
        ]
        closes = {
            1: {"2025-01-01": 100.0, "2025-01-02": 100.0}, 2: {"2025-01-02": 1100.0}, 3: {"2025-01-02": 1100.0},
        }
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        # 25.780 식은 1,000만을 아침에 넣고 1,100만(C 매수)을 또 새 돈으로 봐 1.0455
        assert rows[-1].twr_index == pytest.approx(12_000_000 / 11_000_000)

    def test_왕복_손실_뒤_다른_종목을_사도_제_크기다(self) -> None:
        trades = [
            buy(1, "2025-01-01", 100, 10_000, fee=0),
            pf.Trade(2, 2, "buy", "2025-01-02", 1000, 10_000, "KRW", 1.0, fee=0),
            pf.Trade(3, 2, "sell", "2025-01-02", 900, 10_000, "KRW", 1.0, fee=0, tax=0),
            pf.Trade(4, 3, "buy", "2025-01-02", 900, 10_000, "KRW", 1.0, fee=0),
        ]
        closes = {
            1: {"2025-01-01": 100.0, "2025-01-02": 100.0}, 2: {"2025-01-02": 900.0}, 3: {"2025-01-02": 900.0},
        }
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(10_000_000 / 11_000_000)

    def test_한_번에_산_것을_같은_날_일부만_팔아도_매수를_쪼개지_않는다(self) -> None:
        """첫날 100주@100 을 사서 50주를 110 에 판다, 종가 110 — 실제 +10%. 25.782 식은 1.2 (25.784, 교차검증)."""
        trades = [buy(1, "2025-01-02", 100, 100, fee=0), sell(2, "2025-01-02", 110, 50, fee=0, tax=0)]
        closes = {1: {"2025-01-02": 110.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.1)

    def test_보유_위에_산_것을_일부만_팔아도_제_크기다(self) -> None:
        """100만 보유, B 1천만 매수, 절반을 +10% 에 판다 — 실제 12/11. 25.782 식은 1.1667."""
        trades = [
            buy(1, "2025-01-01", 100, 10_000, fee=0),
            pf.Trade(2, 2, "buy", "2025-01-02", 1000, 10_000, "KRW", 1.0, fee=0),
            pf.Trade(3, 2, "sell", "2025-01-02", 1100, 5_000, "KRW", 1.0, fee=0, tax=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}, 2: {"2025-01-02": 1100.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(12_000_000 / 11_000_000)

    def test_두_묶음을_사서_하나_반을_팔아도_원가는_묶음_전체(self) -> None:
        trades = [
            buy(1, "2025-01-02", 100, 50, fee=0), buy(2, "2025-01-02", 100, 50, fee=0),
            sell(3, "2025-01-02", 110, 70, fee=0, tax=0),
        ]
        closes = {1: {"2025-01-02": 110.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.1)

    def test_전날_몫과_그날_묶음에_걸친_매도_줄의_대금은_매수_자금이_아니다(self) -> None:
        """전날 100주, 100주@100 을 더 사서 한 줄로 200주@110 을 판다 — 실제 +10%. 25.784 식은 1.2 (25.787, 교차검증)."""
        trades = [
            buy(1, "2025-01-01", 100, 100, fee=0),
            buy(2, "2025-01-02", 100, 100, fee=0),
            sell(3, "2025-01-02", 110, 200, fee=0, tax=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 110.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(1.1)

    def test_걸친_줄이_일부만_팔아도(self) -> None:
        """같은 조건에서 150주@90 을 판다, 종가 90 — 실제 −10%. 25.784 식은 0.818."""
        trades = [
            buy(1, "2025-01-01", 100, 100, fee=0),
            buy(2, "2025-01-02", 100, 100, fee=0),
            sell(3, "2025-01-02", 90, 150, fee=0, tax=0),
        ]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 90.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-02", {"KRW": NO_COST})
        assert rows[-1].twr_index == pytest.approx(0.9)


class Test실적_기한:
    def test_연말_결산_다음_분기_기한(self) -> None:
        # 2025-12-31 결산. 2026-09-17 기준: 2026-09-30 분기말 → 45일 뒤 2026-11-14
        assert pf.next_filing_deadline("2025-12-31", date(2026, 9, 17)) == ("분기", "2026-11-14", 58)

    def test_사업보고서_기한이_아직이면_연간(self) -> None:
        assert pf.next_filing_deadline("2025-12-31", date(2026, 2, 1)) == ("연간", "2026-03-31", 58)

    def test_6월_결산(self) -> None:
        kind, deadline, _ = pf.next_filing_deadline("2026-06-30", date(2026, 9, 17))
        assert (kind, deadline) == ("연간", "2026-09-28")


class Test비용을_어느_날_환율로_환산하나:
    """**매수 수수료는 매수일 환율** (docs/infra.md 25.96).

    전부 매도일 환율로 환산하던 때가 있었다. 그러면 바로 옆에 적히는 `realized_pnl_krw`
    와 어긋난다 — 그쪽은 `proceeds * sell_fx - cost * buy_fx` 라 매수 비용을 매수일
    환율로 이미 제대로 세고 있다. 금액은 작지만 **한 화면의 두 숫자가 안 맞는다**.
    """

    @staticmethod
    def _롯():
        return pf.Lot(
            buy_trade_id=1, sell_trade_id=2, stock_id=1, quantity=10,
            buy_price=100.0, sell_price=120.0, currency="USD",
            buy_fx=1300.0, sell_fx=1400.0,
            cost=1002.0, proceeds=1197.0,
            realized_pnl=195.0, realized_pnl_krw=0.0, price_pnl_krw=0.0, fx_pnl_krw=0.0,
            fee_total=5.0, tax_total=1.0, cost_estimated=False, holding_days=30,
            fee_buy=2.0,
        )  # fmt: skip

    def test_손으로_검산(self) -> None:
        # 매수 수수료 2 × 1,300 = 2,600
        # 매도 수수료 3 × 1,400 = 4,200  (fee_total 5 − fee_buy 2)
        # 세금       1 × 1,400 = 1,400
        assert self._롯().fees_taxes_krw == pytest.approx(2_600 + 4_200 + 1_400)

    def test_전부_매도일_환율로_세면_더_크다(self) -> None:
        """부풀어 보이는 쪽이 옛 계산이다. 방향을 고정해 둔다."""
        롯 = self._롯()
        옛계산 = (롯.fee_total + 롯.tax_total) * 롯.sell_fx

        assert 옛계산 > 롯.fees_taxes_krw
        assert 옛계산 - 롯.fees_taxes_krw == pytest.approx(2.0 * (1400 - 1300))

    def test_환율이_같으면_옛_계산과_같다(self) -> None:
        """국내 매매(환율 1.0)에서는 아무것도 안 바뀐다."""
        롯 = pf.Lot(
            buy_trade_id=1, sell_trade_id=2, stock_id=1, quantity=10,
            buy_price=100.0, sell_price=120.0, currency="KRW",
            buy_fx=1.0, sell_fx=1.0, cost=1002.0, proceeds=1197.0,
            realized_pnl=195.0, realized_pnl_krw=0.0, price_pnl_krw=0.0, fx_pnl_krw=0.0,
            fee_total=5.0, tax_total=1.0, cost_estimated=False, holding_days=30, fee_buy=2.0,
        )  # fmt: skip

        assert 롯.fees_taxes_krw == pytest.approx((롯.fee_total + 롯.tax_total) * 롯.sell_fx)

    def test_실현손익과_같은_환율_규칙을_쓴다(self) -> None:
        """**이것이 진짜 지키려는 것이다.** 두 숫자가 같은 규칙을 따라야 한다."""
        원본 = inspect.getsource(pf)

        assert "proceeds * trade.fx_rate - cost * head.fx" in 원본, (
            "실현손익의 환율 규칙이 바뀌었다 — 비용 환산도 같이 보라"
        )

    def test_매수_몫을_실제로_갈라_담는다(self) -> None:
        """`fee_buy` 를 안 채우면 0 이 되어 **매수 수수료가 통째로 매도 환율**을 탄다."""
        원본 = inspect.getsource(pf.split_lots if hasattr(pf, "split_lots") else pf)

        assert "fee_buy=q * head.unit_fee" in 원본


class Test묵은_값으로_평가했나:
    """**평가는 늘 "가장 최근" 이다. 그 '최근' 이 언제인지 아무도 안 따졌다** (docs/infra.md 25.136).

    `price_date` · `fx_date` 는 처음부터 저장하고 화면에도 찍었다. 적어 놓고 **판단하는 곳이
    없었다** — 수집이 멈추면 2주 전 가격과 환율로 낸 금액이 오늘 값처럼 뜬다.

    지금 그것이 **실제로 도는 상태**다. 환율은 미국 일일 배치가 받는데(25.83) 그 배치는
    D1 운영 중 쉰다(25.14). 그 동안 미국 보유분의 원화 평가액은 옛 환율로 계산된다.
    """

    @staticmethod
    def _보유(currency: str, price_date: str, fx_date: str) -> pf.Position:
        pos = pf.Position(
            stock_id=1, quantity=10, currency=currency, avg_price=100.0, avg_fx=1.0,
            cost=1000.0, cost_krw=1000.0, first_buy_date="2026-01-02", horizon="long",
        )  # fmt: skip
        return pf.valuate(pos, 110.0, price_date, 1.0 if currency == "KRW" else 1400.0, fx_date)

    def test_싱싱하면_아무_말도_하지_않는다(self) -> None:
        # 거짓 경보를 쌓으면 사람이 경고를 안 읽는다. 사흘 연휴는 늘 있다
        보유 = self._보유("KRW", "2026-09-18", "-")
        assert pf.stale_notes([보유], "2026-09-21") == []

    def test_정상_휴장으로는_걸리지_않는다(self) -> None:
        """문턱이 최장 휴장 간격(11일, 실측)이다. 추석·연말에도 안 울려야 한다."""
        한도 = pf._종가_묵음_일수()
        보유 = self._보유("KRW", "2026-09-10", "-")
        딱맞는날 = (date.fromisoformat("2026-09-10") + timedelta(days=한도)).isoformat()
        assert pf.stale_notes([보유], 딱맞는날) == []

    def test_종가가_너무_묵으면_말한다(self) -> None:
        한도 = pf._종가_묵음_일수()
        보유 = self._보유("KRW", "2026-09-10", "-")
        하루_더 = (date.fromisoformat("2026-09-10") + timedelta(days=한도 + 1)).isoformat()

        말 = pf.stale_notes([보유], 하루_더)

        assert len(말) == 1
        assert "2026-09-10" in 말[0] and "시세 수집" in 말[0]

    def test_환율이_묵으면_통화를_찍어_말한다(self) -> None:
        한도 = pf._환율_묵음_일수()
        보유 = self._보유("USD", "2026-09-20", "2026-09-10")
        하루_더 = (date.fromisoformat("2026-09-10") + timedelta(days=한도 + 1)).isoformat()

        말 = pf.stale_notes([보유], 하루_더)

        assert any("USD 환율" in m and "환차손익" in m for m in 말), 말

    def test_원화_종목은_환율을_따지지_않는다(self) -> None:
        # 원화는 fx_date 가 "-" 다. 그것을 날짜로 읽어 "56년 묵었다" 고 말하면 안 된다
        보유 = self._보유("KRW", "2026-09-20", "-")
        assert pf.stale_notes([보유], "2026-09-21") == []

    def test_문턱을_두_곳에서_가져다_쓴다(self) -> None:
        """**같은 판단을 평가에서만 다르게 할 이유가 없다** (25.0 「한 규칙이 두 곳에 있다」).

        환율 7일은 `services/fx` 가 매매 입력에 이미 쓰는 값이고,
        종가 11일은 `services/metrics` 가 `exchange_calendars` 로 실측한 값이다.
        """
        from batch.services.fx import MAX_STALE_DAYS
        from batch.services.metrics import MAX_SESSION_GAP_DAYS

        assert pf._환율_묵음_일수() == MAX_STALE_DAYS
        assert pf._종가_묵음_일수() == MAX_SESSION_GAP_DAYS


def test_보유_평가_작업이_그_말을_싣는다() -> None:
    """**부르는 곳이 없으면 함수를 하나 더 만든 것일 뿐이다** (25.123 에서 배운 것)."""
    import ast
    from pathlib import Path

    본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "portfolio.py").read_text("utf-8")
    부름 = [
        n
        for n in ast.walk(ast.parse(본문))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "stale_notes"
    ]
    assert 부름, "jobs/portfolio 가 stale_notes 를 안 부른다 — 묵은 값이 조용히 지나간다"


class Test같은_날_매수_먼저:
    """매수를 지우고 다시 넣으면 새 id 가 매도보다 커진다. 판 주식이 되살아나면 안 된다 (docs/infra.md 25.397)."""

    def test_다시_넣은_매수가_매도보다_뒤_id_여도_짝지어진다(self) -> None:
        trades = [sell(2, "2025-09-10", 120, 10, fee=0), buy(3, "2025-09-10", 100, 10, fee=0)]
        lots, open_lots, warnings = pf.match_fifo(trades, NO_COST)
        assert not warnings
        assert not open_lots
        assert sum(lot.quantity for lot in lots) == pytest.approx(10)

    def test_평가액에도_판_주식이_남지_않는다(self) -> None:
        trades = [sell(2, "2025-09-10", 120, 10, fee=0), buy(3, "2025-09-10", 100, 10, fee=0)]
        closes = {1: {"2025-09-10": 120.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-09-10", {"KRW": NO_COST})
        assert rows[-1].value_krw == pytest.approx(0)


class Test종가가_체결보다_묵으면_체결가:
    """오늘 산 종목을 어제 종가로 평가하지 않는다 (docs/infra.md 25.398)."""

    def test_보유_평가(self) -> None:
        trades = [buy(1, "2025-01-01", 9000, 10, fee=0), buy(2, "2025-01-03", 10_000, 10, fee=0)]
        assert pf.fresher_price({"2025-01-02": 9500.0}, trades) == (10_000, "2025-01-03")

    def test_종가가_같은_날이거나_뒤면_종가(self) -> None:
        trades = [buy(1, "2025-01-03", 10_000, 10, fee=0)]
        assert pf.fresher_price({"2025-01-02": 9500.0, "2025-01-03": 9800.0}, trades) == (9800.0, "2025-01-03")
        assert pf.fresher_price({}, trades) == (None, None)  # 종가가 아예 없으면 비운다

    def test_지수도_체결가로(self) -> None:
        # 다른 종목 때문에 1/2 종가가 있다. 2번 종목을 1/3 에 사고 종가는 아직 1/2(9500) 뿐
        trades = [
            buy(1, "2025-01-02", 100, 10, fee=0),
            pf.Trade(2, 2, "buy", "2025-01-03", 10_000, 1, "KRW", 1.0, fee=0),
        ]
        closes = {1: {"2025-01-02": 100.0, "2025-01-03": 100.0}, 2: {"2025-01-02": 9500.0}}
        rows, _ = pf.value_series(trades, [], closes, {}, "2025-01-03", {"KRW": NO_COST})
        assert rows[-1].value_krw == pytest.approx(11_000)
        assert rows[-1].twr_index == pytest.approx(1.0)


class Test분할_안내대로_고치면_숫자가_맞는다:
    """안내 문구가 가리키는 방법이 실제로 원가·실현손익을 지키는지 (docs/infra.md 25.400)."""

    def test_분할_전_매매를_수량은_곱하고_체결가는_나눠_다시_넣으면(self) -> None:
        # 100주 1만원 매수, 50주 1.2만원 매도, 그 뒤 1:2 분할. 실제 보유 100주, 실현 +10만원
        고친 = [buy(1, "2025-01-02", 5000, 200, fee=0), sell(2, "2025-02-03", 6000, 100, fee=0, tax=0)]
        lots, open_lots, warnings = pf.match_fifo(고친, NO_COST)
        assert not warnings
        assert sum(lot.quantity for lot in lots) == pytest.approx(100)
        assert sum(o.quantity for o in open_lots) == pytest.approx(100)
        assert sum(o.quantity * o.price for o in open_lots) == pytest.approx(500_000)  # 원가 그대로
        assert sum(lot.realized_pnl for lot in lots) == pytest.approx(100_000)

    def test_배치_경고가_그_방법을_말한다(self) -> None:
        from batch.jobs import daily

        assert "체결가 ÷ 비율" in daily.SPLIT_GUIDE
        assert "이전의 그 종목 매매를 모두" in daily.SPLIT_GUIDE
        # 유상증자 권리락은 수량을 고치지 않는다 — 평가 곡선의 한계를 함께 알린다 (docs/infra.md 25.510, 감사)
        assert "수량은 그대로" in daily.RIGHTS_NOTE and "낮게 그려집니다" in daily.RIGHTS_NOTE
        import inspect

        assert "RIGHTS_NOTE" in inspect.getsource(daily._보유_수량_확인)


class Test섞인_투자_기간은_원가가_큰_쪽:
    """장기 10주 뒤 단기 100주 — 포지션 전체가 장기로 판정되던 것 (docs/infra.md 25.406)."""

    def test_돈이_많이_걸린_기간을_따른다(self) -> None:
        trades = [buy(1, "2026-01-02", 100, 10, fee=0, horizon="long"),
                  buy(2, "2026-09-01", 100, 100, fee=0, horizon="short")]  # fmt: skip
        _, open_lots, _ = pf.match_fifo(trades, NO_COST)
        pos = pf.position_from(1, "KRW", open_lots)
        assert pos is not None and pos.horizon == "short"

    def test_같으면_먼저_산_쪽(self) -> None:
        trades = [buy(1, "2026-01-02", 100, 10, fee=0, horizon="long"),
                  buy(2, "2026-09-01", 100, 10, fee=0, horizon="short")]  # fmt: skip
        _, open_lots, _ = pf.match_fifo(trades, NO_COST)
        assert pf.dominant_horizon(open_lots) == "long"
        assert pf.dominant_horizon([]) is None

    def test_판_로트는_세지_않는다(self) -> None:
        # 단기 100주를 먼저 사서 다 팔고, 장기 10주만 남았다
        trades = [buy(1, "2026-01-02", 100, 100, fee=0, horizon="short"), sell(2, "2026-02-02", 100, 100, fee=0),
                  buy(3, "2026-03-02", 100, 10, fee=0, horizon="long")]  # fmt: skip
        _, open_lots, _ = pf.match_fifo(trades, NO_COST)
        assert pf.dominant_horizon(open_lots) == "long"


def test_추정_세금은_수수료를_뺀_나머지를_넘지_않는다() -> None:
    """수수료 100 을 적은 100원 매도에 추정 세금이 붙어 매도대금이 음수가 됐다 (docs/infra.md 25.616, 감사)."""
    rates = pf.CostRates(0, 0, 0.18)
    fee, tax, estimated = pf.fee_and_tax(sell(2, "2025-01-02", 100, 1, fee=100), rates)
    assert (fee, tax, estimated) == (100, 0.0, True)
    fee, tax, _ = pf.fee_and_tax(sell(3, "2025-01-02", 1000, 1, fee=0), rates)
    assert tax == pytest.approx(1.8)


def test_추정_수수료는_입력_세금을_뺀_나머지를_넘지_않는다() -> None:
    """수수료 빈칸 + 세금 100 을 적은 100원 매도의 매도대금이 −0.015 가 됐다 (docs/infra.md 25.737, 감사) — 25.616 의 거울."""
    rates = pf.CostRates(0, 0.015, 0.18)
    fee, tax, estimated = pf.fee_and_tax(sell(2, "2025-01-02", 100, 1, tax=100), rates)
    assert (fee, tax, estimated) == (0.0, 100, True)
    fee, _, _ = pf.fee_and_tax(sell(3, "2025-01-02", 1000, 1, tax=1), rates)
    assert fee == pytest.approx(0.15)


def test_매도만_있고_산_적_없는_종목의_배당도_지수에_넣지_않는다() -> None:
    """매수를 지운 뒤 매도만 남은 종목 — 예전에는 그 매도일에 붙여 지수가 1.5 가 됐다 (docs/infra.md 25.619, 교차검증)."""
    trades = [buy(1, "2025-01-01", 100, 10, fee=0), pf.Trade(2, 9, "sell", "2025-01-01", 50.0, 1, "KRW", 1.0, fee=0, tax=0)]
    closes = {1: {"2025-01-01": 100.0, "2025-01-02": 100.0}}
    receipts = [pf.DividendReceipt(9, "2025-01-02", 500.0, 1.0)]
    rows, warnings = pf.value_series(trades, receipts, closes, {}, "2025-01-02", {"KRW": NO_COST})
    assert rows[-1].twr_index == pytest.approx(1.0)
    assert any("매수 기록이 없는 종목의 배당" in w for w in warnings)


def test_짝_없는_매도는_다_판_날을_밀지_않는다() -> None:
    """매수 01-01 → 전량 매도 01-02 → 짝 없는 매도 01-03 → 지급 01-05 면 01-02 에 붙어야 한다 (docs/infra.md 25.621)."""
    trades = [buy(1, "2025-01-01", 100, 10, fee=0), sell(2, "2025-01-02", 100, 10, fee=0, tax=0),
              sell(3, "2025-01-03", 100, 5, fee=0, tax=0)]  # fmt: skip
    d = pf.DividendReceipt(1, "2025-01-05", 50.0, 1.0)
    assert pf.배당_붙일_날(d, trades) == "2025-01-02"


class Test해외_양도세_추정:
    """한 해 해외 실현손익을 합산(손실 상계)해 250만원을 빼고 세율을 곱한다 (docs/infra.md 25.617)."""

    @staticmethod
    def _lot(sell_id: int, pnl: float, currency: str = "USD") -> pf.Lot:
        return pf.Lot(buy_trade_id=1, sell_trade_id=sell_id, stock_id=1, quantity=1, buy_price=1, sell_price=1,
                      currency=currency, buy_fx=1, sell_fx=1, cost=1, proceeds=1, realized_pnl=pnl,
                      realized_pnl_krw=pnl, price_pnl_krw=pnl, fx_pnl_krw=0, fee_total=0, tax_total=0,
                      cost_estimated=False, holding_days=1)  # fmt: skip

    def test_손으로_계산한_값(self) -> None:
        lots = [self._lot(10, 4_000_000), self._lot(11, -500_000), self._lot(12, 9_000_000, "KRW"), self._lot(13, 1_000_000)]
        dates = {10: "2025-03-01", 11: "2025-06-01", 12: "2025-06-02", 13: "2026-01-05"}
        out = pf.us_capital_gains_estimates(lots, dates, 22.0)
        assert [(e["year"], e["gains_krw"], e["taxable_krw"]) for e in out] == [(2025, 3_500_000, 1_000_000), (2026, 1_000_000, 0)]
        assert out[0]["tax_krw"] == pytest.approx(220_000) and out[1]["tax_krw"] == 0

    def test_세율이_없으면_세액을_비운다(self) -> None:
        out = pf.us_capital_gains_estimates([self._lot(10, 4_000_000)], {10: "2025-03-01"}, None)
        assert out[0]["tax_krw"] is None and out[0]["taxable_krw"] == 1_500_000


class Test지난_구간_매도세는_법정_세율:
    """설정 세율은 지금 구간만 — 2025 년 매도에 2026 년 세율을 물리지 않는다 (docs/infra.md 25.791)."""

    def test_추정_세금은_매도일의_세율(self) -> None:
        from batch.services import backtest as bt_

        rates = pf.CostRates(0.0, 0.0, 0.20, sell_tax_schedule=bt_.KR_SELL_TAX_SCHEDULE)
        _, 세25, 추정 = pf.fee_and_tax(sell(1, "2025-06-02", 100, 100), rates)
        assert 세25 == pytest.approx(10_000 * 0.0015) and 추정
        _, 세26, _ = pf.fee_and_tax(sell(2, "2026-03-03", 100, 100), rates)
        assert 세26 == pytest.approx(10_000 * 0.0020)
        # 설정이 비어도 지난 구간은 법정 표로 안다, 지금 구간은 예전처럼 0(경고)
        빈 = pf.CostRates(0.0, 0.0, None, sell_tax_schedule=bt_.KR_SELL_TAX_SCHEDULE)
        assert pf.fee_and_tax(sell(3, "2021-06-01", 100, 100), 빈)[1] == pytest.approx(23.0)
        assert pf.fee_and_tax(sell(4, "2026-03-03", 100, 100), 빈)[1] == 0.0
        # 입력한 세금은 그대로
        assert pf.fee_and_tax(sell(5, "2025-06-02", 100, 100, tax=7.0), rates)[1] == 7.0

    def test_비용률이_국내에_표를_싣는다(self) -> None:
        from batch.jobs import portfolio as job
        from batch.services import backtest as bt_

        rates, _ = job.cost_rates({}, {"kr_transaction_pct": 0.2})
        assert rates["KRW"].sell_tax_schedule == bt_.KR_SELL_TAX_SCHEDULE
        assert rates["USD"].sell_tax_schedule == ()


class Test마지막날만_빈_가격은_경고하지_않는다:
    """장 마감 전에 넣은 매수는 그날 종가가 다음 배치에야 온다 — 그 경고 하나로 포트폴리오·일일 배치가 partial 이었다 (25.923)."""

    def test_마지막_날만_비면_조용하다(self) -> None:
        trades = [buy(1, "2025-01-01", 100, 10, fee=0), buy(2, "2025-01-03", 50, 10, fee=0)]
        closes = {1: {"2025-01-01": 100.0, "2025-01-02": 101.0, "2025-01-03": 102.0}, 2: {}}
        trades[1] = pf.Trade(**{**trades[1].__dict__, "stock_id": 2})
        rows, warnings = pf.value_series(trades, [], closes, {}, "2025-01-03", {"KRW": NO_COST})
        assert rows[-1].value_krw == pytest.approx(10 * 102 + 10 * 50)  # 체결가로 평가는 그대로
        assert not [w for w in warnings if "가격·환율이 없는 날" in w]

    def test_그_전날이_비면_경고한다(self) -> None:
        trades = [buy(1, "2025-01-01", 100, 10, fee=0)]
        closes = {1: {"2025-01-03": 102.0}, 9: {"2025-01-02": 1.0}}
        _, warnings = pf.value_series(trades, [], closes, {}, "2025-01-03", {"KRW": NO_COST})
        assert [w for w in warnings if "가격·환율이 없는 날" in w]
