"""묵은 최근 변동성을 나이만큼 장기로 옮긴다 (docs/analysis.md 10.5, docs/infra.md 25.1341, 21회차 P2)."""

from __future__ import annotations

import math
from datetime import date

from batch.services import verdict as vd
from tests.test_forecast_range_1055 import 시장


def test_나이_0이면_그대로_오래되면_장기로() -> None:
    assert vd.aged_short_sigma(0.4, 0.2, "2026-10-07", "2026-10-07") == (0.2, 0)
    s30, 나이 = vd.aged_short_sigma(0.4, 0.2, "2026-09-07", "2026-10-07")
    assert 나이 == 30 and 0.2 < s30 < 0.4
    tau = vd.VOL_HALF_LIFE_MONTHS / 12 / math.log(2)
    assert math.isclose(s30 ** 2, 0.16 + (0.04 - 0.16) * math.exp(-(30 / 365.25) / tau))
    s5y, _ = vd.aged_short_sigma(0.4, 0.2, "2021-10-07", "2026-10-07")
    assert math.isclose(s5y, 0.4, rel_tol=1e-6)


def test_날짜_모르면_쓰지_않고_장기가_없으면_그대로() -> None:
    assert vd.aged_short_sigma(0.4, 0.2, None, "2026-10-07") == (None, None)
    assert vd.aged_short_sigma(None, 0.2, "2026-09-07", "2026-10-07") == (0.2, 30)
    assert vd.aged_short_sigma(0.4, None, "2026-09-07", "2026-10-07") == (None, None)


def test_term_sigma_는_그대로() -> None:
    """19회차 A3 사전 등록 스크립트(`scripts/vol_term_qlike.py`)가 직접 부른다 — 값이 바뀌면 안 된다."""
    assert math.isclose(vd.term_sigma(0.4, 0.2, 0.25), 0.2922, abs_tol=1e-4)  # 2026-10-14 값으로 고정


def test_진단_범위와_근거가_옮긴_값을_쓴다() -> None:
    o = vd.outlook(close=100.0, close_date="2026-10-07", currency="KRW", momentum={"high_52w_proximity": 0.8},
                   risk={"volatility_ann": 0.4, "beta": 1.0}, band=None, opinions=[], today=date(2026, 10, 8),
                   market=시장, rf=0.03, vol={"ewma": 0.2, "date": "2026-09-07"})  # fmt: skip
    s30, _ = vd.aged_short_sigma(0.4, 0.2, "2026-09-07", "2026-10-07")
    assert math.isclose(o["forecast"]["sigma_short"], s30)  # 예전: 0.2 그대로
    행 = next(e for e in o["evidence"] if e["label"] == "최근 변동성(EWMA)")
    assert "원값 20.0%, 30일 전" in 행["display"]
