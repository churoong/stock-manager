"""국내 적립 판정의 기준 사업연도는 최빈값이다 (docs/infra.md 25.632, 감사)."""

from __future__ import annotations

from batch.jobs import accumulation as job
from tests.test_report_picks import SqliteClient


def _회사(c: SqliteClient, sid: int, 연도들: list[int], country: str = "KR") -> None:
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (?, ?, 'KOSPI', ?, 'KRW', 'active', 't', 't')",
        [sid, f"{sid:06d}", country],
    )
    for y in 연도들:
        c.conn.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
            " receipt_no, currency, unit, source, fetched_at) VALUES (?, ?, ?, 'A', 1, 't', 'r', 'KRW', 'KRW', 't', 't')",
            [sid, y, job.ANNUAL],
        )


def test_비12월_결산_한_곳이_새_연도를_내도_기준은_대다수의_연도다() -> None:
    c = SqliteClient()
    for sid in (1, 2, 3):
        _회사(c, sid, [2021, 2022, 2023, 2024, 2025])
    _회사(c, 4, [2022, 2023, 2024, 2025, 2026])  # 6월 결산이 2026 사업연도를 먼저 냈다
    assert job.기준_사업연도(c, "KR") == 2025  # type: ignore[arg-type]


def test_대다수가_새_연도를_내면_옮겨_간다() -> None:
    c = SqliteClient()
    for sid in (1, 2):
        _회사(c, sid, [2025, 2026])
    _회사(c, 3, [2024, 2025])
    assert job.기준_사업연도(c, "KR") == 2026  # type: ignore[arg-type]


def test_재무가_없으면_없다() -> None:
    assert job.기준_사업연도(SqliteClient(), "KR") is None  # type: ignore[arg-type]


def test_이익잉여금을_다_모르면_0_이_아니라_모름이다() -> None:
    """모름이 0 구간(가장 나쁨)으로 분포에 들어갔다 (docs/infra.md 25.632, 감사)."""
    import dataclasses

    from tests.test_accumulation import good, judge

    inp = good()
    for y, f in list(inp.years.items()):
        inp.years[y] = dataclasses.replace(f, retained_earnings=None)
    j = judge(inp)
    assert j.passed and j.metrics["retained_up"] is None
    일부 = good()
    첫해 = min(일부.years)
    일부.years[첫해] = dataclasses.replace(일부.years[첫해], retained_earnings=None)
    j2 = judge(일부)
    assert j2.metrics["retained_up"] is None
    assert any("3/3 구간만 앎" in r["display"] for r in j2.criteria)
    assert judge(good()).metrics["retained_up"] == 4.0


def test_배당_행이_없는_해는_무배당과_가른다() -> None:
    """수집 누락이 "현금배당이 없는 해" 로 적혔다 (docs/infra.md 25.634, 감사). 판정은 여전히 떨어진다."""
    from tests.test_accumulation import good, judge

    inp = good()
    del inp.dividends[min(inp.dividends)]
    j = judge(inp)
    assert not j.passed and j.first_failed_gate == "G10"
    assert "배당 자료가 없는 해" in (j.excluded_reason or "")
    assert any("1년 배당 자료 없음" in r["display"] for r in j.criteria)

    무배당 = good()
    첫해 = min(무배당.dividends)
    무배당.dividends[첫해] = __import__("dataclasses").replace(무배당.dividends[첫해], cash_dividend_total=None)
    j2 = judge(무배당)
    assert "현금배당이 없는 해" in (j2.excluded_reason or "")
