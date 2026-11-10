"""빠진 국내 ETF 날을 지난 `KR_ETF_HEAL_SESSIONS` 거래일 안에서 다시 받는다 (docs/infra.md 25.2223, 25.2216 의 남은 길 (가)).

예전엔 `_store_kr_etf_day` 가 그 실행의 날 하나만 받아, 한 번 실패한 날은 열린 ETF 주문이 기다림 끝에 "시세 못
받음" 으로 닫혔다."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.jobs import daily
from batch.jobs import paper as job
from batch.sources import krx
from tests.test_paper_job_1445 import Client

ETF, ETF2 = 10, 11


def _c() -> Client:
    c = Client()
    for sid, t in ((ETF, "069500"), (ETF2, "114800")):
        c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
                       " fetched_at, asset_type) VALUES (?, ?, 'M', 'KR', ?, 'ETF', 'KRW', 'active', 't', 't', 'etf')",
                       [sid, t, t])  # fmt: skip
    return c


def _행(c: Client, sid: int, day: str, close: float = 100.0) -> None:
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, 1000, 'KRW', 'krx', 't')", [sid, day, close, close, close, close])  # fmt: skip


def _응답(불린: list[str], 실패: set[str] = frozenset(), 값: float = 200.0):  # noqa: B006
    def f(bas_dd: str):
        불린.append(bas_dd)
        if bas_dd in 실패:
            return SimpleNamespace(ok=False, data=None, error="시간 초과", attempts=1, source="krx")
        rows = [SimpleNamespace(isu_cd=t, open=값, high=값, low=값, close=값, volume=1, value=1, change_pct=0.0)
                for t in ("069500", "114800")]  # fmt: skip
        return SimpleNamespace(ok=True, data=rows, error=None, attempts=1, source="krx_etf")
    return f


@pytest.fixture(autouse=True)
def _한도(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily.db, "usage_blocked_today", lambda *_a, **_k: False)
    monkeypatch.setattr(daily.db, "record_api_call", lambda *_a, **_k: None)


def _종가(c: Client, sid: int, day: str) -> list:
    return c.q("SELECT close FROM prices WHERE stock_id = ? AND date = ?", [sid, day])


def test_다시_받는_거리는_가상_기다림과_같다() -> None:
    assert daily.KR_ETF_HEAL_SESSIONS == job.MISSING_BAR_WAIT_SESSIONS


def test_지난_날_빠진_ETF_행을_다시_받고_있는_행은_덮지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _c()
    # 10-19(월)·10-16(금) 시장은 열렸고(주식 행), 10-16 은 ETF 하나만 있다, 10-19 는 둘 다 없다
    for d in ("2026-10-16", "2026-10-19"):
        _행(c, 1, d)
    _행(c, ETF, "2026-10-16", 100.0)
    불린: list[str] = []
    monkeypatch.setattr(krx, "fetch_etf_daily", _응답(불린))
    경고 = daily._store_kr_etf_day(c, "20261020", "2026-10-20")
    assert 불린 == ["20261020", "20261019", "20261016"]
    assert _종가(c, ETF, "2026-10-16") == [(100.0,)]  # 있던 행은 그대로
    assert _종가(c, ETF2, "2026-10-16") == [(200.0,)] and _종가(c, ETF, "2026-10-19") == [(200.0,)]
    assert any("다시 받았습니다" in w for w in 경고)


def test_다_있거나_시장이_닫힌_날은_부르지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _c()
    for sid in (1, ETF, ETF2):
        _행(c, sid, "2026-10-19")
    # 10-16 은 주식 행도 없다(우리가 받지 못한 날·휴장) — 부르지 않는다
    불린: list[str] = []
    monkeypatch.setattr(krx, "fetch_etf_daily", _응답(불린))
    daily._store_kr_etf_day(c, "20261020", "2026-10-20")
    assert 불린 == ["20261020"]


def test_오늘_실패해도_지난_날은_다시_받고_경고는_다시_받는다고_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _c()
    _행(c, 1, "2026-10-19")
    불린: list[str] = []
    monkeypatch.setattr(krx, "fetch_etf_daily", _응답(불린, 실패={"20261020"}))
    경고 = daily._store_kr_etf_day(c, "20261020", "2026-10-20")
    assert "20261019" in 불린 and _종가(c, ETF, "2026-10-19") == [(200.0,)]
    assert any("다시 받습니다" in w and "시세 못 받음" in w for w in 경고)
