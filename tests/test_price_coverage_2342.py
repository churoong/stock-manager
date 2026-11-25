"""미국 그날 시세 커버리지 기록 (docs/paper.md 15.13, docs/infra.md 25.2342).

주문 허가 판정(15.13)의 남은 갈래 "커버리지" 의 첫 단 — 문턱 없이 관측만 쌓는다. 반환 꼴(부르는 곳 19곳)은 그대로 두고
받을 dict 를 넘긴 쪽에만 채운다.
"""

from __future__ import annotations

import inspect

import pytest

from tests.test_us_daily import FakeClient


def _바(monkeypatch: pytest.MonkeyPatch, bars: list) -> None:  # type: ignore[type-arg]
    from batch.sources import yfinance_src

    monkeypatch.setattr(yfinance_src, "fetch_daily_bars",
                        lambda symbols, **_k: yfinance_src.FetchResult(ok=True, data=bars))  # fmt: skip


def test_그날_행이_있는_대상만_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import daily
    from batch.sources.yfinance_src import DailyBar

    _바(monkeypatch, [
        DailyBar(ticker="AAPL", date="2026-09-14", close=210.0, volume=1),
        DailyBar(ticker="AAPL", date="2026-09-15", close=220.0, volume=1),  # 그날 — 셈
        DailyBar(ticker="MSFT", date="2026-09-14", close=400.0, volume=1),  # 앞날만 — 안 셈
        DailyBar(ticker="ZZZZ", date="2026-09-15", close=1.0, volume=1),  # 대상 밖 — 안 셈
    ])  # fmt: skip
    client = FakeClient(symbols={"AAPL": 1, "MSFT": 2})
    대상 = len(daily._us_symbol_ids(client, include_etf=True))
    판: dict[str, int] = {}
    daily.collect_us_prices(client, "2026-09-15", coverage=판)
    assert 판 == {"day": 1, "targets": 대상} and 대상 >= 2


def test_넘기지_않으면_그대로(monkeypatch: pytest.MonkeyPatch) -> None:
    """반환 꼴은 바뀌지 않는다 — 셋(행·경고·요약)"""
    from batch.jobs import daily
    from batch.sources.yfinance_src import DailyBar

    _바(monkeypatch, [DailyBar(ticker="AAPL", date="2026-09-15", close=220.0, volume=1)])
    out = daily.collect_us_prices(FakeClient(symbols={"AAPL": 1}), "2026-09-15")
    assert isinstance(out, tuple) and len(out) == 3


def test_국내는_채우지_않고_일일_배치가_있을_때만_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import daily

    받음: dict = {}
    monkeypatch.setattr(daily, "collect_kr_prices", lambda c, d: ([], [], "국내"))
    daily.collect_prices(object(), "KR", "2026-09-15", coverage=받음)  # type: ignore[arg-type]
    assert 받음 == {}
    # 일일 배치는 채워진 때만 `step_log.price_coverage` 에 싣는다(국내 실행엔 칸이 없다)
    글 = inspect.getsource(daily)
    assert "collect_prices(client, market, decision.trade_date, coverage=커버리지)" in 글
    assert 'if 커버리지:\n                    step_log["price_coverage"] = 커버리지' in 글


def test_문서() -> None:
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "docs/paper.md").read_text(encoding="utf-8")
    for 조각 in ("step_log.price_coverage", "{day, targets}", "25.2342"):
        assert 조각 in 글, 조각


def test_시장_갈림길이_미국에_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """일일 배치가 부르는 것은 `collect_prices` 다 — 미국 갈래가 dict 를 넘기지 않으면 칸이 영영 안 선다"""
    from batch.jobs import daily
    from batch.sources.yfinance_src import DailyBar

    _바(monkeypatch, [DailyBar(ticker="AAPL", date="2026-09-15", close=220.0, volume=1)])
    판: dict[str, int] = {}
    daily.collect_prices(FakeClient(symbols={"AAPL": 1}), "US", "2026-09-15", coverage=판)
    assert 판.get("day") == 1
