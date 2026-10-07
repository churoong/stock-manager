"""Turso 접속 클라이언트.

공식 파이썬 라이브러리 대신 HTTP 경로를 직접 쓴다. 이유는 둘이다.
  1. libsql-client 는 2024-05 이후 배포가 없고, 지금 Turso 에 401 을 받는다
  2. /v2/pipeline 은 2026-09-16 에 실제 호출로 동작을 확인했다

의존성이 requests 하나뿐이라 라이브러리가 낡아 깨질 위험도 없다.

프로토콜 요약
  POST https://<host>/v2/pipeline
  Authorization: Bearer <토큰>
  {"requests": [{"type": "execute", "stmt": {"sql": ..., "args": [...]}},
                {"type": "close"}]}

값은 타입이 붙은 형태로 오간다. 정수도 문자열로 실려 오므로 되돌려 놓아야 한다.
  {"type": "integer", "value": "1"}  {"type": "text", "value": "가"}  {"type": "null"}
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from batch import config

log = logging.getLogger(__name__)

TIMEOUT = 60

# 일시적 실패를 다시 보내는 횟수와 간격(초).
#
# 2026-09-17 국내 백필이 두 번 죽었다. 한 번은 읽기 60초 초과(조회를 좁혀 고침),
# 한 번은 쓰기 중 "HTTP 502 upstream forward failed" 였다(실행 35177179867, 코스닥 10일째).
# 둘 다 Turso 쪽 일시 장애다. 배치의 쓰기는 ON CONFLICT 로 같은 행을 덮어쓰므로
# 같은 요청을 다시 보내도 결과가 같다. 그래서 짧게 물러섰다 다시 보낸다.
#
# 다시 보내면 안 되는 경우도 있다. api_usage 카운터처럼 더하는 쓰기는 두 번 세어질 수
# 있다. 한도 판단을 조금 보수적으로 만들 뿐이라 감수한다.
RETRY_DELAYS = (3, 10, 30)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class TursoError(RuntimeError):
    pass


def _host(database_url: str) -> str:
    """libsql:// 또는 https:// 주소에서 호스트만 뽑는다."""
    host = database_url.strip()
    for prefix in ("libsql://", "https://", "http://", "wss://", "ws://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    return host.split("/")[0]


def _encode(value: Any) -> dict[str, Any]:
    """파이썬 값을 프로토콜 표현으로 바꾼다."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        # bool 을 int 보다 먼저 본다. 파이썬에서 bool 은 int 의 하위형이다.
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode()}
    return {"type": "text", "value": str(value)}


def _decode(cell: dict[str, Any]) -> Any:
    """프로토콜 표현을 파이썬 값으로 되돌린다."""
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "blob":
        return base64.b64decode(cell.get("base64", ""))
    return cell.get("value")


# 쓴 행이 이만큼 쌓이면 카운터를 갱신한다. 매번 갱신하면 그 UPDATE 가 왕복을 하나씩 더 만든다.
# 5,000 은 대량 적재(백필 한 조각 = 수천 행) 한 번에 한 번쯤 갱신되는 크기다 [확인필요: 실측으로 조정]
WRITE_FLUSH_ROWS = 5_000
# 읽기는 단위가 훨씬 커서(백테스트 한 번이 수백만 행) 문턱도 크게 잡는다 [확인필요: 실측으로 조정]
READ_FLUSH_ROWS = 50_000


class RowCounter:
    """센 행을 모았다가 문턱을 넘으면 그만큼 내보낸다. 쓰기·읽기가 같은 구조를 쓴다."""

    def __init__(self, flush_rows: int) -> None:
        self.flush_rows = flush_rows
        self.total = 0  # 이 클라이언트가 산 동안의 합계 (로그·테스트용)
        self.pending = 0  # 아직 기록하지 않은 행 수

    def add(self, rows: int) -> int:
        if rows <= 0:
            return 0
        self.total += rows
        self.pending += rows
        return self.take() if self.pending >= self.flush_rows else 0

    def take(self) -> int:
        rows, self.pending = self.pending, 0
        return rows


class WriteCounter(RowCounter):
    """쓴 행 수를 모았다가 일정량마다 내보낸다 (docs/infra.md 23절).

    **왜 클라이언트에서 세나.** 2026-09-17 에 Turso 월 쓰기 한도를 넘겨 운영 DB 가 잠겼다.
    그 뒤 몇몇 적재 함수가 스스로 센 행 수를 기록했는데, `client.batch()` 를 직접 부르는 곳
    (점수·팩터·유니버스·신호…)은 세지 않아 **카운터가 실제보다 훨씬 작게 보였다.** 적게 보이는
    카운터는 없느니만 못하다 — 안전하다고 착각하게 만든다. 그래서 응답의 affected_row_count 를
    한 곳에서 센다.
    """

    def __init__(self, flush_rows: int = WRITE_FLUSH_ROWS) -> None:
        super().__init__(flush_rows)


class ReadCounter(RowCounter):
    """서버가 훑은 행 수. 읽기 한도(월 5억)를 넘겨 계정이 막힌 적이 있다 (docs/infra.md 24절)."""

    #: **이 프로세스의 모든 클라이언트**가 읽은 합 (docs/infra.md 25.885). 한 실행이 클라이언트를 여럿 열기도 해서
    #: (`--pin`·하위 작업) 클라이언트 하나의 `total` 로는 실행별 읽기량을 못 잰다. `db.start/finish_batch_run` 이
    #: 차이를 적는다
    process_total = 0

    def __init__(self, flush_rows: int = READ_FLUSH_ROWS) -> None:
        super().__init__(flush_rows)

    def add(self, rows: int) -> int:
        if rows > 0:
            ReadCounter.process_total += rows
        return super().add(rows)


@dataclass
class ResultSet:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    affected_rows: int = 0
    last_insert_rowid: int | None = None
    # 서버가 실제로 훑은 행 수. 응답에 있으면 채운다(없으면 0 — 지어내지 않는다).
    # 돌려받은 행 수와 다르다: 인덱스를 못 타면 1행을 얻으려고 수만 행을 훑는다. 한도는 이쪽을 센다
    rows_read: int = 0

    def __len__(self) -> int:
        return len(self.rows)

    def scalar(self) -> Any:
        """첫 행 첫 열. 집계 쿼리에 쓴다."""
        if not self.rows:
            return None
        return self.rows[0][0]

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


class TursoClient:
    """한 번의 요청에 여러 문장을 실어 보낸다.

    왕복이 곧 지연이므로 배치에서는 가능한 한 묶어 보낸다.
    """

    def __init__(self, database_url: str | None = None, auth_token: str | None = None):
        url = database_url or config.require("TURSO_DATABASE_URL")
        self._token = auth_token or config.require("TURSO_AUTH_TOKEN")
        self._endpoint = f"https://{_host(url)}/v2/pipeline"
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
        )
        self.writes = WriteCounter()
        self.reads = ReadCounter()
        self._recording = False  # 카운터를 적는 동안 그 쓰기·읽기를 또 세지 않게

    def close(self) -> None:
        self._flush_writes(self.writes.take())
        self._flush_reads(self.reads.take())
        self._session.close()

    def _flush_writes(self, rows: int) -> None:
        """쌓인 쓰기 행 수를 api_usage 에 적는다. 실패해도 본 작업을 막지 않는다.

        db 를 여기서 늦게 불러온다(모듈 맨 위에서 부르면 db → turso → db 로 돈다).
        """
        if rows <= 0:
            return
        if self._recording:
            # 카운터를 적는 동안 일어난 쓰기다. 여기서 버리면 그만큼 영영 세지 않는다.
            # 다시 쌓아 두고 다음 기회(다음 문턱 또는 close)에 함께 적는다
            self.writes.pending += rows
            return
        self._recording = True
        try:
            from batch.core import db

            db.record_rows_written(self, rows)
        except Exception:  # noqa: BLE001 — 카운터가 본 작업을 막으면 안 된다
            log.warning("쓰기 카운터를 갱신하지 못했습니다")
        finally:
            self._recording = False

    def _flush_reads(self, rows: int) -> None:
        """서버가 훑은 행 수를 api_usage 에 적는다. 쓰기와 같은 이유로 클라이언트 한 곳에서 센다."""
        if rows <= 0:
            return
        if self._recording:
            self.reads.pending += rows
            return
        self._recording = True
        try:
            from batch.core import db

            db.record_rows_read(self, rows)
        except Exception:  # noqa: BLE001 — 카운터가 본 작업을 막으면 안 된다
            log.warning("읽기 카운터를 갱신하지 못했습니다")
        finally:
            self._recording = False

    def _post_with_retry(self, payload: dict) -> requests.Response:
        """일시적 실패(연결·타임아웃·5xx·429)만 다시 보낸다. 401 같은 설정 오류는 바로 올린다."""
        attempts = len(RETRY_DELAYS) + 1
        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.post(self._endpoint, json=payload, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if attempt == attempts:
                    raise TursoError(f"Turso 통신 실패: {exc}") from exc
                reason = str(exc)
            else:
                if resp.status_code not in RETRYABLE_STATUS or attempt == attempts:
                    return resp
                reason = f"HTTP {resp.status_code}"
            delay = RETRY_DELAYS[attempt - 1]
            log.warning("Turso 일시 실패 (%s), %d초 뒤 다시 보냅니다 (%d/%d)", reason, delay, attempt, attempts - 1)
            time.sleep(delay)
        raise TursoError("Turso 재시도 소진")  # 도달하지 않는다

    def __enter__(self) -> TursoClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        return self.batch([(sql, args or [])])[0]

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        """여러 문장을 한 번에 보낸다. 앞에서 실패하면 뒤는 실행되지 않는다."""
        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {"sql": sql, "args": [_encode(a) for a in args]},
                }
                for sql, args in statements
            ]
            + [{"type": "close"}]
        }

        resp = self._post_with_retry(payload)

        if resp.status_code == 401:
            raise TursoError(
                "Turso 인증 실패(401). TURSO_AUTH_TOKEN 이 이 데이터베이스의 "
                "토큰인지 확인하세요"
            )
        if resp.status_code != 200:
            raise TursoError(f"Turso HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise TursoError("Turso 응답을 해석하지 못했습니다") from exc

        results: list[ResultSet] = []
        for item in body.get("results", []):
            if item.get("type") == "error":
                message = item.get("error", {}).get("message", "알 수 없는 오류")
                raise TursoError(f"SQL 실패: {message}")
            response = item.get("response", {})
            if response.get("type") != "execute":
                continue  # close 응답
            results.append(_to_result_set(response.get("result", {})))

        # 읽기는 affected_row_count 가 0 이라 그대로 더해도 된다.
        # ON CONFLICT DO NOTHING 으로 건너뛴 행도 0 이라 "실제로 쓴 행" 만 센다
        self._flush_writes(self.writes.add(sum(r.affected_rows for r in results)))
        self._flush_reads(self.reads.add(sum(r.rows_read for r in results)))
        return results


def _to_result_set(result: dict[str, Any]) -> ResultSet:
    columns = [col.get("name") or "" for col in result.get("cols", [])]
    rows = [tuple(_decode(cell) for cell in row) for row in result.get("rows", [])]
    rowid = result.get("last_insert_rowid")
    return ResultSet(
        columns=columns,
        rows=rows,
        affected_rows=result.get("affected_row_count", 0) or 0,
        last_insert_rowid=int(rowid) if rowid is not None else None,
        # hrana 응답의 rows_read. 이름이 다르거나 없는 버전이 있어 둘 다 본다 [확인필요: 운영 응답으로]
        rows_read=int(result.get("rows_read") or (result.get("stat") or {}).get("rows_read") or 0),
    )

