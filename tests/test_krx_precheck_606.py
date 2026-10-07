"""거래소 호출 전 한도 확인·치유 루프 멈춤 (docs/infra.md 25.606, 감사)."""

from __future__ import annotations

from typing import Any

import pytest

from batch.core import db
from batch.jobs import daily, universe
from batch.sources import dart, dart_disclosures, dart_dividends, krx, yfinance_src
from batch.sources.yfinance_src import FetchResult
from tests.test_kr_yahoo_fallback import _client


def _세기(monkeypatch: pytest.MonkeyPatch, 이름: str, 결과: FetchResult) -> list[Any]:
    불린: list[Any] = []
    monkeypatch.setattr(krx, 이름, lambda *a, **k: 불린.append(a) or 결과)
    return 불린


class Test한도_찬_날:
    @pytest.fixture(autouse=True)
    def _막힘(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(db, "usage_blocked_today", lambda client, api: api == "krx_openapi")

    def test_일일_시세는_부르지_않고_야후로_대신한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        불린 = _세기(monkeypatch, "fetch_daily", FetchResult(ok=True, data=[]))
        야후: list[Any] = []
        monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: 야후.append(a) or FetchResult(ok=False))
        monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
        with pytest.raises(RuntimeError, match="한 건도 저장하지 못했습니다"):
            daily.collect_kr_prices(_client(), "2026-09-25")  # type: ignore[arg-type]
        assert 불린 == [] and len(야후) == 2

    def test_유니버스_마스터와_시총도_부르지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        마스터 = _세기(monkeypatch, "fetch_master", FetchResult(ok=True, data=[]))
        시총 = _세기(monkeypatch, "fetch_daily", FetchResult(ok=True, data=[]))
        _n, w1 = universe.refresh_kr_master(_client(), "20260925")  # type: ignore[arg-type]
        w2 = universe.refresh_kr_market_cap(_client(), "20260925")  # type: ignore[arg-type]
        assert 마스터 == [] and 시총 == []
        assert any("한도" in w for w in w1) and any("한도" in w for w in w2)

    def test_치유도_부르지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = _야후날(3)
        불린 = _세기(monkeypatch, "fetch_daily", FetchResult(ok=True, data=[]))
        out = daily._heal_yahoo_days(c, "KOSPI", "2026-09-25", {"005930": 1, "000660": 2})  # type: ignore[arg-type]
        assert 불린 == [] and any("한도" in w for w in out)


def _야후날(n: int) -> Any:
    c = _client()
    for d in range(n):
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, 100, 'KRW', ?, 't')",
            [f"2026-09-{18 + d:02d}", yfinance_src.SOURCE],
        )
    return c


def test_치유는_잇달아_실패하면_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "usage_blocked_today", lambda client, api: False)
    c = _야후날(5)
    불린 = _세기(monkeypatch, "fetch_daily", FetchResult(ok=False, error="HTTP 500"))
    daily._heal_yahoo_days(c, "KOSPI", "2026-09-25", {"005930": 1, "000660": 2})  # type: ignore[arg-type]
    assert len(불린) == 2, "두 날 잇달아 실패하면 남은 날은 다음 배치에 맡긴다"


def test_DART_한도_코드는_한_곳에서_온다() -> None:
    """같은 문자열은 파이썬이 한 객체로 묶어 `is` 로는 사본을 못 가린다 — 소스를 본다."""
    import inspect

    for 모듈 in (dart_disclosures, dart_dividends):
        assert '"020"' not in inspect.getsource(모듈), f"{모듈.__name__} 에 020 사본이 있다"
    assert dart.STATUS_DAILY_LIMIT == "020"


def test_마스터_빈_응답도_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 마스터는 지난주 소속부로 판정하는데 아무 말이 없었다 (docs/infra.md 25.612, 감사)."""
    monkeypatch.setattr(db, "usage_blocked_today", lambda client, api: False)
    _세기(monkeypatch, "fetch_master", FetchResult(ok=True, data=[]))
    _n, w = universe.refresh_kr_master(_client(), "20260925")  # type: ignore[arg-type]
    assert any("응답이 비었습니다" in x and "관리종목" in x for x in w)


def test_한_건도_못_받으면_까닭을_예외에_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """두 시장이 막히고 야후도 실패하면 실패 알림에 한도라는 까닭이 없었다 (docs/infra.md 25.615)."""
    monkeypatch.setattr(db, "usage_blocked_today", lambda client, api: api == "krx_openapi")
    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: FetchResult(ok=False, error="차단"))
    monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
    with pytest.raises(RuntimeError, match="한도"):
        daily.collect_kr_prices(_client(), "2026-09-25")  # type: ignore[arg-type]
