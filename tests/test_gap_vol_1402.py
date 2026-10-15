"""EWMA·변동성 국면도 11일 넘게 벌어진 쌍을 버린다 (docs/infra.md 25.1402, docs/metrics.md 0장)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.services import history as hs
from batch.services import patterns as pt


def _계열(정지: bool) -> tuple[list[str], list[float]]:
    # ±1% 교대 300 거래일(달력 매일로 둔다), 정지면 250번째에 30일 빈 뒤 −40% 로 재개
    날, 값, d, v = [], [], date(2025, 1, 1), 100.0
    for i in range(300):
        if 정지 and i == 250:
            d += timedelta(days=30)
            v *= 0.6
        else:
            v *= 1.01 if i % 2 else 0.99
        날.append(d.isoformat())
        값.append(v)
        d += timedelta(days=1)
    return 날, 값


def test_EWMA_는_재개일_누적_낙폭을_하루치로_세지_않는다() -> None:
    보통 = pt.ewma_vol(*_계열(False))["ewma"]
    정지 = pt.ewma_vol(*_계열(True))["ewma"]
    assert abs(정지 - 보통) < 0.02  # 예전 1.11 대 0.16


def test_변동성_국면은_틈이_든_창을_내지_않는다() -> None:
    v = hs.vol_regime(*_계열(True))
    assert v is not None
    # 지금(재개 49일 뒤) 한 달 변동성은 ±1% 의 연환산 그대로
    assert abs(v["now"] - 0.1625) < 0.005
    # 틈이 든 21개 창은 빠진다 — 예전에는 그 창들이 "시끄러운 편" 칸의 1개월 표본을 80건으로 부풀렸다
    assert v["buckets"]["2"]["1"]["n"] == 59
    assert v["pct"] > 0.75  # 예전 0.71 — 위쪽에 끼어 있던 가짜 고변동 창이 사라졌다


def test_틈_판정() -> None:
    assert pt.gap_too_long("2026-01-01", "2026-01-13") and not pt.gap_too_long("2026-01-01", "2026-01-12")
    assert not pt.gap_too_long("?", "2026-01-12")
