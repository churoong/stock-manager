"""결정 원장 성과 — 창 끝날 지수가 빠지면 기한까지 기다린다 (docs/paper.md 9장, docs/infra.md 25.1579)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.jobs import paper as job
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client


def test_창_끝날_지수가_빠지면_기한까지_기다린다() -> None:
    """20번째 날 지수만 빠지면 그 전날 지수로 채워 COALESCE 때문에 영구히 남았다 (25.1579, 위원회 감사 [참고])."""
    m = Client()
    m.conn.execute(
        "INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
        " created_at) VALUES ('B', 'KR', '2026-09-01', 1, 'hold_flag', '감성급락', 10000, '2026-09-01', 't')"
    )
    날들 = [d for d in (date(2026, 9, 1) + timedelta(days=i) for i in range(1, 50)) if d.weekday() < 5][:30]
    지수 = "INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, ?, 't', 't')"
    m.conn.execute(지수, ["2026-09-01", 2000])
    for i, d in enumerate(날들, 1):
        m.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                       " VALUES (1, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [d.isoformat()] + [10_000 + i * 100] * 4)  # fmt: skip
        if i != 20:
            m.conn.execute(지수, [d.isoformat(), 2_000 + i * 10])
    스무째 = 날들[19]
    job.fill_outcomes(m, "KR", (스무째 + timedelta(days=2)).isoformat(), "t")
    r5, r20, b20 = m.q("SELECT ret_5d, ret_20d, bench_20d FROM paper_decisions")[0]
    assert r5 is not None and r20 is None and b20 is None  # 5 일은 채우고 20 일은 기다린다
    m.conn.execute(지수, [스무째.isoformat(), 2_200])  # 다음 배치가 받았다
    job.fill_outcomes(m, "KR", (스무째 + timedelta(days=3)).isoformat(), "t")
    assert m.q("SELECT bench_20d FROM paper_decisions")[0][0] == pytest.approx(10.0)
    # 끝내 안 오면 기한 뒤 묵은 값(19번째 날 2,190)으로 채운다
    m.conn.execute("UPDATE paper_decisions SET ret_20d = NULL, bench_20d = NULL")
    m.conn.execute("DELETE FROM index_prices WHERE date = ?", [스무째.isoformat()])
    job.fill_outcomes(m, "KR", (스무째 + timedelta(days=pp.BENCH_WAIT_DAYS)).isoformat(), "t")
    assert m.q("SELECT bench_20d FROM paper_decisions")[0][0] == pytest.approx(9.5)


def test_60일_창을_기다리는_날은_포기로_닫지_않는다() -> None:
    """정지가 길어 60번째 거래일이 200일 뒤에야 온 종목 — 그날 지수를 기다리는 동안 "거래일뿐" 으로 닫으면 영영 못 채운다."""
    m = Client()
    m.conn.execute(
        "INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
        " created_at) VALUES ('B', 'KR', '2026-01-02', 1, 'hold_flag', 't', 10000, '2026-01-02', 't')"
    )
    지수 = "INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, 2000, 't', 't')"
    m.conn.execute(지수, ["2026-01-02"])
    날들 = [d for d in (date(2026, 1, 2) + timedelta(days=i) for i in range(1, 120)) if d.weekday() < 5][:59]
    마지막 = date(2026, 8, 3)  # 정지 풀린 뒤 60번째
    for d in [*날들, 마지막]:
        m.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                       " VALUES (1, ?, 1, 1, 1, 10000, 1000, 'KRW', 't', 't')", [d.isoformat()])  # fmt: skip
        if d != 마지막:
            m.conn.execute(지수, [d.isoformat()])
    기록 = job.fill_outcomes(m, "KR", (마지막 + timedelta(days=2)).isoformat(), "t")
    assert 기록["gave_up"] == 0
    assert m.q("SELECT ret_60d, outcome_note FROM paper_decisions")[0] == (None, None)
