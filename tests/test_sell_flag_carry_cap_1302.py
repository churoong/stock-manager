"""판정 못 한 날 플래그를 잇는 기간에 끝이 있다 — 뉴스가 끊긴 뒤 감성급락이 몇 달 남지 않는다 (docs/infra.md 25.1302)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.core import db
from batch.jobs import portfolio as pf_job
from batch.jobs import sell_flags as job
from tests.test_sell_flag_unjudged_1277 import _C


def test_뉴스가_끊기면_며칠_뒤_풀린다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _C()
    monkeypatch.setattr(db, "apply_migrations", lambda client: None)
    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(pf_job, "쓰는_중", lambda cl: False)
    k = c.conn
    k.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')")  # fmt: skip
    k.execute("INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
              " horizon, price_date, close, market_value, updated_at)"
              " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-07-01', 'long', '2026-10-12', 100, 1000, 't')")  # fmt: skip
    k.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
              " horizon, created_at, updated_at) VALUES (1, 'buy', '2026-07-01', 100, 10, 'KRW', 1, 'none', 'long', 't', 't')")  # fmt: skip
    k.execute("INSERT INTO news (stock_id, title, url, published_at, fetched_at, source, lang)"
              " VALUES (1, 't', 'u', '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00', 'yna', 'ko')")  # fmt: skip

    def 감성(as_of: str, s: float | None, d7: float | None) -> None:
        k.execute("INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count,"
                  " negative_count, negative_count_7d, decay_halflife_days, delta_7d, delta_30d, method, calc_version,"
                  " created_at) VALUES (1, ?, ?, 12, 1, 8, 6, 7, ?, NULL, 'korfinasc', 1, 't')", [as_of, s, d7])  # fmt: skip

    감성("2026-10-12", -55.0, -48.0)
    job.run(date(2026, 10, 13))  # 판정해서 걸림
    활성 = lambda: k.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall()  # noqa: E731
    assert 활성() == [("감성급락",)]
    for n in range(1, 12):  # 뉴스가 끊겨 감성 NULL — 날마다 판정 못 함
        d = date(2026, 10, 13) + timedelta(days=n)
        감성((d - timedelta(days=1)).isoformat(), None, None)
        job.run(d)
        if n <= job.MAX_CARRY_DAYS:
            assert 활성() == [("감성급락",)], d
        else:
            assert 활성() == [], d
    assert k.execute("SELECT COUNT(*) FROM sell_flags WHERE resolved_at IS NOT NULL").fetchone()[0] >= 1
