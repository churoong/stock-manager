"""켈리 참고 비중 (docs/analysis.md 35장, docs/infra.md 25.1058).

이론값이 틀리면 "최대 40% 까지 실어도 된다" 같은 위험한 숫자가 된다. 그래서 식이 **실제로 로그 성장을 최대로 하는 점**인지
수치로 확인하고, 기대값이 0 이하면 0 인지, 사다리·근거 줄에 실리는지를 묶는다.
"""

from __future__ import annotations

import math
from datetime import date

from batch.services import verdict as vd


def _성장(f: float, p: float, up: float, down: float) -> float:
    return p * math.log(1 + f * up) + (1 - p) * math.log(1 - f * down)


def test_켈리는_로그_성장의_꼭대기() -> None:
    for p, up, down in ((0.45, 0.10, 0.07), (0.45, 0.25, 0.15), (0.4, 0.5, 0.25)):
        f = vd.kelly(p, up, down)
        assert 0 < f < 1
        격자 = [i / 10000 for i in range(1, 10000)]
        최고 = max(격자, key=lambda x: _성장(x, p, up, down))
        assert math.isclose(f, 최고, abs_tol=2e-4)
    assert math.isclose(vd.kelly(0.6, 1.0, 1.0), 0.2)  # 대칭 내기 2p − 1
    # 손절이 촘촘하면 식의 값이 1 을 넘는다(빚을 내라는 뜻) — 빚 없는 꼭대기 1 로 자른다
    assert vd.kelly(0.6, 0.10, 0.07) == 1.0
    assert _성장(1.0, 0.6, 0.10, 0.07) > _성장(0.99, 0.6, 0.10, 0.07)


def test_기대값이_0_이하면_0() -> None:
    assert vd.kelly(0.4, 0.10, 0.10) == 0.0
    assert vd.kelly(0.5, 0.0, 0.1) is None


def test_사다리와_근거_줄() -> None:
    o = vd.outlook(close=100.0, close_date="2026-10-07", currency="KRW", momentum=None,
                   risk={"volatility_ann": 0.3, "beta": 1.0}, band=None, opinions=[], today=date(2026, 10, 8),
                   market={"annual": 0.08, "years": 10, "since": "a", "until": "b"}, rf=0.03)  # fmt: skip
    signals = [{"horizon": "short", "as_of": "2026-10-07", "buy_zone_low": 97, "buy_zone_high": 101,
                "target_price": 110, "stop_price": 93}]  # fmt: skip
    b = vd.build(vd.Inputs(name="가", ticker="1", score={"as_of": "d", "total": 60}, signals=signals, outlook=o))
    r = b["outlook"]["ladder"]["race"]
    assert math.isclose(r["up"], 0.10) and math.isclose(r["down"], 0.07)
    assert math.isclose(r["kelly"], vd.kelly(r["p"], 0.10, 0.07))
    assert any(x.startswith("켈리 참고 비중(이론상 최대, 비교용):") for x in b["reasons"])
