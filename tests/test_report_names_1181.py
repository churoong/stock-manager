"""미국 리포트 2부·"배분에서 빠진 종목" 에 이름 꼬리가 남던 것 (docs/infra.md 25.1181, 감사 재현)."""

from __future__ import annotations

from batch.services import report_picks as rp


def _row(i: int, name: str, **kw) -> rp.SignalRow:  # noqa: ANN003
    base = dict(stock_id=i, ticker=f"T{i}", name=name, market="NASDAQ", horizon="mid", signal_type="실적 모멘텀",
                currency="USD", buy_zone_low=90.0, buy_zone_high=100.0, suggested_weight_pct=5.0,
                suggested_amount=1000.0, size_reduction=1.0,
                tranche_plan=[{"step": 1, "ratio": 1.0, "price": 100, "amount": 1000}], rationale_text="매출 +18.2%",
                as_of_date="2026-10-08", total_score=80.0 - i, close=100.0, close_date="2026-10-08",
                criteria=[{"name": "x", "label": "매출", "display": "18.2%", "value": 18.2, "threshold": ">0",
                           "source": "sec", "passed": True}])  # fmt: skip
    base.update(kw)
    return rp.SignalRow(**base)  # type: ignore[arg-type]


def test_2부와_제외_줄에도_이름_꼬리를_뗀다() -> None:
    rows = [_row(1, "Kennametal Inc. Common Stock"),
            _row(2, "Acme Holding Ltd - Ordinary Shares", suggested_amount=None, suggested_weight_pct=0.0,
                 size_reduction=0.0)]  # fmt: skip
    글 = rp.compose(rows, 20000.0, "2026-10-08", currency="USD").text
    assert "Common Stock" not in 글 and "Ordinary Shares" not in 글
    assert "Kennametal Inc." in 글 and "Acme Holding Ltd" in 글
