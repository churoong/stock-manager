"""백테스트 시작일은 다음 달 첫날 — 첫 리밸런스가 늘 그달 첫 거래일 (docs/infra.md 25.1320, 백테스트 감사 #4)."""

from __future__ import annotations

from datetime import date

from batch.jobs import backtest as bj
from batch.services import backtest as bt


def test_월말_손_실행도_하루짜리_첫_달이_없다() -> None:
    # 10-29 실행 → 365×5+30 일 앞은 2021-09-30. 예전엔 9-30·10-1 에 잇달아 리밸런스했다
    assert bj.start_of_backtest(date(2026, 10, 29), 5) == "2021-10-01"
    assert bj.start_of_backtest(date(2026, 11, 3), 5) == "2021-11-01"  # 예약 실행(3일)
    assert bj.start_of_backtest(date(2026, 1, 1), 1) == "2025-01-01"  # 12월 → 이듬해 1월
    dates = ["2021-10-01", "2021-10-04", "2021-11-01", "2021-11-02"]
    assert bt.month_starts(dates) == ["2021-10-01", "2021-11-01"]


def test_엔진_판이_올랐다() -> None:
    assert bt.CALC_VERSION >= 6
