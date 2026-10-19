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
    logs = [SimpleNamespace(day=_날(1, d)[0], cutoff=c, buys={7: 0.1}, held=frozenset()) for d, c in
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
    # 핵심 H 는 매수가 드물어 하루뿐인 묶음도 센다(25.1544 [필수] 3 — 운영 증거는 사흘)
    assert e["n_blocks"] == 1 and list(e["blocks"]) == [str(pp._묶음(날[4]))]


def test_짝은_H_G_하나_같은_묶음끼리() -> None:
    """G−B 는 매도 규칙만 달라 매수 성과 차이가 매도의 몫이 아니다 — 짝에서 뺐다(25.1544 [필수] 2)"""
    by = {"core_h": {"excess_blocks": {"1": 3.0, "2": 2.0, "3": 1.0}},
          "core_g": {"excess_blocks": {"1": 1.0, "2": 1.0, "4": 9.0}},
          "core_b": {"excess_blocks": {}}}  # fmt: skip
    p = ce.pairs(by)
    assert set(p) == {"core_h-core_g"}
    assert p["core_h-core_g"]["n_blocks"] == 2 and p["core_h-core_g"]["mean"] == pytest.approx(1.5)


def test_추가_매수는_새_선정이_아니다() -> None:
    """판단 직전 든 종목에 더 산 것은 빼고 센다(25.1544 [권고] 4)"""
    logs = [SimpleNamespace(day="2025-01-02", cutoff="2025-01-01", buys={7: 0.1}, held=frozenset()),
            SimpleNamespace(day="2025-03-02", cutoff="2025-03-01", buys={7: 0.05, 8: 0.1}, held=frozenset({7}))]  # fmt: skip
    assert ce.first_buys(logs) == [("2025-01-01", 7), ("2025-03-01", 8)]


def test_관문과_같은_잣대는_안_산_새_후보_안_분위() -> None:
    prices = _자료(10)
    # 날마다 9·10 을 사고(buy) 1~5 를 안 샀다(skip) — 산 것이 늘 위라 분위 100
    logs = []
    for k in range(60):
        d = 날[k]
        logs.append(SimpleNamespace(day=d, cutoff=d, buys={}, decisions=[
            *({"stock_id": s, "kind": "buy", "reason": "2부 배분", "ref_date": d} for s in (9, 10)),
            *({"stock_id": s, "kind": "skip", "reason": "상한", "ref_date": d} for s in range(1, 6)),
            {"stock_id": 6, "kind": "blocked", "reason": "위험 점검: x", "ref_date": d},
        ]))  # fmt: skip
    g = ce.gate_evidence(logs, ce.Closes(prices))
    assert g["days"] == 60 and g["mean"] == pytest.approx(100.0) and g["n_blocks"] >= 2


def test_판_뒤는_미룬_매도를_빼고_지수_대비() -> None:
    # 종목 1 은 매일 −1%, 지수 제자리 — 판 뒤 20일 초과 ≈ −18.2%(낮을수록 잘 판 것)
    prices = {1: {d: 100 * 0.99**k for k, d in enumerate(날)}}
    logs = [SimpleNamespace(day=날[k + 1], cutoff=날[k], sells={1: ["손절"]}) for k in (0, 30, 60)]
    logs.append(SimpleNamespace(day=날[91], cutoff=날[90], sells={1: ["미룬 매도"]}))
    a = ce.after_sell(logs, ce.Closes(prices), {d: 1000.0 for d in 날})
    assert a["sells"] == 3 and a["mean"] == pytest.approx((0.99**20 - 1) * 100)


def _v(n: int, lo: float | None) -> dict:
    return {"n_blocks": n, "lower": lo}


def test_사전_등록_판정() -> None:
    짝 = _v(6, 0.1)
    좋음 = {"core_h": _v(6, 52.0), "core_r1": _v(6, 45.0), "core_r2": _v(6, None)}
    assert ce.verdict(좋음, 짝) == "선정에 엣지 근거"
    assert ce.verdict({**좋음, "core_h": _v(5, 60.0)}, 짝) == "판정 불가(묶음 부족)"
    assert ce.verdict(좋음, _v(5, 1.0)) == "판정 불가(묶음 부족)"
    assert ce.verdict({**좋음, "core_r2": _v(6, 50.5)}, 짝).startswith("판정 불가(대조 모집단 편향")
    assert ce.verdict({**좋음, "core_h": _v(6, 49.0)}, 짝) == "근거 없음"
    assert ce.verdict(좋음, _v(6, -0.1)) == "근거 없음"
