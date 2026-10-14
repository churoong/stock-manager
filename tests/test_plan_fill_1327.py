"""매수 구간·3회 분할 계획의 체결 성적 — 손으로 계산한 고정 자료 (docs/signals.md 10.2, infra 25.1327, 20회차 P3)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from batch.services import outcomes as oc
from batch.services import plan_fill as pf

PLAN = json.dumps([{"step": 1, "ratio": 0.4, "price": 100.0}, {"step": 2, "ratio": 0.3, "price": 98.0},
                   {"step": 3, "ratio": 0.3, "price": 96.0}])  # fmt: skip


def _날(n: int) -> list[str]:
    out, d = [], date(2026, 1, 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _돌림(closes_v: list[float], plan: str = PLAN, ref: float | None = 101.0) -> pf.PlanFill | None:
    날 = _날(len(closes_v))
    closes = list(zip(날, closes_v, strict=True))
    sig = {"stock_id": 1, "as_of_date": 날[0], "horizon": "short", "target_price": None, "stop_price": None,
           "ref_close": ref, "tranche_plan": plan}  # fmt: skip
    o = oc.evaluate(sig, closes)
    return pf.evaluate(sig, o, closes)


def test_구간_위에서만_놀면_한_회차도_안_찬다() -> None:
    f = _돌림([101.0] + [102.0] * 61)
    assert f is not None and f.filled == [False, False, False] and f.plan_ret == 0 and f.deployed == 0
    assert f.naive_ret == pytest.approx(0.0)


def test_3퍼센트_빠지면_1·2차만_차고_지정가로_산다() -> None:
    # 다음 날 102 → 이틀 뒤 99(1차) → 97.5(2차, 96 위) → 끝 105
    f = _돌림([101.0, 102.0, 99.0, 97.5] + [100.0] * 56 + [105.0, 105.0])
    assert f is not None and f.filled == [True, True, False]
    끝 = 102.0 * (1 + f.naive_ret)
    assert 끝 == pytest.approx(105.0)
    assert f.plan_ret == pytest.approx(0.4 * (105 / 100 - 1) + 0.3 * (105 / 98 - 1))
    assert f.deployed == pytest.approx(0.7)


def test_신호_뒤_5대1_분할도_저장된_가격을_지금_단위로() -> None:
    # 지금 계열은 분할 조정돼 신호 날 101 이 20.2 다 — 신호 날 기준 종가 101 로 배수 0.2
    f = _돌림([20.2, 20.4, 19.9] + [20.0] * 59)
    assert f is not None and f.filled == [True, False, False]  # 100 × 0.2 = 20 ≥ 19.9, 98 × 0.2 = 19.6 < 19.9


def test_60일_수익이_없거나_계획이_깨지면_재지_않는다() -> None:
    assert _돌림([101.0] * 30) is None
    assert _돌림([101.0] * 62, plan="[{\"ratio\": 0.4}]") is None
    assert _돌림([101.0] * 62, plan="깨짐") is None


def test_집계는_하한과_단정하지_않음을_지킨다() -> None:
    f = pf.PlanFill(1, "short", [True, False, False], 0.02, 0.4, 0.01)
    [적음] = pf.summarize([f] * 4)
    assert 적음 == {"horizon": "short", "n": 4, "stocks": 1, "conclusive": False}
    [다섯] = pf.summarize([f] * 5)
    assert 다섯["fill_rate"] == [1.0, 0.0, 0.0] and 다섯["plan_ret"] == 0.02 and 다섯["deployed_ret"] == 0.05
    assert 다섯["naive_ret"] == 0.01 and 다섯["conclusive"] is False


def test_잡이_저장된_분할_계획을_읽어_넘긴다() -> None:
    """배선 — 잡 질의에 `tranche_plan` 이 빠지면 운영에서는 아무것도 재지 않는다."""
    from batch.jobs import signal_outcomes as job
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                   " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    c.conn.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, target_price, stop_price, size_reduction, sector_cap_applied, rationale_text, rationale_data,"
        " calc_version, created_at)"
        " VALUES (1, ?, 'short', 't', 96, 100, 'KRW', ?, 110, 93, 1, 0, 'x', '{\"ref_close\": 101}', 1, 't')",
        [_날(1)[0], PLAN],
    )
    for d, v in zip(_날(62), [101.0, 102.0, 99.0] + [100.0] * 59, strict=True):
        c.conn.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                       " VALUES (1, ?, ?, ?, 'KRW', 't', 't')", [d, v, v])  # fmt: skip
    계획: list[pf.PlanFill] = []
    job.compute(c, "KR", "2026-04-10", 계획)  # type: ignore[arg-type]
    assert len(계획) == 1 and 계획[0].filled == [True, False, False]
