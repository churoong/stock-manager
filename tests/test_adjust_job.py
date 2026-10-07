"""국내 수정주가 배치를 실제 스키마에 끝까지 돌린다. 네트워크를 타지 않는다.

계산은 tests/test_adjust.py 가 검증한다. 여기서는 배치가
  - 등락률이 있는 종목만 조정하고
  - 원본(close)을 건드리지 않고
  - 두 번 돌려도 같은 결과를 내는지
만 본다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import adjust_kr as job


class MemClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '분할하는회사', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (2, '000660', 'KOSPI', 'KR', '옛기록만있는회사', 'KRW', 'active', 't', 't')"
    )

    def price(stock_id: int, date: str, close: float, change_pct: float | None) -> None:
        c.execute(
            "INSERT INTO prices (stock_id, date, close, change_pct, currency, source, fetched_at)"
            " VALUES (?, ?, ?, ?, 'KRW', 'krx_openapi', 't')",
            [stock_id, date, close, change_pct],
        )

    # 1번: 1:10 분할이 있다
    price(1, "2026-01-02", 10_000, 0.0)
    price(1, "2026-01-05", 10_000, 0.0)
    price(1, "2026-01-06", 980, -2.0)
    price(1, "2026-01-07", 1_000, 2.04)
    # 2번: 등락률을 받기 전에 저장된 구간
    price(2, "2026-01-02", 5_000, None)
    price(2, "2026-01-05", 500, None)
    return mem


def test_분할_이전_가격이_조정된다(client: MemClient) -> None:
    assert job.run() == 0
    # 읽는 쪽과 같은 식으로 본다. 종가와 같은 날은 비워 두므로 COALESCE 로 읽어야 한다
    rows = dict(
        client.conn.execute(
            "SELECT date, COALESCE(adj_close, close) FROM prices WHERE stock_id = 1"
        ).fetchall()
    )
    assert rows["2026-01-02"] == pytest.approx(1_000, rel=1e-6)
    assert rows["2026-01-06"] == pytest.approx(980)


def test_종가와_같은_날은_쓰지_않는다(client: MemClient) -> None:
    """모든 행에 UPDATE 를 쓰던 것이 D1 하루 한도를 넘긴 원인이었다 (docs/infra.md 25.7)."""
    job.run()
    stored = dict(client.conn.execute("SELECT date, adj_close FROM prices WHERE stock_id = 1").fetchall())
    assert stored["2026-01-06"] is None and stored["2026-01-07"] is None  # 분할 뒤: 종가 그대로
    assert stored["2026-01-02"] is not None  # 분할 전: 조정값이 있다


def test_다시_돌리면_한_행도_쓰지_않는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    job.run()
    writes: list[str] = []
    original = client.batch

    def spy(statements):
        writes.extend(sql for sql, _ in statements if sql.startswith("UPDATE prices"))
        return original(statements)

    monkeypatch.setattr(client, "batch", spy)
    job.run()
    assert writes == []


def test_바뀌는_행만_고른다() -> None:
    from batch.services import adjust as adj

    days = [adj.Day("d1", 100.0, 0.0), adj.Day("d2", 50.0, 0.0)]
    adjusted = [adj.Adjusted("d1", 50.0, 1.0), adj.Adjusted("d2", 50.0, 1.0)]
    # d1 은 새로 조정됨, d2 는 종가와 같아 비워야 하는데 예전 값이 남아 있다
    assert job.rows_to_write(days, adjusted, {"d1": None, "d2": 49.0}) == [("d1", 50.0), ("d2", None)]
    # 이미 같은 값이면 쓰지 않는다
    assert job.rows_to_write(days, adjusted, {"d1": 50.0, "d2": None}) == []


def test_원본_종가는_그대로다(client: MemClient) -> None:
    job.run()
    closes = dict(client.conn.execute("SELECT date, close FROM prices WHERE stock_id = 1").fetchall())
    assert closes["2026-01-02"] == 10_000  # 한국거래소가 준 미조정 값 그대로
    assert closes["2026-01-06"] == 980


def test_등락률이_없으면_비워_둔다(client: MemClient) -> None:
    job.run()
    rows = client.conn.execute("SELECT adj_close FROM prices WHERE stock_id = 2").fetchall()
    # close 를 그대로 넣으면 "조정된 값" 으로 오해된다. 모르면 비운다
    assert all(r[0] is None for r in rows)


def test_다시_돌려도_같다(client: MemClient) -> None:
    job.run()
    first = client.conn.execute("SELECT date, adj_close FROM prices ORDER BY stock_id, date").fetchall()
    job.run()
    second = client.conn.execute("SELECT date, adj_close FROM prices ORDER BY stock_id, date").fetchall()
    assert first == second


def test_실행_기록에_무엇을_고쳤는지_남는다(client: MemClient) -> None:
    job.run()
    log = client.conn.execute(
        "SELECT step_log FROM batch_runs WHERE job_name = 'adjust_kr' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert "stocks_with_action" in log
    assert "2026-01-06" in log  # 어느 날을 고쳤는지 표본이 남는다
    assert "stocks_without_change_pct" in log
