"""과거 시세 백필의 두 질의가 시세 표를 통째로 읽지 않고, 예전과 같은 답을 낸다 (docs/infra.md 25.866)."""

from __future__ import annotations

from datetime import date

from batch.jobs import backfill_kr as bf
from tests.test_scores_job import _sqlite_client


def _db():
    c = _sqlite_client()
    c.conn.execute("UPDATE stocks SET market = 'KOSPI', listed_date = '2020-01-02' WHERE id = 1")
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, listed_date)"
        " VALUES (3, 'K', 'KOSDAQ', 'KR', 'KRW', 'active', 't', 't', NULL)"
    )
    가격 = "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, 1, 'KRW', ?, 't')"
    for d, src in (("2026-01-02", "krx_openapi"), ("2026-01-05", "yfinance"), ("2026-01-06", "krx_openapi")):
        c.conn.execute(가격, [1, d, src])
    c.conn.execute(가격, [3, "2026-01-05", "krx_openapi"])  # 다른 시장
    c.conn.execute(가격, [2, "2026-01-07", "x"])  # 미국
    return c


def test_예전과_같은_답() -> None:
    c = _db()
    s, e = date(2026, 1, 1), date(2026, 1, 31)
    assert bf.already_stored(c, "KOSPI", s, e) == {"2026-01-02", "2026-01-06"}  # type: ignore[arg-type]
    assert bf.already_stored(c, "KOSDAQ", s, e) == {"2026-01-05"}  # type: ignore[arg-type]
    assert bf.already_stored(c, "KOSPI", date(2026, 1, 3), date(2026, 1, 5)) == set()  # type: ignore[arg-type]
    first, listed = bf.first_seen_and_listed(c, "KOSPI", s, e)  # type: ignore[arg-type]
    assert first == {1: "2026-01-02"} and listed == {1: "2020-01-02"}
    first, listed = bf.first_seen_and_listed(c, "KOSDAQ", date(2026, 1, 6), e)  # type: ignore[arg-type]
    assert first == {} and listed == {}  # 구간 안 시세가 없는 종목은 빠진다


def test_시세_표를_통째로_훑지_않는다() -> None:
    c = _db()
    잡은것: list[tuple[str, list]] = []
    원래 = c.execute

    def 잡기(sql, args=None):
        잡은것.append((sql, list(args or [])))
        return 원래(sql, args)

    c.execute = 잡기  # type: ignore[method-assign]
    bf.already_stored(c, "KOSPI", date(2026, 1, 1), date(2026, 1, 31))  # type: ignore[arg-type]
    bf.first_seen_and_listed(c, "KOSPI", date(2026, 1, 1), date(2026, 1, 31))  # type: ignore[arg-type]
    assert len(잡은것) == 2
    for sql, args in 잡은것:
        계획 = [str(r[-1]) for r in c.conn.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()]
        # 예전 계획: `SEARCH p USING INDEX idx_prices_date (date>? AND date<?)` — 구간 시세를 **전부** 걷는 범위 조회였다
        assert not any(d.startswith(("SCAN p", "SCAN prices")) for d in 계획), 계획
        assert not any(d.startswith("SEARCH p USING INDEX idx_prices_date (date>? AND date<?)") for d in 계획), 계획
