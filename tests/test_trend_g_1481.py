"""가상 G — 목표 뒤 추적 매도(`trend`) · 늦게 연 계좌의 비교 (docs/paper.md 1·2.2·4장, docs/infra.md 25.1481)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.jobs import paper as job
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client


def test_trend_exit_only_after_target_and_above_cost() -> None:
    # 단기 목표 +10%, 손절 −7%: 고점 110 에 닿은 뒤 102.3 이하면 판다(원가 100 위)
    assert pp.trend_exit(100, 110, 102.3, 10, -7) is True
    assert pp.trend_exit(100, 110, 102.4, 10, -7) is False
    # 목표에 못 닿았으면 고점 대비 크게 빠져도 팔지 않는다(손절이 지킨다) — 초안은 목표 절반에서 켜 원가 아래에서 팔았다
    assert pp.trend_exit(100, 109.9, 95, 10, -7) is False
    # 권리락 계수는 원가·고점 모두에 — 권리락 전 고점이 거짓 하락을 만들지 않게
    assert pp.trend_exit(100, 120, 100, 10, -7, rights_scale=0.8) is False  # 고점 96(원가 80 대비 +20%) → 89.3 위
    assert pp.trend_exit(100, None, 100, 10, -7) is False


def test_trend_rule_does_not_sell_at_target_but_trails() -> None:
    assert pp.sells_for("trend", {1: {"목표도달"}}) == {}
    assert pp.sells_for("trend", {1: {"손절"}, 2: {pp.TRAIL_CODE}, 3: {"재무악화"}}) == {1: ["손절"], 2: [pp.TRAIL_CODE]}


def _보유(c: Client, 종가들: list[tuple[str, float]]) -> list[pp.Fill]:
    for d, v in 종가들:
        c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                       " VALUES (1, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [d, v, v, v, v])  # fmt: skip
    return [pp.Fill(1, "G", 1, "buy", "2026-10-01", 10_000, 10, "KRW", 1.0, 0, 0, 0, -100_000, "short", 70.0,
                    "2026-09-30")]  # fmt: skip


def test_sell_decisions_trend_uses_peak_since_first_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c = Client()
    체결 = _보유(c, [("2026-10-01", 10_000), ("2026-10-05", 11_500), ("2026-10-12", 10_600)])
    메모: dict = {}
    out = job.sell_decisions(c, "KR", "trend", 체결, set(), {}, None, date(2026, 10, 13), {}, 메모)
    # 고점 11,500 ≥ 11,000(목표) → 11,500 × 0.93 = 10,695 아래인 10,600 → 판다. B(core) 라면 목표도달로 이미 팔았을 것
    assert out[1][0] == [pp.TRAIL_CODE]
    # 추적손절 근거는 근거표 행으로(25.2009 — 예전엔 빈 목록이라 웹에 판정 근거가 없었다)
    추적 = next(f for f in out[1][1] if f["reason_code"] == pp.TRAIL_CODE)
    assert [c["label"] for c in 추적["criteria"]] == ["원가(주당)", "고점 종가", "지금 종가"]
    assert [c["passed"] for c in 추적["criteria"]] == [None, True, True]
    assert 추적["criteria"][1]["display"].startswith("11,500원") and "목표 +10%" in 추적["criteria"][1]["threshold"]
    assert 추적["criteria"][2]["display"] == "10,600원 (고점 대비 -7.83%)"
    c2 = Client()
    체결 = _보유(c2, [("2026-10-01", 10_000), ("2026-10-05", 11_500), ("2026-10-12", 11_200)])
    assert job.sell_decisions(c2, "KR", "trend", 체결, set(), {}, None, date(2026, 10, 13), {}) == {}
    # 같은 보유를 core 로 보면 목표도달에서 판다 — G 와 B 의 차이가 이 하나
    assert job.sell_decisions(c2, "KR", "core", 체결, set(), {}, None, date(2026, 10, 13), {})[1][0] == ["목표도달"]


def _행(as_of: str, eq: float, twr: float) -> dict:
    return {"as_of": as_of, "invested_krw": 1_000_000, "equity_krw": eq, "twr_index": twr, "positions_json": "[]"}


def test_pair_line_uses_common_window_for_late_account() -> None:
    daily = {"B": [_행("2026-10-16", 1_000_000, 1.0), _행("2026-10-23", 1_100_000, 1.10)],
             "G": [_행("2026-10-23", 1_020_000, 1.02)]}  # fmt: skip
    한도 = {"max_stock_pct": 10, "max_sector_pct": 30, "stop_pct": {"short": -7, "mid": -15, "long": -25}}
    줄 = pp.summary_lines(daily, {}, "2026-10-20", None, {}, {}, 한도, None,
                          {"B": "2026-10-16", "G": "2026-10-19"}, {("B", "2026-10-19"): 1.05, ("G", "2026-10-19"): 1.0})
    # G 는 10-19 부터 +2%, B 는 같은 구간 1.10/1.05 − 1 = +4.76% → −2.8%p (원화 차이 −80,000원이 아니라)
    assert any(x.startswith("  추세익절−기준: 시간가중 -2.8%p (2026-10-19부터 공통 구간") for x in 줄), 줄
    # 같은 날 시작한 짝은 그대로 원화 차이
    assert not any("원화" in x for x in 줄)


def test_compare_real_window_not_pushed_by_late_account() -> None:
    실제 = [("2026-10-16", 1.0), ("2026-10-23", 1.03)]
    c = pp.compare_real({"B": [("2026-10-16", 1.0), ("2026-10-23", 1.02)], "G": [("2026-10-19", 1.0), ("2026-10-23", 1.01)]},
                        실제)  # fmt: skip
    assert c["start"] == "2026-10-16" and c["paper_start"] == {"G": "2026-10-19"}
    assert c["paper_pct"]["B"] == pytest.approx(2.0)
    assert "추세익절 +1.0%(2026-10-19부터)" in pp.compare_line(c)
