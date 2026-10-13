"""업종을 모르는 보유는 섹터 상한·섹터 비중에 못 넣는다고 2부가 밝힌다 (docs/infra.md 25.1314, 2부 감사 재현)."""

from __future__ import annotations

from batch.services import report_picks as rp

업종 = "전자부품·컴퓨터·영상·음향 및 통신장비 제조업"
총액 = 100_000_000.0


def _row() -> rp.SignalRow:
    amt = 총액 * 10 / 100
    tr = [{"step": i + 1, "ratio": r, "price": 70000.0 * (1 - 0.03 * i), "amount": amt * r}
          for i, r in enumerate((0.4, 0.3, 0.3))]  # fmt: skip
    row = rp.SignalRow(stock_id=1, ticker="T1", name="N1", market="KOSPI", horizon="mid", signal_type="x",
                       currency="KRW", buy_zone_low=65800.0, buy_zone_high=70000.0, suggested_weight_pct=10.0,
                       suggested_amount=amt, size_reduction=1.0, tranche_plan=tr, total_score=90, sector=업종,
                       as_of_date="2026-10-12")  # fmt: skip
    row.criteria = [{"label": "x", "display": "1", "threshold": "r", "source": "s"}]
    return row


def test_업종_모르는_보유를_밝힌다() -> None:
    holdings = [rp.Holding(7, "X", "소형", None, 25_000_000.0, country="KR"),
                rp.Holding(8, "Y", "중형", 업종, 15_000_000.0, country="KR")]  # fmt: skip
    text = rp.compose([_row()], 총액, "2026-10-12", holdings=holdings, min_order_amount=100000).text
    assert "업종을 모르는 보유 1종목(총액의 25.0%)은 섹터 상한·섹터 비중에 넣지 못했습니다" in text
    assert "(업종 모르는 1종목은 섹터 비중 제외)" in text


def test_모두_업종을_알면_말하지_않는다() -> None:
    holdings = [rp.Holding(8, "Y", "중형", 업종, 15_000_000.0, country="KR")]
    text = rp.compose([_row()], 총액, "2026-10-12", holdings=holdings, min_order_amount=100000).text
    assert "업종을 모르는" not in text and "업종 모르는" not in text


def test_메모의_비율도_배분_줄과_같은_반올림() -> None:
    """4.25% 는 배분 줄 `fixed` 로 4.3% — 메모도 같은 규칙이어야 한다 (25.1315, 파이썬 `:.1f` 는 짝수 쪽 4.2%)."""
    row = _row()
    row.suggested_weight_pct, row.suggested_amount = 4.25, 총액 * 4.25 / 100
    row.tranche_plan = [{**t, "amount": row.suggested_amount * t["ratio"]} for t in row.tranche_plan]
    text = rp.compose([row], 총액, "2026-10-12", min_order_amount=100000, max_stock_pct=4.25).text
    assert "(4.3%)" in text and "4.2%" not in text
