"""복기 문장·비중이 웹과 같은 반올림 (docs/infra.md 25.1401, 포트폴리오 감사)."""

from __future__ import annotations

import dataclasses

from batch.services import portfolio as pf
from batch.services import review as rv


def _복기(**kw: object) -> rv.Review:
    값 = {f.name: None for f in dataclasses.fields(rv.Review)}
    값.update(buy_trade_id=1, stock_id=1, horizon="mid", buy_date="2026-01-01", last_sell_date="2026-01-13",
              currency="KRW", quantity_sold=1, quantity_bought=1, partial=False, cost=1, proceeds=1, return_pct=0.0625,
              holding_days=12.5, realized_pnl_krw=0, price_pnl_krw=0, fx_pnl_krw=0, has_snapshot=True,
              score_at_trade=72.5, signal_type_at_trade="실적 모멘텀", outcome="none")  # fmt: skip
    값.update(kw)
    return rv.Review(**값)  # type: ignore[arg-type]


def test_복기_문장은_웹과_같이_반값을_올린다() -> None:
    글 = rv.verdict(_복기())
    # 예전: "매수 시 점수 72"·"+6.2% (12일)" — 웹 근거표는 73·13일·+6.3%
    assert "매수 시 점수 73" in 글 and "+6.3% (13일)" in 글


def _보유(sid: int, 값: float) -> pf.Position:
    p = pf.Position(stock_id=sid, quantity=1, currency="KRW", avg_price=1, avg_fx=1, cost=1, cost_krw=1,
                    first_buy_date="2026-01-01", horizon=None)  # fmt: skip
    p.market_value_krw = 값
    return p


def test_비중은_넷째_자리_섹터_상한은_반올림_전_값() -> None:
    # 반도체 12.2496% — 예전 12.25 로 저장돼 웹 한 자리 "12.3%"(맞는 값 12.2)
    out = pf.allocation([_보유(1, 122_496), _보유(2, 877_504)], {1: "반도체", 2: "은행"}, 90.0)
    assert out["by_sector"]["반도체"] == 12.2496
    # 30.004% 는 30% 상한을 넘는다 — 예전엔 30.00 으로 깎아 "넘지 않음"
    out = pf.allocation([_보유(1, 300_040), _보유(2, 699_960)], {1: "반도체", 2: "은행"}, 30.0)
    assert "반도체" in out["sectors_over_cap"]
