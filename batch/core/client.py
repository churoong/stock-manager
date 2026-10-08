"""DB 백엔드 고르기 (docs/infra.md 25절).

`DB_BACKEND` 하나로 Turso 와 Cloudflare D1 을 오간다. 둘 다 SQLite 라 **질의·마이그레이션은 같다.**
배치의 모든 작업이 여기서 클라이언트를 받는다.

왜 이름이 TursoClient 인가: 부르는 곳(작업 30여 개)과 테스트가 이미 그 이름으로 클라이언트를
만든다. 이름을 바꾸면 그 전부를 건드려야 하고, 전환하는 날 실수할 여지가 늘어난다.
겉이름은 두고 속만 바꾼다.

  DB_BACKEND=turso  (기본)  → batch.core.turso.TursoClient
  DB_BACKEND=d1             → batch.core.d1.D1Client
  DB_BACKEND=auto           → 복귀 표시가 생기기 전까지 D1, 생기면 Turso (docs/infra.md 25.12)

**auto 는 10월 1일 자동 복귀를 위해 둔다.** GitHub·Vercel 의 설정값을 자동으로 바꾸려면 권한이 큰
관리자 토큰이 필요하다. 대신 실행할 때마다 D1 의 복귀 표시를 보고 스스로 고른다. 표시는
`scripts/turso_return.py` 가 Turso 에 표와 사람이 넣은 데이터를 옮긴 **뒤에** 남긴다.
auto 는 "D1 로 운영하며 복귀를 기다리는" 모드다. 평소에는 turso 로 둔다.
"""

from __future__ import annotations

import logging
from typing import Any

from batch import config

log = logging.getLogger(__name__)

TURSO = "turso"
D1 = "d1"
AUTO = "auto"

# 복귀를 마쳤다는 표시. D1 의 settings 에 적는다 — Turso 가 다시 막혀도 읽을 수 있는 곳이어야 한다
RETURN_MARKER = "db_return_done_at"

_resolved: str | None = None  # 한 프로세스 안에서는 한 번만 고른다 (작업 도중에 DB 가 바뀌면 안 된다)

#: **한 워크플로 안에서도** 한 번만 고르게 하는 환경변수 (docs/infra.md 25.472). auto 는 프로세스마다 복귀 표시를 다시
#: 보므로, 한 워크플로의 여러 파이썬(환율→재계산, 국내→미국)이 복귀 표시가 도중에 생기면 앞은 D1·뒤는 Turso 에 썼다.
#: 첫 단계가 `python -m batch.core.client --pin >> "$GITHUB_ENV"` 로 정한 값을 뒤 단계가 그대로 쓴다.
#: `DB_BACKEND` 를 덮지 않고 따로 두는 것은 워크플로 `env:` 에 적힌 값과 어느 쪽이 이기는지에 기대지 않기 위해서다
PIN_ENV = "DB_BACKEND_PINNED"


def backend() -> str:
    """설정값 그대로 (turso · d1 · auto)."""
    return (config.get("DB_BACKEND", TURSO) or TURSO).strip().lower()


def decide(turso_ok: bool, turso_quota_blocked: bool, returned: bool | None) -> str:
    """auto 모드의 판정 (docs/infra.md 25.12).

    **복귀 표시가 판정의 중심이다.** Turso 가 살아났다는 것만으로는 돌아가지 않는다.
    리셋 직후의 Turso 에는 막혀 있던 동안 생긴 표(0028~)도, D1 에서 입력한 매매도 없다.
    처음에는 "살아 있으면 Turso" 로 짰다가, 리셋과 복귀 워크플로 사이(최대 몇 시간)에 배치와 웹이
    옛 스키마의 Turso 를 쓰게 되는 틈을 찾아 고쳤다 (2026-09-18).

    - 복귀 표시가 있다 → Turso. 복귀 뒤 Turso 가 다시 막혀도 **D1 로 되돌아가지 않는다.**
      두 DB 에 데이터가 갈라진다. 막힌 채로 두고 화면 띠·건너뜀 알림이 알린다. 그때는 사람이 정한다
    - 복귀 표시가 없다 → D1. Turso 가 살아 있어도 복귀 스크립트가 옮기기 전이다
    - D1 을 읽지 못해 모른다(None) → Turso 상태로 정한다: 살아 있으면 Turso, **한도로** 막혔으면 D1,
      다른 오류(연결 끊김 등)면 Turso — 잠깐의 장애로 D1 에 쓰기 시작하면 안 된다
    """
    if returned is True:
        return TURSO
    if returned is False:
        return D1
    if turso_ok:
        return TURSO
    return D1 if turso_quota_blocked else TURSO


def _probe_turso() -> tuple[bool, bool]:
    """(살아 있다, 한도로 막혔다). 표를 읽지 않는 `SELECT 1` 이라 예산을 쓰지 않는다."""
    from batch.core import db
    from batch.core.turso import TursoClient as _Turso

    try:
        with _Turso() as client:
            client.execute("SELECT 1")
        return True, False
    except Exception as exc:  # noqa: BLE001 — 판정이 목적이다
        return False, db.quota_reason(exc) is not None


def read_return_marker() -> bool | None:
    """복귀 표시가 있는가. D1 의 settings 에서 읽는다. D1 을 읽지 못하면 None (모른다).

    D1Client 가 연결 오류·429·5xx 는 스스로 몇 번 다시 보낸다. 그래도 안 되면 모르는 것이다.
    """
    try:
        from batch.core.d1 import D1Client

        with D1Client() as client:
            rs = client.execute("SELECT value FROM settings WHERE key = ?", [RETURN_MARKER])
        return bool(rs.rows)
    except Exception:  # noqa: BLE001 — 판정이 목적이다
        return None


def returned_to_turso() -> bool:
    """복귀를 마쳤는가. 모르면 False — 복귀 스크립트는 옮기기를 시도하고, D1 을 못 읽으면 거기서 실패한다."""
    return read_return_marker() is True


#: D1 에 붙는 데 필요한 값 — `batch/core/d1.D1Client` 가 읽는 것과 같다
D1_ENV = ("D1_ACCOUNT_ID", "D1_DATABASE_ID", "D1_API_TOKEN")


def d1_configured() -> bool:
    """D1 값이 모두 있는가. 하나라도 비면 D1 으로 갈 수 없다."""
    return all((config.get(k, "") or "").strip() for k in D1_ENV)


def resolved_backend() -> str:
    """지금 실제로 쓸 백엔드. auto 면 한 번 살펴 정하고 프로세스가 끝날 때까지 그대로 쓴다."""
    global _resolved
    configured = backend()
    if configured != AUTO:
        return configured
    pinned = (config.get(PIN_ENV, "") or "").strip().lower()
    if pinned in (TURSO, D1):
        return pinned
    if _resolved is None and not d1_configured():
        # **D1 값이 없으면 auto 라도 Turso 에 머문다** (docs/infra.md 25.1029). 새 공개 저장소는 D1 시크릿을 넣지 않았다
        # (docs/public-repo.md D8). 그런데 변수가 auto 로 남아, Turso 가 월 한도로 막히자(2026-10-08) 모든 배치가
        # D1 으로 가려다 "D1 설정이 비어 있습니다" 로 **죽었다** — 한도 알림 대신 실패 메일, 일일 리포트도 실패로
        # 보였다.
        # Turso 에 머물면 한도 오류는 `entry.guard` 가 "건너뜀" 으로 끝내고 사유를 말한다
        _resolved = TURSO
        log.info("DB_BACKEND=auto 이지만 D1 값이 없어 Turso 를 쓴다")
    if _resolved is None:
        returned = read_return_marker()
        ok = blocked = False
        if returned is None:  # D1 을 못 읽을 때만 Turso 를 본다
            ok, blocked = _probe_turso()
        _resolved = decide(ok, blocked, returned)
        log.info("DB_BACKEND=auto → %s (복귀 표시 %s, Turso 살아 있음 %s, 한도 %s)", _resolved, returned, ok, blocked)
    return _resolved


def TursoClient(*args: Any, **kwargs: Any):  # noqa: N802 — 부르는 쪽이 이미 이 이름을 쓴다
    """설정에 맞는 DB 클라이언트를 만든다. 겉모습(execute·batch·close)은 둘이 같다."""
    if resolved_backend() == D1:
        from batch.core.d1 import D1Client

        return D1Client()
    from batch.core.turso import TursoClient as _Turso

    use_replica = kwargs.pop("use_replica", True)
    remote = _Turso(*args, **kwargs)
    # **시세 사본** (docs/infra.md 25.888): 워크플로가 맞춰 둔 사본이 있으면 시세·종목만 읽는 SELECT 를 거기서 읽는다.
    # 사본이 없거나 낡았거나 쓸 수 없다고 적혀 있으면 그대로 Turso 다
    path = config.get(price_replica_env(), "") if use_replica and not args else ""
    if path:
        from batch.core import price_replica

        conn = price_replica.usable_replica(path)
        if conn is not None:
            return price_replica.ReplicaClient(remote, conn)
    return remote


def price_replica_env() -> str:
    from batch.core.price_replica import REPLICA_ENV

    return REPLICA_ENV


if __name__ == "__main__":
    import sys

    # 표준 출력 한 줄 = GITHUB_ENV 한 줄. **auto 일 때만** 고정한다 (docs/infra.md 25.481, 교차검증) —
    # 단계 env 에만 DB_BACKEND 가 있는 워크플로에서 고정 단계가 설정값을 못 보면 기본값 turso 로 고정해,
    # 뒤 단계의 auto 판정(D1)을 덮어 막힌 Turso 로 보냈다. 고정할 것이 없으면 아무것도 적지 않는다
    if "--pin" in sys.argv[1:] and backend() == AUTO:
        logging.basicConfig(level=logging.INFO)  # 판정 로그를 표준 오류로 남긴다 — 그날 백엔드가 보이게
        print(f"{PIN_ENV}={resolved_backend()}")
