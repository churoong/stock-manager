"""한국거래소가 실패한 시장의 그날 시세를 야후로 받는다 (docs/data-sources.md 2절, docs/infra.md 25.505)."""

from __future__ import annotations

import pytest

from batch.jobs import daily
from batch.sources import krx, yfinance_src
from batch.sources.yfinance_src import DailyBar, FetchResult
from tests.test_report_picks import SqliteClient


def _client() -> SqliteClient:
    c = SqliteClient()
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol) VALUES"
        " (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '005930.KS'),"
        " (2, '000660', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '000660.KS'),"
        " (3, '035720', 'KOSDAQ', 'KR', 'KRW', 'active', 't', 't', '035720.KQ')"
    )
    return c


def test_거래소가_실패하면_그_시장을_야후로_받고_그날_봉만_넣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client()
    monkeypatch.setattr(krx, "fetch_daily", lambda market, bas_dd: FetchResult(ok=False, error="HTTP 500", source="krx"))
    불린: list[list[str]] = []

    def 야후(symbols: list[str], lookback_days: int = 10, **_: object) -> FetchResult:
        불린.append(symbols)
        return FetchResult(ok=True, source="yfinance", data=[
            DailyBar(s, "2026-09-25", 100.0, open=99.0, volume=10, currency="KRW") for s in symbols
        ] + [DailyBar(symbols[0], "2026-09-24", 90.0, currency="KRW")])  # 앞날 봉은 넣지 않는다

    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", 야후)
    monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
    _rows, warnings, _ = daily.collect_kr_prices(c, "2026-09-25")  # type: ignore[arg-type]
    assert 불린 == [["000660.KS", "005930.KS"], ["035720.KQ"]]  # 시장마다
    got = c.conn.execute("SELECT stock_id, date, close, value, source FROM prices ORDER BY stock_id").fetchall()
    assert got == [(1, "2026-09-25", 100.0, None, "yfinance"), (2, "2026-09-25", 100.0, None, "yfinance"),
                   (3, "2026-09-25", 100.0, None, "yfinance")]  # fmt: skip
    assert any("KOSPI 시세를 야후로 대신 받았습니다: 2/2종목" in w for w in warnings)


def test_야후도_실패하면_예전처럼_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client()
    monkeypatch.setattr(krx, "fetch_daily", lambda market, bas_dd: FetchResult(ok=False, error="HTTP 500", source="krx"))
    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: FetchResult(ok=False, error="차단"))
    monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
    with pytest.raises(RuntimeError, match="한 건도 저장하지 못했습니다"):
        daily.collect_kr_prices(c, "2026-09-25")  # type: ignore[arg-type]


def _krx_행(isu_cd: str, close: float) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(isu_cd=isu_cd, open=close, high=close, low=close, close=close, volume=10, value=1000,
                           change_pct=0.5)  # fmt: skip


def test_다음_배치가_야후로_받은_날을_거래소_값으로_바꾼다(monkeypatch: pytest.MonkeyPatch) -> None:
    """야후 행은 거래대금이 비어 단기 신호가 60거래일 막혔다 (docs/infra.md 25.513, 교차검증)."""
    c = _client()
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES"
        " (1, '2026-09-24', 100, 'KRW', 'yfinance', 't'), (2, '2026-09-24', 50, 'KRW', 'yfinance', 't')"
    )
    불린: list[tuple[str, str]] = []

    def 거래소(market: str, bas_dd: str) -> FetchResult:
        불린.append((market, bas_dd))
        데이터 = [_krx_행("005930", 101.0), _krx_행("000660", 51.0)] if market == "KOSPI" else [_krx_행("035720", 9.0)]
        return FetchResult(ok=True, source="krx_openapi", data=데이터)

    monkeypatch.setattr(krx, "fetch_daily", 거래소)
    monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
    감지: list[str] = []
    monkeypatch.setattr(daily, "_detect_kr_actions", lambda client, 날, *a, **k: 감지.append(날) or [])
    _rows, warnings, _ = daily.collect_kr_prices(c, "2026-09-25")  # type: ignore[arg-type]
    assert ("KOSPI", "20260924") in 불린
    # KOSDAQ 은 그날 행이 한 줄도 없다(야후도 실패한 날) — 다른 시장에 행이 있으니 거래일이었다. 다시 받는다 (25.571)
    assert ("KOSDAQ", "20260924") in 불린
    assert c.conn.execute("SELECT close FROM prices WHERE stock_id = 3 AND date = '2026-09-24'").fetchone() == (9.0,)
    got = c.conn.execute("SELECT stock_id, close, value, source FROM prices WHERE date = '2026-09-24' ORDER BY 1")
    assert got.fetchall() == [
        (1, 101.0, 1000.0, "krx_openapi"), (2, 51.0, 1000.0, "krx_openapi"), (3, 9.0, 1000.0, "krx_openapi")
    ]
    # 바꾼 것은 경고가 아니다. 그날의 기업행위도 본다 (25.518, 교차검증)
    assert not any("바꿨습니다" in w for w in warnings)
    assert "2026-09-24" in 감지
    # 거래소 행이 생긴 날은 다음 배치가 다시 부르지 않는다 — 거래정지 종목의 야후 행이 남아도
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (9, '999999', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '999999.KS')"
    )
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
        " VALUES (9, '2026-09-24', 1, 'KRW', 'yfinance', 't')"
    )
    불린.clear()
    daily.collect_kr_prices(c, "2026-09-26")  # type: ignore[arg-type]
    assert ("KOSPI", "20260924") not in 불린


def test_거래소_값이_있으면_폴백이_덮지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """--force 재실행에서만 거래소가 실패하면 실측을 야후 값으로 덮었다 (25.513, 교차검증)."""
    c = _client()
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, close, value, currency, source, fetched_at) VALUES"
        " (1, '2026-09-25', 100, 5000, 'KRW', 'krx_openapi', 't')"
    )
    monkeypatch.setattr(krx, "fetch_daily", lambda market, bas_dd: FetchResult(ok=False, error="HTTP 500", source="krx"))
    불린: list[list[str]] = []

    def 야후(symbols: list[str], lookback_days: int = 10, **_: object) -> FetchResult:
        불린.append(symbols)
        return FetchResult(ok=True, source="yfinance", data=[DailyBar(s, "2026-09-25", 1.0) for s in symbols])

    monkeypatch.setattr(yfinance_src, "fetch_daily_bars", 야후)
    monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
    daily.collect_kr_prices(c, "2026-09-25")  # type: ignore[arg-type]
    assert 불린[0] == ["000660.KS"]  # 005930 은 이미 거래소 값이 있다
    assert c.conn.execute("SELECT close, value, source FROM prices WHERE stock_id = 1").fetchone() == (
        100.0, 5000.0, "krx_openapi")


def test_복구할_날_찾기는_상관_하위질의가_아니다() -> None:
    """야후 행마다 그날 전 종목을 훑어 읽는 행이 약 1,500배 늘었다 (docs/infra.md 25.525, 교차검증 실측)."""
    c = _client()
    계획 = c.conn.execute(
        "EXPLAIN QUERY PLAN " + daily.HEAL_DAYS_SQL, ["a", "b", "b", "krx_openapi", "KOSPI", "yfinance", "KOSPI"]
    ).fetchall()
    # 25.865: 날짜마다 도는 하위 질의는 이제 의도다 — 대신 시세 표를 통째로 훑지 않는다(날짜 색인으로만 찾는다)
    assert not any(str(r[-1]).startswith(("SCAN p", "SCAN k", "SCAN prices")) for r in 계획), 계획
