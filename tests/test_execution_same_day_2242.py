"""실행 품질 — 리포트 거래일과 같은 날 매수는 그 리포트에 잇지 않는다 (docs/infra.md 25.2242, 11-20 결함 회차 [권고]).

리포트 거래일 D(전일 데이터)의 주문표는 D 다음 장 아침에 나온다. 예전엔 0일을 받아 D 장중 매수가 아직 나오지 않은
D 리포트의 1차 가격에 묶였다(1차 대비 +10%·준수 0/1 — 실제 본 주문표로는 −1%·1/1)."""

from __future__ import annotations

import pytest

from batch.services import execution_quality as eq

_계획 = [{"stock_id": 1, "report_date": "2026-10-29", "prices": [10_000, 9_800, 9_600]},
        {"stock_id": 1, "report_date": "2026-10-30", "prices": [9_000, 8_800, 8_600]}]  # fmt: skip


def test_같은_날_매수는_앞_리포트에_묶인다() -> None:
    매매 = [{"stock_id": 1, "side": "buy", "trade_date": "2026-10-30", "price": 9_900}]
    m = eq.measure(매매, _계획, [], [], "2026-10-01", "2026-10-31")
    assert m["vs_first_avg"] == pytest.approx(-1.0)
    assert (m["buys_on_plan"], m["buys_within_limit"]) == (1, 1)


def test_같은_날_1부에만_있던_종목은_리포트_밖() -> None:
    매매 = [{"stock_id": 2, "side": "buy", "trade_date": "2026-10-30", "price": 50_000}]
    m = eq.measure(매매, [], [{"stock_id": 2, "report_date": "2026-10-30"}], [], "2026-10-01", "2026-10-31")
    assert (m["buys_pick_only"], m["buys_off_plan"]) == (0, 1)
