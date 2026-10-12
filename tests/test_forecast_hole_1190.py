"""예측 성적표 기준 종가가 국내 수정주가 **가운데 빈 행**이면 원 종가(10배)가 기준이 되던 것 (docs/infra.md 25.1190)."""

from __future__ import annotations

import json

from batch.core import db
from batch.jobs import verdicts as job
from tests.test_portfolio_job import MemClient

# 1:10 분할(10-01) 전 구간: 원 종가 1000, 수정주가 100. 09-03 은 수정주가가 빈 행(구멍)
행 = [("2026-09-01", 1000, 100), ("2026-09-02", 1000, 100), ("2026-09-03", 1000, None),
     ("2026-09-04", 1000, 100), ("2026-09-07", 1000, 100), ("2026-10-02", 100, 100)]  # fmt: skip


def _mem() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'x', 'KRW', 'active', 't', 't')")  # fmt: skip
    for d, close, a in 행:
        c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                  " VALUES (1, ?, ?, ?, 'KRW', 't', 't')", [d, close, a])  # fmt: skip
    c.execute("INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
              " VALUES ('2026-09-03', 1, 'KR', 1000, ?, 't')",
              [json.dumps({"capm": {"1": {"exp": 0.0, "lo68": -0.05, "hi68": 0.05}}})])  # fmt: skip
    return mem


def test_기준일이_빈_행이면_앞뒤_계수로_메워_채점한다() -> None:
    prior: dict = {}
    market: dict = {}
    경고: list[str] = []
    못함 = job.evaluate_date(_mem(), "KR", "2026-09-03", 1, {1: {"close": 100.0}}, prior, market, set(), 경고)  # type: ignore[arg-type]
    assert 못함 == [] and not 경고
    # 기준 100 → 오늘 100 이면 수익 0 — 68% 구간 안. 원 종가 1000 을 기준으로 쓰면 −90% 로 구간 밖이었다
    비율 = job.ft.rates(market["capm"]["1"])
    assert 비율 is not None and 비율["n"] == 1 and 비율["in68"] == 1.0, 비율


def test_빈_행이_없으면_더_읽지_않는다() -> None:
    mem = _mem()
    mem.conn.execute("UPDATE prices SET adj_close = 100 WHERE date = '2026-09-03'")
    읽은것: list[str] = []
    원래 = mem.execute

    def 세기(sql, args=None):
        읽은것.append(sql)
        return 원래(sql, args)

    mem.execute = 세기  # type: ignore[method-assign]
    job.evaluate_date(mem, "KR", "2026-09-03", 1, {1: {"close": 100.0}}, {}, {}, set(), [])  # type: ignore[arg-type]
    assert job.HOLE_SQL not in 읽은것
