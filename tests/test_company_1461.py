"""모의 투자사 — 부서 판단식과 투자위원회 (docs/company.md 2·3장, docs/infra.md 25.1461). 손으로 셀 수 있는 고정 자료로."""

from __future__ import annotations

import pytest

from batch.services import company as co


def _d(sid: int, **kw: object) -> co.Dossier:
    return co.Dossier(stock_id=sid, ticker=f"T{sid:02d}", as_of="2026-10-15", **kw)  # type: ignore[arg-type]


# ── 부서 ──────────────────────────────────────────────────────────────────────


def test_percentile_ranks_ties_single_empty() -> None:
    assert co.percentile_ranks({}) == {}
    assert co.percentile_ranks({1: 5.0, 2: None}) == {1: 50.0}
    # 10 · 20 · 20 · 30 → 순위 0, 1.5, 1.5, 3 → 0, 50, 50, 100
    assert co.percentile_ranks({1: 10, 2: 20, 3: 20, 4: 30}) == {1: 0.0, 2: 50.0, 3: 50.0, 4: 100.0}


def test_research_uses_rank_within_signals_and_renormalizes_missing() -> None:
    """점수는 백분위가 아니다(50 + 50z/3) — 신호 종목 안 순위로 판단한다 (검증 [필수] 1). 빈 팩터는 남은 가중으로."""
    mix = {"value": 0.5, "quality": 0.5, "growth": 0.0}
    ds = [_d(1, factors={"value": 55, "quality": 55}), _d(2, factors={"value": 52, "quality": None}),
          _d(3, factors={"value": 50, "quality": 50}), _d(4, factors={})]  # fmt: skip
    o = co.research(ds, mix)
    # 섞은 값: 55 · 52(퀄리티 빔 → 밸류만) · 50 → 순위 100 · 50 · 0
    assert (o[1].stance, o[1].conviction) == (1, 1.0)
    assert (o[2].stance, o[2].conviction) == (0, 0.0)
    assert (o[3].stance, o[3].conviction) == (-1, 1.0)
    assert o[4].stance == 0 and o[4].note and "자료 없음" in o[4].note
    # 절대 점수 55 는 문턱 60 아래지만 신호 종목 안에서 가장 높아 찬성 — 절대 문턱이면 아무도 찬성하지 못했다
    assert "100 백분위" in o[1].evidence[0]["display"]


def test_quant_and_risk_thresholds() -> None:
    ds = [_d(i, factors={"momentum": float(i)}, mdd=-0.1 * i, volatility=0.1 * i) for i in range(1, 6)]
    q = co.quant(ds)
    # 순위 0·25·50·75·100 → 반대·반대·중립·찬성·찬성
    assert [q[i].stance for i in range(1, 6)] == [-1, -1, 0, 1, 1]
    r = co.risk(ds, max_mdd=-0.45)
    # 위험(MDD 더 음수·변동성 큼)이 클수록 반대. 5 번은 MDD −50% < 한도 −45% → 거부 조건(확신 1)
    assert [r[i].stance for i in range(1, 6)] == [1, 1, 0, -1, -1]
    assert r[5].veto and r[5].conviction == 1.0 and not r[4].veto
    assert co.risk([_d(1)], None)[1].note is not None  # 성과 지표 없음 → 자료 없음


def test_broker_flows_governance_sentiment_macro() -> None:
    # 증권사: 중앙 상승여력 25% → 찬성, 확신 |0.25 − 0.125| / 0.25 = 0.5
    b = co.broker(_d(1, broker_upsides=[("2026-10-01", "가", 0.30), ("2026-10-02", "나", 0.25), ("2026-10-03", "다", 0.10)]))
    assert (b.stance, b.conviction) == (1, 0.5) and b.evidence[0]["as_of"] == "2026-10-03"
    assert co.broker(_d(1, broker_upsides=[("d", "가", 0.03), ("d", "나", 0.04)])).stance == -1
    assert co.broker(_d(1, broker_upsides=[("d", "가", 0.5)])).note  # 1건 → 자료 없음
    assert co.broker(_d(1, domestic=False, broker_upsides=[("d", "가", 0.5)] * 3)).stance == 0  # 미국은 자료 없음
    # 수급: 5일 합 ÷ 20일 평균 거래대금 = 1.0 → 찬성, 확신 0.5
    f = co.flows(_d(1, flow_sum=100.0, turnover_20d=100.0, flow_days=5, flow_date="2026-10-14"))
    assert (f.stance, f.conviction) == (1, 0.5)
    assert co.flows(_d(1, flow_sum=-60.0, turnover_20d=100.0, flow_days=5)).stance == -1
    assert co.flows(_d(1, flow_sum=100.0, turnover_20d=100.0, flow_days=4)).note  # 5거래일 미만
    # 공시·지배구조: 사건 → 반대 1·거부 조건, 전환사채 ≥ 10% → 반대 0.5, 없으면 중립(찬성 안 함)
    g = co.governance(_d(1, gov_events=[("2026-05-01", "fraud")]))
    assert (g.stance, g.conviction, g.veto) == (-1, 1.0, True)
    g = co.governance(_d(1, cb_pct=12.0))
    assert (g.stance, g.conviction, g.veto) == (-1, 0.5, False)
    assert co.governance(_d(1)).stance == 0
    # 감성: +30 → 찬성, 확신 0.5 / −20 → 반대
    s = co.sentiment(_d(1, sentiment=30.0, sentiment_date="2026-10-15"))
    assert (s.stance, s.conviction) == (1, 0.5)
    assert co.sentiment(_d(1, sentiment=-20.0)).stance == -1
    # 시장: 약세 → 반대 0.5, 강세·모름 → 중립
    assert (co.macro(_d(1, regime="bear", regime_index="KOSPI")).stance, co.macro(_d(1, regime="bear")).conviction) == (-1, 0.5)
    assert co.macro(_d(1, regime="bull")).stance == 0 and co.macro(_d(1)).stance == 0


# ── 헌장 ──────────────────────────────────────────────────────────────────────


def test_charters_obey_committee_rules() -> None:
    for aid, ch in co.CHARTERS.items():
        assert co.charter_errors(ch) == [], aid
    # 한 부서가 40% 를 넘으면 위원회를 열지 않는다 — 혼자 문턱을 넘는 부서를 막는다 (검증 [필수] 3)
    나쁨 = {**co.CHARTERS["A"], "weights": {"research": 3, "quant": 1, "risk": 1}}
    assert any("40%" in e for e in co.charter_errors(나쁨))
    assert co.charter_errors({**co.CHARTERS["A"], "lead": "macro", "weights": {"research": 1, "quant": 1}})
    assert co.charter_hash(co.CHARTERS["A"]) != co.charter_hash(co.CHARTERS["D"])


# ── 위원회 ─────────────────────────────────────────────────────────────────────

헌장 = {"weights": {"research": 1, "quant": 1, "sentiment": 1, "governance": 1}, "lead": "research",
       "veto": ["governance"], "threshold": 1.0, "agenda": 20, "top_n": 2,
       "research_mix": {"value": 1}}  # fmt: skip


def test_meet_quorum_lead_veto_and_order() -> None:
    ds = [
        # 1: 리서치·퀀트·감성 찬성 → 가결 (표 1+1+0.5 = 2.5)
        _d(1, factors={"value": 90, "momentum": 90}, sentiment=30.0, domestic=False),
        # 2: 리서치만 강하게 찬성 → 정족수 미달
        _d(2, factors={"value": 95, "momentum": 50}, domestic=False),
        # 3: 퀀트·감성 찬성, 주관 부서(리서치) 반대 → 쟁점에서 주관 부서 반대 = 부결
        _d(3, factors={"value": 10, "momentum": 95}, sentiment=60.0, domestic=False),
        # 4: 찬성 셋이지만 지배구조 사건 → 거부권
        _d(4, factors={"value": 85, "momentum": 85}, sentiment=60.0, gov_events=[("2026-03-01", "fraud")]),
        # 5: 가결 — 1 과 함께 표 순으로 top_n. 6: 리서치·퀀트 반대
        _d(5, factors={"value": 92, "momentum": 92}, sentiment=60.0, domestic=False),
        _d(6, factors={"value": 20, "momentum": 20}, domestic=False),
        # 7~12: 가운데 종목들 — 백분위가 신호 종목 안 순위라 찬성은 위쪽 몇 개만 받는다
        *[_d(k, factors={"value": 33 + k, "momentum": 33 + k}, domestic=False) for k in range(7, 13)],
    ]
    items, approved = co.meet(ds, 헌장)
    by = {i.stock_id: i for i in items}
    assert by[1].passed and by[1].favor == 3
    assert not by[2].passed and "정족수" in by[2].reason
    assert not by[3].passed and by[3].issue and "주관 부서" in by[3].reason and by[3].minority == ("quant", "sentiment")
    assert not by[4].passed and by[4].vetoed_by == ("governance",) and "거부권" in by[4].reason
    assert not by[6].passed and not any(by[k].passed for k in range(7, 13))
    # 가결: 1·5 (top_n 2) — 표 순
    assert [i.stock_id for i in approved] == sorted([1, 5], key=lambda s: (-by[s].vote, f"T{s:02d}"))
    # 가결된 1 의 소수의견 = 반대한 부서(없음)
    assert by[1].minority == ()


def test_lead_bonus_no_longer_swallows_dissent() -> None:
    """초안은 쟁점이면 주관 부서에 가중을 더해 소수 반대를 지웠다(0.74 → 1.07 가결, 검증 [필수] 3) — 지금은 표 그대로."""
    ch = {**헌장, "threshold": 1.5}
    ds = [_d(1, factors={"value": 99, "momentum": 70}, sentiment=-30.0, domestic=False),
          _d(2, factors={"value": 0, "momentum": 0}, domestic=False),
          _d(3, factors={"value": 50, "momentum": 99}, domestic=False)]  # fmt: skip
    items, _ = co.meet(ds, ch)
    i = next(x for x in items if x.stock_id == 1)
    # 리서치 +1·확신 1, 퀀트(순위 50 → 중립), 감성 −1·확신 0.5 → 표 0.5 < 1.5
    assert i.issue and not i.passed and i.vote == pytest.approx(0.5)


def test_memo_and_minutes_carry_no_evidence() -> None:
    ds = [_d(1, factors={"value": 90, "momentum": 90}, sentiment=30.0, domestic=False),
          _d(2, factors={"value": 10, "momentum": 10}, domestic=False)]  # fmt: skip
    items, approved = co.meet(ds, 헌장)
    m = co.memo("가치투자사", items, approved)
    assert m.startswith("가치투자사 위원회: 안건 2 · 가결 1 · 부결 1 · 거부 0") and "T01" in m
    j = co.minutes_json(items, approved, 헌장, m)
    assert j["items"][0]["approved"] and "evidence" not in str(j)  # 근거는 paper_dossiers 로 (5장)
    assert j["items"][1]["opinions"]["governance"][2].startswith("자료 없음")
