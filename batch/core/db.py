"""스키마 적용과 테이블 접근.

Alembic 을 쓰지 않는다. Alembic 은 SQLAlchemy 방언을 요구하는데,
Turso 용 파이썬 드라이버가 낡아 동작하지 않는다(docs/infra.md 2번).
대신 번호가 붙은 SQL 파일을 순서대로 적용하고 적용 이력을 남긴다.
migrations/ 의 SQL 파일이 스키마의 단일 정의처다.

규칙
  - 적용된 마이그레이션은 다시 적용하지 않는다
  - 파일은 번호 순서로만 적용한다. 중간에 끼워 넣지 않는다
  - 이미 적용한 파일은 고치지 않는다. 새 파일을 추가한다
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from batch import config
from batch.core.turso import ReadCounter, TursoClient

log = logging.getLogger(__name__)

MIGRATIONS_DIR = config.ROOT / "migrations"


def 표가_없나(error: BaseException) -> bool:
    """**표가 아직 없어서** 실패한 것인가 (docs/infra.md 25.164).

    `except Exception: return {}` 는 "마이그레이션 전 DB 에는 이 표가 없다" 를 넘기려고
    붙였는데, **아무 실패나 다 삼킨다.** 한도에 걸려도, 인증이 끊겨도 빈 값이 되고
    배치는 아무 말 없이 계속 간다 — 그리고 그 빈 값이 **점수를 바꾼다**.

    웹의 `db.ifMissingTable` 과 같은 판정이다. 두 언어가 같은 것을 다르게 알면 안 된다.
    """
    return "no such table" in str(error).lower()


def get_setting_in_range(
    client: TursoClient, key: str, default: float | None
) -> tuple[float | None, str | None]:
    """설정을 읽고 **범위까지 본다** (docs/infra.md 25.170). (쓸 값, 경고).

    `get_setting` 과 같은 모양(클라이언트, 키, 기본값)으로 둔다 — `tests/
    test_settings_reachable.py` 가 "파이썬이 어떤 설정을 읽는가" 를 이 꼴로 훑기 때문이다.
    도우미로 감싸 키를 변수로 넘기면 **그 그물의 눈에서 사라진다.**

    범위는 `batch/core/settings_range` 에 있고, 그 표가 `web/lib/settings.ts` 와 같은지는
    `tests/test_settings_range.py` 가 대 본다.
    """
    from batch.core import settings_range

    # 행이 있는데 읽지 못하면(깨진 JSON·null) 그 말을 경고로 돌려준다 — 호출부가 이미 경고를 싣는 자리라 그대로 닿는다
    # (docs/infra.md 25.782, 교차검증: 25.778 이 `get_setting` 에만 달아 원화·비율 같은 단일 값 설정은 로그로만 남았다)
    못읽음: list[str] = []
    값, 말 = settings_range.범위_안(key, get_setting(client, key, default, 못읽음=못읽음), default)
    return 값, 말 or (못읽음[0] if 못읽음 else None)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ----------------------------------------------------------------------
# 마이그레이션
# ----------------------------------------------------------------------
def _split_statements(sql: str) -> list[str]:
    """SQL 파일을 문장 단위로 나눈다.

    주석을 먼저 걷어 내고 세미콜론으로 자른다. 이 프로젝트의 마이그레이션은
    트리거나 BEGIN 블록을 쓰지 않으므로 이 정도로 충분하다.
    """
    without_comments = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


#: 재무 기준 — **연결이 있으면 연결, 없으면 별도** (CLAUDE.md "연결 기준 우선", docs/infra.md 25.314).
#:
#: 종목마다 그 보고서 종류(`report_code`)의 **가장 늦은 사업연도**에 연결이 있으면 연결만, 없으면 별도만 고른다
#: (25.550, DART 감사). 예전에는 "한 해라도 연결이 있으면 연결" 이라, 자회사를 처분해 별도만 내게 된 회사가 영구히
#: 옛 연결 연도에 고정됐다 — 2026 년 점수·성장률·재무악화 플래그·PBR 이 3년 전 숫자였다. 국내는 연결 회사도 별도를
#: 함께 받아(`dart.parse_multi_response`) 옛 해의 별도가 있다. 반대 방향(별도→연결)의 한계는 25.314 그대로다
#: **한 종목 안에서는 기준을 섞지 않는다** — 해마다 섞으면 성장률이 뜻을 잃는다.
#: 예전에는 모든 곳이 `consolidated = 1` 이라 별도재무만 내는 회사(국내 유니버스 약 10%, 92/879)가
#: 재무가 없는 것으로 읽혀 밸류·퀄리티·성장 점수와 중기·장기 신호에서 통째로 빠졌다.
#:
#: **각 질의에 글자 그대로 적는다.** f-string 으로 끼우면 `tests/test_sql_schema.py` 가 그 질의를
#: 실제 스키마에 대 보지 못한다. 대신 `tests/test_financial_basis.py` 가 모든 자리가 이 글과 같은지 본다.
#: 별칭이 `f` 면 `FINANCIAL_BASIS_F`, 별칭 없이 표 이름이면 `FINANCIAL_BASIS_TABLE`, 시점 스냅샷은 `…_SNAPSHOTS`.
FINANCIAL_BASIS_F = (
    "f.consolidated = (SELECT fb.consolidated FROM financials fb"
    " WHERE fb.stock_id = f.stock_id AND fb.report_code = f.report_code"
    " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
)
FINANCIAL_BASIS_TABLE = (
    "financials.consolidated = (SELECT fb.consolidated FROM financials fb"
    " WHERE fb.stock_id = financials.stock_id AND fb.report_code = financials.report_code"
    " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
)
#: **기준일이 있는 질의용** (docs/infra.md 25.856, 7회차 검증 1). 위 둘은 기준을 **지금 DB 의 가장 늦은 사업연도**로
#: 고른다 —
#: 기준일 뒤에 처음 낸 연결 보고서가 기준을 연결로 바꾸면, 기준일까지의 행(별도)은 모두 걸러지고 그 종목은 재무가
#: 통째로 빈다.
#: 기준을 고를 때도 기준일까지 접수된 행만 본다. 자리표시자(`?` = 기준일)가 하나 더 붙는다 — 본 질의의 기준일보다
#: **앞** 자리다
FINANCIAL_BASIS_F_AS_OF = (
    "f.consolidated = (SELECT fb.consolidated FROM financials fb"
    " WHERE fb.stock_id = f.stock_id AND fb.report_code = f.report_code AND fb.report_date <= ?"
    " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
)
FINANCIAL_BASIS_TABLE_AS_OF = (
    "financials.consolidated = (SELECT fb.consolidated FROM financials fb"
    " WHERE fb.stock_id = financials.stock_id AND fb.report_code = financials.report_code AND fb.report_date <= ?"
    " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
)
FINANCIAL_BASIS_SNAPSHOTS = (
    "f.consolidated = (SELECT fb.consolidated FROM financial_snapshots fb"
    " WHERE fb.stock_id = f.stock_id AND fb.report_code = f.report_code"
    " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
)
#: 분기 계열(SUE)용 — **두 기준을 모두 읽고, 기준일마다 고른다** (docs/factors.md 12.2, infra 25.456·25.461).
#: 종목 전체의 MAX(consolidated) 로 고르면 미래 정보다 — 2025 에 처음 연결을 낸 회사가 2024 기준일에 별도 12분기를
#: 잃고, 연결이 끊긴 회사는 영구히 NULL 이 됐다(교차검증). 고르는 곳은 `quarterly_earnings.quarter_series`
FINANCIAL_BASIS_AT_CUTOFF = "f.consolidated AS consolidated_picked_at_cutoff"


def last_signal_calc_date(client: TursoClient, country: str, upto: str | None) -> str | None:
    """신호를 **마지막으로 계산한 날** (docs/infra.md 25.337, 웹 `lib/recommend.LAST_CALC_DATE` 와 같은 잣대).

    아침 리포트(`daily`)와 스트레스 테스트(`stress`, 25.359)가 함께 쓴다 — 같은 규칙을 두 곳에 적지 않는다.

    `signals` 에는 **걸린 신호만** 들어가서 그 표의 MAX 는 "마지막으로 무언가 걸린 날" 이다. 오늘 계산했는데
    한 건도 안 걸리면 MAX 가 어제로 남아 **어제 신호가 오늘 리포트 1부·2부에 금액까지 붙어** 나갔다.
    판정표(`signal_checks`)는 걸리지 않아도 전 종목에 남으므로 둘의 MAX 를 함께 본다. 그날 신호가 없으면
    행은 비고 1부가 "오늘 추천할 종목이 없습니다" 가 된다. 판정표가 없는 옛 DB 에서는 옛 잣대로 돌아간다.
    """
    # 끝을 늘 바인딩한다 — 글자를 이어 붙이면 `tests/test_sql_schema.py` 가 질의를 스키마에 대 보지 못한다
    until = upto or "9999-12-31"
    try:
        rs = client.execute(
            "SELECT MAX(d) FROM ("
            " SELECT MAX(sg.as_of_date) AS d FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
            " WHERE s.country = ? AND sg.as_of_date <= ? UNION ALL"
            " SELECT MAX(ck.as_of_date) AS d FROM signal_checks ck JOIN stocks s ON s.id = ck.stock_id"
            " WHERE s.country = ? AND ck.as_of_date <= ?)",
            [country, until, country, until],
        )
    except Exception as exc:  # noqa: BLE001 — 판정표가 아직 없는 DB(0034 전)
        if not 표가_없나(exc):
            raise
        rs = client.execute(
            "SELECT MAX(sg.as_of_date) FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
            " WHERE s.country = ? AND sg.as_of_date <= ?",
            [country, until],
        )
    value = rs.scalar()
    return str(value) if value else None


def latest_snapshot_sql() -> str:
    """그 나라의 최신 유니버스 스냅샷 날짜. 바인딩 인자로 나라 하나(KR/US)를 받는다.

    **나라마다 따로 잡아야 한다 (2026-09-17 발견).** 예전에는 나라 구분 없이
    `(SELECT MAX(snapshot_date) FROM universe_members)` 를 썼다. 국내는 09-16, 미국은
    09-15 처럼 스냅샷 날짜가 다르면 늦은 쪽(국내)만 남고 미국 편입 종목이 metrics·scores·
    signals 에서 통째로 사라진다. 휴장일이 다르고 수동 실행 시각도 달라 날짜는 자주 어긋난다.
    웹(web/lib/recommend.ts, web/lib/screener.ts)은 같은 이유로 이미 나라별로 잡는다.

    쓰는 쪽은 이 서브쿼리 자리에 나라 값을 바인딩한다.
    """
    return (
        "(SELECT MAX(um.snapshot_date) FROM universe_members um"
        " JOIN stocks su ON su.id = um.stock_id WHERE su.country = ?)"
    )


def snapshot_as_of_sql() -> str:
    """`latest_snapshot_sql()` 의 **시점판**. 인자는 (나라, 기준일) 둘이다.

    기준일 **이하**의 스냅샷 중 가장 늦은 것을 고른다. 과거 기준일로 다시 계산할 때
    오늘의 유니버스를 쓰면 **그때는 알 수 없던 편입 여부와 시가총액**을 쓰게 된다 —
    `universe_members.market_cap` 은 밸류 팩터 넷의 **분모**다 (docs/infra.md 25.97).

    두 함수를 따로 두는 이유: 시점을 볼 필요가 없는 자리(수집·유니버스 자신)가 많고,
    그쪽에 기준일을 억지로 넘기게 하면 "오늘" 을 적어 넣게 되어 뜻이 흐려진다.
    **고를 일이 있는 쪽만 이것을 쓴다.**
    """
    return (
        "(SELECT MAX(um.snapshot_date) FROM universe_members um"
        " JOIN stocks su ON su.id = um.stock_id"
        " WHERE su.country = ? AND um.snapshot_date <= ?)"
    )


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def apply_migrations(client: TursoClient) -> list[str]:
    """아직 적용되지 않은 마이그레이션을 순서대로 적용한다.

    적용한 파일 이름 목록을 돌려준다.
    """
    client.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in client.execute("SELECT version FROM schema_migrations").rows}

    newly: list[str] = []
    for path in migration_files():
        version = path.stem
        if version in applied:
            continue

        statements = _split_statements(path.read_text(encoding="utf-8"))
        log.info("마이그레이션 적용: %s (%d문)", version, len(statements))
        # 적용 기록을 **같은 묶음 끝에** 싣는다 (docs/infra.md 25.335). 따로 보내면 본문은
        # 들어갔는데 기록만 빠지는 틈이 생기고, 다음 실행이 같은 파일을 다시 돌려
        # `ALTER TABLE … ADD COLUMN` 이 "duplicate column" 으로 깨진다 — 그 뒤로는 영영 막힌다.
        # D1 의 batch 는 한 트랜잭션이라 본문과 기록이 함께 들어가거나 함께 빠진다.
        client.batch(
            [(s, []) for s in statements]
            + [
                (
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    [version, now_iso()],
                )
            ]
        )
        newly.append(version)

    return newly


# ----------------------------------------------------------------------
# 종목·시세를 **한 행씩** 넣던 도우미는 지웠다 (2026-09-21, docs/infra.md 25.48)
# ----------------------------------------------------------------------
# `upsert_stock` `get_stock_by_yahoo_symbol` `upsert_price` `latest_prices` 넷이
# 여기 있었는데 **부르는 곳이 한 군데도 없었다.** 실제로 쓰는 길은 따로 있다 —
# 종목은 `jobs/universe.py` 가, 시세는 아래 `bulk_upsert_prices` 가 묶어서 넣는다.
#
# 지운 이유가 "안 쓰니까" 만은 아니다. `upsert_price()` 는 **한 행에 왕복 한 번**이고
# `upsert_stock()` 은 INSERT 뒤에 SELECT 까지 **왕복 두 번**이었다. 이 모듈은 배치가
# 전부 import 하는 곳이라, 이름만 보고 반복문 안에서 부르기 딱 좋다. D1 하루 쓰기
# 한도를 두 배 넘긴 일(25.20)이 바로 쓰기를 헤프게 쓴 결과였다.
# **쓰지 않는 지름길을 눈에 보이는 곳에 두지 않는다.**

# ----------------------------------------------------------------------
# 배치 실행 이력
# ----------------------------------------------------------------------
#: 이보다 오래 `running` 인 기록은 끝맺지 못한 것으로 본다. 워크플로 timeout 중 가장 긴 것이
#: 60분(d1-catchup)이라 그 6배로 넉넉히 잡는다. 같은 작업이 정말로 나란히 도는 일은 없다
#: (워크플로마다 concurrency 그룹이 있다)
STALE_RUN_HOURS = 6

#: 이 프로세스가 열어 둔 실행들. 죽을 때 닫으려고 둔다 (batch/core/entry.guard).
#:
#: **한 칸이 아니라 쌓는다** (2026-09-22, docs/infra.md 25.110). 일일 배치는 제 실행을 연 뒤
#: 그 안에서 `sentiment`·`scores`·`signals`·`monitor_targets` 를 차례로 부른다. 한 칸이면
#: 안쪽 작업이 시작할 때 **바깥(일일 배치)의 실행 번호가 지워지고**, 안쪽이 끝나면 칸이 비어
#: 그 뒤에 죽어도 아무것도 안 닫혔다. 겹쳐 도는 것을 담으려면 쌓아야 한다.
_열린_실행: list[tuple[TursoClient, int]] = []


def reap_stale_runs(client: TursoClient, job_name: str) -> int:
    """끝맺지 못한 채 남은 같은 작업의 `running` 기록을 닫는다. 닫은 개수.

    **왜 필요한가.** `guard()` 가 DB 한도 오류를 "건너뜀"(종료코드 0)으로 바꾸는데
    (docs/infra.md 25.6), 그때 이미 열어 둔 `batch_runs` 행은 `running` 인 채로 남는다.
    2026-09-19 따라잡기에서 `financials` 가 그렇게 남았다. 한도에 걸린 그 순간에는 닫는 UPDATE
    조차 쓰기라서 실패할 수 있다 — 그래서 **닫는 일을 다음 실행이 대신한다.** 다음 날에는 예산이
    있다.

    `ran_within()` 은 `success`/`partial` 만 세므로 열린 행이 건너뛰기를 일으키지는 않는다.
    기록이 거짓말을 하지 않게 하려는 것이다 — 운영 이력은 무슨 일이 있었는지 말해야 한다.
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=STALE_RUN_HOURS)).isoformat()
    try:
        rs = client.execute(
            "SELECT id FROM batch_runs WHERE job_name = ? AND status = 'running' AND started_at < ?",
            [job_name, cutoff],
        )
        ids = [int(row[0]) for row in rs.rows]
        for run_id in ids:
            finish_batch_run(
                client, run_id, status="skipped", error_text=f"끝맺지 못한 기록. {STALE_RUN_HOURS}시간 뒤 정리했다"
            )
        return len(ids)
    except Exception:  # noqa: BLE001 — 정리가 본 작업을 막으면 안 된다
        return 0


def _닫기(client: TursoClient, run_id: int, status: str, error_text: str | None) -> None:
    """한 기록을 닫는다. 저장해 둔 클라이언트가 안 되면 **새로 열어** 한 번 더 해 본다.

    대부분의 작업이 `finally: client.close()` 로 먼저 닫는다 — `guard()` 가 예외를 잡는
    시점에는 그 클라이언트가 이미 닫혀 있다. 닫힌 세션으로 보내도 되는지는 `[확인필요]` 라
    믿지 않고, 안 되면 새로 연다. 둘 다 실패하면 조용히 넘어가고 다음 실행의
    `reap_stale_runs()` 가 닫는다 (두 겹으로 두는 이유).
    """
    try:
        finish_batch_run(client, run_id, status=status, error_text=error_text)
        return
    except Exception:  # noqa: BLE001 — 아래에서 새 클라이언트로 다시 해 본다
        pass
    with contextlib.suppress(Exception):
        from batch.core.client import TursoClient as _Client

        새것 = _Client()
        try:
            finish_batch_run(새것, run_id, status=status, error_text=error_text)
        finally:
            새것.close()


def close_current_run(status: str, error_text: str | None = None) -> None:
    """가장 최근에 연 실행 기록 하나를 닫는다. 실패해도 조용히 넘어간다.

    한도에 걸린 직후에 불리므로 이 UPDATE 도 막힐 수 있다. 막히면 다음 실행의
    `reap_stale_runs()` 가 닫는다. 두 겹으로 두는 이유다.
    """
    if not _열린_실행:
        return
    client, run_id = _열린_실행.pop()
    _닫기(client, run_id, status, error_text)


def fail_open_runs(error_text: str) -> None:
    """죽을 때 열려 있는 실행을 **전부 실패로** 닫는다 (docs/infra.md 25.110).

    **`skipped` 가 아니라 `failed` 다.** 건너뜀은 "한도라 안 했다" 는 뜻이고 실패는
    "하려다 깨졌다" 는 뜻이다. 둘을 같은 글자로 적으면 화면이 거짓말을 한다.

    안 닫으면 그 행은 `running` 으로 남고, 여섯 시간 뒤 `reap_stale_runs()` 가
    **`skipped`·"끝맺지 못한 기록"** 으로 닫는다 — **실패 사유가 DB 어디에도 안 남는다.**
    그러면 사람이 Actions 로그를 열어야 하는데 CLAUDE.md 는 그것을 쓰지 말라고 한다(25.27).

    안쪽부터(나중에 연 것부터) 닫는다.
    """
    while _열린_실행:
        client, run_id = _열린_실행.pop()
        _닫기(client, run_id, "failed", error_text[:1000])


def usage_blocked_today(client: TursoClient, api_name: str) -> bool:
    """오늘(UTC) 그 API 카운터가 이미 100% 인가 — **부르기 전에** 본다 (docs/infra.md 25.387).

    못 읽으면 거짓이다(부른 뒤 세는 `record_api_call` 이 다시 본다). `financials._한도_찼나` 와 같은 판정이다.
    """
    try:
        rs = client.execute(
            "SELECT state FROM api_usage WHERE api_name = ? AND window_type = 'day' AND window_start = ?",
            [api_name, datetime.now(UTC).strftime("%Y-%m-%d")],
        )
        rows = rs.dicts()
    except Exception:  # noqa: BLE001 — 표가 없거나 가짜 클라이언트
        return False
    return bool(rows) and rows[0].get("state") == "blocked"


def open_run_depth() -> int:
    """지금 열려 있는 실행 수. `fail_runs_opened_after` 와 짝이다 (docs/infra.md 25.372)."""
    return len(_열린_실행)


def fail_runs_opened_after(depth: int, error_text: str, status: str = "failed") -> None:
    """`depth` 뒤에 연 실행만 실패로 닫는다 — **바깥 실행은 건드리지 않는다** (docs/infra.md 25.372).

    일일 배치는 제 실행을 연 채 점수·신호·감성·포트폴리오·매도 플래그 같은 하위 작업을 부르고, 하위 작업의
    예외를 **경고로 삼킨다**(리포트는 나가야 하므로). 그러면 `guard()` 의 `fail_open_runs` 에 닿지 않아 하위 작업의
    기록이 `running` 으로 굳었다 — /status 의 수동 실행 단추가 여섯 시간 "(실행 중)" 으로 막히고, 뒤에
    `reap_stale_runs()` 가 "끝맺지 못한 기록" 으로 닫아 **실패 사유가 사라졌다**(25.110·25.231 이 막으려던 모양).
    """
    while len(_열린_실행) > depth:
        client, run_id = _열린_실행.pop()
        _닫기(client, run_id, status, error_text[:1000])


def trigger_source() -> str:
    """무엇이 이 실행을 깨웠는지. **`batch_runs.trigger_source` 의 단일 정의처다.**

    2026-09-21 까지 이 함수는 `jobs/daily.py` 안에만 있었고, **나머지 스물몇 작업은
    `trigger="manual"` 을 손으로 박아** 넘겼다. 그래서 매월 예약으로 도는 ETF 판정도,
    주간 워크플로도, DB 에는 전부 "사람이 손으로 돌렸다" 로 남았다 —
    `/status` 화면이 그 글자를 그대로 보여 준다(docs/infra.md 25.95).

    **깃발이 거짓말하면 깃발이 없느니만 못하다**(docs/infra.md 25.0). 무엇이 깨웠는지는
    "예약이 안 불렸나, 불렸는데 실패했나" 를 가르는 첫 물음이다.

    `GITHUB_EVENT_NAME` 은 Actions 가 모든 단계에 넣어 주는 기본 환경변수다.
    로컬에서는 비어 있으므로 `manual` 이 되고, 그것은 사실이다.
    """
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "schedule":
        return "schedule"
    if event in ("repository_dispatch", "workflow_dispatch"):
        return "dispatch"
    return "manual"


def start_batch_run(
    client: TursoClient,
    *,
    job_name: str,
    market: str | None,
    trade_date: str | None,
    trigger: str | None = None,
    scheduled_for: str | None = None,
) -> int:
    """`trigger` 를 안 주면 **환경에서 알아낸다**(`trigger_source`).

    기본값을 둔 이유: 안 주면 틀리는 것이 아니라 **맞게** 되도록. 예전에는 필수 인자라
    부르는 쪽마다 뭔가를 적어야 했고, 스물몇 곳이 전부 `"manual"` 을 적었다.
    """
    trigger = trigger_source() if trigger is None else trigger
    reap_stale_runs(client, job_name)
    delay = None
    if scheduled_for:
        try:
            planned = datetime.fromisoformat(scheduled_for)
            delay = int((datetime.now(UTC) - planned).total_seconds())
        except ValueError:
            delay = None

    넣음 = client.execute(
        "INSERT INTO batch_runs"
        " (job_name, market, trade_date, trigger_source, scheduled_for, started_at,"
        "  status, delay_seconds)"
        " VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
        [job_name, market, trade_date, trigger, scheduled_for, now_iso(), delay],
    )
    # 넣은 행 번호를 응답에서 받는다 (docs/infra.md 25.869). 예전 `SELECT MAX(id) … WHERE job_name = ?` 는 실행마다 그
    # 작업의
    # 기록 전부를 읽었고, 같은 작업이 동시에 둘 돌면(따라잡기·수동 실행) **다른 실행의 번호**를 받아 남의 기록을
    # 끝맺을 수 있었다.
    # 응답에 번호가 없는 클라이언트만 예전 방식으로 찾는다
    run_id = getattr(넣음, "last_insert_rowid", None)
    if not run_id:
        rs = client.execute("SELECT MAX(id) FROM batch_runs WHERE job_name = ?", [job_name])
        run_id = int(rs.scalar())
    _열린_실행.append((client, run_id))
    _읽기_시작[run_id] = ReadCounter.process_total
    return run_id


#: 실행을 열 때의 프로세스 읽기 합 (docs/infra.md 25.885). 끝낼 때 차이를 `step_log.db_rows_read` 로 적는다
_읽기_시작: dict[int, int] = {}

#: **이 프로세스가 시세를 쓴 종목과 그 가장 이른 날짜** (docs/infra.md 25.888). 시세 사본(`core/price_replica`)이
#: 이 범위를 다시 받는다 — 새 행은 id 로 알 수 있지만 **있던 행을 고친 것**(수정주가·다시 받기)은 id 가 그대로라
#: 따로 적지 않으면 사본이 옛 값을 쥔다. 시세를 쓰는 길은 `bulk_upsert_prices` 와 `adjust_kr` 의 UPDATE 둘뿐이다
#: (`tests/test_price_replica.py` 가 묶는다)
_가격_손댐: dict[int, str] = {}

#: 실행 기록에 적는 열쇠와, 종목을 하나하나 적는 상한. 넘으면 "전 종목, 이 날짜부터" 로 줄인다(일일 배치는 수천 종목)
PRICES_TOUCHED_KEY = "prices_touched"
PRICES_TOUCHED_MAX_STOCKS = 300


class ReadSteps:
    """한 실행 안의 **단계별 읽은 행** (docs/infra.md 25.898).

    `mark(이름)` 은 앞 표시 뒤로 이 프로세스가 읽은 행을 그 이름에 적는다. 25.885 의 실행별 합계로 미국 점수가
    하루 301만 행(2026-10-02)인 것은 알았는데 어느 질의인지는 몰랐다 — 짐작으로 고치지 않으려고 단계마다 나눠 남긴다.
    `step_log["reads_by_step"]` 로 싣는다.
    """

    def __init__(self) -> None:
        self._at = ReadCounter.process_total
        self.steps: dict[str, int] = {}

    def mark(self, name: str) -> None:
        now = ReadCounter.process_total
        self.steps[name] = self.steps.get(name, 0) + now - self._at
        self._at = now


def note_prices_touched(pairs: Iterable[tuple[Any, Any]]) -> None:
    """(종목, 날짜) 들을 "이 프로세스가 고친 시세" 에 더한다."""
    for sid, day in pairs:
        종목, 날 = int(sid), str(day)[:10]
        if 날 < _가격_손댐.get(종목, "9999"):
            _가격_손댐[종목] = 날


def prices_touched_summary() -> dict[str, Any] | None:
    """실행 기록에 적을 요약. 없으면 None."""
    if not _가격_손댐:
        return None
    가장_이른 = min(_가격_손댐.values())
    if len(_가격_손댐) > PRICES_TOUCHED_MAX_STOCKS:
        return {"all": True, "min_date": 가장_이른}
    return {"all": False, "min_date": 가장_이른, "stocks": {str(k): v for k, v in sorted(_가격_손댐.items())}}

#: 실행별 읽기량을 적는 step_log 열쇠. 하위 작업을 품은 실행(일일 배치)은 하위 몫까지 들어 있다
ROWS_READ_KEY = "db_rows_read"


def finish_batch_run(
    client: TursoClient,
    run_id: int,
    *,
    status: str,
    step_log: dict[str, Any] | None = None,
    error_text: str | None = None,
) -> None:
    # step_log 을 안 주면 **있던 것을 둔다** (docs/infra.md 25.668, 감사). `_닫기`·`reap_stale_runs` 가 안 넘겨
    # 백테스트가 `note_progress` 로 적은 "어디까지 갔나" 가 실패·정리 때 NULL 로 지워졌다
    #
    # **이 실행이 읽은 행 수를 함께 적는다** (docs/infra.md 25.885). Turso 월 읽기 한도의 어디에 얼마가 드는지 몰라
    # 줄일 곳을 고를 수 없었다. 이 프로세스가 연 실행만 잰다(남의 실행을 정리하는 `reap_stale_runs` 는 모른다 — 적지
    # 않는다)
    시작 = _읽기_시작.pop(run_id, None)
    # step_log 을 줄 때만 더한다. 안 주는 닫기(실패 정리 `_닫기`·`fail_open_runs`)는 있던 것을 **그대로** 둔다(25.668) —
    # 성공한 실행은 거의 모두 step_log 을 주므로 순위를 매기기엔 충분하다
    if step_log and 시작 is not None:
        step_log = {**step_log, ROWS_READ_KEY: ReadCounter.process_total - 시작}
    client.execute(
        "UPDATE batch_runs SET finished_at = ?, status = ?, step_log = COALESCE(?, step_log), error_text = ?"
        " WHERE id = ?",
        [
            now_iso(),
            status,
            json.dumps(step_log, ensure_ascii=False) if step_log else None,
            error_text,
            run_id,
        ],
    )
    # **실패로 닫아도** 고친 시세 범위는 적는다 (25.888) — 쓰다 죽은 실행이 남긴 행도 사본이 다시 받아야 한다
    손댐 = prices_touched_summary()
    if 손댐 is not None:
        client.execute(
            "UPDATE batch_runs SET step_log = CASE WHEN step_log IS NULL THEN json_object(?, json(?))"
            " WHEN json_valid(step_log) THEN json_set(step_log, ?, json(?)) ELSE step_log END WHERE id = ?",
            [PRICES_TOUCHED_KEY, json.dumps(손댐), f"$.{PRICES_TOUCHED_KEY}", json.dumps(손댐), run_id],
        )
    for i, (_c, 번호) in enumerate(_열린_실행):
        if 번호 == run_id:
            del _열린_실행[i]
            break
    if not _열린_실행:
        _가격_손댐.clear()


def set_run_trade_date(client: TursoClient, run_id: int, trade_date: str) -> None:
    """열어 둔 실행의 `trade_date` 를 나중에 고친다 (docs/infra.md 25.114).

    **왜 필요한가.** 어떤 작업은 실제 기준일을 **받아 봐야** 안다(ETF 국내는 며칠치를 받아
    그중 가장 늦은 날이 기준일이다). 그렇다고 기록을 받은 **뒤에** 열면, 받다가 죽은 날에는
    `batch_runs` 에 행이 하나도 안 남아 "예약이 안 불렸다" 와 구별이 안 된다.
    **먼저 열고 나중에 고친다.**
    """
    client.execute("UPDATE batch_runs SET trade_date = ? WHERE id = ?", [trade_date, run_id])


def note_progress(client: TursoClient, run_id: int, progress: dict[str, Any]) -> None:
    """도는 중인 실행의 step_log 에 진행 상태를 적는다. 끝날 때 step_log 을 주면 덮어쓰고 안 주면 남는다(25.668)."""
    client.execute(
        "UPDATE batch_runs SET step_log = ? WHERE id = ? AND status = 'running'",
        [json.dumps({"progress": progress}, ensure_ascii=False), run_id],
    )


def has_successful_run(
    client: TursoClient, job_name: str, trade_date: str
) -> bool:
    """같은 거래일에 이미 성공한 실행이 있는지 본다.

    예약 실행이 중복으로 뜨거나 놓친 배치를 따라잡을 때 두 번 돌지 않게 한다.
    """
    rs = client.execute(
        "SELECT COUNT(*) FROM batch_runs"
        " WHERE job_name = ? AND trade_date = ? AND status IN ('success', 'partial')",
        [job_name, trade_date],
    )
    return int(rs.scalar() or 0) > 0


def last_successful_run(client: TursoClient, job_name: str) -> dict[str, Any] | None:
    rs = client.execute(
        "SELECT id, trade_date, started_at, finished_at, status, delay_seconds"
        " FROM batch_runs WHERE job_name = ? AND status IN ('success', 'partial')"
        " ORDER BY started_at DESC LIMIT 1",
        [job_name],
    )
    rows = rs.dicts()
    return rows[0] if rows else None


def ran_within(
    client: TursoClient, job_name: str, days: float, market: str | None = None, *, include_partial: bool = True
) -> bool:
    """그 작업이 최근 days 일 안에 성공했는가.

    D1 따라잡기가 매일 도는데, 주 1회면 되는 작업(유니버스·재무)이 매번 수천 행을 다시 쓰면
    그만큼 시세를 덜 받는다 (docs/infra.md 25.8). 최근에 돌았으면 건너뛰게 하려고 둔다.
    모르면(표가 없거나 읽기 실패) False — 돌리는 쪽이 안전하다.

    **이 DB 에서 돈 것만 센다.** D1 으로 옮길 때 백업 되살리기가 Turso 의 실행 기록(batch_runs)까지
    넣었다(2026-09-18, 실패한 시도가 표 몇 개를 먼저 넣고 멈췄다). 그 기록을 믿으면 D1 에 재무·업종이
    하나도 없는데 "최근에 돌았다" 며 건너뛴다. 마이그레이션을 처음 적용한 시각을 이 DB 가 생긴 때로 본다.
    """
    # `include_partial=False` — 일부만 받고 끝난 실행을 "돌았다" 로 치지 않는다 (docs/infra.md 25.320).
    # 재무는 DART 오류로 partial 이면 빈 칸이 남는데, 따라잡기가 그것을 7일 동안 건너뛰었다
    # 두 번째 자리를 바인딩으로 — 질의 글이 하나로 남아야 `test_sql_schema` 가 스키마에 대 본다
    둘째 = "partial" if include_partial else "success"
    sql = (
        "SELECT MAX(finished_at) FROM batch_runs WHERE job_name = ? AND status IN ('success', ?)"
        " AND started_at >= (SELECT MIN(applied_at) FROM schema_migrations)"
        + (" AND market = ?" if market else "")
    )
    try:
        last = client.execute(sql, [job_name, 둘째, market] if market else [job_name, 둘째]).scalar()
    except Exception:  # noqa: BLE001 — 모르면 "안 돌았다" 로 본다. 한 번 더 도는 쪽이 건너뛰는 쪽보다 안전하다
        return False
    if not last:
        return False
    try:
        finished = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return False
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return (datetime.now(UTC) - finished).total_seconds() < days * 86_400


# ----------------------------------------------------------------------
# 무료 한도 사용량
# ----------------------------------------------------------------------
#: `api_usage.api_name` 에 쓸 수 있는 이름. **한 소스에 이름 하나다.**
#:
#: 2026-09-21 까지 DART 를 두 이름으로 세고 있었다 — 재무·배당·공시는 `dart_opendart`,
#: 업종만 `dart`. 이름이 갈리면 하루 한도(20,000)를 보는 카운터가 **둘로 쪼개져**
#: 한쪽에 쓴 만큼은 한도 계산에 안 보인다. CLAUDE.md 가 "80% 경고, 100% 중단" 을 시키는데
#: 세는 자리가 둘이면 그 판정이 틀린다 (docs/infra.md 25.68).
#:
#: `turso_*` `d1_*` 은 바깥 API 가 아니라 **우리가 쓴 행 수**를 담는 자리다(23·24절).
API_NAMES = (
    "dart_opendart",      # DART OpenAPI — 재무·배당·공시·업종
    "krx_openapi",        # 한국거래소 Open API
    "sec_edgar",          # SEC EDGAR
    "nasdaqtrader_symdir", # 나스닥 심볼 디렉터리
    "yfinance",           # yfinance
    "yahoo_quotesummary",  # 야후 펀드 프로필 (batch/sources/yahoo_fund.SOURCE)
    "samsungfund_kodex",  # KODEX 구성종목 (batch/sources/kodex_pdf.SOURCE, 25.974 — 사용자 결정으로 사용)
    "turso_writes", "turso_reads", "d1_writes", "d1_reads",  # 쓴/읽은 행 수
    "d1_db_size",         # D1 DB 가 차지한 바이트 (횟수가 아니라 수위다)
)


def record_and_guard(
    client: TursoClient,
    api_name: str,
    *,
    count: int = 1,
    limit_value: int | None = None,
    warn_at_pct: int = 80,
    label: str = "",
) -> str:
    """호출을 세고 **그 결과를 돌려준다** — `ok` · `warn` · `blocked` · `unknown`.

    CLAUDE.md 비용 규칙은 두 마디다: **"80% 도달 시 경고, 100%에서 중단."**
    세는 것은 절반이고, 나머지 절반은 **그 수를 보고 멈추는 것**이다.
    2026-09-22 까지 그 나머지 절반은 `jobs/financials` 와 `jobs/backfill_kr` **둘에만**
    있었고, 경고를 찍는 일곱 줄이 두 곳에 각각 적혀 있었다 (docs/infra.md 25.117).

    **바깥 API 가 거절할 때까지 기다리는 것과 다르다.** `result.limit_state == "blocked"` 는
    "넘고 나서 안 것" 이다. 이 함수는 **넘기 전에** 멈추라고 말한다.

    한도를 모르면 `unknown` 이고, **모르는 것을 나쁘다고 단정하지 않는다** — 부르는 쪽은
    `blocked` 일 때만 멈춘다(25.0).
    """
    usage = record_api_call(
        client, api_name, count=count, limit_value=limit_value, warn_at_pct=warn_at_pct
    )
    state = str(usage.get("state", "unknown"))
    이름 = label or api_name
    if state == "warn":
        log.warning(
            "%s 호출이 한도의 %s%% 를 넘었습니다 (%s/%s)",
            이름, usage.get("warn_at_pct"), usage.get("call_count"), usage.get("limit_value"),
        )
    elif state == "blocked":
        log.warning(
            "%s 일일 한도에 도달했습니다 (%s/%s). 더 부르지 않습니다",
            이름, usage.get("call_count"), usage.get("limit_value"),
        )
    return state


def record_api_call(
    client: TursoClient,
    api_name: str,
    *,
    count: int = 1,
    limit_value: int | None = None,
    warn_at_pct: int = 80,
) -> dict[str, Any]:
    """호출 횟수를 센다. 한도를 모르면 state 는 unknown 으로 둔다.

    돌려주는 값에 현재 상태가 들어 있어 호출부가 경고할 수 있다.
    """
    # **하루 경계는 UTC 자정이다** (docs/infra.md 25.322). D1 은 UTC 로 초기화되어 맞지만, DART·KRX 가 한국 자정에
    # 초기화한다면 경계가 9시간 어긋난다 `[확인필요: DART·KRX 한도 초기화 시각]`.
    # 웹(`lib/apiUsage`)도 UTC 라 둘은 같게 센다
    window_start = datetime.now(UTC).strftime("%Y-%m-%d")

    client.execute(
        "INSERT INTO api_usage"
        " (api_name, window_type, window_start, call_count, limit_value,"
        "  warn_at_pct, state, last_call_at, updated_at)"
        " VALUES (?, 'day', ?, ?, ?, ?, 'unknown', ?, ?)"
        " ON CONFLICT (api_name, window_type, window_start) DO UPDATE SET"
        "   call_count = api_usage.call_count + excluded.call_count,"
        "   limit_value = COALESCE(excluded.limit_value, api_usage.limit_value),"
        "   last_call_at = excluded.last_call_at,"
        "   updated_at = excluded.updated_at",
        [api_name, window_start, count, limit_value, warn_at_pct, now_iso(), now_iso()],
    )

    rs = client.execute(
        "SELECT call_count, limit_value, warn_at_pct FROM api_usage"
        " WHERE api_name = ? AND window_type = 'day' AND window_start = ?",
        [api_name, window_start],
    )
    row = rs.dicts()[0]
    state = evaluate_limit_state(
        row["call_count"], row["limit_value"], row["warn_at_pct"]
    )

    client.execute(
        "UPDATE api_usage SET state = ?"
        " WHERE api_name = ? AND window_type = 'day' AND window_start = ?",
        [state, api_name, window_start],
    )
    row["state"] = state
    row["api_name"] = api_name
    return row


#: Turso 무료 플랜의 월 쓰기(행) 한도. docs/infra.md 2절.
#:
#: 2026-09-17 에 이 한도를 실제로 넘겨 **운영 DB 쓰기가 막혔다**(infra 23절).
#: 국내 5년 백필을 다시 돌리면서 같은 달 예산이 얼마나 남았는지 보지 않은 탓이다.
#: 그래서 이제 쓴 행 수를 센다. 숫자 자체는 Turso 문서 기준이고 대시보드로 확인해야 한다 [확인필요]
TURSO_MONTHLY_WRITE_LIMIT = 10_000_000

#: Turso 무료 플랜의 월 **읽기(훑은 행)** 한도.
#:
#: 2026-09-18 대시보드 실측: 읽기 10.5억 / 5억(210%), 쓰기 2,787만 / 1,000만(279%) 로
#: 계정이 통째로 막혔다. **쓰기보다 읽기가 먼저 터졌다.** 돌려받은 행이 아니라 서버가 훑은
#: 행을 센다 — 인덱스를 못 타면 한 줄을 얻으려고 수만 행을 훑는다 (docs/infra.md 24절).
TURSO_MONTHLY_READ_LIMIT = 500_000_000


#: Cloudflare D1 무료 플랜의 **하루** 쓰기 한도 (자정 UTC 리셋, docs/infra.md 25절).
D1_DAILY_WRITE_LIMIT = 100_000

#: Cloudflare D1 무료 플랜의 **하루 읽기(훑은 행)** 한도 (자정 UTC 리셋).
#:
#: 출처는 docs/infra.md 25절 표("하루 500만 읽기 · 하루 10만 쓰기 · 5GB", 2026-09-18 확인).
#: **한 번도 재 본 적이 없다** `[확인필요]` — 2026-09-22 까지 D1 의 읽기를 세는 자리가
#: 아예 없었기 때문이다. 읽은 행은 `turso_reads`(월 5억)에만 쌓여서, D1 하루 예산의
#: **100배 넉넉한 잣대**로 재고 있었다. 게이지는 늘 한 자릿수 퍼센트였고 경고는 울릴 수 없었다.
#:
#: 이것이 왜 나쁜가: 2026-09-18 에 계정을 막은 것은 쓰기가 아니라 **읽기**였다(24절).
#: 그리고 infra 의 여러 결정이 "읽기는 넉넉하다(하루 500만 행)" 를 근거로 삼았는데
#: (25.63 10분 감시, 25.61 여섯 줄 분할), 그 "넉넉하다" 를 뒷받침할 실측이 없었다.
D1_DAILY_READ_LIMIT = 5_000_000


def _record_rows(
    client: TursoClient,
    api_name: str,
    rows: int,
    limit: int,
    window_type: str = "month",
    window_start: str | None = None,
) -> dict[str, Any] | None:
    """이번 달 누계에 rows 를 더하고 상태를 다시 판정한다. 실패해도 본 작업을 막지 않는다.

    왜 세는가: 한도를 넘으면 **계정이 통째로 막혀 일일 리포트까지 멈춘다.** 넘고 나서 아는 것과
    넘기 전에 아는 것은 다르다. 시스템 상태 화면이 이 값을 게이지로 보여 준다.

    **부르는 곳은 `core/turso.TursoClient` 하나다.** 적재·조회 함수가 각자 세면 빠지는 곳이
    생기고, 적게 보이는 카운터는 없느니만 못하다 (2026-09-17 에 실제로 그랬다).
    """
    if rows <= 0:
        return None
    month = window_start or datetime.now(UTC).strftime("%Y-%m")
    try:
        client.execute(
            "INSERT INTO api_usage"
            " (api_name, window_type, window_start, call_count, limit_value,"
            "  warn_at_pct, state, last_call_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 80, 'unknown', ?, ?)"
            " ON CONFLICT (api_name, window_type, window_start) DO UPDATE SET"
            "   call_count = api_usage.call_count + excluded.call_count,"
            "   limit_value = excluded.limit_value,"
            "   last_call_at = excluded.last_call_at,"
            "   updated_at = excluded.updated_at",
            [api_name, window_type, month, rows, limit, now_iso(), now_iso()],
        )
        rs = client.execute(
            "SELECT call_count, limit_value, warn_at_pct FROM api_usage"
            " WHERE api_name = ? AND window_type = ? AND window_start = ?",
            [api_name, window_type, month],
        )
        row = rs.dicts()[0]
        state = evaluate_limit_state(row["call_count"], row["limit_value"], row["warn_at_pct"])
        client.execute(
            "UPDATE api_usage SET state = ? WHERE api_name = ?"
            " AND window_type = ? AND window_start = ?",
            [state, api_name, window_type, month],
        )
        row["state"] = state
        return row
    except Exception as exc:  # noqa: BLE001 - 카운터가 본 작업을 막으면 안 된다
        # 이유를 함께 적는다 (docs/infra.md 25.884). 2026-10-02 복귀 뒤 Turso 실행마다 첫 줄에 이 경고가 떴는데
        # 이유가 없어 고장인지 한도인지 가를 수 없었다
        log.warning("%s 카운터를 갱신하지 못했습니다: %s", api_name, str(exc)[:200])
        return None


def quota_reason(error: BaseException | str) -> str | None:
    """DB 한도에 걸린 오류면 사람이 읽을 사유를, 아니면 None (docs/infra.md 25.6).

    한도 초과는 **고장이 아니라 기다리면 풀리는 상태**다. 실패로 다루면 매번 실패 알림이 쏟아지고,
    더 나쁘게는 배치가 시작 기록을 쓰다 죽어 알림조차 못 보낸다(2026-09-18). 그래서 따로 알아본다.
    """
    text = str(error).lower()
    # 읽기 한도를 먼저 가린다 (docs/infra.md 25.863). 예전에는 "free tier … limit" 이면 모두 쓰기 한도로 읽어,
    # 2026-10-01 하루 읽기
    # 한도 초과(500만 행)를 "하루 쓰기 한도(10만 행)" 로 안내했다 — 사람은 쓰기 예산을 고치러 간다
    if "row read limit" in text:
        return "Cloudflare D1 하루 읽기 한도(500만 행)에 걸렸습니다. 매일 09:00 KST(자정 UTC)에 풀립니다"
    if "daily row write limit" in text or ("free tier" in text and "limit" in text):
        return "Cloudflare D1 하루 쓰기 한도(10만 행)에 걸렸습니다. 매일 09:00 KST(자정 UTC)에 풀립니다"
    if "operations are forbidden" in text or ("blocked" in text and "upgrade your plan" in text):
        return "Turso 월 한도에 걸렸습니다. 다음 결제 주기에 풀립니다"
    return None


def in_chunk(reserve: int = 4) -> int:
    """`IN (?, ?, …)` 에 한 번에 넣을 수 있는 개수 (docs/infra.md 25.5).

    **D1 은 질의당 파라미터 100개가 한도다.** 목록이 긴 질의는 나눌 수 없어(나누면 뜻이 달라진다)
    부르는 쪽이 애초에 짧게 끊어야 한다. reserve 는 같은 질의의 다른 파라미터(기준일 등) 몫이다.
    Turso 는 수천 개를 받으므로 넉넉히 둔다 — 왕복 수를 늘릴 이유가 없다.
    """
    from batch.core import client as backend

    if backend.resolved_backend() == backend.D1:
        from batch.core.d1 import MAX_PARAMS

        return max(1, MAX_PARAMS - reserve)
    return 200


def record_rows_written(client: TursoClient, rows: int) -> dict[str, Any] | None:
    """이번 달에 쓴 행 수를 센다 (docs/infra.md 23절). D1 이면 **오늘(UTC)** 누계도 센다(25.8절)."""
    row = _record_rows(client, "turso_writes", rows, TURSO_MONTHLY_WRITE_LIMIT)
    from batch.core import client as backend

    if backend.resolved_backend() == backend.D1:
        _record_rows(client, "d1_writes", rows, D1_DAILY_WRITE_LIMIT, "day", _utc_today())
    return row


def _utc_today() -> str:
    """D1 한도는 자정 UTC 에 풀린다. 한국 날짜가 아니라 UTC 날짜로 센다."""
    return datetime.now(UTC).date().isoformat()


def d1_writes_today(client: TursoClient) -> int | None:
    """오늘(UTC) D1 에 쓴 행 수. **못 읽으면 `None`** — 0 과 구별한다.

    **0 과 "모름" 을 섞으면 안 된다.** 사람이 읽는 자리(따라잡기 알림·사용량 한 줄)에서
    "오늘 0행 썼다" 는 "아직 아무것도 안 썼다" 로 읽힌다. 못 읽었을 뿐인데 그렇게 보이면
    다음 판단이 통째로 틀어진다 — 2026-09-20 에 이 자리가 0 을 내놓고 있었다.

    계산에 쓰는 `remaining_d1_daily_writes()` 는 일부러 다르게 군다(아래).
    """
    try:
        rs = client.execute(
            "SELECT call_count FROM api_usage WHERE api_name = 'd1_writes'"
            " AND window_type = 'day' AND window_start = ?",
            [_utc_today()],
        )
        return int(rs.scalar() or 0)
    except Exception:  # noqa: BLE001 — **모르면 None 이다.** 0 으로 두면 "오늘 안 썼다" 가 된다(25.30)
        return None


def remaining_d1_daily_writes(client: TursoClient) -> int:
    """오늘(UTC) D1 에 더 쓸 수 있는 행 수. 모르면 한도 전체.

    **2026-09-22 부터 웹이 쓰는 행도 여기 들어온다** (docs/infra.md 25.124). 그 전에는
    "웹은 매 요청이 따로라 카운터를 둘 곳이 없다" 고 적어 두고 빼 두었는데, `lib/apiUsage.ts`
    가 웹에서 `api_usage` 에 적는 길을 낸 뒤로 그 이유가 낡았다. 이제 `web/lib/webUsage.ts`
    가 같은 행(`d1_writes`)을 갱신한다.

    그래도 **읽기 쪽은 조금 덜 센다** `[확인필요]`. 웹은 읽은 행을 10,000행마다 적는데
    서버리스 인스턴스가 그 전에 사라지면 모아 둔 몫을 잃는다. 부르는 쪽이 남기는
    여유(reserve)는 그 몫과 다음 날 아침 배치 몫을 함께 덮는다.

    **못 읽었을 때 "한도 전체" 로 보는 것은 위험한 쪽이다** `[확인필요]`. 그만큼 더 써도 된다고
    보게 되기 때문이다. 그런데도 그대로 두는 이유: 카운터 읽기가 한 번 실패했다고 그날 수집을
    통째로 멈추면 잃는 것이 더 크고, 실제 멈춤은 도는 동안 다시 재는 `budget_stop_reason` 이
    정한다(docs/infra.md 25.20). **사람에게 보이는 숫자는 이 함수가 아니라
    `d1_writes_today()` 를 쓴다** — 거기서는 모르는 것을 모른다고 해야 한다.
    """
    used = d1_writes_today(client)
    return max(0, D1_DAILY_WRITE_LIMIT - (used or 0))


def record_rows_read(client: TursoClient, rows: int) -> dict[str, Any] | None:
    """이번 달에 **훑은** 행 수를 센다 (docs/infra.md 24절). D1 이면 **오늘(UTC)** 누계도 센다.

    **쓰기와 같은 모양이어야 한다** (2026-09-22, docs/infra.md 25.122). `record_rows_written`
    은 처음부터 D1 하루 창을 함께 적었는데 읽기는 월 창만 적었다. 지금 도는 백엔드가 D1 인데
    잣대가 Turso 월 5억이면, 하루 500만을 다 써도 게이지는 1% 로 보인다 —
    "80% 경고, 100% 중단"(CLAUDE.md)의 앞 절반이 읽기 쪽에서는 작동할 수 없었다.
    """
    row = _record_rows(client, "turso_reads", rows, TURSO_MONTHLY_READ_LIMIT)
    from batch.core import client as backend

    if backend.resolved_backend() == backend.D1:
        _record_rows(client, "d1_reads", rows, D1_DAILY_READ_LIMIT, "day", _utc_today())
    return row


def record_db_size(client: TursoClient, size_bytes: int) -> str:
    """지금 DB 가 차지한 바이트를 적고 상태를 돌려준다 (docs/infra.md 25.123).

    **더하지 않고 덮어쓴다.** 이것은 "그동안 몇 번 썼나" 가 아니라 **지금 얼마나 찼나** 다.
    `_record_rows` 를 그대로 쓰면 날마다 용량이 더해져 며칠이면 한도를 넘은 것처럼 보인다.

    **셋 중 이것만 리셋이 없다.** 하루 쓰기·읽기 한도는 자정 UTC 에 풀리지만 용량은
    지우기 전까지 안 풀린다. 그래서 80% 경고가 가장 일찍 필요한 자리인데
    2026-09-22 까지 재는 장치가 없었다.
    """
    from batch.core.d1 import FREE_DB_BYTES

    state = evaluate_limit_state(size_bytes, FREE_DB_BYTES, 80)
    try:
        client.execute(
            "INSERT INTO api_usage"
            " (api_name, window_type, window_start, call_count, limit_value,"
            "  warn_at_pct, state, last_call_at, updated_at)"
            " VALUES ('d1_db_size', 'day', ?, ?, ?, 80, ?, ?, ?)"
            " ON CONFLICT (api_name, window_type, window_start) DO UPDATE SET"
            "   call_count = excluded.call_count,"  # ← 더하지 않는다. 수위(水位)다
            "   limit_value = excluded.limit_value,"
            "   state = excluded.state,"
            "   last_call_at = excluded.last_call_at,"
            "   updated_at = excluded.updated_at",
            [_utc_today(), size_bytes, FREE_DB_BYTES, state, now_iso(), now_iso()],
        )
    except Exception:  # noqa: BLE001 — 재는 일이 재어지는 일을 망치면 안 된다
        log.warning("d1_db_size 카운터를 갱신하지 못했습니다")
    return state


def measure_db_size(client: TursoClient) -> str | None:
    """D1 이면 용량을 재서 적는다. 아니거나 못 재면 `None`.

    부르는 곳은 **하루 한 번 도는 작업 하나**다(`jobs/daily`). 작업마다 부르면 관리 API 를
    하루 서른 번 두드린다 — 값은 하루에 그만큼 바뀌지 않는다.
    """
    from batch.core import client as backend

    if backend.resolved_backend() != backend.D1:
        return None
    size = getattr(client, "database_size", lambda: None)()
    if size is None:
        return None
    state = record_db_size(client, size)
    from batch.core.d1 import FREE_DB_BYTES

    log.info("D1 용량 %.1fMB / %.0fMB (%s)", size / 1_048_576, FREE_DB_BYTES / 1_048_576, state)
    return state


def db_size_note(client: TursoClient) -> str | None:
    """오늘 잰 용량이 80% 를 넘었으면 **사람이 읽을 한 줄** (docs/infra.md 25.142).

    **재는 일과 말하는 일을 나눈다.** 재는 쪽(`measure_db_size`)은 관리 API 를 두드리고,
    말하는 쪽은 **적어 둔 값만** 본다. 그래서 이 함수는 예산을 거의 쓰지 않고, 못 잰 날에는
    아무 말도 하지 않는다 — 어제 값으로 오늘을 말하면 그것이 곧 거짓말이다.

    왜 필요한가: 25.123 이 게이지를 만들었지만 그 수를 **아무도 보지 않았다.**
    `step_log` 에만 상태가 적혀 배치 기록을 펼쳐야 보였다. 규칙은 "80% 경고, 100% 중단"
    인데(CLAUDE.md) 앞 절반만 있었다 — 25.0 「절반만 지킨 규칙」.

    그리고 이 한도는 **셋 중 유일하게 자정에 안 풀린다.** 하루 쓰기·읽기는 기다리면
    되지만 용량은 지울 때까지 그대로다. 게다가 지울 기준이 아직 없다(25.131).
    그러니 경고는 **일찍, 사람이 읽는 자리**에 나와야 한다.
    """
    try:
        rs = client.execute(
            "SELECT call_count, limit_value, state FROM api_usage"
            " WHERE api_name = 'd1_db_size' AND window_type = 'day' AND window_start = ?",
            [_utc_today()],
        )
        row = rs.rows[0] if rs.rows else None
    except Exception:  # noqa: BLE001 — 말하는 일이 배치를 죽이면 안 된다
        return None
    if not row:
        return None

    used, limit, state = int(row[0] or 0), int(row[1] or 0), str(row[2] or "")
    if state not in ("warn", "blocked") or limit <= 0:
        return None

    쓴MB, 한도MB = used / 1_048_576, limit / 1_048_576
    비율 = used * 100 / limit
    머리 = "🛑 DB 용량이 한도에 닿았습니다" if state == "blocked" else "⚠ DB 용량 경고"
    return (
        f"{머리}: {쓴MB:.0f}MB / {한도MB:.0f}MB ({비율:.0f}%)."
        " 이 한도는 자정에 풀리지 않습니다 — 차면 모든 쓰기가 막혀 다음 날 아침 리포트도 안 나옵니다."
        " 무엇이 채웠는지는 Actions 의 'DB 상태' 를 --tables 로 돌려 봅니다."
    )


def d1_reads_today(client: TursoClient) -> int | None:
    """오늘(UTC) D1 이 훑은 행 수. **못 읽으면 `None`** — 0 과 구별한다(`d1_writes_today` 와 같다)."""
    try:
        rs = client.execute(
            "SELECT call_count FROM api_usage WHERE api_name = 'd1_reads'"
            " AND window_type = 'day' AND window_start = ?",
            [_utc_today()],
        )
        return int(rs.scalar() or 0)
    except Exception:  # noqa: BLE001 — **모르면 None 이다.** 0 으로 두면 "오늘 안 썼다" 가 된다(25.30)
        return None


def _remaining_budget(client: TursoClient, api_name: str, limit: int) -> int:
    month = datetime.now(UTC).strftime("%Y-%m")
    try:
        rs = client.execute(
            "SELECT call_count FROM api_usage WHERE api_name = ?"
            " AND window_type = 'month' AND window_start = ?",
            [api_name, month],
        )
        used = int(rs.scalar() or 0)
    except Exception:  # noqa: BLE001 — 모르면 한도 전체를 남은 것으로 본다. 막는 것이 더 나쁜 자리다(restore_backup 주석)
        used = 0
    return max(0, limit - used)


def remaining_write_budget(client: TursoClient) -> int:
    """이번 달 남은 쓰기 행 수. 모르면 한도 전체를 남은 것으로 본다.

    큰 백필을 시작하기 전에 이것을 보고 결정한다. 넘고 나서 아는 것과 넘기 전에
    아는 것은 다르다 (docs/infra.md 23절).
    """
    return _remaining_budget(client, "turso_writes", TURSO_MONTHLY_WRITE_LIMIT)


def remaining_read_budget(client: TursoClient) -> int:
    """이번 달 남은 읽기(훑을 수 있는) 행 수 (docs/infra.md 24절)."""
    return _remaining_budget(client, "turso_reads", TURSO_MONTHLY_READ_LIMIT)


def remaining_read_budget_or_none(client: TursoClient) -> int | None:
    """이번 달 남은 Turso 읽기. **못 읽으면 None** — 진도 문(scripts/turso_read_gate.py, 25.886)이
    "모름" 과 "다 남음" 을 가른다."""
    month = datetime.now(UTC).strftime("%Y-%m")
    try:
        rs = client.execute(
            "SELECT call_count FROM api_usage WHERE api_name = 'turso_reads'"
            " AND window_type = 'month' AND window_start = ?",
            [month],
        )
    except Exception:  # noqa: BLE001 — 모르면 None. 부르는 진도 문이 "재지 못해 들어갑니다" 로 말한다(25.886)
        return None
    return max(0, TURSO_MONTHLY_READ_LIMIT - int(rs.scalar() or 0))


def remaining_d1_daily_reads(client: TursoClient) -> int:
    """오늘(UTC) D1 이 더 훑을 수 있는 행 수. 모르면 한도 전체 (`remaining_d1_daily_writes` 와 같다)."""
    used = d1_reads_today(client)
    return max(0, D1_DAILY_READ_LIMIT - (used or 0))


def check_read_budget(client: TursoClient, estimate: int, label: str) -> tuple[bool, str]:
    """수백만 행을 읽는 작업을 시작해도 되는가. (시작해도 됨, 사람이 읽을 문장).

    **예상치는 질의로 재지 않는다.** COUNT(*) 자체가 표를 훑어 예산을 쓴다. 종목 수 × 일수처럼
    이미 아는 값으로 어림한다. 어림이 틀릴 수 있으므로 넉넉히 잡는 쪽이 맞다.

    **지금 도는 백엔드의 한도로 잰다** (2026-09-22, docs/infra.md 25.122). 전에는 D1 위에서도
    Turso 월 5억으로만 재서, 300만 행짜리 백테스트가 언제나 통과했다 — D1 하루 예산은 500만이고
    같은 날 아침 배치가 이미 그 대부분을 쓴다. **가장 좁은 문을 보고 판단한다.**
    """
    from batch.core import client as backend

    남은들 = [("이번 달", remaining_read_budget(client))]
    if backend.resolved_backend() == backend.D1:
        남은들.append(("오늘(D1)", remaining_d1_daily_reads(client)))
    어디, remaining = min(남은들, key=lambda kv: kv[1])
    text = f"{label}: 예상 {estimate:,}행 · {어디} 남은 읽기 {remaining:,}행"
    return (estimate <= remaining, text)


def evaluate_limit_state(
    call_count: int, limit_value: int | None, warn_at_pct: int = 80
) -> str:
    """사용량을 상태로 바꾼다.

    한도를 모르면 unknown 이다. 모르는 것을 ok 라고 부르지 않는다.
    """
    if not limit_value or limit_value <= 0:
        return "unknown"
    ratio = call_count / limit_value * 100
    if ratio >= 100:
        return "blocked"
    if ratio >= warn_at_pct:
        return "warn"
    return "ok"


# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
def 설정_못읽음_말(key: str, 까닭: str) -> str:
    return (
        f"설정 '{key}' 를 읽지 못해({까닭}) 저장하지 않은 것으로 보고 계산했습니다 — 설정 화면에서 다시 저장하세요"
    )


def get_setting(client: TursoClient, key: str, default: Any = None, *, 못읽음: list[str] | None = None) -> Any:
    """설정 한 칸. 행이 없으면 `default`.

    **행이 있는데 읽지 못하면 말한다** (docs/infra.md 25.778). JSON 이 깨졌거나 최상위가 `null` 이면
    `default` 를 돌려주되 로그에 남기고, `못읽음` 목록을 주면 거기에도 적는다(실행 기록·리포트 경고로 간다).
    예전에는 "행 없음" 과 구별되지 않아 **말없이 기본값**을 썼다 — 웹 설정 화면은 같은 행을 "읽지 못한 칸" 이라
    띄우는데 배치는 기본 가중치·기본 목표/손절로 조용히 계산했다(25.767 에서 남긴 것). 웹 스키마의 최상위 칸은
    모두 null 을 받지 않으므로(`web/lib/settings.ts`) 최상위 `null` 은 복구·손질이 남긴 깨진 값이다.
    """
    rs = client.execute("SELECT value FROM settings WHERE key = ?", [key])
    raw = rs.scalar()
    if raw is None:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        말 = 설정_못읽음_말(key, "JSON 이 깨짐")
    else:
        if value is not None:
            return value
        말 = 설정_못읽음_말(key, "값이 null")
    log.warning("%s", 말)
    if 못읽음 is not None:
        못읽음.append(말)
    return default


def setting_statement(key: str, value: Any) -> tuple[str, list[Any]]:
    """`set_setting` 과 같은 문장을 **묶음(batch)에 넣을 꼴로** 돌려준다 — 다른 쓰기와 한 트랜잭션으로 쓸 때 (25.541).
    배치가 쓰는 것은 사람이 바꾸지 않는 **기록 키**뿐이다(`test_settings_reachable` 의 예외 목록)."""
    return (
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET"
        "   value = excluded.value, updated_at = excluded.updated_at",
        [key, json.dumps(value, ensure_ascii=False), now_iso()],
    )


def set_setting(client: TursoClient, key: str, value: Any) -> None:
    client.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET"
        "   value = excluded.value, updated_at = excluded.updated_at",
        [key, json.dumps(value, ensure_ascii=False), now_iso()],
    )


# ----------------------------------------------------------------------
# 대량 적재
# ----------------------------------------------------------------------
# adj_close 를 넣은 이유 (2026-09-16)
#   백테스트 첫 실행에서 미조정 가격이 결과를 왜곡한다는 것이 수치로 드러났다.
#   야후는 Adj Close 를 주는데 받아 놓고 저장하지 않고 있었다. 국내(한국거래소)는
#   원자료가 미조정이라 None 으로 넣는다. docs/infra.md 15절.
# change_pct 를 넣은 이유 (2026-09-17)
#   국내 원자료가 미조정이라 분할·감자가 있던 날의 종가가 전날과 이어지지 않는다.
#   한국거래소가 같은 응답에서 주는 등락률(FLUC_RT)이 있으면 조정 계수를 되살릴 수
#   있다. 받은 값 그대로 저장하고, 계산은 services/adjust.py 가 한다. 0027 마이그레이션.
#: **분할만 반영한 가격** (docs/adjust.md 7장, docs/infra.md 25.212·25.213). `p` 는 prices, `s` 는 stocks 별칭.
#:
#: 국내 `adj_close` 는 거래소 등락률로 되살린 값이라 분할·감자만 담는다. 미국 `adj_close` 는 야후 `Adj Close` 라
#: **배당까지** 담는다. "그날의 시가총액" 이나 "그날 들고 있던 것의 값" 처럼 **값의 크기**를 여러 날에 걸쳐 볼 때는
#: 배당이 섞이면 안 된다 — 미국은 야후 `Close`(`auto_adjust=False`, 분할만 반영)를 쓴다.
#: 수익률의 누적(성과 지표·모멘텀·백테스트)은 배당 포함 총수익이 맞으므로 이것을 쓰지 않는다
SPLIT_ONLY_PRICE_SQL = "CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END"


PRICE_COLUMNS = (
    "stock_id, date, open, high, low, close, adj_close, volume, value,"
    " currency, source, fetched_at, change_pct"
)


def price_column_index(name: str) -> int:
    """PRICE_COLUMNS 에서 열의 위치. 테스트가 인덱스 숫자를 손으로 적지 않게 한다.

    열을 하나 끼워 넣으면 뒤 열의 위치가 전부 밀린다. 숫자로 적어 둔 테스트는
    그때 전부 틀린다. 이름으로 찾으면 열 순서가 바뀌어도 그대로다.
    """
    names = [c.strip() for c in PRICE_COLUMNS.split(",") if c.strip()]
    return names.index(name)


def column_count(columns: str) -> int:
    """열 목록 문자열에서 열 개수를 직접 센다.

    손으로 센 숫자를 따로 두면 열을 더할 때 그 숫자를 같이 고치는 것을 잊는다.
    재무 배치가 그래서 "24 values for 26 columns" 로 운영에서 죽었다.
    """
    return len([c for c in columns.split(",") if c.strip()])


def column_count_of_prices() -> int:
    """PRICE_COLUMNS 의 열 개수."""
    return column_count(PRICE_COLUMNS)


_PRICE_PARAMS = column_count_of_prices()
_PRICE_PLACEHOLDER = "(" + ", ".join(["?"] * _PRICE_PARAMS) + ")"

# 한 문장에 묶을 행 수.
#
# 왜 이렇게 하는가: 행마다 INSERT 를 하나씩 보내면 네트워크 왕복이 행 수만큼
# 늘어난다. 하루 2,700종목을 200개씩 나눠 보내면 14번을 왕복하고, 30 거래일
# 두 시장이면 800번이 넘는다. 실제로 백필이 30분 제한에 걸렸다.
#
# 여러 행을 한 문장에 넣으면 왕복이 한 자릿수로 줄어든다.
# SQLite 의 바인딩 변수 상한(32,766)에 걸리지 않게 여유를 둔다.
BULK_ROWS_PER_STATEMENT = 400

# 한 요청(POST)에 싣는 최대 행 수.
#
# 문장을 여럿 만들어도 한 요청에 모두 실으면 요청이 커진다. 국내 하루치(2,700행)는 한 번에 가도
# 되지만, 미국 5년 백필은 한 조각(200종목)만 26만 행이다. 한 요청으로 보내면 Turso 응답 60초
# 제한에 걸린다(조사 워크플로 2026-09-17: turso.py TIMEOUT 60, 전체 행을 POST 1회로 보냄).
# 1만 행이면 국내 일일은 여전히 한 번에 가고, 미국은 여러 번으로 나뉜다.
MAX_ROWS_PER_REQUEST = 10_000


def bulk_upsert_prices(
    client: TursoClient, rows: list[tuple], chunk_rows: int = BULK_ROWS_PER_STATEMENT
) -> int:
    """여러 행을 한 번에 넣는다. 같은 (종목, 거래일) 이 있으면 덮어쓴다.

    Args:
        rows: PRICE_COLUMNS 순서의 값 묶음 목록

    **종가가 0 이하인 행은 넣지 않는다** (docs/infra.md 25.203). 가격이 아니라 "값 없음" 이다
    (docs/metrics.md 0장: 거래정지 종목에서 0 이 들어오는 경우가 있다). 넣으면 그 한 행이
    MDD −100%·베타 어긋남·보유 평가 0원(손절 플래그)으로 번진다. 국내·미국·백필이 모두 이 문을 지나므로
    여기서 한 번 막는다. 이미 들어 있는 행은 지우지 않는다 — 되돌릴 수 없는 일이라 읽는 쪽이 거른다.
    """
    if not rows:
        return 0
    종가 = price_column_index("close")
    받은수 = len(rows)
    # 열 개수가 틀린 행은 아래에서 `ValueError` 로 멈춘다. 여기서 먼저 터지지 않게 통과시킨다
    rows = [row for row in rows if len(row) <= 종가 or row[종가] is None or row[종가] > 0]
    if len(rows) != 받은수:
        log.warning("종가가 0 이하인 %d행을 넣지 않았습니다 (값 없음으로 본다)", 받은수 - len(rows))
    if not rows:
        return 0

    note_prices_touched((row[0], row[1]) for row in rows)  # 시세 사본이 다시 받을 범위 (25.888)
    statements: list[tuple[str, list]] = []
    for start in range(0, len(rows), chunk_rows):
        chunk = rows[start : start + chunk_rows]
        placeholders = ", ".join([_PRICE_PLACEHOLDER] * len(chunk))
        sql = (
            f"INSERT INTO prices ({PRICE_COLUMNS}) VALUES {placeholders}"
            " ON CONFLICT (stock_id, date) DO UPDATE SET"
            "   open = excluded.open, high = excluded.high, low = excluded.low,"
            # **계산해 둔 국내 수정주가를 NULL 로 지우지 않는다** (docs/infra.md 25.250). 국내 행은 수정주가를 늘 비워서
            # 보내고(`adjust_kr` 가 나중에 채운다), 예전 식은 그 NULL 로 덮었다 — `backfill_kr --refresh` 를 분할 구간에
            # 돌리면 `COALESCE(adj_close, close)` 가 원래 종가로 떨어져 −80% 가 되살아났다. 새 값이 있으면 그것,
            # 없고 종가가 그대로면 있던 값. **종가가 정정되면 같은 조정 계수(수정÷원)로 옮긴다**
            # (docs/infra.md 25.705, 교차검증) — 예전에는 비워, 분할 전 수정 계열 한가운데 원 종가 한 행이 끼어
            # 점수·신호·상관·성적표·백테스트가 −90%/+900% 를 봤다(`COALESCE` 를 쓰는 가격 경로 전부).
            # 계수는 그날까지의 분할·병합이라 종가 정정과 무관하다
            "   adj_close = CASE WHEN excluded.adj_close IS NOT NULL THEN excluded.adj_close"
            "                    WHEN excluded.close = prices.close THEN prices.adj_close"
            "                    WHEN prices.adj_close IS NOT NULL AND prices.close > 0"
            "                         THEN prices.adj_close * excluded.close / prices.close END,"
            "   close = excluded.close,"
            "   volume = excluded.volume,"
            "   value = excluded.value, source = excluded.source,"
            "   fetched_at = excluded.fetched_at,"
            # 다시 받으면 등락률도 갱신한다. 옛 행에는 이 값이 비어 있어 채워야 한다
            "   change_pct = excluded.change_pct"
        )
        args: list = []
        for row in chunk:
            if len(row) != _PRICE_PARAMS:
                raise ValueError(
                    f"열 개수가 맞지 않습니다: {len(row)}개, {_PRICE_PARAMS}개여야 합니다"
                )
            args.extend(row)
        statements.append((sql, args))

    # 여러 문장을 한 요청에 실어 보낸다. 행이 많으면 MAX_ROWS_PER_REQUEST 단위로 요청을 나눈다.
    per_request = max(1, MAX_ROWS_PER_REQUEST // chunk_rows)
    for start in range(0, len(statements), per_request):
        client.batch(statements[start : start + per_request])

    # 쓴 행 수는 TursoClient 가 응답에서 직접 센다(core/turso.WriteCounter). 여기서 또 세지 않는다
    return len(rows)
