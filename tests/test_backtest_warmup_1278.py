"""백테스트 워밍업이 리스크 3년 창을 채운다 (docs/infra.md 25.1278, 백테스트 감사 재현)."""

from __future__ import annotations

from datetime import date, timedelta

import exchange_calendars as xc

from batch.jobs import backtest as bj

세션 = {str(x.date()) for x in xc.get_calendar("XKRX").sessions_in_range("2022-01-01", "2026-12-31")}


def _계열(앞: int, 끝: date) -> list[tuple[str, float]]:
    """2.2년 전 급락(3년 창 안, 1년 창 밖)이 있는 실제 국내 거래일 계열."""
    out, d, px, i = [], 끝 - timedelta(days=앞), 100.0, 0
    급락 = (끝 - timedelta(days=800), 끝 - timedelta(days=700))
    while d <= 끝:
        if d.isoformat() in 세션:
            i += 1
            px *= 1 + (-0.006 if 급락[0] <= d <= 급락[1] else 0.0008) + (0.01 if i % 2 else -0.01)
            out.append((d.isoformat(), px))
        d += timedelta(days=1)
    return out


def test_첫_리밸런스에도_3년_창으로_리스크를_낸다() -> None:
    끝 = date(2026, 9, 30)
    r = bj.risk_from_prices(_계열(bj.WARMUP_CALENDAR_DAYS, 끝), risk_free_annual=0.03, until=끝)
    assert r.get("mdd_abs", 0) > 0.3, "워밍업만큼 읽은 과거로 3년 창 낙폭(2.2년 전 급락)이 잡혀야 한다"
