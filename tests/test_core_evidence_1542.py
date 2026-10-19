"""핵심 H 결정 단위 판정 (docs/backtest.md 10장 조각 1e, docs/infra.md 25.1542) — 손으로 셀 수 있는 작은 자료."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from batch.services import core_evidence as ce
from batch.services import paper as pp


def _날(n: int, 시작: str = "2025-01-01") -> list[str]:
    d0 = date.fromisoformat(시작)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


날 = _날(200)


def _자료(N: int = 30) -> dict[int, dict[str, float]]:
    # 종목 i 는 하루 i×0.01% 씩 오른다 — 20일 수익이 i 순서
    return {i: {d: 100 * (1 + 0.0001 * i) ** k for k, d in enumerate(날)} for i in range(1, N + 1)}


def test_앞으로_n번째_종가() -> None:
    c = ce.Closes({1: {"2025-01-01": 100.0, "2025-01-02": 110.0, "2025-01-03": 121.0}})
    assert c.forward(1, "2025-01-01", 2) == (pytest.approx(21.0), "2025-01-03")
    assert c.forward(1, "2025-01-02", 2) is None  # 안 참
    assert c.forward(1, "2024-12-31", 1) is None  # 기준일 종가 없음


def test_에피소드_첫_매수만() -> None:
    logs = [SimpleNamespace(day=_날(1, d)[0], cutoff=c, buys={7: 0.1}) for d, c in
            (("2025-01-02", "2025-01-01"), ("2025-01-10", "2025-01-09"), ("2025-02-05", "2025-02-04"))]  # fmt: skip
    # 01-10 은 01-02 뒤 8일 — 같은 에피소드, 02-05 는 34일 — 새 에피소드
    assert ce.first_buys(logs) == [("2025-01-01", 7), ("2025-02-04", 7)]


def test_가장_오른_종목을_사면_분위_100() -> None:
    prices = _자료(60)
    c = ce.Closes(prices)
    # 28일 묶음마다 사흘(묶음 하한 3일) × 묶음 다섯 — 늘 위쪽 15종목(46~60, 서로 달라 에피소드가 겹치지 않음)을 산다
    logs = []
    for b in range(5):
        for k in range(3):
            d = 날[28 * b + 7 * k]
            logs.append(SimpleNamespace(day=d, cutoff=d, buys={46 + 3 * b + k: 0.1}))
    e = ce.evidence(logs, c, lambda _d: set(prices), {d: 1000.0 for d in 날})
    assert e["buys"] == len(logs) and e["scored"] == len(logs) and e["no_control"] == 0
    # 무작위 대조 20 가운데 산 것보다 높은 것은 위쪽 몇 개뿐 — 분위 평균이 높고 하한이 50 을 넘는다
    assert e["n_blocks"] >= 4 and e["mean"] >= 70 and e["lower"] > 50  # 28일 묶음 경계가 달력 서수라 넷이나 다섯
    # 지수 제자리라 초과 = 20일 수익
    assert all(v > 0 for v in e["excess_blocks"].values())


def test_대조가_모자라면_분위를_내지_않는다() -> None:
    prices = _자료(8)  # 유니버스 8 — 대조 7 < 10
    logs = [SimpleNamespace(day=날[0], cutoff=날[0], buys={3: 0.1})]
    e = ce.evidence(logs, ce.Closes(prices), lambda _d: set(prices), {})
    assert (e["buys"], e["scored"], e["no_control"], e["n_blocks"]) == (1, 0, 1, 0)


def test_대조는_가상모의투자와_같은_씨앗() -> None:
    prices = _자료()
    본 = []

    def 시점(d: str) -> set[int]:
        본.append(d)
        return set(prices)

    logs = [SimpleNamespace(day=날[5], cutoff=날[4], buys={3: 0.1})]
    e = ce.evidence(logs, ce.Closes(prices), 시점, {})
    assert 본 == [날[4]]  # 기준일 = cutoff 의 시점 유니버스
    기대대조 = pp.control_picks(sorted(prices), "KR", 날[4], 3)
    c = ce.Closes(prices)
    q = pp.control_pct(c.forward(3, 날[4], 20)[0], [c.forward(x, 날[4], 20)[0] for x in 기대대조])
    assert e["scored"] == 1 and q is not None and e["days"] == 1
    assert e["n_blocks"] == 0 and e["blocks"] == {}  # 하루뿐인 묶음은 묶음 하한(사흘) 밑이라 세지 않는다


def test_짝_차이는_같은_묶음끼리() -> None:
    by = {"core_h": {"excess_blocks": {"1": 3.0, "2": 2.0, "3": 1.0}},
          "core_g": {"excess_blocks": {"1": 1.0, "2": 1.0, "4": 9.0}},
          "core_b": {"excess_blocks": {}}}  # fmt: skip
    p = ce.pairs(by)
    assert p["core_h-core_g"]["n_blocks"] == 2 and p["core_h-core_g"]["mean"] == pytest.approx(1.5)
    assert p["core_g-core_b"]["n_blocks"] == 0
