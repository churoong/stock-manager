"""KIS 실현손익 보유 구간은 KIS 0 기준점에서만 닫는다 (docs/infra.md 25.2349 — 11-24 결함 루프 건너뛴 ⓑ).

기록 보유가 0 이 되는 것만으로 새 구간을 열면, 그 뒤 KIS > 기록이 드러나도 앞 매도의 값이 영구히 남았다.
"""

from __future__ import annotations

from batch.services import broker_mirror as bm
from tests.test_broker_realized_1698 import _o

# 25.1701 S1 과 같은 행 — 10주 @100(기록) + 기록 밖 5주가 실제로 찬 채 10주 @120 매도(기록 0, KIS 5), 3주 @110 다시 삼,
# 8주 @125 매도. KIS 평균 원가로 맞는 첫 매도 값은 +100 — 기록 원가로는 +200
_행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(3, "2026-10-15", "sell", 10, 120.0),
      _o(4, "2026-10-16", "buy", 3, 110.0), _o(5, "2026-10-19", "sell", 8, 125.0)]  # fmt: skip


def test_10_16_기준점이_빠져도_앞_매도를_모름으로() -> None:
    # 10-16 개장 전 기준점이 빠지고(개장 뒤 덮어씀·잔고 잘림) 10-19 기준점만 — 그날 KIS 8 > 기록 3 이 드러남
    r = bm.realized(_행, lambda d: None, {}, snapshots=[("2026-10-19", {"C1": 8})])
    assert r[3] == (None, None)


def test_기준점이_아예_없어도_뒤에_기록보다_많이_팔면_앞_매도도_모름() -> None:
    r = bm.realized(_행, lambda d: None, {}, snapshots=[])
    assert r[3] == (None, None) and r[5] == (None, None)


def test_KIS_0_을_확인한_한_바퀴는_그대로() -> None:
    """닫는 길은 KIS 0 기준점 하나 — 깨끗한 한 바퀴(10-16 KIS 0)는 뒤 어긋남이 건드리지 않는다(25.1701 S2 와 같은 잣대)"""
    행 = [_o(1, "2026-10-14", "buy", 10, 100.0), _o(2, "2026-10-15", "sell", 10, 120.0),
         _o(3, "2026-10-19", "buy", 3, 110.0), _o(4, "2026-10-20", "sell", 5, 125.0)]  # fmt: skip
    r = bm.realized(행, lambda d: None, {}, snapshots=[("2026-10-16", {})])
    assert r[2] == (200.0, 0.0) and r[4] == (None, None)
