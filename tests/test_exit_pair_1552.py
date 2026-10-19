"""같은 로트 청산 대조(`exit_pair`)·하루 손실 관문 빈도(`loss_gate`)·가중 대조 상한 본페로니
(docs/backtest.md 10장, docs/factors.md 12.35 32회차 P1·P4, docs/infra.md 25.1552).

지키는 것
- 진짜 전략(core_h)이 매도 판정의 "목표도달" 날과 투자 비중을 하루 기록에 남기고, 청산 대조가 그날 종가와 실제 추적 매도 날
  종가로 로트 하나의 d 를 낸다(손계산)
- 못 산 것·상장폐지·목표 전 추가 매수·끝 절단을 따로 세고, 끝 절단은 결정에서 뺀다(나간 것만 세지 않는다)
- 뉴이–웨스트 시차 3 표준오차(손계산)와 사전 등록 결정 규칙, 상한·하한 모두 한쪽 97.5%
- 관문 빈도는 사유 글이 아니라 코드 기준 문턱, KOSPI 같은 창, 시장 조정 한도의 날 수(손계산)
- 가중 대조(`holding_eq`)의 `equal` 상한이 97.5%(바꾸는 갈래만 — `keep` 하한은 95% 그대로)
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from batch.services import core_evidence as ce
from batch.services import core_strategy as cs
from batch.services import exit_pair as xp
from batch.services import holding_eq as heq
from batch.services import loss_gate as lg
from batch.services import paper as pp
from batch.services import risk_gate as rg
from tests.test_core_strategy_1532 import _날별, _돌림


def _날(n: int, 시작: str = "2024-01-01") -> list[str]:
    d = date.fromisoformat(시작)
    return [(d + timedelta(days=k)).isoformat() for k in range(n)]


def test_진짜_전략이_목표날과_실제_추적매도로_로트를_낸다() -> None:
    """100 에 산 core_h — cutoff 110(+10%)을 본 날 목표도달, 고점 120 뒤 111.6(−7%)에서 실제 추적 매도.
    x_C = 목표도달 결정일 종가 120, x_T = 추적 매도 결정일 종가 111.6 → d = 100·ln(111.6/120)(지수 제자리)."""
    s, _r, 날 = _돌림("core_h", {1: [100, 100, 105, 110, 120, 115, 111.7, 111.6, 111.6, 111.6]}, {0: [(1, 10.0)]})
    로그 = _날별(s)
    assert 로그[날[4]].target_hit == {1: "short"}
    assert 로그[날[8]].sells == {1: [pp.TRAIL_CODE]}
    assert 로그[날[2]].invested == 0 and 로그[날[3]].invested == pytest.approx(0.10)  # 직전 cutoff 비중
    eps, 센 = xp.episodes(s.log)
    assert 센["lots"] == 1 and [(e["start"], e["xc"], e["xt"]) for e in eps] == [(날[1], 날[4], 날[8])]
    prices = {1: dict(zip(날, [100, 100, 105, 110, 120, 115, 111.7, 111.6, 111.6, 111.6], strict=True))}
    out = xp.summarize(s.log, ce.Closes(prices), {d: 1000.0 for d in 날})
    # 자료가 열흘이라 첫 매수 + 92 + 14 일이 끝을 넘는다 — 결정에서 빼고 사실(기간별)에만
    assert out["touched"] == 1 and out["truncated"] == 1 and out["n"] == 0 and out["verdict"] == "undecidable"
    assert out["by_horizon"]["short"]["mean"] == pytest.approx(100 * math.log(111.6 / 120), abs=1e-4)


def _기록(day: str, *, held=(), buys=(), sells=None, hit=None) -> cs.DayLog:
    return cs.DayLog(day=day, cutoff=day, held=frozenset(held), buys={s: 0.1 for s in buys}, sells=sells or {},
                     target_hit=hit or {})  # fmt: skip


def test_못_산_것_상장폐지_추가매수_끝절단을_센다() -> None:
    날 = _날(400)
    logs = [_기록(날[0], buys=[1, 2, 3, 4])]  # 넷 다 새로 산다
    logs.append(_기록(날[1], held=[1, 3, 4]))  # 2 는 엔진이 못 샀다
    logs.append(_기록(날[2], held=[1, 3, 4], buys=[3]))  # 3 은 목표 전 더 샀다
    logs.append(_기록(날[3], held=[1, 3, 4], hit={1: "short", 3: "short", 4: "short"}))
    logs.append(_기록(날[10], held=[1, 3, 4], sells={3: ["추적손절"], 1: ["미룬 매도"]}))
    logs.append(_기록(날[11], held=[1, 4]))  # 4 는 매도 결정 없이 이어 들고, 1 은 미룬 매도뿐(추적 매도 날 없음)
    logs.append(_기록(날[20], held=[1]))  # 4 가 사라졌다 — 상장폐지(앞 거래일 날[11] 종가)
    logs.append(_기록(날[300], held=[1]))
    eps, 센 = xp.episodes(logs)
    assert 센 == {"lots": 3, "vanished": 1, "delisted": 1, "added_before_touch": 1}
    by = {e["sid"]: e for e in eps}
    assert by[3]["xt"] == 날[10] and by[4]["xt"] == 날[11] and by[4]["delisted"] and by[1]["xt"] is None
    prices = {s: {d: 100.0 + k for k, d in enumerate(날)} for s in (1, 3, 4)}
    out = xp.summarize(logs, ce.Closes(prices), {d: 1000.0 for d in 날})
    # 1 은 끝까지 안 나가 절단(마지막 종가로 사실만), 3·4 는 결정 대상
    assert out["touched"] == 3 and out["truncated"] == 1 and out["n"] == 2
    assert out["by_horizon"]["short"]["n"] == 3
    assert out["first_half"] == pytest.approx(100 * math.log(110 / 103), abs=1e-4)


def test_뉴이웨스트_시차3_손계산() -> None:
    # 평균 3, 편차 −2·0·−1·3 → 분산 14/3, 시차 1 짝 0, 시차 2 짝 (0,2)=2 ×0.5, 시차 3 짝 (2,5)=−3 ×0.25 → 보정 0.25
    b = xp.nw_bound({0: 1.0, 1: 3.0, 2: 2.0, 5: 6.0})
    se = math.sqrt(14 / 3 + 2 * 0.25 / 3) / 2
    t = pp.t_quantile(0.975, 3)
    assert b["n_blocks"] == 4 and b["se"] == pytest.approx(se)
    assert b["lower"] == pytest.approx(3 - t * se) and b["upper"] == pytest.approx(3 + t * se)
    # 보정이 음수면 쓰지 않는다(더 작게 고치지 않는다)
    assert xp.nw_bound({0: 1.0, 1: -1.0, 2: 1.0, 3: -1.0})["se"] == pytest.approx(math.sqrt(4 / 3) / 2)


def _판(n, nb, lo, up, a, b) -> dict:
    return {"n": n, "n_blocks": nb, "lower": lo, "upper": up, "first_half": a, "second_half": b}


def test_사전_등록_결정_규칙() -> None:
    assert xp.decide(_판(29, 12, 1, 2, 1, 1)) == "undecidable"
    assert xp.decide(_판(30, 11, 1, 2, 1, 1)) == "undecidable"
    assert xp.decide(_판(30, 12, 0.1, 2, 1, 1)) == "trend"
    assert xp.decide(_판(30, 12, 0.1, 2, 1, -1)) == "none"
    assert xp.decide(_판(30, 12, -2, -0.1, -1, -1)) == "target"
    assert xp.decide(_판(30, 12, -2, -0.1, -1, 0.5)) == "none"
    assert xp.decide(_판(30, 12, -1, 1, 1, 1)) == "none"


def _관문기록(cutoff: str, r: float | None, invested: float | None, blocked: bool = False) -> cs.DayLog:
    return cs.DayLog(day=cutoff, cutoff=cutoff, day_return_pct=r, invested=invested,
                     decisions=[{"kind": "blocked", "stock_id": 9}] if blocked else [])  # fmt: skip


def test_관문_빈도_손계산() -> None:
    날 = _날(6)
    kospi = {날[0]: 100.0, 날[1]: 100.0, 날[2]: 97.0, 날[3]: 97.0, 날[4]: 97.0, 날[5]: 97.0}
    logs = [
        _관문기록(날[0], None, None),
        _관문기록(날[1], -3.0, 0.5),  # 문턱 그대로 걸림, 지수 0 → 조정해도 −3 걸림
        _관문기록(날[2], -4.0, 0.5, blocked=True),  # 지수 −3% → 조정 −4 + 1.5 = −2.5 안 걸림, 지수도 빠진 날
        _관문기록(날[3], -2.9, 0.5),  # 안 걸림
        _관문기록(날[4], None, 0.5),  # 하루 수익 모름 — 세지 않음
        _관문기록(날[5], -1.0, 0.5),
    ]
    f = lg.frequency(logs, kospi)
    assert f["days"] == 4 and f["blocked_days"] == 2 and f["blocked_pct"] == 50.0
    assert f["kospi_down_days"] == 1 and f["with_candidates"] == 1
    assert f["adjusted_days"] == 1 and f["only_raw"] == 1 and f["only_adjusted"] == 0
    assert f["threshold_pct"] == rg.DAILY_LOSS_HALT_PCT


def test_가중_대조_상한은_975() -> None:
    시작 = date(2024, 1, 1)
    days = [((시작 + timedelta(days=28 * k)).isoformat(), [100 + k], [200 + k]) for k in range(14)]  # 묶음마다 하나
    성과 = {}
    for k, (d, w, e) in enumerate(days):
        성과[(d, w[0])] = 1.0 + (k % 3)
        성과[(d, e[0])] = 0.0
    s = heq.summarize(days, 성과)
    n = s["n_blocks"]
    bb = pp.block_bound({pp._묶음(d): 1.0 + (k % 3) for k, (d, _w, _e) in enumerate(days)})
    assert n == bb["n_blocks"]
    assert s["upper"] == pytest.approx(bb["mean"] + pp.t_quantile(0.975, n - 1) * bb["se"], abs=1e-4)
    assert s["lower"] == pytest.approx(bb["lower"], abs=1e-4)  # 지금대로(keep) 쪽은 95% 그대로
