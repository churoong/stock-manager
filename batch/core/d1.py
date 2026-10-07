"""Cloudflare D1 어댑터 (docs/infra.md 25절).

Turso 가 월 한도로 막힌 동안(2026-09-18 ~ 결제 주기 리셋) 같은 앱을 D1 위에서 돌리기 위한 것이다.
**D1 도 SQLite** 라 마이그레이션과 질의는 그대로 쓴다. 다른 것은 전송 규격뿐이다.

REST 규격 (2026-09-18 문서 확인, developers.cloudflare.com/api → D1 → query)
  POST /accounts/{account_id}/d1/database/{database_id}/query
  본문  {"sql": "...", "params": [...]}  또는  {"batch": [{"sql": ..., "params": ...}, ...]}
  응답  {"result": [{"success": true, "results": [ {열: 값}, ... ],
                    "meta": {"rows_read": n, "rows_written": n, "last_row_id": n}}], ...}

Turso 와 다른 점 두 가지를 이 파일이 흡수한다.
  1. 행이 **열 이름을 가진 객체**로 온다 (Turso 는 열 목록 + 값 배열)
  2. 훑은 행 수가 meta.rows_read 로 **항상 온다** — 읽기 카운터가 여기서는 추정이 아니다
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from batch import config
from batch.core.turso import ReadCounter, ResultSet, TursoError, WriteCounter

log = logging.getLogger(__name__)

BASE_URL = "https://api.cloudflare.com/client/v4"
TIMEOUT = 60
RETRY_DELAYS = (1, 3, 7)

# 2026-09-18 문서 실측 (developers.cloudflare.com/d1/platform/limits).
# **질의 하나에 바인딩 파라미터 100개.** Turso 는 수천 개를 받아 적재가 한 문장에 수백 행을 넣는데,
# 그대로 보내면 "too many SQL variables" 로 막힌다(첫 전환에서 유니버스 적재가 이걸로 죽었다).
# 여러 행을 넣는 INSERT 는 여기서 쪼갠다. 부르는 쪽 30여 곳을 고치는 것보다 한 곳이 낫다.
MAX_PARAMS = 100
# 무료 플랜 DB 용량은 **500MB** 다(5GB 는 유료). 시세를 몇 년치씩 넣을 수 없다 — docs/infra.md 25.1
#
# **셋 중 이것만 리셋이 없다.** 하루 쓰기 10만·하루 읽기 500만은 자정 UTC 에 풀리지만
# 용량은 지우기 전까지 안 풀린다. 그런데 2026-09-22 까지 이 상수를 **아무도 읽지 않았다**
# (docs/infra.md 25.123). 지금은 `database_size()` 가 재고 `db.record_db_size()` 가 적는다.
FREE_DB_BYTES = 500 * 1024 * 1024

# INSERT ... VALUES (?, ?), (?, ?) ... 꼴을 찾는다. 뒤(ON CONFLICT ...)에는 파라미터가 없어야 한다
_VALUES = re.compile(
    r"^(?P<head>\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+.*?VALUES\s*)"
    r"(?P<groups>\(\s*\?(?:\s*,\s*\?)*\s*\)(?:\s*,\s*\(\s*\?(?:\s*,\s*\?)*\s*\))*)"
    r"(?P<tail>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def split_for_params(sql: str, args: list[Any], max_params: int = MAX_PARAMS) -> list[tuple[str, list[Any]]]:
    """파라미터가 한도를 넘으면 여러 문장으로 쪼갠다. 넘지 않으면 그대로 하나.

    쪼갤 수 있는 것은 "여러 행을 넣는 INSERT" 뿐이다. `IN (?, ?, …)` 처럼 목록이 긴 질의는
    쪼개면 뜻이 달라지므로 **쪼개지 않고 알린다** — 조용히 반쪽만 도는 것이 가장 나쁘다.
    """
    if len(args) <= max_params:
        return [(sql, args)]

    match = _VALUES.match(sql)
    if not match or "?" in match.group("tail"):
        raise TursoError(
            f"D1 은 질의당 파라미터 {max_params}개까지인데 {len(args)}개입니다. "
            f"부르는 쪽에서 나눠 보내세요: {sql[:80]}"
        )

    groups = [g for g in match.group("groups").split("),") if g.strip()]
    groups = [g if g.strip().endswith(")") else g + ")" for g in groups]
    per_group = groups[0].count("?")
    if per_group == 0 or len(args) % per_group != 0:
        raise TursoError(f"D1 파라미터 나누기 실패 (행당 {per_group}개, 전체 {len(args)}개): {sql[:80]}")

    rows_per_statement = max(1, max_params // per_group)
    out: list[tuple[str, list[Any]]] = []
    for start in range(0, len(groups), rows_per_statement):
        chunk = groups[start : start + rows_per_statement]
        sql_chunk = match.group("head") + ", ".join(g.strip() for g in chunk) + match.group("tail")
        arg_start = start * per_group
        out.append((sql_chunk, args[arg_start : arg_start + len(chunk) * per_group]))
    return out


class D1Client:
    """TursoClient 와 같은 겉모습(execute·batch·close)을 가진다. 부르는 쪽은 차이를 몰라도 된다."""

    def __init__(
        self,
        account_id: str | None = None,
        database_id: str | None = None,
        token: str | None = None,
    ) -> None:
        self.account_id = account_id or config.get("D1_ACCOUNT_ID")
        self.database_id = database_id or config.get("D1_DATABASE_ID")
        token = token or config.get("D1_API_TOKEN")
        missing = [
            name
            for name, value in (
                ("D1_ACCOUNT_ID", self.account_id),
                ("D1_DATABASE_ID", self.database_id),
                ("D1_API_TOKEN", token),
            )
            if not value
        ]
        if missing:
            raise TursoError(f"D1 설정이 비어 있습니다: {', '.join(missing)}")

        self._endpoint = f"{BASE_URL}/accounts/{self.account_id}/d1/database/{self.database_id}/query"
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.writes = WriteCounter()
        self.reads = ReadCounter()
        self._recording = False

    # ------------------------------------------------------------------
    # 공개 API (TursoClient 와 같은 이름·같은 뜻)
    # ------------------------------------------------------------------

    def __enter__(self) -> D1Client:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        return self._send([(sql, args or [])])[0]

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        """여러 문장을 한 번에. **D1 의 batch 는 한 트랜잭션으로 돈다**(문서 기준)."""
        if not statements:
            return []
        return self._send(statements)

    def close(self) -> None:
        self._flush(self.writes.take(), self.reads.take())
        self._session.close()

    def database_size(self) -> int | None:
        """지금 DB 가 차지한 바이트. **모르면 `None`** — 0 과 구별한다(25.55 와 같은 약속).

        Cloudflare 의 DB 조회 엔드포인트가 주는 `result.file_size` 다
        `[확인필요: 필드 이름을 운영 응답으로 확인해야 한다]`. 질의가 아니라 관리 API 라서
        **읽기·쓰기 한도를 쓰지 않는다.**

        재는 일이 재어지는 일을 망치면 안 되므로 무슨 일이 있어도 예외를 올리지 않는다.
        """
        url = f"{BASE_URL}/accounts/{self.account_id}/d1/database/{self.database_id}"
        try:
            response = self._session.get(url, timeout=TIMEOUT)
            if response.status_code != 200:
                log.warning("D1 용량 조회 HTTP %d", response.status_code)
                return None
            result = (response.json() or {}).get("result") or {}
            size = result.get("file_size")
            return int(size) if isinstance(size, (int, float)) else None
        except Exception:  # noqa: BLE001 — 용량을 못 재도 배치는 돈다
            log.warning("D1 용량을 재지 못했습니다")
            return None

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _send(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        # 파라미터 한도(100개)를 넘는 적재는 여러 문장으로 나눈다
        statements = [part for sql, args in statements for part in split_for_params(sql, args)]
        if len(statements) == 1:
            sql, args = statements[0]
            payload: dict[str, Any] = {"sql": sql, "params": [_encode(v) for v in args]}
        else:
            payload = {"batch": [{"sql": sql, "params": [_encode(v) for v in args]} for sql, args in statements]}

        body = self._post(payload)
        if not body.get("success", False):
            raise TursoError(f"D1 실패: {_error_text(body)}")

        results: list[ResultSet] = []
        written = 0
        read = 0
        for item in body.get("result", []):
            if not item.get("success", True):
                raise TursoError(f"D1 SQL 실패: {_error_text(item)}")
            meta = item.get("meta") or {}
            written += int(meta.get("rows_written") or 0)
            read += int(meta.get("rows_read") or 0)
            results.append(_to_result_set(item))
        self._flush(self.writes.add(written), self.reads.add(read))
        return results

    def _post(self, payload: dict) -> dict:
        last: Exception | None = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                response = self._session.post(self._endpoint, json=payload, timeout=TIMEOUT)
            except requests.RequestException as exc:  # 연결·타임아웃만 다시 보낸다
                last = exc
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise TursoError("D1 응답을 해석하지 못했습니다") from exc
                if response.status_code in (429, 500, 502, 503, 504):
                    last = TursoError(f"D1 HTTP {response.status_code}: {response.text[:200]}")
                else:
                    # 401·403·400 은 다시 보내도 같다. 설정을 고쳐야 한다
                    raise TursoError(f"D1 HTTP {response.status_code}: {response.text[:300]}")
            if attempt < len(RETRY_DELAYS):
                import time

                time.sleep(RETRY_DELAYS[attempt])
        raise TursoError(f"D1 통신 실패: {last}")

    def _flush(self, written: int, read: int) -> None:
        """카운터를 적는다. 적는 동안 일어난 읽기·쓰기를 버리지 않는다(turso.py 와 같은 규칙)."""
        if written <= 0 and read <= 0:
            return
        if self._recording:
            self.writes.pending += max(0, written)
            self.reads.pending += max(0, read)
            return
        self._recording = True
        try:
            from batch.core import db

            if written > 0:
                db.record_rows_written(self, written)
            if read > 0:
                db.record_rows_read(self, read)
        except Exception:  # noqa: BLE001 — 카운터가 본 작업을 막으면 안 된다
            log.warning("D1 카운터를 갱신하지 못했습니다")
        finally:
            self._recording = False


def _encode(value: Any) -> Any:
    """D1 은 JSON 값을 그대로 받는다. bool 만 SQLite 식으로 바꾼다."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (bytes, bytearray)):
        raise TursoError("D1 어댑터는 블롭을 보내지 않는다 (이 앱은 쓰지 않는다)")
    return value


def _to_result_set(item: dict) -> ResultSet:
    """행이 객체로 온다. 열 순서는 첫 행의 키 순서를 쓴다.

    **같은 이름의 열이 둘이면 하나로 합쳐진다**(JSON 객체라서). 이 앱의 질의는 조인마다 별칭을
    붙이므로 지금은 문제가 없다. 새 질의를 쓸 때 별칭을 빼먹지 않는다.
    """
    rows_raw = item.get("results") or []
    meta = item.get("meta") or {}
    if not isinstance(rows_raw, list) or not rows_raw or not isinstance(rows_raw[0], dict):
        return ResultSet(
            columns=[],
            rows=[],
            # `changes`(바뀐 행)와 `rows_written`(인덱스까지 쓴 행)은 다른 수다 (25.152)
            affected_rows=int(meta.get("changes") or meta.get("rows_written") or 0),
            last_insert_rowid=_opt_int(meta.get("last_row_id")),
            rows_read=int(meta.get("rows_read") or 0),
        )
    columns = list(rows_raw[0].keys())
    rows = [tuple(row.get(col) for col in columns) for row in rows_raw]
    return ResultSet(
        columns=columns,
        rows=rows,
        affected_rows=int(meta.get("changes") or meta.get("rows_written") or 0),
        last_insert_rowid=_opt_int(meta.get("last_row_id")),
        rows_read=int(meta.get("rows_read") or 0),
    )


def _opt_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _error_text(body: dict) -> str:
    errors = body.get("errors") or body.get("error") or []
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return f"{first.get('code', '')} {first.get('message', '')}".strip()
        return str(first)
    return str(errors or "알 수 없는 오류")
