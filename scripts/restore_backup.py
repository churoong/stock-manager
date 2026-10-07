"""백업(.sql.gz)을 지금 쓰는 DB 백엔드에 되살린다 (docs/infra.md 25절).

Turso 가 막혀 Cloudflare D1 로 옮길 때 쓴다. 백업 파일은 `scripts/backup_db.py` 가 만든
INSERT 문 모음이라 SQLite 계열이면 그대로 들어간다.

**다시 만들 수 없는 것만 먼저 넣는다.** 시세·점수·신호는 원본에서 다시 받으면 되지만,
매매·배당·설정·관심종목·시점 스냅샷·지나간 뉴스는 이 파일에만 있다.

주의
  - D1 은 **하루 쓰기 10만 행** 제한이 있다. `--max-rows` 를 안 줘도 **남은 예산을 보고** 자른다
    (2026-09-22, docs/infra.md 25.120). 자를 때는 **표 경계에서** 자르고, 다음에 넣을
    `--only …` 를 그대로 찍어 준다
  - `--only` 로 표를 골라 넣는다. 기본은 파일에 든 모든 표
  - 이미 있는 행은 기본으로 **건너뛴다**(`--on-conflict skip`). **기본이 맞다** —
    되살릴 DB 는 비어 있는 것이 정상이다 (2026-09-23 정정, docs/infra.md 25.155).
    전에는 여기에 "마이그레이션이 기본값을 넣어 둔 `settings` 는 `replace` 로 덮는다" 고
    적혀 있었는데 **마이그레이션은 `settings` 에 아무것도 넣지 않는다.** 틀린 근거로
    위험한 쪽을 권하고 있었다 — `replace` 는 되살리는 사이에 사람이 새로 넣은 값을 덮는다.
    운영 표시(복귀 표시 등)는 애초에 백업에 담기지 않는다(`backup_db.설정_제외_사유`)
  - **외래키 순서를 지킨다.** D1 은 외래키를 실제로 강제한다(Turso 에서는 걸리지 않았다).
    `news` 는 `stocks` 가 먼저 있어야 들어간다

실행
  python scripts/restore_backup.py 백업.sql.gz --dry-run
  python scripts/restore_backup.py 백업.sql.gz --only settings,trades,watchlist
  python scripts/restore_backup.py 백업.sql.gz --max-rows 80000
"""

from __future__ import annotations

import argparse
import gzip
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import db  # noqa: E402
from batch.core.client import TursoClient  # noqa: E402
from scripts.backup_db import ROWS_MARK, STOCK_KEY_MARK  # noqa: E402

log = logging.getLogger("restore")

# 한 번에 보낼 문장 수. D1·Turso 모두 한 요청이 너무 커지면 거절한다
CHUNK = 100
_INSERT = re.compile(r"^INSERT INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s", re.IGNORECASE)
#: 백업의 종목 INSERT 첫 값(id). `SELECT *` 라 id 가 첫 열이다 (25.681)
_첫값 = re.compile(r"\(id,[^)]*\)\s*VALUES\s*\((\d+),", re.IGNORECASE)


# 충돌 처리. skip 은 이미 있는 행을 두고, replace 는 백업 값으로 덮는다
CONFLICT_PREFIX = {"skip": "INSERT OR IGNORE INTO", "replace": "INSERT OR REPLACE INTO", "fail": "INSERT INTO"}


def with_conflict(sql: str, mode: str) -> str:
    """`INSERT INTO ...` 를 충돌 처리가 붙은 형태로 바꾼다. 뒷부분은 손대지 않는다."""
    return CONFLICT_PREFIX[mode] + sql[len("INSERT INTO") :]


def _문장들(글: str, 주석: list[str] | None = None):
    """따옴표 밖의 `;` 에서만 끊어 SQL 문장을 하나씩 내놓는다.

    **한 줄씩 읽으면 안 된다** (2026-09-22, docs/infra.md 25.119).
    백업은 값을 그대로 쓰고 따옴표만 겹친다(`scripts/backup_db.quote`) — 값에 **줄바꿈**이
    있으면 `INSERT` 한 문장이 여러 줄이 된다. 매매 메모·무응답 알림 문구가 실제로 그렇다.
    예전에는 "`INSERT` 로 시작하고 `;` 로 끝나는 줄" 만 주웠고, 그런 행은 **소리 없이
    사라졌다.** 값 안의 `;` 도 같은 이유로 따옴표를 세어야 한다.
    """
    버퍼: list[str] = []
    따옴표안 = False
    i = 0
    n = len(글)
    while i < n:
        c = 글[i]
        if 따옴표안:
            if c == "'":
                if i + 1 < n and 글[i + 1] == "'":  # '' 는 값 안의 따옴표다
                    버퍼.append("''")
                    i += 2
                    continue
                따옴표안 = False
            버퍼.append(c)
        elif c == "'":
            따옴표안 = True
            버퍼.append(c)
        elif c == "-" and 글.startswith("--", i):
            # **주석은 따옴표 밖에서만** (docs/infra.md 25.692, 교차검증). 예전에는 문장을 다 모은 뒤 `--` 로 시작하는
            # **줄**을 걷어, 값 안의 줄(매매 메모 "첫줄\n-- 손절 5%")까지 지웠다 — 메모가 조용히 바뀌거나 문장이 잘렸다
            끝 = 글.find("\n", i)
            if 주석 is not None:
                주석.append(글[i : n if 끝 < 0 else 끝])
            i = n if 끝 < 0 else 끝
            continue
        elif c == ";":
            yield "".join(버퍼)
            버퍼 = []
        else:
            버퍼.append(c)
        i += 1
    if 따옴표안:
        # **따옴표가 닫히지 않은 채 끝나면 셈이 어긋난 것이다** (docs/infra.md 25.697, 교차검증). 25.695 뒤로 행 수
        # 표식도 같은
        # 셈을 거쳐, 어긋나면 그 뒤의 행과 표식이 **함께** 사라져 행 수 대조가 조용히 통과했다(DDL 의 `/* b's note */`
        # 등)
        raise ValueError("백업의 따옴표가 닫히지 않은 채 끝났습니다 — 문장을 가르지 못했습니다")
    if "".join(버퍼).strip():
        yield "".join(버퍼)


def _주석줄들(글: str) -> str:
    """**따옴표 밖의** 주석 줄만 이어 붙인 글 (docs/infra.md 25.695, 교차검증).

    행 수(`-- rows`)와 종목 대응표(`-- stock_key`)를 원문 전체에 줄 단위 정규식으로 찾으면, 매매 메모 속 줄
    "`-- stock_key 1\tAAPL\tNASDAQ…`" 이 머리의 진짜 줄을 덮어써 빈 새 DB 의 1번 자리가 AAPL 로 만들어졌다 —
    25.692 가 고친 원인이 여기 둘에 남아 있었다. 문장을 끊는 `_문장들` 과 같은 따옴표 셈을 쓴다.
    """
    모은것: list[str] = []
    for _ in _문장들(글, 모은것):
        pass
    return "\n".join(모은것)


def 적힌_행수(글: str) -> dict[str, int]:
    """백업이 적어 둔 표별 행 수 (`-- rows <표> <n>`). 옛 백업에는 없다."""
    나온것: dict[str, int] = {}
    for m in re.finditer(rf"^{re.escape(ROWS_MARK)}\s+(\w+)\s+(\d+)\s*$", _주석줄들(글), re.M):
        나온것[m.group(1)] = int(m.group(2))
    return 나온것


def statements(path: Path) -> list[tuple[str, str]]:
    """(표 이름, INSERT 문) 목록. DDL·PRAGMA·트랜잭션 문은 버린다.

    표는 마이그레이션이 이미 만든다. 백업의 DROP/CREATE 를 실행하면 **새 마이그레이션으로 생긴
    열이 사라진다.** 데이터만 넣는다.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        글 = handle.read()
    out: list[tuple[str, str]] = []
    for 문장 in _문장들(글):
        # 주석은 `_문장들` 이 따옴표 밖에서만 걷었다 (25.692). 값 안의 줄바꿈·`--` 는 그대로다
        본문 = 문장.strip()
        match = _INSERT.match(본문)
        if match:
            out.append((match.group(1), 본문))
    return out


def 종목_대응표(path: Path) -> dict[int, tuple[str, str, str, str]]:
    """백업이 적어 둔 번호 → (종목코드, 시장, 나라, 통화) (docs/infra.md 25.676·25.677). 옛 백업에는 없다.

    25.676 첫 판은 (코드, 시장) 셋 칸이었다 — 그 모양도 읽고 나라·통화는 시장에서 채운다.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        글 = handle.read()
    out: dict[int, tuple[str, str, str, str]] = {}
    칸 = r"([^\t\n]*)"
    줄 = rf"^{re.escape(STOCK_KEY_MARK)} (\d+)\t{칸}\t{칸}(?:\t{칸}\t{칸})?$"
    for m in re.finditer(줄, _주석줄들(글), re.M):
        시장 = m.group(3)
        국내 = 시장.upper().startswith(("KOSPI", "KOSDAQ", "KONEX"))
        나라 = m.group(4) or ("KR" if 국내 else "US")
        out[int(m.group(1))] = (m.group(2), 시장, 나라, m.group(5) or ("KRW" if 나라 == "KR" else "USD"))
    return out


def 파일_대응표(파일_종목: list[tuple[str, str]]) -> dict[int, tuple[str, str, str, str]]:
    """대응표 줄이 없는 옛 **전체** 백업이면 파일의 종목 행으로 대응표를 만든다 (docs/infra.md 25.683, 교차검증).

    25.681 이 파일에 종목 행이 있어도 대조하게 되면서, 대응표 줄이 없는 옛 전체 백업은 빈 새 DB 에도 막혔고
    trust 로 넣으면 여전히 남의 종목에 붙었다 — 대조할 재료(번호·코드·시장)가 파일에 다 있는데도.
    값은 파이썬이 적은 SQL 리터럴이라 손으로 가르지 않고 메모리 SQLite 에 그대로 넣어 읽는다.
    """
    out: dict[int, tuple[str, str, str, str]] = {}
    conn = sqlite3.connect(":memory:")
    try:
        for _t, sql in 파일_종목:
            m = re.search(r"^INSERT INTO\s+stocks\s*\(([^)]*)\)", sql, re.I)
            if not m:
                continue
            열 = [c.strip() for c in m.group(1).split(",")]
            conn.execute("DROP TABLE IF EXISTS stocks")
            # 메모리 표 하나 — 운영 DB 질의가 아니라 파일 문장의 열 목록을 그대로 옮긴다 (test_sql_schema 상한 밖)
            conn.execute(
                re.sub(r"^INSERT INTO\s+stocks\s*(\([^)]*\)).*$", r"CREATE TABLE stocks \1", sql, flags=re.S | re.I)
            )
            try:
                conn.execute(sql)
            except sqlite3.Error as exc:
                # 문장이 깨진 경우(25.692 전에는 값 안의 `--` 줄을 주석으로 걷어 잘렸다). **한 행이라도 못
                # 읽으면 대조할 수 없다** (25.689, 교차검증) — 25.686 은 그 행만 빼, 빠진 번호를 가리키는 다른 표의 행이
                # 대조 없이 남의 종목에 붙었다. 부르는 쪽이 넣지 않고 멈춘다
                raise ValueError(f"백업의 종목 행을 읽지 못했습니다: {exc}") from exc
            for 행 in conn.execute("SELECT * FROM stocks").fetchall():
                값 = dict(zip(열, 행, strict=True))
                if 값.get("id") is None or not 값.get("ticker") or not 값.get("market"):
                    continue
                시장 = str(값["market"])
                국내 = 시장.upper().startswith(("KOSPI", "KOSDAQ", "KONEX"))
                나라 = str(값.get("country") or ("KR" if 국내 else "US"))
                통화 = str(값.get("currency") or ("KRW" if 나라 == "KR" else "USD"))
                out[int(값["id"])] = (str(값["ticker"]), 시장, 나라, 통화)
    finally:
        conn.close()
    return out


def 종목번호를_가진_표(rows: list[tuple[str, str]]) -> list[str]:
    """넣을 문장 가운데 `stock_id` 열을 가진 표 (25.677). 사람 입력 셋만 보면 `--only news` 가 대조 없이 들어갔다."""
    본것: list[str] = []
    for table, sql in rows:
        if table not in 본것 and table != "stocks" and re.search(r"\(([^)]*\bstock_id\b[^)]*)\)\s*VALUES", sql, re.I):
            본것.append(table)
    return 본것


종목키 = tuple[str, str, str, str]


def 종목_계획(client, 대응표: dict[int, 종목키]) -> tuple[list[str], list[tuple[int, 종목키]]]:
    """(어긋남, 만들 자리). 대상 DB 에 같은 번호가 **다른 종목**이거나 같은 종목이 **다른 번호**면 어긋남이다.
    번호도 종목도 없으면 그 번호 그대로 종목 자리를 만든다 — 빈 새 DB(25.3 절차)에 되살린 뒤 마스터를 받으면
    (코드, 시장)으로 그 자리에 덮여 번호가 이어진다 (25.677, 교차검증: 25.676 은 빈 DB 를 전부 어긋남으로 막았다).
    """
    있는것 = {int(r[0]): (str(r[1]), str(r[2])) for r in client.execute("SELECT id, ticker, market FROM stocks").rows}
    번호_of = {key: sid for sid, key in 있는것.items()}
    어긋남: list[str] = []
    만들것: list[tuple[int, 종목키]] = []
    for sid in sorted(대응표):
        키 = 대응표[sid][:2]
        if sid in 있는것:
            # 같은 번호·같은 코드인데 시장만 다르면 거래소를 옮긴 것이다(`universe.market_move_statements` 가 번호를
            # 지킨 채 시장을 바꾼다) — 어긋남이 아니다 (25.679, 교차검증)
            if 있는것[sid] != 키 and 있는것[sid][0] != 키[0]:
                어긋남.append(f"{sid}번: 백업 {키} / 대상 {있는것[sid]}")
        elif 키 in 번호_of:
            어긋남.append(f"{키}: 백업 {sid}번 / 대상 {번호_of[키]}번")
        else:
            만들것.append((sid, 대응표[sid]))
    return 어긋남, 만들것


def 행수_대조(path: Path, rows: list[tuple[str, str]]) -> list[str]:
    """백업이 적어 둔 행 수와 **읽어 낸 수**를 대 본다 (docs/infra.md 25.119).

    파서가 조용히 빠뜨리는 것을 잡는 유일한 방법이다 — 빠뜨린 행은 오류를 내지 않고,
    세어 본 수는 **빠뜨린 뒤의 수**라 저절로 아귀가 맞는다. 바깥에 적어 둔 수와 대야 한다.

    옛 백업에는 이 표시가 없다. **없는 것을 틀렸다고 하지 않는다** — 한 줄 알리고 넘어간다.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        적힌것 = 적힌_행수(handle.read())
    if not 적힌것:
        print("  (이 백업에는 행 수 표시가 없다 — 대조를 건너뛴다)")
        return []
    읽은것: dict[str, int] = {}
    for table, _sql in rows:
        읽은것[table] = 읽은것.get(table, 0) + 1
    return [
        f"{표}: 백업에 {적힌:,}행인데 {읽은것.get(표, 0):,}행만 읽었다"
        for 표, 적힌 in sorted(적힌것.items())
        if 읽은것.get(표, 0) != 적힌
    ]


def 표_경계로_자르기(
    rows: list[tuple[str, str]], max_rows: int
) -> tuple[list[tuple[str, str]], list[str], str | None]:
    """한도만큼 자르되 **표 가운데서 끊지 않는다** (2026-09-22, docs/infra.md 25.120).

    돌려주는 것은 (이번에 넣을 행, 다음에 넣을 표 이름들, 쪼갠 표 이름).

    예전에는 `rows[:max_rows]` 로 잘랐다. 그러면 표 하나가 **반쯤 들어간 상태**가 되는데,
    다음 실행에 넘길 `--only` 로는 그 상태를 말할 수 없다 — 그 표를 통째로 다시 보내야 한다.
    안내는 "나머지는 --only 로 이어서" 였지만 **어느 표가 남았는지도 안 알려 줬다.**

    표 경계에서 자르면 다음 실행이 정확히 이어진다. 표 **하나**가 한도보다 크면 어쩔 수 없이
    가운데서 자르고 **그 사실을 말한다** — 말없이 반쪽을 남기지 않는다.
    """
    끝 = 0
    표시작 = 0
    현재표 = rows[0][0] if rows else None
    for i, (table, _sql) in enumerate(rows):
        if table != 현재표:
            if i > max_rows:
                break
            끝 = i
            표시작 = i
            현재표 = table
    else:
        if len(rows) <= max_rows:
            return rows, [], None
    if 끝 == 0:  # 첫 표 하나가 이미 한도보다 크다
        쪼갠표 = rows[0][0]
        남은표 = _남은표(rows[max_rows:])
        return rows[:max_rows], 남은표, 쪼갠표
    _ = 표시작
    return rows[:끝], _남은표(rows[끝:]), None


def _남은표(rows: list[tuple[str, str]]) -> list[str]:
    """남은 행에 나오는 표 이름 (파일 순서 그대로). 외래키 순서를 지키려면 순서가 중요하다."""
    본것: list[str] = []
    for table, _sql in rows:
        if table not in 본것:
            본것.append(table)
    return 본것


#: D1 하루 쓰기에서 남겨 둘 몫. 되살리는 날에도 일일 배치·웹 크론이 돈다
#: **어림이다**(infra 25.8·25.26). 넉넉히 잡는 쪽이 맞다 `[확인필요: 실측]`
예산여유 = 20_000


def _예산만큼(client, rows: list[tuple[str, str]], max_rows: int | None) -> list[tuple[str, str]]:
    """D1 이면 **남은 하루 쓰기**를 보고 더 자른다 (2026-09-22, docs/infra.md 25.120).

    이 스크립트의 설명문은 "D1 은 하루 쓰기 10만 행 제한이 있다. `--max-rows` 로 나눠 넣고"
    라고 적어 두고 **그 수를 어디서도 보지 않았다.** 사람이 어림해서 넘겨야 했다.
    25.117 과 같은 모양이다 — 세는 장치는 있는데 **보는 쪽이 없다.**

    Turso 면 아무것도 안 한다(월 단위라 하루로 자를 값이 아니다). 못 읽으면 막지 않는다 —
    되살리는 일을 막는 것이 더 나쁘다(`remaining_d1_daily_writes` 와 같은 판단).
    """
    from batch.core import client as backend

    if backend.resolved_backend() != backend.D1:
        return rows
    전체남은 = db.remaining_d1_daily_writes(client)
    남은 = 전체남은 - 예산여유
    print(f"  D1 남은 하루 쓰기 {전체남은:,}행 (여유 {예산여유:,} 빼면 {max(0, 남은):,})")
    if 남은 <= 0:
        print("  오늘 D1 쓰기 예산이 없다. 내일 이어서 한다")
        return []
    if len(rows) <= 남은:
        return rows
    자른것, 남은표, 쪼갠표 = 표_경계로_자르기(rows, 남은)
    print(f"  → 예산에 맞춰 {len(자른것):,}행만 넣는다")
    if 쪼갠표:
        print(f"  주의: {쪼갠표} 하나가 예산을 넘어 표 가운데서 잘랐다")
    if 남은표:
        print(f"  다음 실행: --only {','.join(남은표)}")
    _ = max_rows
    return 자른것


def run(
    path: Path,
    only: set[str] | None,
    max_rows: int | None,
    dry_run: bool,
    on_conflict: str = "skip",
    trust_stock_ids: bool = False,
) -> int:
    try:
        rows = statements(path)
        어긋남 = 행수_대조(path, rows)
    except ValueError as exc:
        print(f"{exc}. **넣지 않습니다** (docs/infra.md 25.697)")
        return 1
    if 어긋남:
        print("백업을 온전히 읽지 못했습니다. **넣지 않습니다** — 반쪽만 되살리면 더 나쁩니다:")
        for 줄 in 어긋남:
            print(f"  {줄}")
        return 1
    # 파일의 종목 행은 `--only` 로 거르기 전에 따로 둔다 — 대조가 빈 번호를 채울 때 이름 없는 자리 대신 쓴다 (25.681)
    파일_종목 = [(table, sql) for table, sql in rows if table == "stocks"]
    if only:
        rows = [(table, sql) for table, sql in rows if table in only]
    counts: dict[str, int] = {}
    for table, _sql in rows:
        counts[table] = counts.get(table, 0) + 1

    print(f"{path.name}: 넣을 행 {len(rows):,} (표 {len(counts)}개)")
    for table, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {table:24s} {n:>9,}")
    if max_rows is not None and len(rows) > max_rows:
        rows, 남은표, 쪼갠표 = 표_경계로_자르기(rows, max_rows)
        print(f"  → {len(rows):,}행까지만 넣는다 (한도 {max_rows:,})")
        if 쪼갠표:
            print(
                f"  주의: {쪼갠표} 하나가 한도를 넘어 **표 가운데서** 잘랐다."
                " 다음 실행은 그 표를 통째로 다시 보낸다 (--on-conflict skip 이라 값은 안 틀린다)"
            )
        if 남은표:
            print(f"  다음 실행: --only {','.join(남은표)}")
    if dry_run:
        print("시험 실행이라 넣지 않았습니다")
        return 0

    client = TursoClient()
    try:
        db.apply_migrations(client)
        # **종목 번호가 같은 종목인지 먼저 본다** (docs/infra.md 25.676·25.677, 감사·교차검증). 필수 백업에는 종목
        # 마스터가 없어, 마스터를 새로 받은 DB 에 되살리면 종목 번호를 가진 표가 다른 종목에 조용히 붙었다.
        대상표 = 종목번호를_가진_표(rows)
        # **파일에 종목 행이 있어도 대조한다** (25.681, 교차검증). 25.677·25.679 는 "번호 그대로 들어간다" 고 보고
        # 건너뛰었는데, 대상에 마스터가 있으면 종목 행은 INSERT OR IGNORE 로 무시되고 다른 표만 옛 번호로 남의 종목에
        # 붙었다
        if 대상표:
            try:
                대응표 = 종목_대응표(path) or 파일_대응표(파일_종목)
            except ValueError as exc:
                print(f"{exc}. 종목 번호를 대조할 수 없어 **넣지 않습니다** (docs/infra.md 25.689)")
                return 1
            if not 대응표:
                if not trust_stock_ids:
                    print("종목 대응표가 없는 옛 백업이라 번호가 같은 종목인지 확인할 수 없습니다. **넣지 않습니다** —"
                          " 대상 DB 의 종목 번호가 원본과 같다고 확신하면(원본을 되살린 DB 등) trust_stock_ids 를 켜고"
                          " 다시 돌리세요")
                    return 1
            else:
                어긋남, 만들것 = 종목_계획(client, 대응표)
                # 대응표가 있으면 trust 로도 넘기지 않는다 (25.679, 교차검증) — 확인된 어긋남을 넘기면 25.676 이
                # 막으려던
                # "다른 종목에 붙음" 이 그대로 일어난다. trust 는 대응표 없는 옛 백업 전용이다
                if 어긋남:
                    print(f"대상 DB 의 종목 번호가 백업과 다릅니다({len(어긋남)}건). **넣지 않습니다** — 빈 새 DB 에"
                          " 되살리고 종목 마스터는 그 뒤에 받으세요(docs/infra.md 25.3):")
                    for 줄 in 어긋남[:20]:
                        print(f"  {줄}")
                    return 1
                if 만들것:
                    # 파일에 그 번호의 **실제 종목 행**이 있으면 그것을 넣는다(업종·상장일·상태가 산다) — 없으면 이름
                    # 없는 자리. 25.679 는 종목 행을 통째로 앞에 붙여 대조를 끄고 `--max-rows` 를 먹었다 (25.681,
                    # 교차검증)
                    파일_행 = {int(m.group(1)): sql for _t, sql in 파일_종목 if (m := _첫값.search(sql))}
                    지금 = db.now_iso()
                    문장들: list[tuple[str, list[Any]]] = [
                        (with_conflict(파일_행[sid], "skip"), []) if sid in 파일_행 else
                        ("INSERT OR IGNORE INTO stocks (id, ticker, market, country, currency, status, source,"
                         " fetched_at) VALUES (?, ?, ?, ?, ?, 'active', 'restore_backup', ?)", [sid, *키, 지금])
                        for sid, 키 in 만들것
                    ]  # fmt: skip
                    for start in range(0, len(문장들), CHUNK):
                        client.batch(문장들[start : start + CHUNK])
                    실제 = sum(1 for sid, _ in 만들것 if sid in 파일_행)
                    print(f"  종목 {len(만들것):,}개를 백업 번호 그대로 넣었다(파일의 종목 행 {실제:,}, 이름 없는 자리"
                          f" {len(만들것) - 실제:,}) — 자리는 마스터 수집이 채운다")
        rows = _예산만큼(client, rows, max_rows)
        written = 0
        for start in range(0, len(rows), CHUNK):
            chunk = rows[start : start + CHUNK]
            client.batch([(with_conflict(sql, on_conflict), []) for _table, sql in chunk])
            written += len(chunk)
            if written % 5_000 == 0:
                print(f"  {written:,}행")
        print(f"되살린 행 {written:,}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="백업을 지금 DB 에 되살린다")
    parser.add_argument("path", type=Path)
    parser.add_argument("--only", help="표 이름을 쉼표로 (비우면 전부)")
    parser.add_argument("--max-rows", type=int, dest="max_rows", help="이번에 넣을 최대 행 수")
    parser.add_argument("--dry-run", action="store_true", help="무엇이 들어가는지만 본다")
    parser.add_argument(
        "--on-conflict", dest="on_conflict", choices=sorted(CONFLICT_PREFIX), default="skip",
        help="이미 있는 행 처리 (기본 skip)",
    )  # fmt: skip
    parser.add_argument(
        "--trust-stock-ids", dest="trust_stock_ids", action="store_true",
        help="대응표가 없는 옛 백업만: 종목 번호 대조를 건너뛴다(원본과 번호가 같은 DB 에만). 대응표가 있으면 무시",
    )  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    only = {t.strip() for t in args.only.split(",") if t.strip()} if args.only else None
    return run(args.path, only, args.max_rows, args.dry_run, args.on_conflict, args.trust_stock_ids)


if __name__ == "__main__":
    sys.exit(main())
