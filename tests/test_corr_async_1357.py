"""국내–미국 쌍의 하루 밀어 맞춘 상관 — 기록만 (docs/infra.md 25.1357, 23회차 P1)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from batch.services import report_picks as rp


def _날들(n: int, 시작: date = date(2026, 1, 5)) -> list[str]:
    out, d = [], 시작
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _계열(날: list[str], 수익: list[float]) -> dict[str, float]:
    값, out = 100.0, {}
    for d, r in zip(날, [0.0, *수익], strict=False):
        값 *= 1 + r
        out[d] = 값
    return out


# 미국 수익이 하루 뒤 국내에 그대로 드러난다 — 같은 날짜로 맞추면 상관이 0 근처, 하루 밀면 1
날 = _날들(140)
미국수익 = [0.02 * math.sin(i * 1.7) + 0.01 * math.cos(i * 0.31) for i in range(139)]
미국 = _계열(날, 미국수익)
국내 = _계열(날, [0.0, *미국수익[:-1]])


def test_하루_밀면_상관이_드러난다() -> None:
    동시 = rp.pearson(*rp._aligned_returns(국내, 미국))
    밀림 = rp.pearson(*rp.lagged_aligned_returns(국내, 미국))
    assert 동시 is not None and abs(동시) < 0.5
    assert 밀림 == pytest.approx(1.0, abs=1e-9)


def test_미국이_쉰_날은_한_번만_잇는다() -> None:
    # 국내 날짜 둘(화·수)이 같은 미국 날짜(월)를 가리키면 앞의 것 하나만
    kr = {"2026-01-06": 100.0, "2026-01-07": 101.0, "2026-01-08": 102.0}
    us = {"2026-01-05": 50.0, "2026-01-07": 51.0}
    좌, 우 = rp.lagged_aligned_returns(kr, us)
    # 01-06 ← 01-05, 01-07 ← 01-05(이미 씀, 건너뜀), 01-08 ← 01-07 → 수익 한 쌍
    assert len(좌) == 1 and 우[0] == pytest.approx(51 / 50 - 1)


def test_진단은_나라가_다른_쌍만_세고_금액은_그대로() -> None:
    보유 = [rp.Holding(2, "US1", "미국보유", None, 1.0, country="US"),
            rp.Holding(3, "KR2", "국내보유", None, 1.0, country="KR")]  # fmt: skip
    계열 = {1: 국내, 2: 미국, 3: 국내}
    기록 = rp.cross_country_lag_log([1], "KR", 보유, 계열)
    assert 기록 == {"pairs": 1, "raised": 1, "crossed": 1, "max_increase": pytest.approx(기록["max_increase"])}
    assert 기록["max_increase"] > 0.5
    # 2부 금액이 쓰는 같은 날짜 상관은 바뀌지 않았다 — 미국 보유와는 낮게 재어 줄이지 않는다
    배수, _ = rp.correlation_reduction(국내, [(2, "미국보유", 미국)])
    assert 배수 == 1.0
    # 분할 대기·ETF·같은 나라만 있으면 None
    assert rp.cross_country_lag_log([1], "KR", 보유[1:], 계열) is None
    assert rp.cross_country_lag_log([1], "KR", 보유, 계열, series_unknown={2: "분할"}) is None
