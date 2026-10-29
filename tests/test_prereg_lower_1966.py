"""25.1966 (25.1963 교차검증 [필수]) — 판마다 사전 등록 지표의 `lower` 는 사전 등록한 한쪽 95% 하한 그대로다.

25.1963 이 관문 선정 칸의 `now` 를 Pocock 하한(α 0.0221)으로 바꾸자 `prereg_status` 가 그 칸을 읽어 모든 판의
사전 등록 값이 소리 없이 더 엄격한 잣대로 적혔다. 손 계산: 평균 60, 표준오차 2, 묶음 6 →
Pocock 하한 60 − t(0.9779, 5) × 2 ≈ 54.5, 사전 등록 값은 증거의 95% 하한 55.9 그대로
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.services import desk
from batch.services import paper as pp


def test_prereg_lower_is_registered_95_not_pocock() -> None:
    날 = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(80)]
    a = [(d, 1 + 0.002 * i) for i, d in enumerate(날)]
    r = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    ev = {"mean": 60.0, "se": 2.0, "lower": 55.9, "n_blocks": 6}
    out = desk.prereg_status({"A": a, "R1": r, "R2": r}, {"A": (1, "")}, {"A": ev}, {})
    assert out["A"]["lower"] == 55.9
    pocock = 60.0 - pp.t_quantile(1 - desk.GATE_POCOCK_ALPHA, 5) * 2.0
    assert pocock == pytest.approx(54.5, abs=0.2) and out["A"]["lower"] != pytest.approx(pocock)
    # 관문 칸은 Pocock 그대로(H 관문에만 쓴다)
    g = desk.gate({"A": a, "R1": r, "R2": r}, "A", recon_bad_days=0, batch_failed=0, kis=None, evidence=ev)
    칸 = {i["key"]: i for i in g["items"]}["selection"]
    assert 칸["now"] == pytest.approx(pocock) and 칸["lower_95"] == 55.9
