"""첫 화면 "오늘 볼 것" 은 종목으로 센다 (docs/infra.md 25.2374, 결함 루프 2026-11-26).

짧은 이름으로 거르면 같은 회사의 다른 주식 종류(GOOGL·GOOG → "Alphabet")가 하나로 세어져 손절·새로 걸림 수가 적게
나갔다. 같은 종목의 같은 색 플래그 둘(25.1738)은 여전히 하나다.
"""

from __future__ import annotations

import inspect

from batch.jobs import sell_flags as jsf
from batch.notify import formatter


def _f(sid: int, name: str, level: str = "red", new: bool = True, ticker: str | None = None) -> dict:
    return {"stock_id": sid, "name": name, "level": level, "new": new, "ticker": ticker, "as_of_date": "2026-11-27"}


def test_같은_짧은_이름의_다른_종목은_둘로_센다() -> None:
    줄 = formatter.todo_line([_f(1, "Alphabet Inc. Class A Common Stock", ticker="GOOGL"),
                              _f(2, "Alphabet Inc. Class C Capital Stock", ticker="GOOG")], "USD", "2026-11-27")  # fmt: skip
    assert 줄 == "🔔 보유 매도 플래그: 🔴손절 2(Alphabet(GOOGL)·Alphabet(GOOG)) — 새로 걸림 2"


def test_색이_달라도_새로_걸림은_종목마다() -> None:
    줄 = formatter.todo_line([_f(1, "Fox Corporation Class A Common Stock", "red", ticker="FOXA"),
                              _f(2, "Fox Corporation Class B Common Stock", "yellow", ticker="FOX")], "USD", "2026-11-27")  # fmt: skip
    assert 줄 is not None and 줄.endswith("— 새로 걸림 2")


def test_같은_종목의_같은_색_둘은_하나() -> None:
    """25.1738 그대로 — 황 기간초과와 재무악화가 한 종목에 함께 서도 하나"""
    줄 = formatter.todo_line([_f(7, "삼성전자", "yellow"), _f(7, "삼성전자", "yellow")], "KRW", "2026-11-27")
    assert 줄 == "🔔 보유 매도 플래그: 🟡점검 1(삼성전자) — 새로 걸림 1"


def test_겹치지_않으면_티커를_붙이지_않는다() -> None:
    줄 = formatter.todo_line([_f(1, "Apple Inc. Common Stock", "green", False, "AAPL")], "USD", "2026-11-27")
    assert 줄 == "🔔 보유 매도 플래그: 🟢목표 도달 1(Apple)"


def test_종목_번호가_없으면_이름으로() -> None:
    """옛 꼴 행(종목 번호 없음)은 이름으로 센다 — 깨지지 않는다"""
    줄 = formatter.todo_line([{"name": "삼성전자", "level": "red"}, {"name": "삼성전자", "level": "red"}], "KRW")
    assert 줄 == "🔔 보유 매도 플래그: 🔴손절 1(삼성전자)"


def test_리포트_행이_티커를_싣는다() -> None:
    src = inspect.getsource(jsf.active_flags_for_report)
    assert "s.ticker" in src and '"ticker": r.get("ticker")' in src


def test_실제_질의에서_첫_줄까지() -> None:
    """25.2380 (25.2374 교차검증 [권고]) — 글 대조가 아니라 `active_flags_for_report` 가 실제로 티커를 돌려주고 첫 줄까지
    이어지는지"""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sid, tk, 이름 in ((901, "GOOGL", "Alphabet Inc. Class A Common Stock"), (902, "GOOG", "Alphabet Inc. Class C Capital Stock")):
        mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)"
                         " VALUES (?, ?, 'NASDAQ', 'US', ?, 'USD', 'active', 't', 't')", [sid, tk, 이름])  # fmt: skip
        mem.conn.execute("INSERT INTO sell_flags (stock_id, as_of_date, level, reason_code, rationale_text, rationale_data,"
                         " is_active, first_seen_date, created_at) VALUES (?, '2026-11-27', 'red', '손절', 'x', '{}', 1,"
                         " '2026-11-27', '2026-11-27T00:00:00+00:00')", [sid])  # fmt: skip
    행 = jsf.active_flags_for_report(mem, "US", "2026-11-27")  # type: ignore[arg-type]
    assert sorted(r["ticker"] for r in 행) == ["GOOG", "GOOGL"]
    줄 = formatter.todo_line(행, "USD", "2026-11-27")
    assert 줄 is not None and "🔴손절 2(" in 줄 and "Alphabet(GOOGL)" in 줄 and "Alphabet(GOOG)" in 줄
