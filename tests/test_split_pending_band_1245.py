"""미국 분할 재수집 대기 종목은 밸류에이션 밴드·장기 밴드 신호를 내지 않는다 (docs/infra.md 25.1245, 미국 시세 감사 재현)."""

from __future__ import annotations

from batch.core import db
from batch.jobs import valuation_bands as job
from batch.services import signals as sg
from tests.test_portfolio_job import MemClient
from tests.test_signals import long_input


def test_대기열의_분할만_고른다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sid in (1, 2, 3):
        mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                         " VALUES (?, ?, 'NYSE', 'US', 'USD', 't', 't')", [sid, f"T{sid}"])  # fmt: skip
    q = ("INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after, done_at)"
         " VALUES (?, 't', '2026-10-05', ?, ?, ?)")  # fmt: skip
    mem.conn.execute(q, [1, 0.98, 0.98, None])  # 분할 — 두 비가 같다
    mem.conn.execute(q, [2, 0.98, 0.97, None])  # 배당 어긋남
    mem.conn.execute(q, [3, 0.98, 0.98, "2026-10-10"])  # 이미 재수집함
    assert db.split_pending_us(mem) == {1}  # type: ignore[arg-type]


def test_저장_밴드는_까닭만_남긴다() -> None:
    prices = {1: [(f"2026-0{m}-1{d}", 100.0) for m in range(1, 10) for d in range(0, 10)]}
    row = job.build_rows([{"stock_id": 1, "listed_shares": 10, "sector": None, "currency": "USD"}], prices,
                         {1: [("2025-12-31", 1000.0)]}, "US", "2026-10-09", "t", {1})[0]  # fmt: skip
    assert row[9] is None and job.SPLIT_PENDING_REASON in row


def test_판정표가_분할_대기를_말한다() -> None:
    행 = [r for r in sg.judgement("long", long_input(band=None, pbr_now=None, band_split_pending=True), {})
         if r["label"] == "밸류에이션 밴드"]  # fmt: skip
    assert 행 and "분할 재수집 대기" in 행[0]["display"]


def test_신호_작업이_밴드를_만들지_않는다() -> None:
    from pathlib import Path

    from batch.jobs import signals as job_sig

    글 = Path(job_sig.__file__).read_text(encoding="utf-8")
    assert "db.split_pending_us(client) if country == \"US\"" in 글 and 'row["_band_split_pending"] = True' in 글
