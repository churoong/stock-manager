"""**시세 사본** — 과거 시세를 오래 읽는 작업이 Turso 대신 읽는 로컬 SQLite (docs/infra.md 25.888).

왜: Turso 무료 플랜은 한 달 읽기 5억 행이다. 시세(`prices`)는 약 730만 행으로 DB 의 대부분인데, 지표(5년치)·밸류 밴드·
신호 성과·백테스트·스트레스가 **주마다 같은 과거 시세를 다시** 읽는다(2026-10-02 사용자 결정 "C 진행"). 과거 시세는 거의
바뀌지 않으니 한 번 받아 GitHub Actions 캐시(무료)에 두고 **바뀐 것만** 받는다.

**무엇이 바뀌었는지 아는 법** — 마이그레이션 없이:
  1. 새 행: `id > 마지막으로 본 id` (기본 키 범위라 받은 행만큼만 읽는다)
  2. 있던 행을 고친 것(다시 받기·수정주가): 시세를 쓰는 길 둘(`db.bulk_upsert_prices`·`adjust_kr`)이
     실행 기록 `step_log.prices_touched` 에 종목·가장 이른 날을 적는다(`db.note_prices_touched`).
     그 뒤로 끝난 실행의 범위를 다시 받는다
  3. 표본 검증: 날마다 다른 종목 몇 개의 전 이력(행 수·종가 합·수정주가 합·마지막 날)을 Turso 와 견준다.
     어긋나면 그 종목을 다시 받고, 많이 어긋나면 **사본을 쓰지 않는다**(읽는 쪽이 Turso 로 간다 —
     틀린 시세로 계산하느니 비용을 낸다)

**쓰는 법**: 워크플로가 캐시를 꺼내 `scripts/price_replica.py sync` 로 맞추고, 작업은 환경변수
`PRICE_REPLICA_PATH` 가 있으면 `client.TursoClient()` 가 돌려주는 `ReplicaClient` 를 통해
**시세·종목만 읽는 SELECT** 를 사본에서 읽는다. 그 밖은 Turso 로 간다.
작업이 시세·종목을 쓰면 그 프로세스는 그때부터 사본을 쓰지 않는다(쓴 값이 사본에 없다).

Turso 가 아니면(D1 임시 운영) 쓰지 않는다 — id 가 다르다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from batch.core import db
from batch.core.turso import ResultSet

log = logging.getLogger(__name__)

REPLICA_ENV = "PRICE_REPLICA_PATH"

#: 사본에 두는 시세 열 — 과거 시세를 읽는 작업들이 쓰는 것만(지표·밴드·성과·백테스트·스트레스). 없는 열을 찾는 질의는
#: 로컬에서 실패하고 Turso 로 간다
PRICE_COLS = ("id", "stock_id", "date", "close", "adj_close", "volume", "value")

#: 한 번에 받을 행 수. Turso 응답 크기를 넘지 않게 [확인필요: 응답 한도 — 2만 행 × 7열은 수 MB]
PAGE_ROWS = 20_000
#: 종목을 묶어 범위를 다시 받을 때 한 질의의 종목 수 (`db.in_chunk` 와 같은 뜻)
STOCK_CHUNK = 90
#: 표본 검증 종목 수. 종목당 전 이력(약 1,250행)을 Turso 에서 세므로 약 2.5만 행
VERIFY_SAMPLE = 20
#: 표본 중 이만큼 넘게 어긋나면 사본을 쓰지 않는다 — 기록이 빠지는 길이 따로 있다는 뜻이다
VERIFY_MAX_MISMATCH = 3
#: 사본을 믿는 시간. 맞춘 뒤 이만큼 지나면 쓰지 않는다(맞춘 뒤의 쓰기를 모른다)
FRESH_HOURS = 12
#: 실행 기록의 끝난 시각과 맞춘 시각 사이의 여유 — 시계가 조금 달라도 범위를 놓치지 않게
TOUCH_MARGIN = timedelta(minutes=30)
#: "전 종목 다시 받기" 의 시작일이 이만큼(달력일) 넘게 앞서면 다시 받지 않고 사본을 비운다 — 다음 주간 작업이
#: **진도 문을 거쳐** 새로 만든다 (docs/infra.md 25.907, 감사). 약 8천 종목 × 60일(거래일 약 42일) ≈ 34만 행으로
#: 새로 만들기(약 760만)의 약 5%. 그보다 넓으면 비용이 새로 만들기와 비슷해지는데 그 길만 진도 문을 안 거쳤다
#: (백필 뒤 약 730만 행)
WIDE_RANGE_DAYS = 60
#: 시세를 고치는 작업들. 이 작업의 실행이 강제 종료·쓰기 한도로 끝맺지 못하면(다음 실행이 정리,
#: `db.reap_stale_runs`) 고친 범위 기록(`prices_touched`)이 남지 않는다 — 범위를 모르므로 "전부" 로 본다 (25.907, 감사)
PRICE_WRITER_JOBS = ("daily_kr", "daily_us", "backfill_kr", "backfill_us", "adjust_kr", "refresh_us_adjusted")
#: `db.reap_stale_runs` 가 남기는 오류 문구의 앞부분
REAPED_PREFIX = "끝맺지 못한 기록"

ROUTABLE_TABLES = frozenset({"prices", "stocks"})
_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", re.I)
_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
_CTE = re.compile(r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^()]*\))?\s+AS\s*\(", re.I)


def referenced_tables(sql: str) -> set[str]:
    """SQL 이 읽는 표 이름(소문자). CTE 이름은 뺀다. 모르는 모양이면 넓게 잡는다 — 그러면 Turso 로 간다."""
    ctes = {m.lower() for m in _CTE.findall(sql)}
    return {t.lower() for t in _TABLE.findall(sql)} - ctes


def is_write(sql: str) -> bool:
    return bool(_WRITE.match(sql))


def open_replica(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS prices (id INTEGER, stock_id INTEGER NOT NULL, date TEXT NOT NULL, close REAL,"
        " adj_close REAL, volume INTEGER, value INTEGER, UNIQUE (stock_id, date));"
        "CREATE INDEX IF NOT EXISTS idx_prices_id ON prices (id);"
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
    )
    return conn


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM meta").fetchall())


def _set_meta(conn: sqlite3.Connection, **values: Any) -> None:
    conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [(k, str(v)) for k, v in values.items()])


_UPSERT = (
    "INSERT INTO prices (id, stock_id, date, close, adj_close, volume, value) VALUES (?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (stock_id, date) DO UPDATE SET id = excluded.id, close = excluded.close,"
    " adj_close = excluded.adj_close, volume = excluded.volume, value = excluded.value"
)
#: Turso 에서 받는 질의 — 열은 `PRICE_COLS` 와 같은 순서(`tests/test_price_replica.py` 가 묶는다)
_PULL_NEW = (
    "SELECT id, stock_id, date, close, adj_close, volume, value FROM prices WHERE id > ? ORDER BY id LIMIT ?"
)
#: 종목 목록은 JSON 하나로 넘긴다 — 질의가 고정 문자열이라 스키마 대조(`test_sql_schema`)가 본다.
#: `IN (SELECT value FROM json_each(?))` 도 (stock_id, date) 색인을 탄다
_PULL_RANGE = (
    "SELECT id, stock_id, date, close, adj_close, volume, value FROM prices"
    " WHERE stock_id IN (SELECT value FROM json_each(?)) AND date >= ?"
)
#: 정수로 바꿔 더한다 — 실수 합은 더하는 순서에 따라 끝자리가 달라 같은 자료도 어긋나 보인다
_FINGERPRINT = (
    "SELECT COUNT(*), SUM(CAST(ROUND(close * 10000) AS INTEGER)),"
    " SUM(CAST(ROUND(COALESCE(adj_close, 0) * 10000) AS INTEGER)), MAX(date) FROM prices WHERE stock_id = ?"
)


def _upsert(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(_UPSERT, rows)


def _copy_stocks(remote, conn: sqlite3.Connection) -> int:
    """종목 표는 작아서(약 8천 행) 맞출 때마다 통째로 받는다. 열은 Turso 의 것 그대로."""
    rs = remote.execute("SELECT * FROM stocks")
    cols = ", ".join(f'"{c}"' for c in rs.columns)
    conn.execute("DROP TABLE IF EXISTS stocks")
    conn.execute(f"CREATE TABLE stocks ({cols})")
    conn.executemany(f"INSERT INTO stocks VALUES ({', '.join('?' * len(rs.columns))})", rs.rows)
    return len(rs.rows)


def _pull_new(remote, conn: sqlite3.Connection, after_id: int) -> int:
    n = 0
    while True:
        rs = remote.execute(_PULL_NEW, [after_id, PAGE_ROWS])
        if not rs.rows:
            return n
        _upsert(conn, rs.rows)
        n += len(rs.rows)
        after_id = int(rs.rows[-1][0])
        if len(rs.rows) < PAGE_ROWS:
            return n


def _pull_range(remote, conn: sqlite3.Connection, stock_ids: list[int], since: str) -> int:
    n = 0
    for start in range(0, len(stock_ids), STOCK_CHUNK):
        ids = stock_ids[start : start + STOCK_CHUNK]
        rs = remote.execute(_PULL_RANGE, [json.dumps(ids), since])
        _upsert(conn, rs.rows)
        n += len(rs.rows)
    return n


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return date(1, 1, 1)  # "0000-00-00" — 처음부터


def touched_ranges(remote, since_iso: str) -> tuple[str | None, dict[int, str]]:
    """그 뒤로 끝난 실행들이 고친 시세 범위. (전 종목을 다시 받을 가장 이른 날, 종목별 가장 이른 날)."""
    rs = remote.execute(
        "SELECT step_log FROM batch_runs WHERE finished_at >= ? AND step_log LIKE ?",
        [since_iso, f'%"{db.PRICES_TOUCHED_KEY}"%'],
    )
    전체: str | None = None
    종목별: dict[int, str] = {}
    끝맺지_못함 = remote.execute(
        "SELECT COUNT(*) FROM batch_runs WHERE finished_at >= ? AND error_text LIKE ?"
        " AND job_name IN (?, ?, ?, ?, ?, ?)",
        [since_iso, f"{REAPED_PREFIX}%", *PRICE_WRITER_JOBS],
    )
    if 끝맺지_못함.rows and int(끝맺지_못함.rows[0][0] or 0):
        전체 = "0000-00-00"  # 어디를 고쳤는지 모른다
    for (raw,) in rs.rows:
        try:
            t = json.loads(raw)[db.PRICES_TOUCHED_KEY]
        except (ValueError, KeyError, TypeError):
            continue
        if t.get("all"):
            전체 = min(전체 or "9999", str(t["min_date"]))
            continue
        for sid, day in (t.get("stocks") or {}).items():
            if str(day) < 종목별.get(int(sid), "9999"):
                종목별[int(sid)] = str(day)
    return 전체, 종목별


def _sample(conn: sqlite3.Connection, seed: str, k: int) -> list[int]:
    ids = [r[0] for r in conn.execute("SELECT DISTINCT stock_id FROM prices ORDER BY stock_id").fetchall()]
    return sorted(ids, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())[:k]


def verify(remote, conn: sqlite3.Connection, seed: str, k: int | None = None) -> list[int]:
    """표본 종목의 전 이력을 Turso 와 견준다. 어긋난 종목 번호."""
    어긋남 = []
    for sid in _sample(conn, seed, VERIFY_SAMPLE if k is None else k):
        원격 = tuple(remote.execute(_FINGERPRINT, [sid]).rows[0])
        로컬 = tuple(conn.execute(_FINGERPRINT, [sid]).fetchone())
        if 원격 != 로컬:
            어긋남.append(sid)
    return 어긋남


def sync(remote, conn: sqlite3.Connection, *, backend: str, allow_build: bool, now: datetime | None = None) -> dict:
    """사본을 Turso 에 맞춘다. 결과 요약(`usable` 이 쓸 수 있는지)."""
    now = now or datetime.now(UTC)
    meta = _meta(conn)
    비었나 = conn.execute("SELECT 1 FROM prices LIMIT 1").fetchone() is None
    report: dict[str, Any] = {"backend": backend}
    if backend != "turso":
        _set_meta(conn, usable=0)
        conn.commit()
        return {**report, "usable": False, "reason": "Turso 가 아니다(D1 임시 운영) — id 가 달라 사본을 쓰지 않는다"}
    if not 비었나 and meta.get("backend") != "turso":
        conn.execute("DELETE FROM prices")
        비었나 = True
    if 비었나 and not allow_build:
        _set_meta(conn, usable=0)
        conn.commit()
        return {**report, "usable": False, "reason": "사본이 비었고 지금은 처음부터 만들지 않는다(읽기 진도)"}

    시작 = now.isoformat()
    report["stocks"] = _copy_stocks(remote, conn)
    if 비었나:
        report["built"] = _pull_new(remote, conn, 0)
    else:
        report["new"] = _pull_new(remote, conn, int(meta.get("max_id") or 0))
        지난 = datetime.fromisoformat(meta["synced_at"]) - TOUCH_MARGIN if meta.get("synced_at") else None
        if 지난 is None:
            전체, 종목별 = "0000-00-00", {}
        else:
            전체, 종목별 = touched_ranges(remote, 지난.isoformat())
        다시 = 0
        if 전체 and (now.date() - _day(전체)).days > WIDE_RANGE_DAYS:
            # 넓은 다시 받기는 새로 만들기와 비용이 비슷하다 — 비우고 진도 문 있는 주간 작업에 넘긴다 (25.907)
            conn.execute("DELETE FROM prices")
            _set_meta(conn, usable=0, max_id=0)
            conn.commit()
            글 = f"{전체} 부터 전 종목을 다시 받아야 한다 — 비우고 진도 문을 거쳐 새로 만든다"
            return {**report, "usable": False, "cleared": True, "reason": 글}
        if 전체:
            모두 = [r[0] for r in conn.execute("SELECT id FROM stocks ORDER BY id").fetchall()]
            다시 += _pull_range(remote, conn, 모두, 전체)
        for sid, day in sorted(종목별.items()):
            if not 전체 or day < 전체:
                다시 += _pull_range(remote, conn, [sid], day)
        report["repulled"] = 다시
    어긋남 = verify(remote, conn, now.strftime("%Y-%m-%d"))
    for sid in 어긋남:
        conn.execute("DELETE FROM prices WHERE stock_id = ?", [sid])
        _pull_range(remote, conn, [sid], "0000-00-00")
    report["mismatch"] = 어긋남
    usable = len(어긋남) <= VERIFY_MAX_MISMATCH
    max_id = conn.execute("SELECT MAX(id) FROM prices").fetchone()[0] or 0
    _set_meta(conn, backend="turso", max_id=max_id, synced_at=시작, usable=int(usable))
    conn.commit()
    report["usable"] = usable
    if not usable:
        report["reason"] = f"표본 {VERIFY_SAMPLE} 중 {len(어긋남)}종목이 어긋났다 — 기록이 빠지는 쓰기 길이 있다"
    return report


def usable_replica(path: str | Path, now: datetime | None = None) -> sqlite3.Connection | None:
    """맞춘 지 `FRESH_HOURS` 안이고 쓸 수 있다고 적힌 사본만 연다. 아니면 None(→ Turso)."""
    if not Path(path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        meta = _meta(conn)
    except sqlite3.Error as error:
        log.warning("시세 사본을 열지 못했다 — Turso 에서 읽는다: %s", error)
        return None
    now = now or datetime.now(UTC)
    synced = meta.get("synced_at")
    if meta.get("usable") != "1" or meta.get("backend") != "turso" or not synced:
        conn.close()
        return None
    if now - datetime.fromisoformat(synced) > timedelta(hours=FRESH_HOURS):
        log.warning("시세 사본이 %s 에 맞춘 것이라 오래됐다 — Turso 에서 읽는다", synced)
        conn.close()
        return None
    return conn


class ReplicaClient:
    """시세·종목만 읽는 SELECT 는 사본에서, 나머지는 Turso 로. 시세·종목을 쓰면 그때부터 모두 Turso."""

    def __init__(self, remote, conn: sqlite3.Connection) -> None:
        self._remote = remote
        self._conn = conn
        self.dirty = False
        self.local_queries = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._remote, name)

    def _routable(self, sql: str) -> bool:
        if self.dirty:
            return False
        if is_write(sql):
            if referenced_tables(sql) & ROUTABLE_TABLES or re.search(r"\b(prices|stocks)\b", sql, re.I):
                self.dirty = True
                log.info("시세·종목을 쓰는 문장이 있어 이 프로세스는 이제 Turso 에서 읽는다")
            return False
        tables = referenced_tables(sql)
        return bool(tables) and tables <= ROUTABLE_TABLES

    def _local(self, sql: str, args: list | None) -> ResultSet | None:
        try:
            cur = self._conn.execute(sql, list(args or []))
        except sqlite3.Error as error:
            log.debug("사본에서 못 읽어 Turso 로: %s", error)
            return None
        cols = [d[0] for d in cur.description or []]
        self.local_queries += 1
        return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()])

    def execute(self, sql: str, args: list | None = None) -> ResultSet:
        if self._routable(sql):
            rs = self._local(sql, args)
            if rs is not None:
                return rs
        return self._remote.execute(sql, args)

    def batch(self, statements: list[tuple[str, list]]) -> list[ResultSet]:
        if statements and all(self._routable(sql) for sql, _ in statements):
            out = [self._local(sql, args) for sql, args in statements]
            if all(r is not None for r in out):
                return out  # type: ignore[return-value]
        for sql, _ in statements:
            self._routable(sql)  # 쓰기가 섞였으면 dirty 를 세운다
        return self._remote.batch(statements)

    def close(self) -> None:
        if self.local_queries:
            log.info("시세 사본에서 읽은 질의 %d개", self.local_queries)
        self._conn.close()
        self._remote.close()
