"""오프라인 모드 수집 테스트.

CLAUDE.md 규칙: 테스트는 API 키 없이 돌아가야 한다.
이 테스트는 네트워크를 타지 않고 tests/fixtures 를 읽는다.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch):
    """오프라인 모드로 모듈을 다시 읽는다."""
    monkeypatch.setenv("OFFLINE_MODE", "1")
    from batch import config

    importlib.reload(config)
    from batch.sources import yfinance_src

    importlib.reload(yfinance_src)
    return yfinance_src


def test_오프라인에서_픽스처를_읽는다(offline) -> None:
    result = offline.fetch_daily_quotes(["005930.KS", "AAPL"])

    assert result.ok
    assert result.from_cache
    assert result.source == "yfinance"
    assert result.fetched_at is not None
    assert len(result.data) == 2


def test_요청하지_않은_종목은_돌려주지_않는다(offline) -> None:
    result = offline.fetch_daily_quotes(["AAPL"])

    assert result.ok
    assert [q.ticker for q in result.data] == ["AAPL"]


def test_등락률_계산(offline) -> None:
    result = offline.fetch_daily_quotes(["AAPL"])
    quote = result.data[0]

    # (255 - 250) / 250 * 100 = 2.0
    assert quote.change_pct == pytest.approx(2.0)


def test_국내_종목_등락률_계산(offline) -> None:
    result = offline.fetch_daily_quotes(["005930.KS"])
    quote = result.data[0]

    # (71000 - 70000) / 70000 * 100 = 1.428571...
    assert quote.change_pct == pytest.approx(1.4285714, rel=1e-6)


def test_통화_판정(offline) -> None:
    result = offline.fetch_daily_quotes(["005930.KS", "AAPL"])
    by_ticker = {q.ticker: q for q in result.data}

    assert by_ticker["005930.KS"].currency == "KRW"
    assert by_ticker["AAPL"].currency == "USD"


def test_전일_종가가_없으면_등락률은_None() -> None:
    from batch.sources.yfinance_src import DailyQuote

    quote = DailyQuote(
        ticker="TEST",
        trade_date="2026-09-15",
        close=100.0,
        prev_close=None,
        volume=None,
        currency="USD",
    )
    assert quote.change_pct is None


def test_전일_종가가_0이면_나누지_않는다() -> None:
    from batch.sources.yfinance_src import DailyQuote

    quote = DailyQuote(
        ticker="TEST",
        trade_date="2026-09-15",
        close=100.0,
        prev_close=0.0,
        volume=None,
        currency="USD",
    )
    assert quote.change_pct is None
