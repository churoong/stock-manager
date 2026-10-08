"""운영 DB 백업 (docs/backup.md, Step 18).

왜 있나: Turso 무료 플랜에는 자동 백업이 없다. 사람이 손으로 넣은 매매·배당·설정은
**어디서도 다시 만들 수 없다.** 시세·재무는 다시 받을 수 있지만 국내 3년 백필은
하루가 걸렸다(docs/infra.md 15절). 그래서 두 층으로 나눠 받는다.

  essential  다시 만들 수 없는 것. 작고 자주 받는다
  full       전부. 크고 가끔 받는다 (Actions 아티팩트 보관 용량을 아낀다)

산출물은 **gzip 으로 압축한 SQL 파일** 하나다. sqlite3 로 그대로 되살릴 수 있다.

  실행
    python scripts/backup_db.py --scope essential --out backup/
    python scripts/backup_db.py --scope full --out backup/

  되살리기
    gunzip -c stock-manager-essential-2026-09-17.sql.gz | sqlite3 restored.db

**덤프는 읽기만 한다.** `run()` 은 DB 에 아무것도 쓰지 않는다(테스트로 고정).

`--record-run` 은 `batch_runs` 에 **실행 기록만** 연다 (docs/infra.md 25.167).
그 전에는 백업이 **아무 흔적도 남기지 않아서**, 주 1회 백업이 몇 주째 안 돌아도
알 길이 없었다 — 잃으면 끝인 자료를 지키는 유일한 장치인데. 자료 표는 건드리지 않는다.

**닫는 것은 `--finish-run <id>` 이고, 워크플로가 아티팩트를 올린 뒤에 부른다.**
덤프가 끝난 것과 내려받을 파일이 생긴 것은 다른 일이다.
"""

from __future__ import annotations

import argparse
import gzip
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import db  # noqa: E402
from batch.core.client import TursoClient  # noqa: E402
from batch.core.turso import ReadCounter  # noqa: E402

#: `batch_runs.job_name`. `/status` 의 데이터 신선도와 최근 배치가 이 이름으로 찾는다 —
#: **웹의 `health.FRESHNESS` 와 같은 글자여야 한다** (테스트가 대 본다)
JOB_NAME = "backup"

log = logging.getLogger("backup")

# 다시 만들 수 없는 표. 사람이 넣었거나, 넣은 시점이 있어야 뜻이 있는 것들.
#
#   trades·dividend_receipts  사용자 입력 원본 (CLAUDE.md "trades 는 사용자 입력값만")
#   watchlist·settings        사용자가 정한 것
#   financial_snapshots       발표일 기준 시점 스냅샷. 지나간 시점은 다시 받을 수 없다 (point-in-time)
#   alerts·health_alerts      언제 무엇을 알렸는지의 기록
#   batch_runs                운영 이력. 무엇이 언제 돌았나
#   news·sentiment_scores     원문을 저장하지 않으므로 지나간 기사는 되받을 수 없다
#
# **`scripts/move_user_data.py` 가 옮기는 표는 전부 여기 있어야 한다** (2026-09-22,
# docs/infra.md 25.118). 그쪽은 "다른 DB 로 옮겨야 할 만큼 사람 손이 닿은 것" 을 고르고
# 여기는 "잃으면 안 되는 것" 을 고른다 — 같은 뜻인데 목록이 둘이라 갈라졌다.
# `screener_presets` 가 그쪽에만 있었다. `tests/test_backup.py` 가 둘을 대조한다.
ESSENTIAL_TABLES = [
    "trades",
    "dividend_receipts",
    "watchlist",
    # 국내 뉴스 매칭 별칭 — 사람이 관리하는 표(25.840). 첫 목록은 마이그레이션이 다시 넣지만
    # 사람이 더한 줄은 여기에만 있다 (25.848)
    "stock_aliases",
    "settings",
    "screener_presets",
    "financial_snapshots",
    "alerts",
    "health_alerts",
    "batch_runs",
    "news",
    "article_sentiments",
    "sentiment_scores",
    "universe_members",
    "schema_migrations",
]

#: **웹이 쓰는데 백업에 없는 표와 그 사유** (2026-09-22, docs/infra.md 25.130).
#:
#: 사람 손이 닿는 것은 거의 다 웹으로 들어온다 — 매매·배당·관심종목·설정·스크리너 조건.
#: 그래서 "웹이 쓰는 표" 를 훑으면 **잃으면 안 되는 것의 목록**이 거의 그대로 나온다.
#: 2026-09-22 에 `screener_presets` 가 빠져 있던 것도(25.118) 이 대조를 했으면 바로 걸렸다.
#:
#: 여기 있는 표는 **잃어도 되는 이유가 있다.** 사유 없는 예외를 두지 않는다.
#: `tests/test_backup_covers_web.py` 가 이 표와 `ESSENTIAL_TABLES` 를 웹 소스와 대 본다.
백업_제외_사유 = {
    "analysis_requests": "\"지금 분석\" 요청의 진행 기록(25.1018). 잃으면 단추의 15분 잠금이 풀릴 뿐"
    " — 분석 결과는 stock_verdicts, 관심 종목은 watchlist 에 있다",
    "api_usage": "한도 카운터. 날/달마다 리셋되는 값이라 되살릴 뜻이 없다."
    " 다만 되살린 날은 그날 카운터가 0 에서 시작하므로 D1 하루 쓰기 예산을"
    " 실제보다 넉넉히 본다 [확인필요: 되살리기 직후 하루는 큰 수집을 미룰지]",
    "api_tokens": "바깥 API 접근토큰(KIS, 25.983). 비밀값이라 백업 파일에 두지 않는다."
    " 유효 24시간이고 잃으면 다음 장중 호출이 새로 받는다",
    "cron_heartbeats": "마지막 호출 기록. 다음 호출이 곧 다시 채운다",
    "login_attempts": "로그인 시도 제한용. 스스로 오래된 행을 지우고(loginGuard),"
    " 잃으면 잠금이 풀릴 뿐 데이터가 사라지지 않는다",
    "news_fetch_log": "종목별 마지막 수집 시각. 다음 수집이 다시 채운다",
    "sell_flags": "배치가 매일 다시 만드는 파생 표. **다만 `dismissed_at`(사용자가 확인한 시각)"
    " 은 사람 입력이라 되살릴 수 없다** — 알고 두는 것이다(docs/backup.md 3장, infra 25.118)."
    " 표 전체를 담으면 지나간 판정까지 되살아나 화면이 옛 플래그를 켠다",
    "trade_lots": "`trades` 에서 FIFO 로 다시 만든다(`jobs/portfolio`). 원본이 아니다",
    "trade_reviews": "`trades` 와 체결 묶음에서 다시 만든다(`jobs/portfolio`)."
    " 웹은 매수를 지울 때 외래키 때문에 비우기만 한다(25.225)",
}

#: **`settings` 에서 빼는 열쇠** (2026-09-23, docs/infra.md 25.155).
#:
#: `settings` 는 대부분 사용자 값이지만, 몇 줄은 **지금 이 DB 가 어떤 상태인지**를 적는
#: 운영 표시다. 그것을 백업에 담으면 되살릴 때 **옛 운영 상태가 새 DB 에 심긴다** —
#: `api_usage`·`cron_heartbeats` 를 통째로 뺀 것과 같은 이유다.
#:
#: 가장 나쁜 것은 복귀 표시다. `auto` 백엔드 판정이 그 한 줄을 보고 Turso 냐 D1 이냐를
#: 정한다(25.12). 옛 백업을 `--on-conflict replace` 로 되살리면 **엉뚱한 DB 를 고른다.**
#:
#: 이름은 코드가 정의처다. `tests/test_backup.py` 가 그 상수들과 대 본다 — 이름이 바뀌면
#: 여기가 낡고, 낡은 줄 모른 채 다시 백업에 들어온다 (25.0 「테스트가 자료를 손으로 베낀다」).
설정_제외_사유 = {
    "db_return_done_at": "복귀 표시. `auto` 판정이 이 한 줄로 Turso/D1 을 고른다"
    " (batch/core/client.RETURN_MARKER · web/lib/db.RETURN_MARKER)",
    "db_return_requested_at": "복귀를 깨운 기록. 다음 감시가 다시 쓴다"
    " (web/lib/tursoWatch.REQUESTED_KEY)",
    "db_return_fill_done_at": "복귀 뒤 메우기를 마쳤다는 표시. 되살리면 다음 복귀의 메우기가 실패해도"
    " 끝난 것으로 보인다 (scripts/turso_return.FILL_DONE_MARKER)",
    "db_return_fill_plan": "복귀 때 잰 빠진 구간과 재시도 수. 그 복귀에만 뜻이 있다"
    " (scripts/turso_return.FILL_PLAN_KEY)",
    "login_global_lock_alerted_at": "잠금 알림을 한 번만 보내려고 둔 표시."
    " 되살리면 진짜 잠긴 날의 알림이 막힌다 (web/lib/loginGuard)",
}

# 한 번에 읽어 오는 행 수. Turso HTTP 응답이 지나치게 커지지 않게 나눠 읽는다.
# 2,000 은 실측 없이 고른 값이다 [확인필요: 더 키워도 되는지]
PAGE = 2000
#: 이어 읽기용 rowid 열 이름 — 백업에는 쓰지 않는다(첫 열을 버린다). 표의 진짜 열과 겹치지 않게
ROWID_COL = "__backup_rowid"


def 외래키_의존() -> dict[str, set[str]]:
    """표 → 그 표가 **먼저 있어야 하는** 표들. `migrations/` 가 단일 정의처다 (CLAUDE.md).

    라이브 DB 에 `PRAGMA foreign_key_list` 를 묻지 않는다 — D1 이 그 PRAGMA 를 주는지
    확인되지 않았고(`[확인필요]`), 백업은 **어느 백엔드에서도 같은 순서**로 나와야 한다.
    """
    import re

    from batch.core import db as core_db

    의존: dict[str, set[str]] = {}
    for 길 in core_db.migration_files():
        본문 = 길.read_text(encoding="utf-8")
        for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", 본문, re.S):
            표 = m.group(1)
            참조 = {r.group(1) for r in re.finditer(r"REFERENCES\s+(\w+)\s*\(", m.group(2))}
            의존.setdefault(표, set()).update(참조 - {표})
    return 의존


def 의존_순서(tables: list[str]) -> list[str]:
    """참조되는 표가 **먼저** 오도록 줄 세운다 (2026-09-22, docs/infra.md 25.121).

    D1 은 외래키를 실제로 강제한다(Turso 에서는 안 걸렸다). `article_sentiments` 를
    `news` 보다 먼저 넣으면 되살리기가 **한참 쓰다가** 중간에 깨진다.

    같은 층은 **넘겨받은 순서**를 지킨다 — 뜻이 있는 순서(`ESSENTIAL_TABLES`)를 흩지 않는다.
    고리(서로 참조)가 있으면 남은 것을 원래 순서로 붙인다. 고리를 지어내 풀지 않는다.
    """
    의존 = 외래키_의존()
    남은 = list(tables)
    놓인: list[str] = []
    while 남은:
        갈_수_있는 = [t for t in 남은 if not (의존.get(t, set()) & set(남은) - {t})]
        if not 갈_수_있는:  # 고리. 원래 순서로 남긴다
            놓인.extend(남은)
            break
        놓인.extend(갈_수_있는)
        남은 = [t for t in 남은 if t not in 갈_수_있는]
    return 놓인


def 순서_어긋남(tables: list[str]) -> list[str]:
    """이 순서로 넣으면 외래키에 걸리는 자리. 없으면 빈 목록.

    **목록 밖의 표는 따지지 않는다.** `ESSENTIAL_TABLES` 의 거의 모든 표가 `stocks` 를
    참조하는데 `stocks` 는 필수 백업에 없다 — 종목 마스터는 수집으로 다시 만들고,
    되살리기는 그것이 이미 있는 DB 에 넣는다(docs/backup.md). 여기서 보는 것은
    **같은 파일 안에서의 순서**다.
    """
    의존 = 외래키_의존()
    본것: set[str] = set()
    어긋남: list[str] = []
    for 표 in tables:
        늦은것 = sorted((의존.get(표, set()) & set(tables)) - 본것 - {표})
        if 늦은것:
            어긋남.append(f"{표} 가 {', '.join(늦은것)} 보다 먼저 온다")
        본것.add(표)
    return 어긋남


def list_tables(client: TursoClient) -> list[str]:
    rs = client.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        # D1 내부 표(`_cf_KV` 등)는 사용자 접근이 막혀 있고 WITHOUT ROWID 라, 전체 백업이 그 표에서 멈춘다 (25.854,
        # 교차검증)
        " AND name NOT LIKE '\\_cf\\_%' ESCAPE '\\' ORDER BY name"
    )
    return [str(r[0]) for r in rs.rows]


def create_sql(client: TursoClient, table: str) -> str | None:
    rs = client.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", [table])
    value = rs.scalar()
    return str(value) if value else None


#: 표마다 몇 행을 썼는지 적는 주석. 되살리는 쪽(`scripts/restore_backup.py`)이 이것과 대 본다.
#: **두 스크립트를 잇는 글자라 한 곳에서 온다** (docs/infra.md 25.113 의 교훈)
ROWS_MARK = "-- rows"

#: 종목 번호 → (종목코드, 시장, 나라, 통화) 대응표 (docs/infra.md 25.676·25.677). 필수 백업에는 `stocks` 가 없어
#: 매매·배당·관심종목·재무 스냅샷·뉴스의 `stock_id` 가 원본 번호 그대로다 — 종목 마스터를 새로 받은 DB(번호가 다르다)에
#: 되살리면 **다른 종목에 조용히 붙었다.** 되살리는 쪽이 이 표로 대상 DB 의 번호를 대 보고, 빈 번호는 같은 번호로
#: 종목 자리를 만든다
STOCK_KEY_MARK = "-- stock_key"


def stock_keys(client: TursoClient, available: set[str]) -> dict[int, tuple[str, str, str, str]]:
    """전 종목의 (종목코드, 시장, 나라, 통화). 종목 마스터는 수천 행이라 통째로 읽는다(질의는 고정 글)."""
    if "stocks" not in available:
        return {}
    return {
        int(r[0]): (str(r[1]), str(r[2]), str(r[3]), str(r[4]))
        for r in client.execute("SELECT id, ticker, market, country, currency FROM stocks").rows
    }


def quote(value: object) -> str:
    """SQL 리터럴. 따옴표만 처리하면 되고 숫자·NULL 은 그대로 둔다."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def dump_table(client: TursoClient, table: str, out) -> int:
    """한 표를 INSERT 문으로 쓴다. 행 수를 돌려준다."""
    ddl = create_sql(client, table)
    if not ddl:
        return 0
    out.write(f"\n-- {table}\n")
    out.write(f"DROP TABLE IF EXISTS {table};\n")
    out.write(ddl.rstrip().rstrip(";") + ";\n")

    # 운영 표시는 담지 않는다 (docs/infra.md 25.155). 표를 통째로 빼는 것과 같은 뜻인데,
    # `settings` 는 대부분 사용자 값이라 **줄 단위**로 뺀다
    거르기 = ""
    if table == "settings" and 설정_제외_사유:
        자리 = ", ".join(["?"] * len(설정_제외_사유))
        거르기 = f" AND key NOT IN ({자리})"
    인자 = sorted(설정_제외_사유) if 거르기 else []

    # **rowid 로 이어 읽는다** (docs/infra.md 25.852, 교차검증). `LIMIT … OFFSET n` 은 건너뛴 n 행도 훑어, 행이 N 개인
    # 표를
    # 읽는 데 약 N²/(2·PAGE) 행이 들었다 — 시세 표 하나로 D1 하루 읽기 한도(500만)를 넘길 수 있었다. 모든 표가 rowid
    # 표다
    # (WITHOUT ROWID 없음 — `test_backup.py` 가 본다)
    total = 0
    마지막 = None
    while True:
        이어서 = "rowid > ?" if 마지막 is not None else "1 = 1"
        rs = client.execute(
            f"SELECT rowid AS {ROWID_COL}, * FROM {table} WHERE {이어서}{거르기} ORDER BY rowid LIMIT {PAGE}",
            ([마지막] if 마지막 is not None else []) + 인자 or None,
        )
        if not rs.rows:
            break
        columns = ", ".join(rs.columns[1:])
        for row in rs.rows:
            values = ", ".join(quote(v) for v in row[1:])
            out.write(f"INSERT INTO {table} ({columns}) VALUES ({values});\n")
        total += len(rs.rows)
        마지막 = rs.rows[-1][0]
        if len(rs.rows) < PAGE:
            break
    # **몇 줄을 썼는지 적어 둔다** (2026-09-22, docs/infra.md 25.119).
    # 되살리는 쪽이 읽어 낸 수와 대 본다 — 파서가 조용히 빠뜨리는 것을 잡는 유일한 방법이다.
    # `--` 주석이라 `sqlite3` 로 그대로 흘려 넣어도 아무 일이 없다
    out.write(f"{ROWS_MARK} {table} {total}\n")
    return total


def run(scope: str, out_dir: Path) -> int:
    client = TursoClient()
    try:
        available = set(list_tables(client))
        if scope == "essential":
            tables = [t for t in ESSENTIAL_TABLES if t in available]
            missing = [t for t in ESSENTIAL_TABLES if t not in available]
            for t in missing:
                log.warning("표가 없어 건너뜁니다: %s", t)
        else:
            # **이름 순이 아니라 의존 순이다** (25.121). 되살릴 때 외래키가 걸린다
            tables = 의존_순서(sorted(available))

        out_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(UTC).date().isoformat()
        path = out_dir / f"stock-manager-{scope}-{today}.sql.gz"

        counts: dict[str, int] = {}
        with gzip.open(path, "wt", encoding="utf-8") as out:
            out.write(f"-- stock-manager 백업 ({scope})\n")
            out.write(f"-- 만든 시각 {datetime.now(UTC).isoformat()}\n")
            out.write("-- 되살리기: gunzip -c <파일> | sqlite3 restored.db\n")
            for sid, (ticker, market, country, currency) in sorted(stock_keys(client, available).items()):
                out.write(f"{STOCK_KEY_MARK} {sid}\t{ticker}\t{market}\t{country}\t{currency}\n")
            out.write("PRAGMA foreign_keys = OFF;\nBEGIN;\n")
            for table in tables:
                counts[table] = dump_table(client, table, out)
            out.write("COMMIT;\n")

        size_mb = path.stat().st_size / 1_048_576
        print(f"{path.name}  {size_mb:.1f}MB  표 {len(tables)}개, 행 {sum(counts.values()):,}")
        for table, n in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {table:24s} {n:>9,}")
        return 0
    finally:
        client.close()


def run_recorded(scope: str, out_dir: Path) -> int:
    """실행 기록을 **열어 두고** 덤프한다 (docs/infra.md 25.167).

    **성공은 여기서 적지 않는다.** 덤프가 끝난 것과 **내려받을 파일이 생긴 것**은
    다른 일이다 — 아티팩트 업로드가 실패하면 파일이 없는데 기록만 성공으로 남는다.
    그러면 신선도 칸이 초록인 채로 백업이 없다. 가장 나쁜 거짓말이다.

    그래서 워크플로가 **업로드가 끝난 뒤에** `--finish-run` 으로 닫는다. 그 전에
    죽으면 기록은 `running` 으로 남고, 신선도 칸(`status = 'success'`)은 낡은 채다 —
    그것이 맞는 신호다.

    실행 번호는 `$GITHUB_OUTPUT` 으로 다음 스텝에 넘긴다.
    """
    client = TursoClient()
    try:
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=None, trade_date=None)
    finally:
        client.close()
    _내보내기("run_id", str(run_id))

    시작 = ReadCounter.process_total
    try:
        코드 = run(scope, out_dir)
    except Exception as exc:
        마무리(run_id, "failed", str(exc))
        raise
    # 덤프가 읽은 행 수 — 닫는 스텝(`--finish-run`, 다른 프로세스)이 실행 기록에 붙인다 (docs/infra.md 25.885·25.887)
    _내보내기("rows_read", str(ReadCounter.process_total - 시작))
    if 코드 != 0:
        마무리(run_id, "failed", f"덤프 종료코드 {코드}")
    return 코드


def full_done_this_month(client, now: datetime | None = None) -> bool | None:
    """이번 달(UTC) 전체 백업이 성공했나. 못 읽으면 None (docs/infra.md 25.887).

    매월 8일 전체 백업은 **1일 것이 실패했을 때를 위한 예비**다(25.872). 1일 것이 성공한 달에도 돌아 시세까지 모든 표를
    한 번 더 읽었다 — 2026-10-02 실측 약 780만 행, Turso 월 읽기 한도(5억)의 1.6%.
    """
    now = now or datetime.now(UTC)
    try:
        rs = client.execute(
            "SELECT COUNT(*) FROM batch_runs WHERE job_name = ? AND status = 'success' AND started_at >= ?"
            " AND json_extract(step_log, '$.scope') = 'full'",
            [JOB_NAME, now.strftime("%Y-%m-01")],
        )
    except Exception as error:  # noqa: BLE001 — 모르면 None. 부르는 쪽이 예비를 그대로 돌린다(백업이 먼저다)
        print(f"이번 달 전체 백업 기록을 읽지 못했다: {error}")
        return None
    return int(rs.scalar() or 0) > 0


def _내보내기(key: str, value: str) -> None:
    """다음 스텝이 읽을 값. Actions 밖에서는 화면에만 적는다."""
    길 = os.environ.get("GITHUB_OUTPUT")
    if 길:
        with open(길, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")
    print(f"{key}={value}")


def 마무리(run_id: int, status: str, error: str | None, step_log: dict | None = None) -> None:
    client = TursoClient()
    try:
        db.finish_batch_run(client, run_id, status=status, step_log=step_log, error_text=error)
    finally:
        client.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="운영 DB 백업 (덤프는 읽기 전용)")
    parser.add_argument("--scope", choices=["essential", "full"], default="essential")
    parser.add_argument("--out", default="backup", help="파일을 쓸 디렉터리")
    parser.add_argument(
        "--record-run",
        action="store_true",
        help="batch_runs 에 실행 기록을 연다 (자료 표는 건드리지 않는다). 닫는 것은 --finish-run",
    )
    parser.add_argument(
        "--finish-run",
        type=int,
        metavar="RUN_ID",
        help="아티팩트까지 올라간 뒤 그 실행 기록을 success 로 닫는다",
    )
    parser.add_argument("--rows-read", type=int, help="--finish-run 과 함께: 덤프가 읽은 행 수(덤프 스텝 출력)")
    parser.add_argument(
        "--full-done-this-month",
        action="store_true",
        help="이번 달 전체 백업이 이미 성공했으면 0, 아니거나 모르면 1 (8일 예비가 건너뛸지)",
    )
    args = parser.parse_args()
    if args.full_done_this_month:
        client = TursoClient()
        try:
            done = full_done_this_month(client)
        finally:
            client.close()
        print(f"이번 달 전체 백업: {'있음 — 예비는 건너뜁니다' if done else '없음(또는 모름) — 예비를 돌립니다'}")
        return 0 if done else 1
    if args.finish_run:
        로그 = {"scope": args.scope, "artifact": True}
        if args.rows_read is not None:
            로그[db.ROWS_READ_KEY] = args.rows_read
        마무리(args.finish_run, "success", None, 로그)
        print(f"백업 실행 기록 {args.finish_run} 을 success 로 닫았습니다")
        return 0
    if args.record_run:
        return run_recorded(args.scope, Path(args.out))
    return run(args.scope, Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
