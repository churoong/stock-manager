"""대량 적재 테스트.

행마다 INSERT 를 하나씩 보내면 네트워크 왕복이 행 수만큼 늘어난다.
실제로 백필이 30분 제한에 걸려 중단됐다. 여러 행을 한 문장에 넣어
왕복을 줄였고, 그 구조가 유지되는지 여기서 확인한다.
"""

from __future__ import annotations

import pytest

from batch.core import db


class FakeClient:
    """client.batch 호출을 기록만 한다. 네트워크를 타지 않는다."""

    def __init__(self) -> None:
        self.calls: list[list[tuple[str, list]]] = []

    def batch(self, statements):
        self.calls.append(statements)
        return []


def make_rows(n: int) -> list[tuple]:
    """열 개수를 db 에서 가져온다.

    예전에는 값 열두 개를 손으로 적어 두었다. 2026-09-17 에 change_pct 열이 늘자
    이 파일의 테스트 여섯 개가 한꺼번에 깨졌다. 열 이름으로 자리를 찾으면
    열이 늘거나 순서가 바뀌어도 테스트는 그대로다.
    """
    width = db.column_count_of_prices()
    at = db.price_column_index
    rows: list[tuple] = []
    for i in range(n):
        row: list = [None] * width
        row[at("stock_id")] = i
        row[at("date")] = "2026-09-15"
        row[at("open")] = 1.0
        row[at("high")] = 2.0
        row[at("low")] = 0.5
        row[at("close")] = 1.5
        row[at("volume")] = 100
        row[at("value")] = 1000
        row[at("currency")] = "KRW"
        row[at("source")] = "krx_openapi"
        row[at("fetched_at")] = "지금"
        rows.append(tuple(row))
    return rows


class Test왕복줄이기:
    def test_한_요청으로_보낸다(self) -> None:
        client = FakeClient()
        db.bulk_upsert_prices(client, make_rows(2700))

        # 2,700행을 200개씩 나눠 보내면 14번 왕복한다. 한 번이어야 한다
        assert len(client.calls) == 1

    def test_행_수에_맞게_문장을_나눈다(self) -> None:
        client = FakeClient()
        db.bulk_upsert_prices(client, make_rows(1000), chunk_rows=400)

        statements = client.calls[0]
        assert len(statements) == 3  # 400 + 400 + 200

    def test_바인딩_변수가_상한을_넘지_않는다(self) -> None:
        # SQLite 의 변수 상한은 32,766 이다. 넘으면 그 문장이 통째로 실패한다
        client = FakeClient()
        db.bulk_upsert_prices(client, make_rows(5000))

        for _sql, args in client.calls[0]:
            assert len(args) <= 32_766

    def test_아주_많으면_요청을_나눈다(self) -> None:
        # 미국 5년 백필은 한 조각만 26만 행이다. 한 요청이면 Turso 60초 제한에 걸린다
        client = FakeClient()
        db.bulk_upsert_prices(client, make_rows(25_000))
        assert len(client.calls) == 3  # 10,000 + 10,000 + 5,000
        assert sum(len(args) for call in client.calls for _sql, args in call) == 25_000 * db.column_count_of_prices()

    def test_기본_묶음_크기가_상한_안이다(self) -> None:
        assert db.BULK_ROWS_PER_STATEMENT * db.column_count_of_prices() < 32_766


class Test정확성:
    def test_모든_행의_값이_순서대로_들어간다(self) -> None:
        client = FakeClient()
        rows = make_rows(3)
        db.bulk_upsert_prices(client, rows)

        _sql, args = client.calls[0][0]
        # 열 개수를 숫자로 적지 않는다. 열이 끼어들면 틀린다
        n = db.column_count_of_prices()
        assert len(args) == 3 * n
        assert args[:n] == list(rows[0])
        assert args[n : 2 * n] == list(rows[1])

    def test_저장한_행_수를_돌려준다(self) -> None:
        client = FakeClient()
        assert db.bulk_upsert_prices(client, make_rows(753)) == 753

    def test_빈_목록이면_아무것도_보내지_않는다(self) -> None:
        client = FakeClient()

        assert db.bulk_upsert_prices(client, []) == 0
        assert client.calls == []

    def test_같은_종목과_거래일이면_덮어쓴다(self) -> None:
        client = FakeClient()
        db.bulk_upsert_prices(client, make_rows(2))

        sql, _args = client.calls[0][0]
        assert "ON CONFLICT (stock_id, date) DO UPDATE" in sql

    def test_열_개수가_다르면_바로_실패한다(self) -> None:
        # 조용히 어긋난 값을 넣느니 실패하는 편이 낫다
        client = FakeClient()
        with pytest.raises(ValueError, match="열 개수가 맞지 않습니다"):
            db.bulk_upsert_prices(client, [(1, "2026-09-15", 1.0)])


class Test쓰기예산:
    """Turso 월 쓰기 한도를 세는 장치 (docs/infra.md 23절).

    2026-09-17 에 한도를 넘겨 운영 DB 쓰기가 통째로 막혔다. 일일 리포트까지 멈춘다.
    넘고 나서 아는 것과 넘기 전에 아는 것은 다르다.
    """

    def _client(self):
        import sqlite3
        from typing import Any

        from batch.core.turso import ResultSet

        class Mem:
            def __init__(self) -> None:
                self.conn = sqlite3.connect(":memory:")

            def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
                cur = self.conn.execute(sql, args or [])
                cols = [d[0] for d in cur.description or []]
                return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

            def batch(self, statements):
                return [self.execute(sql, args) for sql, args in statements]

            def close(self) -> None:
                pass

        mem = Mem()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        # `make_rows` 는 종목 번호 0..n-1 을 쓴다. 외래키가 켜져 있으므로(conftest, 25.226) 그 종목들이 있어야 한다
        mem.conn.executemany(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
            [(i, f"T{i}") for i in range(10)],
        )
        return mem

    def test_쓴_행_수를_달마다_센다(self) -> None:
        client = self._client()
        db.record_rows_written(client, 5)  # type: ignore[arg-type]
        row = client.conn.execute(
            "SELECT call_count, limit_value, window_type FROM api_usage WHERE api_name = 'turso_writes'"
        ).fetchone()
        assert row[0] == 5
        assert row[1] == db.TURSO_MONTHLY_WRITE_LIMIT
        assert row[2] == "month"  # 한도가 월 단위다

    def test_남은_예산이_줄어든다(self) -> None:
        client = self._client()
        before = db.remaining_write_budget(client)  # type: ignore[arg-type]
        assert before == db.TURSO_MONTHLY_WRITE_LIMIT
        db.record_rows_written(client, 7)  # type: ignore[arg-type]
        assert db.remaining_write_budget(client) == before - 7  # type: ignore[arg-type]

    def test_카운터가_실패해도_저장은_계속된다(self) -> None:
        # 카운터 때문에 본 작업이 멈추면 안 된다
        client = self._client()
        client.conn.execute("DROP TABLE api_usage")
        assert db.bulk_upsert_prices(client, make_rows(3)) == 3  # type: ignore[arg-type]
        assert db.record_rows_written(client, 3) is None  # type: ignore[arg-type]

    def test_적재_함수는_스스로_세지_않는다(self) -> None:
        """세는 곳은 TursoClient 하나다 (core/turso.WriteCounter). 두 곳에서 세면 두 배가 된다."""
        client = self._client()
        db.bulk_upsert_prices(client, make_rows(5))  # type: ignore[arg-type]
        assert client.conn.execute("SELECT COUNT(*) FROM api_usage WHERE api_name = 'turso_writes'").fetchone()[0] == 0


class Test종가_0은_넣지_않는다:
    """**가격이 아니라 "값 없음" 이다** (docs/infra.md 25.203)."""

    @staticmethod
    def _종가(rows: list[tuple], i: int, 값: float | None) -> list[tuple]:
        at = db.price_column_index("close")
        row = list(rows[i])
        row[at] = 값
        rows[i] = tuple(row)
        return rows

    def test_0_과_음수는_빠지고_나머지는_들어간다(self) -> None:
        client = FakeClient()
        rows = self._종가(self._종가(make_rows(5), 1, 0.0), 3, -1.0)

        stored = db.bulk_upsert_prices(client, rows)

        assert stored == 3
        (_sql, args), = client.calls[0]
        assert len(args) == 3 * db.column_count_of_prices()

    def test_전부_0_이면_아무것도_보내지_않는다(self) -> None:
        client = FakeClient()
        rows = self._종가(make_rows(1), 0, 0.0)

        assert db.bulk_upsert_prices(client, rows) == 0
        assert client.calls == []

    def test_미끼__멀쩡한_행은_모두_들어간다(self) -> None:
        client = FakeClient()
        assert db.bulk_upsert_prices(client, make_rows(5)) == 5


def test_다시_받아도_계산해_둔_국내_수정주가를_지우지_않는다() -> None:
    """docs/infra.md 25.250 — `backfill_kr --refresh` 가 분할 구간의 adj_close 를 NULL 로 덮었다."""
    import sqlite3
    from pathlib import Path

    from batch.core import db as _db
    from batch.core.turso import ResultSet

    conn = sqlite3.connect(":memory:")
    for path in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                 " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip

    class C:
        def execute(self, sql, args=None):
            cur = conn.execute(sql, args or [])
            return ResultSet(columns=[], rows=cur.fetchall(), last_insert_rowid=cur.lastrowid)

        def batch(self, statements):
            return [self.execute(sql, args) for sql, args in statements]

    열 = [c.strip() for c in _db.PRICE_COLUMNS.split(",")]

    def 행(날: str, 종가: float, 수정: float | None) -> tuple:
        값 = {"stock_id": 1, "date": 날, "close": 종가, "adj_close": 수정, "currency": "KRW",
             "source": "krx", "fetched_at": "t"}  # fmt: skip
        return tuple(값.get(c) for c in 열)

    _db.bulk_upsert_prices(C(), [행("2026-01-02", 1000.0, None), 행("2026-01-05", 1000.0, None)])
    conn.execute("UPDATE prices SET adj_close = 200.0")  # adjust_kr 가 채운 값
    _db.bulk_upsert_prices(C(), [행("2026-01-02", 1000.0, None), 행("2026-01-05", 990.0, None)])

    수정 = dict(conn.execute("SELECT date, adj_close FROM prices").fetchall())
    assert 수정["2026-01-02"] == 200.0  # 종가 그대로 → 지키고
    # 종가 정정 → 비우지 않고 같은 계수(200/1000)로 옮긴다 (25.705) — 비우면 수정 계열 한가운데 원 종가가 낀다
    assert 수정["2026-01-05"] == pytest.approx(198.0)

    _db.bulk_upsert_prices(C(), [행("2026-01-02", 1000.0, 333.0)])  # 새 값이 오면(미국) 그것
    assert conn.execute("SELECT adj_close FROM prices WHERE date = '2026-01-02'").fetchone()[0] == 333.0
