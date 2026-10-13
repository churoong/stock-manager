"""백테스트 리스크 지표가 미국 3Y 창 앞을 자르지 않는다 — 운영과 같은 창 (docs/infra.md 25.1318, 백테스트 감사 #2)."""

from __future__ import annotations

from datetime import date, timedelta

import exchange_calendars as xc
import pandas as pd

from batch.jobs import backtest as bj
from batch.jobs import metrics as mj
from batch.services import metrics as m


def test_미국_3년_창을_운영과_같게() -> None:
    cal = xc.get_calendar("XNYS")
    end = date(2026, 9, 30)
    start = end - timedelta(days=mj.WINDOW_DAYS["3Y"])
    sess = [d.date() for d in cal.sessions_in_range(pd.Timestamp(start - timedelta(days=400)), pd.Timestamp(end))]
    inwin = [d for d in sess if d >= start]
    assert len(inwin) > 750  # 미국 3년은 750거래일보다 길다
    closes = []
    for d in sess:  # 창 둘째 날 130 으로 튀었다가 100 으로 — 그 고점에서의 낙폭이 창의 MDD 다
        px = 130.0 if d == inwin[1] else 100.0 + (10 * (sess.index(d) - sess.index(inwin[1])) / 750 if d > inwin[1] else 0)
        closes.append((d.isoformat(), px))
    bt = bj.risk_from_prices(closes, risk_free_annual=0.03, until=end)
    live = m.compute("3Y", [m.PricePoint(date=date.fromisoformat(d), close=c) for d, c in closes
                            if date.fromisoformat(d) >= start], risk_free_annual=0.03, span=(start, end))  # fmt: skip
    assert live.mdd is not None and live.mdd < -0.2
    assert abs(bt["mdd_abs"] - abs(live.mdd)) < 1e-9


def test_창을_담을_만큼_넉넉하다() -> None:
    assert bj.RISK_LOOKBACK_DAYS >= 760  # 미국 3Y 최대 약 756거래일
