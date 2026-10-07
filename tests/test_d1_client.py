"""Cloudflare D1 어댑터 (docs/infra.md 25절). 네트워크를 타지 않는다.

Turso 와 **겉모습이 같아야** 부르는 쪽(작업 30여 개)이 바뀌지 않는다. 여기서 보는 것은
응답 모양 변환(객체 → 열·행), 카운터, 설정 누락 메시지다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from batch.core import client as switch
from batch.core import d1
from batch.core.turso import TursoError


class Test응답_변환:
    def test_객체_행을_열과_값으로_바꾼다(self) -> None:
        rs = d1._to_result_set({
            "success": True,
            "results": [{"id": 1, "name": "삼성전자"}, {"id": 2, "name": "SK하이닉스"}],
            "meta": {"rows_read": 2, "rows_written": 0, "last_row_id": 0},
        })  # fmt: skip
        assert rs.columns == ["id", "name"]
        assert rs.rows == [(1, "삼성전자"), (2, "SK하이닉스")]
        assert rs.rows_read == 2 and rs.affected_rows == 0
        assert rs.dicts()[1]["name"] == "SK하이닉스"

    def test_빈_결과도_읽은_행은_센다(self) -> None:
        rs = d1._to_result_set({"success": True, "results": [], "meta": {"rows_read": 1500}})
        assert rs.rows == [] and rs.columns == [] and rs.rows_read == 1500

    def test_쓰기_응답은_바뀐_행_수를_준다(self) -> None:
        rs = d1._to_result_set({"success": True, "results": [], "meta": {"rows_written": 40, "last_row_id": 7}})
        assert rs.affected_rows == 40 and rs.last_insert_rowid == 7


class Test값_변환:
    @pytest.mark.parametrize(("value", "expected"), [(True, 1), (False, 0), (3, 3), (1.5, 1.5), ("가", "가"), (None, None)])
    def test_json_값을_그대로_보낸다(self, value, expected) -> None:
        assert d1._encode(value) == expected

    def test_블롭은_거절한다(self) -> None:
        with pytest.raises(TursoError):
            d1._encode(b"\x00")


class Test설정:
    def test_없으면_무엇이_없는지_말한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("D1_ACCOUNT_ID", "D1_DATABASE_ID", "D1_API_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(TursoError) as exc:
            d1.D1Client()
        for key in ("D1_ACCOUNT_ID", "D1_DATABASE_ID", "D1_API_TOKEN"):
            assert key in str(exc.value)


class Test백엔드_전환:
    def test_기본은_turso(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DB_BACKEND", raising=False)
        assert switch.backend() == "turso"

    def test_설정하면_d1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "D1")
        assert switch.backend() == "d1"

    def test_d1_을_고르면_d1_클라이언트를_만든다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "d1")
        monkeypatch.setenv("D1_ACCOUNT_ID", "a")
        monkeypatch.setenv("D1_DATABASE_ID", "b")
        monkeypatch.setenv("D1_API_TOKEN", "c")
        made = switch.TursoClient()
        try:
            assert isinstance(made, d1.D1Client)
        finally:
            made._session.close()


class Test백업_되살리기:
    """docs/infra.md 25절. 표는 마이그레이션이 만들고, 백업에서는 **데이터만** 가져온다."""

    def _file(self, tmp_path, lines: list[str]):
        import gzip

        path = tmp_path / "backup.sql.gz"
        with gzip.open(path, "wt", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
        return path

    def test_DDL_과_트랜잭션_문은_버린다(self, tmp_path) -> None:
        import restore_backup as restore

        path = self._file(tmp_path, [
            "-- 주석",
            "PRAGMA foreign_keys = OFF;",
            "BEGIN;",
            "DROP TABLE IF EXISTS settings;",
            "CREATE TABLE settings (key TEXT);",
            "INSERT INTO settings (key, value) VALUES ('a', '1');",
            "INSERT INTO trades (id) VALUES (1);",
            "COMMIT;",
        ])  # fmt: skip
        rows = restore.statements(path)
        assert [t for t, _sql in rows] == ["settings", "trades"]
        assert rows[0][1].endswith("VALUES ('a', '1')")  # 끝의 세미콜론은 뗀다

    def test_표를_골라_넣을_수_있다(self, tmp_path, capsys) -> None:
        import restore_backup as restore

        path = self._file(tmp_path, [
            "INSERT INTO settings (key) VALUES ('a');",
            "INSERT INTO news (id) VALUES (1);",
        ])  # fmt: skip
        assert restore.run(path, only={"settings"}, max_rows=None, dry_run=True) == 0
        out = capsys.readouterr().out
        assert "settings" in out and "news" not in out


def test_with_구문을_쓸_수_있다(monkeypatch: pytest.MonkeyPatch) -> None:
    """작업들이 `with TursoClient() as client:` 를 쓴다. 겉모습이 같아야 한다 (첫 전환에서 걸렸다)."""
    monkeypatch.setenv("D1_ACCOUNT_ID", "a")
    monkeypatch.setenv("D1_DATABASE_ID", "b")
    monkeypatch.setenv("D1_API_TOKEN", "c")
    with d1.D1Client() as client:
        assert client.account_id == "a"


def test_겉모습이_turso_와_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """이름이 하나라도 어긋나면 전환한 날 그 작업만 죽는다 (with 구문이 실제로 그랬다)."""
    from batch.core.turso import TursoClient as Turso

    for name in ("execute", "batch", "close", "__enter__", "__exit__"):
        assert hasattr(Turso, name) and hasattr(d1.D1Client, name), name

    # 카운터는 인스턴스 속성이라 만들어 봐야 안다
    monkeypatch.setenv("D1_ACCOUNT_ID", "a")
    monkeypatch.setenv("D1_DATABASE_ID", "b")
    monkeypatch.setenv("D1_API_TOKEN", "c")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://x.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "t")
    with d1.D1Client() as made, Turso() as original:
        for name in ("writes", "reads"):
            assert hasattr(original, name) and hasattr(made, name), name


class Test충돌_처리:
    """되살릴 때 이미 있는 행 (docs/infra.md 25.3). 마이그레이션이 settings 기본값을 넣어 둔다."""

    def test_기본은_건너뛰기(self) -> None:
        import restore_backup as restore

        sql = "INSERT INTO settings (key, value) VALUES ('a', '1')"
        assert restore.with_conflict(sql, "skip").startswith("INSERT OR IGNORE INTO settings")

    def test_덮어쓰기도_고를_수_있다(self) -> None:
        import restore_backup as restore

        sql = "INSERT INTO settings (key, value) VALUES ('a', '1')"
        assert restore.with_conflict(sql, "replace").startswith("INSERT OR REPLACE INTO settings")

    def test_원본_뒷부분은_그대로(self) -> None:
        import restore_backup as restore

        sql = "INSERT INTO news (id, title) VALUES (1, 'x')"
        assert restore.with_conflict(sql, "skip").endswith("VALUES (1, 'x')")


class Test파라미터_나누기:
    """D1 은 질의당 파라미터 100개까지다 (docs/infra.md 25.5). 첫 전환에서 유니버스 적재가 막혔다."""

    def test_한도_안이면_그대로(self) -> None:
        out = d1.split_for_params("INSERT INTO t (a) VALUES (?)", [1])
        assert out == [("INSERT INTO t (a) VALUES (?)", [1])]

    def test_여러_행_INSERT_를_나눈다(self) -> None:
        sql = "INSERT INTO t (a, b) VALUES " + ", ".join(["(?, ?)"] * 100)
        args = list(range(200))
        out = d1.split_for_params(sql, args, max_params=100)
        assert len(out) == 2
        # 나눠도 값의 순서와 개수가 그대로여야 한다
        assert [a for _sql, chunk in out for a in chunk] == args
        assert all(chunk_sql.count("?") == len(chunk) for chunk_sql, chunk in out)
        assert all(len(chunk) <= 100 for _sql, chunk in out)

    def test_ON_CONFLICT_꼬리를_지킨다(self) -> None:
        sql = ("INSERT INTO t (a, b) VALUES " + ", ".join(["(?, ?)"] * 60)
               + " ON CONFLICT (a) DO UPDATE SET b = excluded.b")  # fmt: skip
        out = d1.split_for_params(sql, list(range(120)), max_params=100)
        assert len(out) == 2
        assert all(chunk_sql.endswith("ON CONFLICT (a) DO UPDATE SET b = excluded.b") for chunk_sql, _ in out)

    def test_나눌_수_없으면_조용히_반쪽만_돌지_않는다(self) -> None:
        """IN 목록이 길면 나눌 수 없다. 반쪽만 도는 것이 가장 나쁘다 — 알리고 멈춘다."""
        sql = "DELETE FROM t WHERE id NOT IN (" + ", ".join(["?"] * 200) + ")"
        with pytest.raises(TursoError) as exc:
            d1.split_for_params(sql, list(range(200)), max_params=100)
        assert "나눠 보내세요" in str(exc.value)


class Test_IN_묶음_크기:
    """IN 목록은 나눌 수 없어 부르는 쪽이 짧게 끊어야 한다 (docs/infra.md 25.5)."""

    def test_D1_이면_한도_안으로(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from batch.core import db

        monkeypatch.setenv("DB_BACKEND", "d1")
        assert db.in_chunk(reserve=1) + 1 <= d1.MAX_PARAMS

    def test_Turso_면_넉넉히(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from batch.core import db

        monkeypatch.delenv("DB_BACKEND", raising=False)
        assert db.in_chunk() > d1.MAX_PARAMS


class Test한도_알아보기:
    """한도는 고장이 아니다 (docs/infra.md 25.6). 알아보지 못하면 배치가 시작 기록을 쓰다 조용히 죽는다."""

    def test_D1_하루_쓰기_한도(self) -> None:
        from batch.core import db

        raw = ("D1 HTTP 400: Your account has exceeded D1's free tier daily row write limit. "
               "Upgrade to a paid plan or wait until tomorrow (midnight UTC) to continue.")  # fmt: skip
        assert "09:00 KST" in (db.quota_reason(TursoError(raw)) or "")

    def test_Turso_월_한도(self) -> None:
        from batch.core import db

        raw = "SQL 실패: Operation was blocked: SQL read operations are forbidden (reads are blocked)"
        assert "Turso" in (db.quota_reason(raw) or "")

    def test_다른_오류는_아니다(self) -> None:
        from batch.core import db

        assert db.quota_reason("no such table: prices") is None
        assert db.quota_reason(TursoError("D1 HTTP 401")) is None


def test_일일_배치는_한도면_건너뛰고_한_번_알린다(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from batch.jobs import daily

    def blocked(*_a, **_k):
        raise TursoError("D1 HTTP 400: exceeded D1's free tier daily row write limit")

    sent: list[str] = []
    monkeypatch.setattr(daily, "run", blocked)
    monkeypatch.setattr(daily.telegram, "send", lambda message: sent.append(message) or [])
    monkeypatch.setattr("sys.argv", ["daily", "--market", "KR"])
    assert daily.main() == 0  # 실패가 아니라 건너뜀
    assert len(sent) == 1 and "건너뜁니다" in sent[0] and "09:00 KST" in sent[0]


class Test백필_날짜_고르기:
    """D1 하루 한도에 맞춰 나눠 채울 때 **가장 최근의 빠진 날부터** 받는다 (docs/infra.md 25.8)."""

    def test_최근부터_그만큼만(self) -> None:
        from datetime import date

        from batch.jobs import backfill_kr as job

        sessions = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8)]
        stored = {"2026-09-08"}
        picked = job.pick_days(sessions, stored, max_days=2)
        assert picked == [date(2026, 9, 4), date(2026, 9, 7)]

    def test_제한이_없으면_빠진_날_전부(self) -> None:
        from datetime import date

        from batch.jobs import backfill_kr as job

        sessions = [date(2026, 9, d) for d in (1, 2, 3)]
        assert job.pick_days(sessions, {"2026-09-02"}, None) == [date(2026, 9, 1), date(2026, 9, 3)]

    def test_0_이면_받지_않는다(self) -> None:
        from datetime import date

        from batch.jobs import backfill_kr as job

        assert job.pick_days([date(2026, 9, 1)], set(), 0) == []


class Test유니버스만_받기:
    """과거 시세는 점수가 쓰는 종목만 받는다 — 같은 예산으로 3배 긴 기간 (docs/infra.md 25.8)."""

    def test_편입_보유_관심만_고른다(self) -> None:
        from batch.core import db
        from batch.jobs import backfill_kr as job
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        for sid in (1, 2, 3, 4):
            c.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
                [sid, f"00000{sid}"],
            )
        c.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at) VALUES"
            " ('2026-09-17', 1, 1, 'KRW', 't'), ('2026-09-17', 2, 0, 'KRW', 't')"
        )
        c.execute("INSERT INTO watchlist (stock_id, added_at) VALUES (3, 't')")
        assert job.watched_ids(mem) == {1, 3}  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("remaining", "reserve", "per_day", "expected"),
        [(100_000, 20_000, 880, 90), (15_000, 20_000, 880, 0), (100_000, 0, 0, 0)],
    )
    def test_남은_예산으로_날_수를_정한다(self, remaining, reserve, per_day, expected) -> None:
        from batch.jobs import backfill_kr as job

        assert job.days_within_budget(remaining, reserve, per_day) == expected


def test_D1_이면_오늘_쓰기를_따로_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    """D1 한도는 하루 단위(자정 UTC)라 월 누계로는 판단할 수 없다 (docs/infra.md 25.8)."""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    monkeypatch.setenv("DB_BACKEND", "d1")
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    db.record_rows_written(mem, 30_000)  # type: ignore[arg-type]
    db.record_rows_written(mem, 5_000)  # type: ignore[arg-type]
    assert db.remaining_d1_daily_writes(mem) == db.D1_DAILY_WRITE_LIMIT - 35_000  # type: ignore[arg-type]
    rows = mem.conn.execute("SELECT api_name, window_type FROM api_usage ORDER BY api_name").fetchall()
    assert ("d1_writes", "day") in rows and ("turso_writes", "month") in rows


def test_Turso_면_하루_카운터를_쓰지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    monkeypatch.delenv("DB_BACKEND", raising=False)
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    db.record_rows_written(mem, 10)  # type: ignore[arg-type]
    assert mem.conn.execute("SELECT COUNT(*) FROM api_usage WHERE api_name = 'd1_writes'").fetchone()[0] == 0


class Test최근에_돌았나:
    """주 1회면 되는 작업이 매일 수천 행을 다시 쓰지 않게 (docs/infra.md 25.8)."""

    def _mem(self):
        from batch.core import db
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        return mem

    def test_기록이_없으면_돌린다(self) -> None:
        from batch.core import db

        assert db.ran_within(self._mem(), "universe", 6, "KR") is False  # type: ignore[arg-type]

    def test_방금_성공했으면_건너뛴다_시장은_따로(self) -> None:
        from batch.core import db

        mem = self._mem()
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)"
            " VALUES ('universe', 'KR', 't', 'manual', ?, ?, 'success')",
            [db.now_iso(), db.now_iso()],
        )
        assert db.ran_within(mem, "universe", 6, "KR") is True  # type: ignore[arg-type]
        assert db.ran_within(mem, "universe", 6, "US") is False  # type: ignore[arg-type]

    def test_일부만_받은_실행은_재무처럼_원하면_세지_않는다(self) -> None:
        """재무가 DART 오류로 partial 이면 빈 칸이 남는다 — 7일 건너뛰면 안 된다 (docs/infra.md 25.320)."""
        from batch.core import db

        mem = self._mem()
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)"
            " VALUES ('financials', 'KR', 't', 'manual', ?, ?, 'partial')",
            [db.now_iso(), db.now_iso()],
        )
        assert db.ran_within(mem, "financials", 7, "KR") is True  # type: ignore[arg-type]
        assert db.ran_within(mem, "financials", 7, "KR", include_partial=False) is False  # type: ignore[arg-type]

    def test_재무_잡은_partial_을_세지_않게_부른다(self) -> None:
        import inspect

        from batch.jobs import financials

        assert "include_partial=False" in inspect.getsource(financials.main)

    def test_실패한_실행은_세지_않는다(self) -> None:
        from batch.core import db

        mem = self._mem()
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)"
            " VALUES ('financials', 'KR', 't', 'manual', ?, ?, 'failed')",
            [db.now_iso(), db.now_iso()],
        )
        assert db.ran_within(mem, "financials", 7, "KR") is False  # type: ignore[arg-type]

    def test_다른_DB_에서_되살린_기록은_세지_않는다(self) -> None:
        # 2026-09-18: D1 에 Turso 의 9/17 실행 기록이 되살려져 있었다. D1 에는 재무가 하나도 없는데
        # 그 기록 때문에 재무·업종·유니버스를 건너뛸 뻔했다
        from datetime import UTC, datetime, timedelta

        from batch.core import db

        mem = self._mem()  # 마이그레이션을 지금 적용했다 = 이 DB 가 지금 생겼다
        yesterday = datetime.now(UTC) - timedelta(days=1)
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)"
            " VALUES ('financials', 'KR', 't', 'manual', ?, ?, 'success')",
            [yesterday.isoformat(), (yesterday + timedelta(minutes=5)).isoformat()],
        )
        assert db.ran_within(mem, "financials", 7, "KR") is False  # type: ignore[arg-type]


class Test유니버스_건너뛰기:
    """편입 0 인 스냅샷은 돈 것으로 치지 않는다 (2026-09-18 D1, universe.should_skip)."""

    def _mem(self, included: int):
        from batch.core import db
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        mem.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, exclude_reason, currency, created_at)"
            " VALUES ('2026-09-17', 1, ?, ?, 'KRW', 't')",
            [included, None if included else "데이터없음"],
        )
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)"
            " VALUES ('universe', 'KR', '2026-09-17', 'manual', ?, ?, 'partial')",
            [db.now_iso(), db.now_iso()],
        )
        return mem

    def test_방금_돌았어도_편입_0_이면_다시_돈다(self) -> None:
        from batch.jobs import universe

        assert universe.should_skip(self._mem(included=0), "KR", 6) is False  # type: ignore[arg-type]

    def test_방금_돌았고_편입이_있으면_건너뛴다(self) -> None:
        from batch.jobs import universe

        mem = self._mem(included=1)
        assert universe.should_skip(mem, "KR", 6) is True  # type: ignore[arg-type]
        # 나라는 따로 본다
        assert universe.should_skip(mem, "US", 6) is False  # type: ignore[arg-type]


class Test용량_재기:
    """`D1Client.database_size()` — 관리 API 한 번. 질의가 아니라 예산을 쓰지 않는다 (25.123)."""

    def _client(self, monkeypatch: pytest.MonkeyPatch, 응답):
        for key, value in (("D1_ACCOUNT_ID", "a"), ("D1_DATABASE_ID", "b"), ("D1_API_TOKEN", "c")):
            monkeypatch.setenv(key, value)
        client = d1.D1Client()
        client._session = 응답  # type: ignore[assignment]
        return client

    def test_파일_크기를_읽는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class 세션:
            def get(self, url, timeout=None):
                assert url.endswith("/d1/database/b"), f"질의 경로가 아니라 DB 조회 경로여야 한다: {url}"
                return _응답(200, {"result": {"file_size": 123_456_789}})

        assert self._client(monkeypatch, 세션()).database_size() == 123_456_789

    @pytest.mark.parametrize(
        "세션만들기",
        [
            lambda: _고정세션(_응답(403, {})),
            lambda: _고정세션(_응답(200, {"result": {}})),        # 필드 이름이 바뀐 날
            lambda: _고정세션(_응답(200, {})),                    # result 자체가 없다
            lambda: _터지는세션(),
        ],
        ids=["403", "필드없음", "result없음", "예외"],
    )
    def test_모르면_None_이다(self, monkeypatch: pytest.MonkeyPatch, 세션만들기) -> None:
        """**0 을 돌려주면 "텅 비었다" 로 읽힌다.** 재는 일이 재어지는 일을 망치면 안 된다."""
        assert self._client(monkeypatch, 세션만들기()).database_size() is None


class _응답:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _고정세션:
    def __init__(self, 응답) -> None:
        self._응답 = 응답

    def get(self, url, timeout=None):
        return self._응답


class _터지는세션:
    def get(self, url, timeout=None):
        raise OSError("연결이 끊겼다")


class Test바뀐_행과_쓴_행은_다른_수다:
    """`changes` 는 바뀐 행, `rows_written` 은 인덱스까지 쓴 행 (docs/infra.md 25.152).

    우리가 `affected_rows` 라고 부르는 것은 앞쪽이다 — Turso 의 `affected_row_count`
    와 같은 뜻이어야 한다. 뒤쪽을 쓰면 **한 행을 지웠는데 넷이라고 답한다.**
    """

    def test_changes_가_오면_그것을_쓴다(self) -> None:
        from batch.core.d1 import _to_result_set

        rs = _to_result_set({"results": [], "meta": {"changes": 1, "rows_written": 4}})
        assert rs.affected_rows == 1, "인덱스 쓰기를 행 수로 셌다"

    def test_changes_가_없으면_옛_동작_그대로다(self) -> None:
        from batch.core.d1 import _to_result_set

        rs = _to_result_set({"results": [], "meta": {"rows_written": 4}})
        assert rs.affected_rows == 4

    def test_행이_있는_결과에서도_같다(self) -> None:
        from batch.core.d1 import _to_result_set

        rs = _to_result_set({"results": [{"a": 1}], "meta": {"changes": 2, "rows_written": 9}})
        assert rs.affected_rows == 2
        assert rs.rows == [(1,)]

    def test_훑은_행은_따로다(self) -> None:
        """읽기 예산은 `rows_read` 가 센다 (25.122). 섞이면 둘 다 틀린다."""
        from batch.core.d1 import _to_result_set

        rs = _to_result_set({"results": [], "meta": {"changes": 1, "rows_written": 4, "rows_read": 900}})
        assert (rs.affected_rows, rs.rows_read) == (1, 900)


def test_하루_읽기_한도는_쓰기_한도와_가른다_25_863() -> None:
    """2026-10-01 실제 응답 — 읽기 한도 초과를 "쓰기 한도(10만 행)" 로 안내했다."""
    from batch.core import db

    raw = (
        "D1 HTTP 400: {\"errors\":[{\"code\":7500,\"message\":\"Your account has exceeded D1's free tier daily row read"
        " limit. Upgrade to a paid plan or wait until tomorrow (midnight UTC) to continue.\"}]}"
    )
    사유 = db.quota_reason(raw) or ""
    assert "읽기 한도" in 사유 and "쓰기" not in 사유
    assert "쓰기 한도" in (db.quota_reason("exceeded D1's free tier daily row write limit") or "")
