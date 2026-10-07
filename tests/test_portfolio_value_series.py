"""아무것도 들고 있지 않은 날을 성과 지표에서 뺀다 (docs/infra.md 25.736)."""

from __future__ import annotations

import inspect

from batch.jobs import portfolio as job
from batch.services import portfolio as pf


def _row(date: str, value: float, flow: float = 0.0, idx: float = 1.0, div: float = 0.0) -> pf.ValueRow:
    return pf.ValueRow(date=date, value_krw=value, net_flow_krw=flow, dividends_krw=div, twr_index=idx)


def test_현금_상태인_날은_빠지고_전량_매도한_날은_남는다() -> None:
    rows = [
        _row("2026-01-02", 100.0, flow=100.0),
        _row("2026-01-05", 110.0, idx=1.1),
        _row("2026-01-06", 0.0, flow=-110.0, idx=1.1),  # 전량 매도 — 그날 수익률을 담는다
        _row("2026-01-07", 0.0, idx=1.1),  # 현금
        _row("2026-01-08", 0.0, idx=1.1),  # 현금
        _row("2026-01-09", 50.0, flow=50.0, idx=1.1),  # 다시 매수
    ]
    kept = [r.date for r in pf.invested_rows(rows)]
    assert kept == ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-09"]


def test_현금_날에_배당이_붙으면_남긴다() -> None:
    rows = [_row("2026-01-02", 100.0, flow=100.0), _row("2026-01-05", 0.0, flow=-100.0), _row("2026-01-06", 0.0, div=5.0)]
    assert [r.date for r in pf.invested_rows(rows)] == ["2026-01-02", "2026-01-05", "2026-01-06"]


def test_지표는_현금_날을_뺀_계열로_낸다() -> None:
    assert "for v in pf.invested_rows(values)" in inspect.getsource(job)
