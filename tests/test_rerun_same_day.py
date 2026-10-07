"""같은 기준일로 점수·신호를 다시 계산했을 때 옛 값이 남지 않는지 (docs/infra.md 17절).

실제 마이그레이션을 적용한 메모리 SQLite 에 적재 문장을 그대로 돌린다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from batch.jobs import scores, signals

ROOT = Path(__file__).resolve().parent.parent


class SqliteClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.conn.executescript(path.read_text(encoding="utf-8"))
        for stock_id, country in ((1, "KR"), (2, "KR"), (3, "KR"), (9, "US")):
            self.conn.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'M', ?, 'KRW', 'active', 't', 't')",
                [stock_id, f"T{stock_id}", country],
            )

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list:
        for sql, args in statements:
            self.conn.execute(sql, args)
        return []


def factor_row(stock_id: int, factor: str, score: float | None, as_of: str = "2026-09-16") -> tuple:
    return (stock_id, as_of, factor, "{}", None, score, "market:M", 10, "[]", 1, "t")


def signal_row(stock_id: int, horizon: str, as_of: str = "2026-09-16") -> tuple:
    return (
        stock_id, as_of, horizon, "x", 1.0, 2.0, "KRW", "[]", None, None, 5.0, None, 1.0, 0, None,
        "문장", "{}", 1, "t",
    )  # fmt: skip


class Test점수:
    def test_같은_날_다시_계산하면_덮어쓴다(self) -> None:
        client = SqliteClient()
        cols = scores._FACTOR_COLS
        keys = "stock_id, as_of_date, factor, calc_version"
        scores._bulk(client, "factors", cols, keys, [factor_row(1, "risk", None)])
        scores._bulk(client, "factors", cols, keys, [factor_row(1, "risk", 71.5)])
        got = client.conn.execute("SELECT score FROM factors WHERE stock_id = 1").fetchall()
        assert got == [(71.5,)]

    def test_유니버스에서_빠진_종목의_옛_행은_지운다_다른_나라_다른_날은_둔다(self) -> None:
        client = SqliteClient()
        cols = scores._FACTOR_COLS
        keys = "stock_id, as_of_date, factor, calc_version"
        rows = [factor_row(1, "risk", 1), factor_row(2, "risk", 2), factor_row(9, "risk", 9),
                factor_row(2, "risk", 2, as_of="2026-09-15")]  # fmt: skip
        scores._bulk(client, "factors", cols, keys, rows)
        # 다시 계산했더니 1번만 남았다: 넣기 전에 그 나라·그 날을 지우고 새로 넣는다
        client.batch([scores.clear_statement("factors", "KR", "2026-09-16", 1)])
        scores._bulk(client, "factors", cols, keys, [factor_row(1, "risk", 1)])
        left = client.conn.execute("SELECT stock_id, as_of_date FROM factors ORDER BY 1, 2").fetchall()
        assert left == [(1, "2026-09-16"), (2, "2026-09-15"), (9, "2026-09-16")]

    def test_지우는_문장은_종목을_나열하지_않는다(self) -> None:
        """D1 은 질의당 파라미터 100개다. 종목 수만큼 붙이면 막힌다 (docs/infra.md 25.5)."""
        sql, args = scores.clear_statement("scores", "KR", "2026-09-16", 3)
        assert len(args) == 3 and sql.count("?") == 3


class Test신호:
    def test_조건이_풀린_옛_신호는_지운다(self) -> None:
        client = SqliteClient()
        signals.store(
            client, [signal_row(1, "long"), signal_row(1, "mid"), signal_row(2, "short"), signal_row(9, "long")]
        )
        # 다시 계산했더니 1번의 중기만 남았다: 지우고 새로 넣는다
        client.batch([signals.clear_statement("KR", "2026-09-16")])
        signals.store(client, [signal_row(1, "mid")])
        left = client.conn.execute("SELECT stock_id, horizon FROM signals ORDER BY 1, 2").fetchall()
        assert left == [(1, "mid"), (9, "long")]

    def test_신호가_하나도_없으면_그_나라_그_날_신호를_모두_지운다(self) -> None:
        client = SqliteClient()
        signals.store(client, [signal_row(1, "long"), signal_row(9, "long")])
        client.batch([signals.clear_statement("KR", "2026-09-16")])
        left = client.conn.execute("SELECT stock_id FROM signals").fetchall()
        assert left == [(9,)]
