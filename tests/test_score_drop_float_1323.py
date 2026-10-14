"""정확히 20점 하락이 부동소수 찌꺼기로 빗나가지 않는다 (docs/infra.md 25.1323, 매도 플래그 감사 #1).

64.1 − 44.1 = 19.999999999999993 — 예전에는 "20점 이상 하락" 재무악화 플래그가 뜨지 않았다."""

from __future__ import annotations

import pytest

from batch.services import sell_flags as sf
from tests.test_sell_flags import TODAY, codes, holding


@pytest.mark.parametrize(("buy", "now"), [(64.1, 44.1), (50.3, 30.3), (66.6, 46.6)])
def test_정확히_20점_하락은_플래그(buy: float, now: float) -> None:
    assert buy - now < 20  # 부동소수로는 20 아래 — 이 시험이 지키는 경우
    assert codes(sf.evaluate(holding(score_at_buy=buy, score_now=now), TODAY)) == [("yellow", "재무악화")]


def test_20점_못_미치면_그대로_없다() -> None:
    assert sf.evaluate(holding(score_at_buy=64.1, score_now=44.2), TODAY) == []
