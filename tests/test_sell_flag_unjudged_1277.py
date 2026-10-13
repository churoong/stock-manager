"""판정할 자료가 없던 날은 플래그를 풀지 않는다 — 확인한 플래그가 NEW 로 재발하지 않는다 (docs/infra.md 25.1277, 매도 플래그 감사 재현)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.core import db
from batch.jobs import portfolio as pf_job
from batch.jobs import sell_flags as job
from tests.test_report_picks import SqliteClient


class _C(SqliteClient):
    def close(self) -> None:
        pass


def test_감성_채점이_하루_빠져도_확인이_이어진다(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-12T23:27:00+00:00")
    job.run(date(2026, 10, 13))
    k.execute("UPDATE sell_flags SET dismissed_at = '2026-10-13T01:00:00+00:00' WHERE as_of_date = '2026-10-13'")
    감성("2026-10-13", None, None)  # 국내 채점 실패 — 그날 감성·변화 NULL
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-13T23:27:00+00:00")
    job.run(date(2026, 10, 14))
    assert k.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall() == [("감성급락",)]
    assert k.execute("SELECT COUNT(*) FROM sell_flags WHERE resolved_at IS NOT NULL").fetchone()[0] == 0
    감성("2026-10-14", -54.0, -47.0)
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-14T23:27:00+00:00")
    job.run(date(2026, 10, 15))
    (첫날, 확인, 만든), = k.execute(
        "SELECT first_seen_date, dismissed_at, created_at FROM sell_flags WHERE is_active = 1").fetchall()
    assert (첫날, 확인, 만든) == ("2026-10-13", "2026-10-13T01:00:00+00:00", "2026-10-12T23:27:00+00:00")
    rows = job.active_flags_for_report(c, "KR", "2026-10-15", "2026-10-13T23:31:00+00:00")
    assert not any(r["new"] for r in rows)


def test_판정했더니_아니면_풀린다() -> None:
    """자료가 있는 날 조건이 풀리면 예전처럼 풀린다 — 잇기는 '판정 못 함' 에만."""
    from batch.services import sell_flags as sf

    기본 = dict(stock_id=1, name="에이", horizon="long", first_buy_date="2026-07-01", cost=1000.0, market_value=1000.0,
              price_date="2026-10-14")  # fmt: skip
    필드 = sf.HoldingInput.__dataclass_fields__
    h = sf.HoldingInput(**{k: v for k, v in 기본.items() if k in 필드},
                        **{k: None for k in 필드 if k not in 기본 and 필드[k].default is 필드[k].default_factory})  # type: ignore[arg-type]  # fmt: skip
    assert "손절" not in sf.unjudged(h, date(2026, 10, 15))
