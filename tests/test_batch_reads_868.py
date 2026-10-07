"""25.865~867 의 질의 고침을 지키는 검사 — 교차검증이 "인자 순서가 틀려도 안 깨진다" 고 짚은 자리 (docs/infra.md 25.868).

값은 파이썬 쪽에서 같게 나와도, SQL 이 잘라 주지 않으면 **전체 이력을 읽는 상태로 조용히 돌아간다.** 그래서 SQL 이 돌려준 행 자체를 본다.
"""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import daily, scores, signals, universe
from tests.test_scores_job import _sqlite_client


def _db(일수: int = 80):
    c = _sqlite_client()
    for k in range(일수):
        d = (date(2026, 1, 1) + timedelta(days=k)).isoformat()
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, value, currency, source, fetched_at)"
            " VALUES (1, ?, ?, 10, 'KRW', 'krx_openapi', 't')",
            [d, 100 + k],
        )
    c.conn.execute(
        "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
        " VALUES ('2026-01-01', 1, 1, 'KRW', 't')"
    )
    return c


def _잡기(c):
    잡은것: list[tuple[str, list]] = []
    원래 = c.execute

    def 잡기(sql, args=None):
        rs = 원래(sql, args)
        잡은것.append((sql, rs))
        return rs

    c.execute = 잡기  # type: ignore[method-assign]
    return 잡은것


def test_OFFSET_질의는_SQL_에서_N행으로_자른다() -> None:
    """OFFSET 자리와 기준일 자리가 바뀌면 `x.date <= 60` 이 늘 거짓 → COALESCE('') 로 전체 이력을 읽는다."""
    c = _db()
    잡은것 = _잡기(c)
    signals.load_recent_prices(c, "KR", "2026-03-21", days=20)  # type: ignore[arg-type]
    assert len(잡은것[-1][1].rows) == 20
    universe._avg_turnover_map(c, "KR", "2026-03-21", days=20)  # type: ignore[arg-type]
    assert len(잡은것[-1][1].rows) == 20


def test_기준일_뒤_스냅샷에만_든_종목은_읽지_않는다() -> None:
    c = _db()
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (5, 'N', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (5, '2026-03-20', 1, 'KRW', 't', 't')"
    )
    c.conn.execute(
        "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
        " VALUES ('2026-04-01', 5, 1, 'KRW', 't')"
    )
    assert 5 not in scores.load_series(c, "KR", "2026-03-21", days=10)  # type: ignore[arg-type]
    assert 5 not in signals.load_recent_prices(c, "KR", "2026-03-21", days=10)  # type: ignore[arg-type]


def test_빈_시장일_찾기는_끝날을_넣지_않는다() -> None:
    """MISSING 의 상한은 `date < trade_date` — 오늘 행은 아직 다 들어오지 않았을 수 있다."""
    c = _db(10)
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (6, 'Q', 'KOSDAQ', 'KR', 'KRW', 'active', 't', 't')"
    )
    rows = c.execute(daily.MISSING_MARKET_DAYS_SQL, ["2026-01-01", "2026-01-05", "2026-01-05", "KOSDAQ", "KOSDAQ"]).rows
    assert [r[0] for r in rows] == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
