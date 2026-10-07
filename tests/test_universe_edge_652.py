"""미국 상장일이 수집 시작 가장자리면 "상장 1년 미만" 으로 자르지 않는다 (docs/infra.md 25.652, 감사)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.services import pit_universe as pit
from batch.services import universe as uni


def _후보(listed: str, edge: bool) -> uni.Candidate:
    return uni.Candidate(
        ticker="ABC", name="ABC", market="NASDAQ", listed_date=listed,
        market_cap=5_000_000_000, avg_turnover_20d=50_000_000, listed_at_history_edge=edge,
    )


def test_가장자리_상장일은_1년_규칙으로_자르지_않는다() -> None:
    f = uni.UniverseFilters(min_market_cap=1_000_000_000, min_avg_turnover_20d=5_000_000)
    as_of = date(2026, 11, 20)
    assert uni.judge(_후보("2026-09-16", False), f, as_of).reason == uni.REASON_NEWLY_LISTED
    assert uni.judge(_후보("2026-09-16", True), f, as_of).included


def test_수집_시작일에_몰린_날을_가장자리로_찾는다() -> None:
    첫날들 = ["2021-09-17"] * 50 + ["2026-09-16"] * 3000 + ["2026-10-02"]  # 마지막은 진짜 새 상장 하나
    시작 = pit.history_starts_of(첫날들)
    assert "2026-09-16" in 시작 and "2026-10-02" not in 시작


@pytest.mark.parametrize(("수집시작", "편입"), [("2026-09-16", True), ("2026-09-25", True), (None, False)])
def test_운영_판정은_미국_일일_수집_시작일을_가장자리로_본다(수집시작: str | None, 편입: bool) -> None:
    """상장일 무리 대신 수집 시작일 하나만 본다 — IPO 가 몰린 주를 가장자리로 잡지 않게 (25.656, 교차검증)."""
    from datetime import date as _d
    from datetime import timedelta

    from batch.core import db
    from batch.jobs import daily
    from batch.jobs import universe as job
    from tests.test_universe_snapshot import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at,"
        " listed_date, share_kind, market_cap) VALUES (1, 'ABC', 'NASDAQ', 'US', 'ABC', 'USD', 'active', 't', 't',"
        " '2026-09-16', '보통주', 5000000000)"
    )
    끝 = _d(2026, 11, 20)
    for i in range(40):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, volume, value, currency, source, fetched_at)"
            " VALUES (1, ?, 100, 1000000, 100000000, 'USD', 't', 't')",
            [(끝 - timedelta(days=i)).isoformat()],
        )
    if 수집시작:
        c.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, status, started_at) VALUES (?, 'US', ?, 'success', 't')",
            [daily.job_name("US"), 수집시작],
        )
    job.build_snapshot(mem, "US", 끝.isoformat())  # type: ignore[arg-type]
    포함, 사유 = c.execute("SELECT included, exclude_reason FROM universe_members WHERE stock_id = 1").fetchone()
    assert bool(포함) is 편입, 사유
