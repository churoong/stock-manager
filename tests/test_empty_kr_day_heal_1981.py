"""25.1981 (결함 루프 10-29 — 감사 [필수]·반박 [권고]) — 국내 두 시장이 다 빈 거래일도 다음 배치가 거래소 값으로 다시 받는다.

예전 되받기 질의 둘(`HEAL_DAYS_SQL`·`MISSING_MARKET_DAYS_SQL`)은 같은 시장의 야후 행이나 다른 국내 시장 행이 있어야 그날을
골라, 아침 실행 둘이 모두 한 줄도 못 저장한 날(10-27)은 영영 다시 받지 않았다(25.574 가 알고 둔 구멍). 그날 가상모의투자
주문은 "시세 못 받음" 으로 만료되고 KIS모의투자에서는 이미 체결돼 다음 날 정리 매도가 나갔다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.core import db
from batch.jobs import daily
from batch.sources import krx
from batch.sources.yfinance_src import FetchResult
from tests.test_kr_yahoo_fallback import _client


def _행(c: Any, sid: int, 날: str) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, 100, 'KRW', ?, 't')",
        [sid, 날, daily.KRX_SOURCE],
    )


def test_두_시장이_다_빈_거래일을_다시_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "usage_blocked_today", lambda client, api: False)
    c = _client()
    # 창(10-19 ~ 10-28) 안 거래일 가운데 10-27 만 국내 두 시장 행이 다 없다
    for 날 in ("2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"):
        for sid in (1, 3):
            _행(c, sid, 날)
    # 예전 질의 둘로는 안 걸린다
    시작 = "2026-10-18"
    assert c.execute(daily.MISSING_MARKET_DAYS_SQL, [시작, "2026-10-28", "2026-10-28", "KOSPI", "KOSPI"]).rows == []
    assert daily._빈_거래일(c, "KOSPI", 시작, "2026-10-28") == ["2026-10-27"]
    assert daily._빈_거래일(c, "KOSDAQ", 시작, "2026-10-28") == ["2026-10-27"]
    불린: list[Any] = []
    monkeypatch.setattr(krx, "fetch_daily", lambda market, 날: 불린.append((market, 날)) or FetchResult(ok=False, error="x"))
    daily._heal_yahoo_days(c, "KOSPI", "2026-10-28", {"005930": 1, "000660": 2})  # type: ignore[arg-type]
    assert ("KOSPI", "20261027") in 불린


def test_휴장일과_행이_있는_날은_고르지_않는다() -> None:
    c = _client()
    _행(c, 1, "2026-10-23")
    # 10-24·25 는 주말, 10-23 은 KOSPI 행이 있다 — KOSDAQ 만 빈 날로
    assert daily._빈_거래일(c, "KOSPI", "2026-10-23", "2026-10-26") == []
    assert daily._빈_거래일(c, "KOSDAQ", "2026-10-23", "2026-10-26") == ["2026-10-23"]
