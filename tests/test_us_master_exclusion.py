"""미국 마스터에서 보통주가 아닌 종목을 빼는 테스트.

필터를 붙였는데도 이미 들어간 우선주가 active 로 남아 매 거래일 야후에
헛되이 물어본 일이 있었다(2026-09-17). 문자열만 보면 맞아 보여도 SQL 이
실제로 행을 바꾸는지는 돌려 봐야 안다. 그래서 실제 마이그레이션을 적용한
메모리 SQLite 에 문장을 그대로 실행한다. 네트워크를 타지 않는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from batch.jobs import universe as job
from batch.sources.nasdaq_symbols import UsSymbol

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
NOW = "2026-09-17T00:00:00+00:00"


def sym(symbol: str, name: str = "Acme Corp Common Stock") -> UsSymbol:
    return UsSymbol(symbol=symbol, name=name, exchange="NYSE", is_etf=False, is_test=False)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def insert(conn: sqlite3.Connection, ticker: str, status: str = "active") -> None:
    conn.execute(
        "INSERT INTO stocks (ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (?, 'NYSE', 'US', 'USD', ?, 'test', ?)",
        [ticker, status, NOW],
    )


def status_of(conn: sqlite3.Connection, ticker: str) -> str:
    return conn.execute("SELECT status FROM stocks WHERE ticker = ?", [ticker]).fetchone()[0]


def run(conn: sqlite3.Connection, statements) -> None:
    for sql, params in statements:
        conn.execute(sql, params)


class Test제외:
    def test_이미_들어간_우선주는_excluded_가_된다(self, conn) -> None:
        insert(conn, "AMH$G")
        run(conn, job.us_exclusion_statements([sym("AMH$G")], NOW))
        assert status_of(conn, "AMH$G") == "excluded"

    def test_보통주는_건드리지_않는다(self, conn) -> None:
        insert(conn, "BRK.B")
        statements = job.us_exclusion_statements([sym("BRK.B")], NOW)
        assert statements == []
        assert status_of(conn, "BRK.B") == "active"

    def test_상장폐지로_표시된_행은_바꾸지_않는다(self, conn) -> None:
        # delisted 는 백테스트가 생존편향 판단에 쓰는 값이다. 덮어쓰면 안 된다.
        insert(conn, "OLD$A", status="delisted")
        run(conn, job.us_exclusion_statements([sym("OLD$A")], NOW))
        assert status_of(conn, "OLD$A") == "delisted"

    def test_국내_종목은_건드리지_않는다(self, conn) -> None:
        conn.execute(
            "INSERT INTO stocks (ticker, market, country, currency, status, source, fetched_at)"
            " VALUES ('ABC-W', 'KOSPI', 'KR', 'KRW', 'active', 'test', ?)",
            [NOW],
        )
        run(conn, job.us_exclusion_statements([sym("ABC-W")], NOW))
        assert status_of(conn, "ABC-W") == "active"
