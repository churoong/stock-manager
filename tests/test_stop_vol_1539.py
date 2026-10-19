"""손절 거리 σ 단위 기록 (docs/paper.md 9장 "손절 거리 σ", docs/factors.md 12.34 31회차 P2, docs/infra.md 25.1539).
손으로 셀 수 있는 계열 + 가상모의투자 하루 단계에서 산 결정 행에 붙는지."""

from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, date, datetime, timedelta

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import stop_vol as sv
from tests.test_paper_job_1445 import _신호, client, 설정  # noqa: F401 — 고정 자료(client)를 이 파일에서도 쓴다


def _계열(수익: list[float], 시작: float = 100.0) -> dict[str, float]:
    d0, p, out = date(2026, 1, 1), 시작, {}
    out[d0.isoformat()] = p
    for k, r in enumerate(수익, start=1):
        p *= 1 + r
        out[(d0 + timedelta(days=k)).isoformat()] = p
    return out


def test_손계산() -> None:
    수익 = [0.01, -0.02, 0.015, -0.005] * 20  # 80개
    v = sv.stop_vol(_계열(수익), -7.0, "KR")
    기대 = statistics.stdev([math.log(1 + r) for r in 수익])
    assert v["n"] == 80 and v["sigma_d"] == pytest.approx(기대, abs=1e-6)
    assert v["stop_z"] == pytest.approx(0.07 / (기대 * math.sqrt(20)), abs=1e-4)
    assert v["stop_pct"] == -7.0 and v["from"] == "2026-01-01" and "jumps" not in v


def test_국내만_30퍼센트_넘는_날을_뺀다() -> None:
    수익 = [0.01, -0.01] * 35 + [1.0]  # 마지막 날 2:1 분할 같은 점프
    kr, us = sv.stop_vol(_계열(수익), -7.0, "KR"), sv.stop_vol(_계열(수익), -7.0, "US")
    assert kr["n"] == 70 and kr["jumps"] == 1
    assert us["n"] == 71 and us["sigma_d"] > kr["sigma_d"]


def test_모름() -> None:
    assert sv.stop_vol(_계열([0.01, -0.01] * 29), -7.0, "KR") == {"unknown": "수익 58개 < 60"}
    assert sv.stop_vol(None, -7.0, "KR") == {"unknown": "시세 계열 없음"}
    assert sv.stop_vol(_계열([0.01] * 70), None, "KR") == {"unknown": "손절폭 없음"}


def _산_행(c, monkeypatch, price_series, series_unknown=None):  # noqa: ANN001, ANN202
    다음 = cal.next_session("KR", date(2026, 10, 14))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[_신호()], settings=설정,
                 price_series=price_series, series_unknown=series_unknown or {}, allocate=True,
                 now=datetime(2026, 10, 15, tzinfo=UTC))  # fmt: skip
    rows = c.q("SELECT account_id, kind, detail_json FROM paper_decisions WHERE stock_id = 1 ORDER BY 1")
    return {a: (k, json.loads(d or "{}")) for a, k, d in rows}


def test_산_결정_행에_그날_손절폭으로_붙는다(client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    # 설정의 단기 손절 −9 — 기본(−7)이 아니라 그날 매도 판정이 쓰는 값을 적는다
    client.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('horizon_targets', ?, 't')",
                        [json.dumps({"short": {"target_pct": 12.0, "stop_pct": -9.0}})])  # fmt: skip
    행 = _산_행(client, monkeypatch, {1: _계열([0.01, -0.02, 0.015, -0.005] * 20, 10_000)})
    for a in ("A", "B"):
        kind, d = 행[a]
        assert kind == "buy"
        assert d["vol"]["stop_pct"] == -9.0 and d["vol"]["n"] == 80 and d["vol"]["stop_z"] > 0


def test_분할_재수집_대기는_모름(client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    행 = _산_행(client, monkeypatch, {1: _계열([0.01, -0.01] * 40, 10_000)}, {1: "분할 대기"})
    assert 행["A"][1]["vol"] == {"unknown": "분할 재수집 대기"}
