"""야후가 삼킨 실패를 성공으로 보지 않는다 (docs/infra.md 25.601, 감사)."""

from __future__ import annotations

import sys
import types

import pandas as pd

from batch import config
from batch.sources import yfinance_src as y


def _온라인(monkeypatch) -> None:
    import dataclasses

    monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, offline_mode=False))


def test_빈_예정일_응답은_답한_것으로_치지_않는다(monkeypatch) -> None:
    """yfinance 는 5xx 를 삼키고 {} 를 돌려준다 — 그것을 '날짜 없음' 으로 보면 확정 일정이 지워졌다."""
    _온라인(monkeypatch)
    가짜 = types.SimpleNamespace(Ticker=lambda s: types.SimpleNamespace(calendar={}))
    monkeypatch.setitem(sys.modules, "yfinance", 가짜)
    r = y.fetch_earnings_dates("AAPL")
    assert r.ok is False and "비었습니다" in r.error


def test_조각이_잇달아_비면_막힌_것으로_보고_멈춘다(monkeypatch) -> None:
    """종목별 429 를 삼킨 빈 표가 성공처럼 와 남은 조각을 계속 불렀다."""
    _온라인(monkeypatch)
    불린: list[list[str]] = []

    def 빈다운(group, lookback):
        불린.append(group)
        return pd.DataFrame(), "", "ok"

    monkeypatch.setattr(y, "_download", 빈다운)
    r = y.fetch_daily_bars([f"T{i}" for i in range(10)], lookback_days=5, chunk_size=2)
    assert r.limit_state == "blocked"
    assert len(불린) == 2, "두 조각 잇달아 비면 멈춘다"


def test_로그에_찍힌_호출_제한으로_막힌_것을_안다(monkeypatch) -> None:
    """1.7.0 은 종목별 오류를 밖으로 내지 않는다 — 로그를 모아 본다 (docs/infra.md 25.603, 교차검증)."""
    import logging

    _온라인(monkeypatch)

    def 다운(**kw):
        # 1.7.0 multi.py 처럼 같은 문구의 오류를 **한 줄로 묶어** 찍는다 (25.605, 교차검증 — 예전 가짜는 종목마다
        # 한 줄을 찍어 실제 모양과 달랐고, 3종목 이상에서 판정이 한 번도 서지 않는 것을 못 잡았다)
        종목 = kw["tickers"].split()
        logging.getLogger("yfinance").error("%d Failed downloads:", len(종목))
        logging.getLogger("yfinance").error(f"{종목}: YFRateLimitError('Too Many Requests. Rate limited.')")
        return pd.DataFrame()

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=다운))
    _frame, error, state = y._download([f"T{i}" for i in range(200)], 5)
    assert state == "blocked" and "200/200종목" in error
    assert _frame is not None, "받은 표는 버리지 않는다 (25.606)"


def test_일부만_막히면_막힌_것으로_보지_않는다(monkeypatch) -> None:
    import logging

    _온라인(monkeypatch)

    def 다운(**kw):
        logging.getLogger("yfinance").error("['T0', 'T1']: YFRateLimitError('Too Many Requests.')")
        return pd.DataFrame({"x": [1]})

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=다운))
    _frame, _error, state = y._download([f"T{i}" for i in range(10)], 5)
    assert state == "ok"


def test_로그_줄의_종목_수를_센다() -> None:
    assert y._로그_종목수("['A', 'B', 'C']: YFRateLimitError(...)") == 3
    assert y._로그_종목수("A: Too Many Requests") == 1


def test_백필은_조각을_따로_불러도_잇달아_비면_멈춘다(monkeypatch) -> None:
    """백필은 조각마다 따로 불러 fetch_daily_bars 의 멈춤에 닿지 않았다 (25.603, 교차검증)."""
    import inspect

    from batch.jobs import backfill_us, refresh_us_adjusted

    for 모듈 in (backfill_us, refresh_us_adjusted):
        src = inspect.getsource(모듈)
        assert "빈조각 = 빈조각 + 1 if not result.data else 0" in src
        assert 'if result.limit_state == "blocked" or 빈조각 >= 2:' in src


def test_절반이_막힌_조각도_받은_종목은_살리고_멈춘다(monkeypatch) -> None:
    """예전에는 blocked 판정이 표를 None 으로 바꿔 멀쩡한 종목까지 버렸다 (docs/infra.md 25.606, 교차검증)."""
    _온라인(monkeypatch)
    불린: list[list[str]] = []

    def 반쯤(group, lookback):
        불린.append(group)
        return "표", "야후 호출 제한: 1/2종목", "blocked"

    monkeypatch.setattr(y, "_download", 반쯤)
    monkeypatch.setattr(y, "_to_bars", lambda frame, group: ([y.DailyBar(group[0], "2026-09-25", 1.0)], group[1:]))
    r = y.fetch_daily_bars([f"T{i}" for i in range(6)], lookback_days=5, chunk_size=2)
    assert len(불린) == 1, "막혔으면 남은 조각은 부르지 않는다"
    assert r.limit_state == "blocked" and [b.ticker for b in r.data] == ["T0"]
