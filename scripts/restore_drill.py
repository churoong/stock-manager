"""백업 **복구 리허설** — 백업 파일을 빈 SQLite 에 되살려 표별 행 수를 백업이 적어 둔 값과 대 본다
(docs/backup.md 6장, docs/infra.md 25.946).

백업은 복구해 봐야 백업이다. 매주 덤프가 돌고 기록도 남지만(25.167), 그 파일로 실제로 되살려 본 적이
운영 기록에 없었다. 이 스크립트는 **운영 DB 를 건드리지 않는다** — 임시 SQLite 파일에 마이그레이션을 적용하고
`restore_backup` 과 같은 길(문장 가르기·행 수 표식)로 넣은 뒤 `COUNT(*)` 를 `-- rows <표> <n>` 과 대조한다.
하나라도 어긋나면 종료코드 1 이다.

`--record-run` 을 주면 결과를 `batch_runs`(job `restore_drill`)에 한 줄 적는다 — `/status` 신선도 칸이
"마지막 리허설" 을 보인다. 그 한 줄 말고는 쓰지 않는다.

실행
  python scripts/restore_drill.py backup/stock-manager-essential-2026-10-05.sql.gz
  python scripts/restore_drill.py 백업.sql.gz --record-run
"""

from __future__ import annotations

import argparse
import gzip
import logging
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import db  # noqa: E402
from batch.core.turso import ResultSet  # noqa: E402
from scripts.backup_db import ROWS_MARK  # noqa: E402
from scripts.restore_backup import _INSERT, _문장들, with_conflict  # noqa: E402

log = logging.getLogger("restore_drill")

JOB_NAME = "restore_drill"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# 정규식이 SQL 로 보이지 않게 `(?i)` 로 시작한다 — `tests/test_sql_schema.py` 가 SQL 머리말로 시작하는 문자열을
# 질의 조각으로 센다
_SEED = re.compile(r"(?i)(?:INSERT)\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)")


def prefilled_tables() -> set[str]:
    """마이그레이션이 **미리 채우는** 표 — SQL 파일의 INSERT 를 읽어 낸다 (손으로 적으면 새 씨앗이 생길 때 빠진다).

    2026-10-04 네 번째 실행: `cron_heartbeats` 가 0025 의 씨앗 행(health/ALL)과 겹쳐 UNIQUE 로 걸렸는데 목록엔 없었다.
    `schema_migrations` 는 `apply_migrations` 가 채운다.
    """
    out = {"schema_migrations"}
    for f in MIGRATIONS_DIR.glob("*.sql"):
        out.update(m.group(1) for m in _SEED.finditer(f.read_text(encoding="utf-8")))
    return out


#: 마이그레이션이 미리 채우는 표 — 백업 행을 **덮어쓴다**(INSERT OR REPLACE) 그래야 행 수가 백업과 같아진다.
#: 그래도 씨앗 행이 백업에 없는 경우가 있어 "적힌 값 이상" 이면 통과로 본다
PREFILLED = prefilled_tables()
#: 한 번에 넣는 문장 수
CHUNK = 2_000
#: **마이그레이션이 만들지 않는 표**와 왜 대조하지 않는지. 전체 백업에는 담기지만 `restore_backup` 은 DDL 을
#: 실행하지 않아 빈 DB 에 되살릴 수 없다 — 사유 없는 예외는 두지 않는다. 운영 표가 여기 들어오면 그것은
#: 스키마 표류다(handoff 에 적는다)
OUTSIDE_MIGRATIONS = {
    "step0_check": "되살아난 날의 점검 작업(`jobs/step0_check`)이 스스로 만드는 확인용 표. 되살릴 뜻이 없다"
    " (2026-10-04 네 번째 실행에서 확인)",
}


class SqliteClient:
    """`db.apply_migrations` 가 기대하는 최소 모양 — 로컬 SQLite 파일. 운영 백엔드가 아니다."""

    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path)

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

    def batch(self, items: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in items]

    def close(self) -> None:
        self.conn.close()


@dataclass
class DrillResult:
    ok: bool
    expected: dict[str, int]
    actual: dict[str, int]
    inserted: int
    mismatches: list[tuple[str, int, int]] = field(default_factory=list)  # (표, 적힌 값, 실제)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # 마이그레이션 밖 표 — 대조하지 않는다

    def lines(self) -> list[str]:
        out = [f"넣은 문장 {self.inserted:,}, 표 {len(self.expected)}개 대조"]
        for t in sorted(self.expected):
            e, a = self.expected[t], self.actual.get(t, 0)
            표시 = "✓" if (a == e or (t in PREFILLED and a >= e)) else "✗"
            out.append(f"  {표시} {t:24s} 적힌 {e:>9,}  실제 {a:>9,}")
        for t in self.skipped:
            out.append(f"  – {t}: 대조 안 함 — {OUTSIDE_MIGRATIONS[t]}")
        for 글 in self.errors:
            out.append(f"  ✗ {글}")
        out.append("복구 리허설 통과" if self.ok else "복구 리허설 **실패** — 위 ✗ 를 본다")
        return out


def _stream(path: Path):
    """백업을 **흘려 읽으며** (표, INSERT 문)을 내놓고, 따옴표 밖 주석 줄을 `comments` 에 모은다.

    `restore_backup.statements` 는 파일 전체를 글로 올린다 — 전체 백업(226MB gz, 800만 문장)은 러너 메모리를 넘겨
    리허설이 죽었다(2026-10-04 세 번째 실행, 뒤 단계가 전부 skipped — OOM 으로 본다 `[확인필요: 러너 로그]`).
    줄 단위로 읽되 **따옴표 홀짝**으로 값 안의 줄바꿈을 넘긴다(`''` 는 짝수라 셈에 안 걸린다) — 문장이 끝난
    자리에서만 `_문장들` 에 넘겨 같은 규칙으로 가른다.
    """
    comments: list[str] = []
    버퍼: list[str] = []
    홀짝 = 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            버퍼.append(line)
            홀짝 ^= line.count("'") & 1
            if 홀짝 == 0 and line.rstrip().endswith(";"):
                for 문장 in _문장들("".join(버퍼), comments):
                    본문 = 문장.strip()
                    m = _INSERT.match(본문)
                    if m:
                        yield m.group(1), 본문
                버퍼 = []
        if 버퍼:
            for 문장 in _문장들("".join(버퍼), comments):
                본문 = 문장.strip()
                m = _INSERT.match(본문)
                if m:
                    yield m.group(1), 본문
    yield "", "\n".join(comments)  # 마지막에 주석 묶음 하나 — 부르는 쪽이 표식을 읽는다


def _rows_marks(comments: str) -> dict[str, int]:
    return {m.group(1): int(m.group(2))
            for m in re.finditer(rf"^{re.escape(ROWS_MARK)}\s+(\w+)\s+(\d+)\s*$", comments, re.M)}  # fmt: skip


def drill(path: Path, work_db: str | None = None) -> DrillResult:
    """백업 하나를 빈 SQLite 에 되살려 대조한다. 운영 DB 에 닿지 않는다. 메모리는 묶음(`CHUNK`) 하나만큼만 쓴다."""
    if work_db is None:
        fd, work_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
    client = SqliteClient(work_db)
    errors: list[str] = []
    inserted = 0
    표들: set[str] = set()
    건너뛴표: set[str] = set()
    expected: dict[str, int] = {}

    def 넣기(chunk: list[tuple[str, str]]) -> None:
        """묶음을 한 트랜잭션으로. 깨지면 **되돌리고** 한 문장씩 다시 넣어 어느 문장인지 찾는다.

        `executescript("BEGIN; … COMMIT;")` 을 쓰지 않는다 (2026-10-04 네 번째 실행) — 깨진 뒤 `rollback()` 이
        묶음을 다 되돌리지 못해 다시 넣는 문장이 UNIQUE 에 걸렸다(cron_heartbeats, 행 수는 맞았는데 오류만 남았다).
        자동 커밋 모드에서 `BEGIN`·`ROLLBACK` 을 직접 치면 되돌림이 확실하다. 한 문장씩이어도 `synchronous=OFF` 라
        50만 문장 20초 안이다
        """
        nonlocal inserted
        conn = client.conn
        conn.execute("BEGIN")
        try:
            for _t, sql in chunk:
                conn.execute(sql)
            conn.execute("COMMIT")
            inserted += len(chunk)
            return
        except sqlite3.Error:
            conn.execute("ROLLBACK")
        for table, sql in chunk:
            try:
                conn.execute(sql)
                inserted += 1
            except sqlite3.Error as exc:
                errors.append(f"{table}: {exc} — {sql[:80]}…")

    try:
        db.apply_migrations(client)  # type: ignore[arg-type]
        client.conn.isolation_level = None  # 자동 커밋 — 트랜잭션은 `넣기` 가 BEGIN/COMMIT/ROLLBACK 으로 직접 다룬다
        client.conn.execute("PRAGMA foreign_keys = OFF")  # 덤프 순서는 의존 순이지만(25.121) 리허설은 행 수가 목적이다
        client.conn.execute("PRAGMA journal_mode = MEMORY")  # 버리는 DB 다 — 내구성보다 속도
        client.conn.execute("PRAGMA synchronous = OFF")
        # 마이그레이션이 미리 채운 표는 백업 행으로 덮어쓴다. 나머지는 **그대로** — 겹치면 백업이 이상한 것이다.
        # 전체 백업은 800만 문장이라 한 문장씩 넣으면 오래 걸린다 — 묶음으로 `executescript` 하고(50만 문장 23초 실측),
        # 묶음이 깨지면 그 묶음만 한 문장씩 다시 넣어 어느 문장인지 찾는다
        chunk: list[tuple[str, str]] = []
        for table, sql in _stream(path):
            if table == "":
                expected = _rows_marks(sql)
                break
            if table in OUTSIDE_MIGRATIONS:
                건너뛴표.add(table)
                continue
            표들.add(table)
            chunk.append((table, with_conflict(sql, "replace" if table in PREFILLED else "fail")))
            if len(chunk) >= CHUNK:
                넣기(chunk)
                chunk = []
                if len(errors) >= 20:
                    errors.append("오류가 20개를 넘어 멈췄다")
                    break
        if chunk and len(errors) < 20:
            넣기(chunk)
        actual: dict[str, int] = {}
        for t in (set(expected) | 표들) - 건너뛴표:
            try:
                actual[t] = int(client.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
            except sqlite3.Error as exc:
                errors.append(f"{t}: 셀 수 없다 ({exc})")
    finally:
        client.close()
    for t in sorted(건너뛴표):
        expected.pop(t, None)  # 대조 밖 — 사유는 `OUTSIDE_MIGRATIONS`. 글에는 따로 적는다
    mismatches = [
        (t, e, actual.get(t, 0)) for t, e in sorted(expected.items())
        if not (actual.get(t, 0) == e or (t in PREFILLED and actual.get(t, 0) >= e))
    ]  # fmt: skip
    if not expected:
        errors.append("백업에 행 수 표식(-- rows)이 없다 — 옛 백업이라 대조할 수 없다")
    return DrillResult(ok=not mismatches and not errors, expected=expected, actual=actual, inserted=inserted,
                       mismatches=mismatches, errors=errors, skipped=sorted(건너뛴표))  # fmt: skip


def record(result: DrillResult, path: Path) -> None:
    """운영 `batch_runs` 에 한 줄. 못 적어도 리허설 결과는 그대로다."""
    from batch.core.client import TursoClient

    try:
        client = TursoClient()
    except Exception as exc:  # noqa: BLE001
        print(f"실행 기록을 열지 못했다: {exc}")
        return
    요약 = ("; ".join(f"{t} 적힌 {e} 실제 {a}" for t, e, a in result.mismatches) or "; ".join(result.errors))[:500]
    try:
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=None, trade_date=None)
        db.finish_batch_run(
            client, run_id, status="success" if result.ok else "failed",
            step_log={"file": path.name, "tables": len(result.expected), "inserted": result.inserted,
                      "mismatches": [list(m) for m in result.mismatches], "errors": result.errors[:5]},
            error_text=None if result.ok else 요약,
        )
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="백업 복구 리허설 (운영 DB 를 건드리지 않는다)")
    parser.add_argument("path", type=Path)
    parser.add_argument("--record-run", dest="record_run", action="store_true",
                        help="결과를 batch_runs 에 한 줄 적는다")  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    result = drill(args.path)
    print(f"파일 {args.path.name}")
    print("\n".join(result.lines()))
    if args.record_run:
        record(result, args.path)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
