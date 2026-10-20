"""부서 결정 기여 — 회의록으로 부서 하나 빼고 다시 열기 (docs/factors.md 12.36 33회차 P2, docs/infra.md 25.1589).

지키는 것
- `meet` 의 표결을 뗀 `tally` 가 같은 결과(판단 불변)
- 회의록(`minutes_json`)에서 다시 연 회의가 저장된 표·가결·집행과 같다(어긋나면 빼고 mismatch)
- 손계산: 퀀트를 빼면 1번이 정족수 미달로 떨어져 집행이 1 → 2 로 바뀌고, 리스크를 빼면 그대로
"""

from __future__ import annotations

import json

from batch.services import company as co

헌장 = {"weights": {"research": 2, "quant": 1, "risk": 1}, "lead": "research", "veto": ["risk"], "threshold": 1,
        "top_n": 1, "agenda": 2}  # fmt: skip


def _표() -> dict[int, dict[str, co.Opinion]]:
    op = co.Opinion
    return {
        1: {"research": op("research", 1, 1.0, []), "quant": op("quant", 1, 1.0, []), "risk": op("risk", 0, 0.0, [])},
        2: {"research": op("research", 1, 0.5, []), "quant": op("quant", 1, 0.5, []), "risk": op("risk", 1, 1.0, [])},
    }


def _회의록() -> dict:
    items, approved = co.tally([(1, "AAA"), (2, "BBB")], _표(), 헌장)
    return json.loads(json.dumps(co.minutes_json(items, approved, 헌장, "m")))


def test_손계산() -> None:
    items, approved = co.tally([(1, "AAA"), (2, "BBB")], _표(), 헌장)
    assert [(i.vote, i.passed) for i in items] == [(3.0, True), (2.5, True)] and [i.stock_id for i in approved] == [1]
    r = co.decisive_share([_회의록()], 헌장)
    assert r["meetings"] == 1 and r["mismatch"] == 0
    assert r["depts"] == {"quant": {"n": 1, "changed": 1}, "risk": {"n": 1, "changed": 0}}  # 주관 리서치는 빼 보지 않는다


def test_회의록이_결정을_재현하지_못하면_뺀다() -> None:
    록 = _회의록()
    록["items"][0]["vote"] = 9.0
    r = co.decisive_share([록, _회의록()], 헌장)
    assert (r["meetings"], r["mismatch"]) == (1, 1)


def test_거부권과_자료없음_메모를_되살린다() -> None:
    표 = _표()
    표[1]["risk"] = co.Opinion("risk", -1, 1.0, [], veto=True)
    표[2]["quant"] = co.Opinion("quant", 0, 0.0, [], note="자료 없음 — 테스트")
    items, approved = co.tally([(1, "AAA"), (2, "BBB")], 표, 헌장)
    # 1 은 리스크 거부, 2 는 리서치 1.0 + 리스크 1.0 = 2.0 · 찬성 둘로 가결
    assert items[0].vetoed_by == ("risk",) and [i.stock_id for i in approved] == [2]
    록 = json.loads(json.dumps(co.minutes_json(items, approved, 헌장, "m")))
    r = co.decisive_share([록], 헌장)
    # 리스크를 빼면 1 의 거부가 풀려(표 3.0) 집행이 2 → 1 로 바뀐다. 퀀트(자료 없음)를 빼도 그대로
    assert r["mismatch"] == 0 and r["depts"] == {"quant": {"n": 1, "changed": 0}, "risk": {"n": 1, "changed": 1}}


def test_다른_헌장의_회의록은_재현하지_않는다() -> None:
    """지문이 다르거나 헌장 가중에 없는 부서 의견이 있으면 mismatch (25.1590, 25.1589 반박 [권고] 1)."""
    다른 = {**헌장, "threshold": 2}
    assert co.decisive_share([_회의록()], 다른)["mismatch"] == 1
    록 = _회의록()
    록["items"][0]["opinions"]["macro"] = [0, 0.0]
    assert co.decisive_share([록], 헌장)["mismatch"] == 1  # KeyError 로 멈추지 않는다


def test_무작위_회의_왕복은_늘_재현된다() -> None:
    """고정 씨앗 무작위 안건 — 회의록 왕복이 표·가결·집행을 늘 재현하고(mismatch 0), 부서를 뺀 결과가 가중 0 헌장의 tally 와 같다."""
    import random

    rng = random.Random(1589)
    부서 = ["research", "quant", "risk", "flows", "macro"]
    for _ in range(300):
        가중 = {k: rng.choice([0, 0.5, 1, 2]) for k in 부서}
        가중["research"] = 2
        h = {"weights": 가중, "lead": "research", "veto": rng.sample(부서, rng.randint(0, 2)),
             "threshold": rng.choice([0.5, 1, 1.5]), "top_n": rng.randint(1, 4), "agenda": 8}  # fmt: skip
        쓰는 = [k for k, w in 가중.items() if w > 0]
        안건 = [(i, f"T{rng.randint(0, 5)}") for i in range(rng.randint(0, 8))]
        표 = {sid: {k: co.Opinion(k, rng.choice([-1, 0, 1]), rng.random(), [], veto=rng.random() < 0.1) for k in 쓰는}
             for sid, _t in 안건}  # fmt: skip
        items, approved = co.tally(안건, 표, h)
        록 = json.loads(json.dumps(co.minutes_json(items, approved, h, "m")))
        r = co.decisive_share([록], h)
        assert r["mismatch"] == 0
        기준 = {x.stock_id for x in approved}
        for k in 쓰는:
            if k == "research":
                continue
            _i, 뺀 = co.tally(안건, {s: {d: o for d, o in v.items() if d != k} for s, v in 표.items()},
                              {**h, "weights": {**가중, k: 0}})  # fmt: skip
            assert r["depts"][k]["changed"] == int({x.stock_id for x in 뺀} != 기준)
