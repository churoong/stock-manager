"""국내 시총 갱신 — 빈 응답이면 직전 거래일로 한 번 물러난다 (docs/infra.md 25.955)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.core import calendar as cal
from batch.jobs import universe
from batch.sources import krx
from batch.sources.krx import KrxDailyRow
from batch.sources.yfinance_src import FetchResult


def _row(code: str, bas_dd: str, cap: float) -> KrxDailyRow:
    return KrxDailyRow(bas_dd=bas_dd, isu_cd=code, isu_nm="n", mkt_nm="KOSPI", close=1.0, change=0.0, change_pct=0.0,
                       open=1.0, high=1.0, low=1.0, volume=1, value=1, market_cap=cap, listed_shares=1)  # fmt: skip


class _Client:
    def __init__(self) -> None:
        self.statements: list = []

    def batch(self, statements):  # noqa: ANN001
        self.statements += statements


def test_빈_응답이면_직전_거래일로_물러나_그_날짜로_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    불린: list[tuple[str, str]] = []

    def 거래소(market: str, bas_dd: str) -> FetchResult:
        불린.append((market, bas_dd))
        if bas_dd == "20261002":  # 연휴 전 마지막 거래일 치 — 거래소가 아직 안 냈다 (2026-10-04 실측)
            return FetchResult(ok=True, data=[], source="krx")
        return FetchResult(ok=True, data=[_row("005930", bas_dd, 100.0)], source="krx")

    monkeypatch.setattr(krx, "fetch_daily", 거래소)
    monkeypatch.setattr(universe.db, "record_api_call", lambda *_a, **_k: {"state": "ok"})
    monkeypatch.setattr(universe.db, "usage_blocked_today", lambda *_a, **_k: False)
    monkeypatch.setattr(cal, "previous_session", lambda market, before=None: date(2026, 10, 1))
    c = _Client()
    경고 = universe.refresh_kr_market_cap(c, "20261002")  # type: ignore[arg-type]
    assert [b for _m, b in 불린] == ["20261002", "20261001", "20261002", "20261001"]  # 시장마다 한 번 물러난다
    assert all("직전 거래일 20261001 값으로 갱신" in w for w in 경고) and len(경고) == 2
    # market_cap_date 는 물러난 날짜 — 어느 날 값인지 남는다
    assert c.statements and all(args[1] == "2026-10-01" for _sql, args in c.statements)


def test_물러나도_비면_지난_시총으로_판정한다고_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(krx, "fetch_daily", lambda *_a: FetchResult(ok=True, data=[], source="krx"))
    monkeypatch.setattr(universe.db, "record_api_call", lambda *_a, **_k: {"state": "ok"})
    monkeypatch.setattr(universe.db, "usage_blocked_today", lambda *_a, **_k: False)
    monkeypatch.setattr(cal, "previous_session", lambda market, before=None: date(2026, 10, 1))
    경고 = universe.refresh_kr_market_cap(_Client(), "20261002")  # type: ignore[arg-type]
    assert len(경고) == 2 and all("비었습니다(직전 거래일 20261001 도)" in w for w in 경고)
    assert universe.MARKET_CAP_FALLBACK_STEPS == 1
