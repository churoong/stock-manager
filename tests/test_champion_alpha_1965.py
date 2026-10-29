"""25.1965 — 챔피언 결정 단위 하한의 유의수준은 본페로니(0.05 ÷ 도전자 수)와 관문 Pocock 문턱(0.0221) 가운데 작은 쪽.

손 계산: 도전자 1 → min(0.05, 0.0221) = 0.0221 · 2 → min(0.025, 0.0221) = 0.0221 · 3 → min(0.01667, 0.0221) = 0.05/3 ·
0(줄 없음) → 1 로 본다 → 0.0221. 도전자 하나일 때 짝 차이 (2, 3, 4, 2.5, 3.5) 의 하한은
평균 3 − t(1 − 0.0221, 4) × 표준오차 — 고치기 전(α 0.05)보다 낮다
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from batch.services import desk
from batch.services import paper as pp


def test_champion_alpha_hand() -> None:
    assert desk.champion_alpha(1) == pytest.approx(0.0221)
    assert desk.champion_alpha(2) == pytest.approx(0.0221)
    assert desk.champion_alpha(3) == pytest.approx(0.05 / 3)
    assert desk.champion_alpha(6) == pytest.approx(0.05 / 6)
    assert desk.champion_alpha(0) == pytest.approx(0.0221)
    # 더 보수적으로만 — 어떤 도전자 수에도 고치기 전 본페로니보다 크지 않다
    assert all(desk.champion_alpha(n) <= desk.CHAMPION_ALPHA / n for n in range(1, 30))


def _날(n: int) -> list[str]:
    시작 = date(2026, 1, 1)
    return [(시작 + timedelta(days=i)).isoformat() for i in range(n)]


def test_single_challenger_uses_pocock_lower() -> None:
    날 = _날(61)
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    c = [(d, 1 + 0.002 * i) for i, d in enumerate(날)]
    차 = (2.0, 3.0, 4.0, 2.5, 3.5)
    근거 = {"C": {"excess_blocks": {str(k): 1.0 + v for k, v in enumerate(차)}},
          "H": {"excess_blocks": {str(k): 1.0 for k in range(5)}}}  # fmt: skip
    r = {x["id"]: x for x in desk.champion({"H": h, "C": c}, "H", 근거)}["C"]
    ev = r["evidence_diff"]
    assert ev["alpha"] == pytest.approx(0.0221)
    m = sum(차) / 5
    se = math.sqrt(sum((x - m) ** 2 for x in 차) / 4) / math.sqrt(5)
    assert ev["lower"] == pytest.approx(m - pp.t_quantile(1 - 0.0221, 4) * se)
    assert ev["lower"] < m - pp.t_quantile(1 - 0.05, 4) * se
    assert r["candidate"]
