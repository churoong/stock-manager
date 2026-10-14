"""2부 보유 점수 줄이 매수일 잣대를 **매매 점수를 낸 판**으로 고른다 — 매도 플래그(25.1094)와 같은 규칙
(docs/infra.md 25.1325, 매도 플래그 감사 #2).

매수일(08-31)이 뒤에 새 판(10)으로 다시 계산돼 그날 판이 둘이다. 매매에 적힌 70점은 옛 판(8) 값이다.
예전: 2부는 그날 가장 높은 판(10)을 읽어 "같은 잣대" 로 보고 "70 → 60, −10점" 을 냈고, 매도 플래그는 다른 잣대로 봤다.
"""

from __future__ import annotations

from batch.core import db
from batch.jobs import daily
from batch.jobs import sell_flags as job
from batch.services import holding_scores as hs
from tests.test_portfolio_job import MemClient


def _읽기() -> hs.HoldingScore:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')")  # fmt: skip
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-09-01', 'long', '2026-09-16', 100, 1000, 't')"
    )
    c.execute(
        "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " snapshot_as_of, score_at_trade, created_at, updated_at)"
        " VALUES (1, 'buy', '2026-09-01', 100, 10, 'KRW', 1, 'none', '2026-08-31', 70, 't', 't')"
    )
    for day, total, 판 in (("2026-08-31", 70, 8), ("2026-08-31", 75, 10), ("2026-09-15", 60, 10)):
        c.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
            " weights_json, calc_version, created_at) VALUES (1, ?, ?, '{}', 0, '{\"value\": 50}', ?, 't')",
            [day, total, 판],
        )
    경고: list[str] = []
    [x] = daily._holding_scores(mem, "2026-09-16", 경고)  # type: ignore[arg-type]
    assert 경고 == []
    return x


def test_매수일이_새_판으로_다시_계산돼도_매매_점수의_판과_견준다() -> None:
    x = _읽기()
    assert not x.same_yardstick and "→" not in hs.line(x)


def test_매수일_판_고르기() -> None:
    rows = [{"calc_version": 10, "total_score": 75.0}, {"calc_version": 8, "total_score": 70.0}]
    assert job.매수일_판(rows, 70.0) == [rows[1]]
    assert job.매수일_판(rows, 71.0) == [rows[0]]  # 맞는 판이 없으면 가장 높은 판
    assert job.매수일_판(rows, None) == [rows[0]]
