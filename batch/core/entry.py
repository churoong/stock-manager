"""배치 진입점 공통 처리 (docs/infra.md 25.6).

**DB 한도는 고장이 아니다.** D1 하루 쓰기 한도나 Turso 월 한도에 걸린 작업은 실패가 아니라
"건너뜀" 으로 끝낸다. 실패로 끝내면 GitHub 이 실패 메일을 보내고, 따라잡는 며칠 동안 그 메일이
매일 쌓인다 — 진짜 실패가 그 속에 묻힌다.

일일 배치(`jobs/daily.py`)는 따로 텔레그램 알림을 한 번 보낸다(리포트가 안 나가는 것을 알려야 한다).
나머지 작업은 로그 한 줄로 충분하다.

쓰는 법 (각 작업의 맨 끝)
    if __name__ == "__main__":
        sys.exit(guard(main))

**D1 임시 운영 중에 쉬는 워크플로** (docs/infra.md 25.14): 워크플로가 환경변수 `SKIP_ON_D1=1` 을 주면
지금 DB 가 D1 일 때 작업을 시작하지 않고 0 으로 끝낸다. 따라잡기(d1-catchup)가 매일 하는 일을 주간
작업이 또 하거나(유니버스·재무·점수…), D1 에 데이터가 없는 미국 작업이 하루 쓰기 한도를 태우지 않게 한다.
워크플로마다 조건을 달지 않고 여기 한 곳에서 본다. Turso 로 돌아가면 저절로 원래대로 돈다.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from batch.core import db

log = logging.getLogger(__name__)

#: 이 값이 "1" 인 워크플로의 작업은 D1 임시 운영 중에 쉰다
SKIP_ON_D1_ENV = "SKIP_ON_D1"

#: **아무 일도 하지 않았다**는 표식 (2026-09-21, docs/infra.md 25.84).
#:
#: 이 글자로 시작하는 줄이 로그에 있으면 워크플로가 **이슈 코멘트를 올리지 않는다.**
#: 클라우드 세션이 읽는 것은 이슈 #1 "운영 출력" 의 **마지막 코멘트** 하나인데(25.17),
#: 아무것도 안 한 실행이 매일 그 자리를 덮으면 정작 읽어야 할 것이 밀려난다.
#:
#: **`건너뜀: `(DB 한도)과 다르다** — 그쪽은 알려야 할 운영 사건이라 올린다.
#: 여기 있는 이유: 진입점의 공통 약속이고, 일일 배치(`jobs/daily.py`)와
#: 복귀 스크립트(`scripts/turso_return.py`)가 **같은 글자**를 써야 한다.
NOTHING_DONE = "실행하지 않음: "


def paused_on_d1() -> str | None:
    """이 작업이 지금 쉬어야 하면 사유, 아니면 None."""
    if os.environ.get(SKIP_ON_D1_ENV, "").strip() != "1":
        return None
    from batch.core import client as backend

    if backend.resolved_backend() != backend.D1:
        return None
    return (
        "D1 임시 운영 중이라 이 작업은 쉽니다. 국내는 따라잡기(d1-catchup)가 대신하고,"
        " 미국은 Turso 로 돌아간 뒤 다시 돈다 (docs/infra.md 25.14)"
    )


def guard(main: Callable[[], int]) -> int:
    """main 을 돌리고, DB 한도 오류면 사유를 찍고 0(건너뜀)으로 끝낸다. 다른 오류는 그대로 올린다."""
    pause = paused_on_d1()
    if pause:
        print(f"쉼: {pause}")
        return 0
    try:
        return main()
    except Exception as exc:
        reason = db.quota_reason(exc)
        if reason is None:
            # **진짜 실패도 기록을 닫는다** (2026-09-22, docs/infra.md 25.110).
            # 전에는 여기서 그냥 올려 보냈고, `batch_runs` 행은 `running` 으로 남았다.
            # 여섯 시간 뒤 `reap_stale_runs()` 가 `skipped`("끝맺지 못한 기록")로 닫아서
            # **실패 사유가 DB 어디에도 안 남았다** — 화면은 "건너뜀" 이라고 말한다.
            # 닫는 글자는 `failed` 다. 건너뜀과 실패는 다른 사실이다.
            db.fail_open_runs(str(exc))
            raise
        log.warning("DB 한도로 건너뜀: %s", exc)
        # 이미 열어 둔 batch_runs 행을 `running` 인 채로 두면 운영 이력이 거짓말을 한다
        # (2026-09-19 따라잡기의 financials 가 그렇게 남았다). 한도에 걸린 직후라 이 UPDATE 도
        # 막힐 수 있다 — 막히면 다음 실행의 reap_stale_runs() 가 닫는다
        db.close_current_run("skipped", f"DB 한도로 건너뜀: {reason}")
        print(f"건너뜀: {reason}")
        return 0
