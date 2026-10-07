"""점수 계열의 날짜를 지수 일봉으로 잡는다 (docs/infra.md 25.899).

미국 날짜 걷기가 운영에서 334걸음에 152만 행을 읽었다 — 날짜 색인 안에서 국내 행이 미국 행보다 앞에 와서.
"""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import scores
from tests.test_scores_job import _sqlite_client


def _지수(c, code: str, end: str, n: int) -> None:
    d = date.fromisoformat(end)
    for _ in range(n):
        c.execute(
            "INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES (?, ?, 1, 't', 't')",
            [code, d.isoformat()],
        )
        d -= timedelta(days=1)


def test_지수가_넉넉하면_그_날짜를_쓴다() -> None:
    c = _sqlite_client()
    _지수(c, "SP500", "2026-10-01", 40)
    dates = scores.market_dates_from_index(c, "US", "2026-10-01", 30)
    assert dates is not None and len(dates) == 30 and dates[0] == "2026-10-01"


def test_모자라거나_멈춘_지수는_믿지_않는다() -> None:
    c = _sqlite_client()
    _지수(c, "SP500", "2026-10-01", 10)
    assert scores.market_dates_from_index(c, "US", "2026-10-01", 30) is None  # 모자람
    _지수(c, "KOSPI", "2026-09-01", 40)
    assert scores.market_dates_from_index(c, "KR", "2026-10-01", 30) is None  # 한 달 전에 멈춤


def test_계열_읽기가_지수가_있으면_날짜를_걷지_않는다() -> None:
    c = _sqlite_client()
    _지수(c, "SP500", "2026-10-01", 400)
    질의: list[str] = []
    원래 = c.execute
    c.execute = lambda sql, args=None: (질의.append(sql), 원래(sql, args))[1]  # type: ignore[method-assign]
    scores.load_series(c, "US", "2026-10-01", 274)  # type: ignore[arg-type]
    assert not [q for q in 질의 if "WITH RECURSIVE d(x)" in q]
