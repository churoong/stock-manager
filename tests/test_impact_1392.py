"""2부 1차 금액의 거래대금 참여율과 제곱근 충격 — 기록만 (docs/infra.md 25.1392, docs/factors.md 12.28 P5)."""

from __future__ import annotations

import math

import pytest

from batch.notify import report_sections as rs
from batch.services import report_picks as rp
from tests.test_report_picks import row


def _날(i: int) -> str:
    return f"2026-{7 + i // 28:02d}-{1 + i % 28:02d}"


def _재료(대금: float, 수익: float) -> tuple[dict, dict]:
    # 교대로 +r, -r — 표본 표준편차가 r 에 가깝다
    종가, px = {}, 10_000.0
    for i in range(61):
        종가[_날(i)] = px
        px *= 1 + (수익 if i % 2 == 0 else -수익)
    return {1: {_날(i): 대금 for i in range(61)}}, {1: 종가}


def _배분(금액: float, 비율: float = 0.4) -> rs.Allocation:
    return rs.Allocation("000001", "종목1", 금액, 10.0, tranches=[{"step": 1, "ratio": 비율}])


def test_1차_금액의_참여율과_제곱근_충격() -> None:
    대금, 종가 = _재료(500_000_000.0, 0.03)  # ADV20 5억 = 유니버스 하한
    out = rp.impact_log([_배분(12_500_000.0)], [row(1)], 대금, 종가)  # 1차 500만 → p = 0.01
    assert out["n"] == 1 and out["p_max"] == 0.01
    σ = out["impact_max_pct"] / 100 / math.sqrt(0.01)
    assert σ == pytest.approx(0.03, rel=0.05)  # 충격 ≈ 100 × 0.03 × 0.1 = 0.3%
    assert out["over"] == 1 and out["threshold_pct"] == 0.10


def test_작은_금액은_문턱_아래() -> None:
    대금, 종가 = _재료(50_000_000_000.0, 0.02)  # ADV 500억
    out = rp.impact_log([_배분(1_000_000.0)], [row(1)], 대금, 종가)
    assert out["over"] == 0 and out["impact_max_pct"] < 0.01


def test_거래대금이나_시세가_모자라면_모름() -> None:
    _, 종가 = _재료(1.0, 0.02)
    out = rp.impact_log([_배분(1_000_000.0)], [row(1)], {}, 종가)
    assert out == {"n": 0, "unknown": 1, "threshold_pct": 0.10}
    out = rp.impact_log([_배분(1_000_000.0, 비율=0)], [row(1)], *_재료(1e9, 0.02))
    assert out["unknown"] == 1


def test_금액은_기록에_남지_않는다() -> None:
    대금, 종가 = _재료(777_777_777.0, 0.03)
    out = rp.impact_log([_배분(12_345_678.0)], [row(1)], 대금, 종가)
    assert "12345678" not in str(out) and "4938271" not in str(out)
    assert out["p_max"] == float(f"{12_345_678.0 * 0.4 / 777_777_777.0:.2g}")
