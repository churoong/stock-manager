"""테스트가 **전역 상태를 남기지 않는지** 본다 (docs/infra.md 25.135).

**왜 필요한가.** 25.133·25.134 에서 같은 모양을 두 번 봤다 — 모듈 전역에 무언가를 모아 두는
코드는 **호출 사이**에 상태가 살고, 한 번씩만 불러 보는 검사는 그것을 못 잡는다.
그 위험은 **테스트 자신**에게도 있다. 한 테스트가 남긴 전역은 그 뒤에 도는 모든 테스트가
물려받는다.

실제로 그랬다. `tests/test_trigger_source.py` 가 `db.start_batch_run` 을 부르고 닫지 않아
`db._열린_실행` 에 둘을 남겼고, **그 뒤 495개 테스트가 그 상태로 돌았다.**

무엇이 나쁜가: `_열린_실행` 은 `guard()` 가 죽을 때 닫을 실행 기록의 스택이다. 남의 기록이
들어 있으면 뒤 테스트의 `guard()`·`fail_open_runs()` 가 **그것을 닫으려 든다.** 그리고
닫기가 실패하면 `_닫기()` 는 **진짜 `TursoClient()` 를 새로 연다** — 자격증명이 있는
환경에서 테스트를 돌리면 운영 DB 로 손이 간다.

**치우지 않고 말한다.** 조용히 비워 주면 다음에 또 새고 아무도 모른다.
"""

from __future__ import annotations

from typing import Any

import pytest

#: 이름 → (읽기, 비우기). 새 전역이 생기면 여기 한 줄을 더한다.
_전역들: dict[str, tuple[Any, Any]] = {}


def _준비() -> None:
    from batch.core import client as _c
    from batch.core import db as _d

    _전역들["db._열린_실행"] = (lambda: list(_d._열린_실행), _d._열린_실행.clear)
    _전역들["client._resolved"] = (
        lambda: _c._resolved,
        lambda: setattr(_c, "_resolved", None),
    )


_준비()


@pytest.fixture(autouse=True)
def _전역을_남기지_않는다(request: pytest.FixtureRequest):  # noqa: ANN201
    """테스트가 끝난 뒤 전역이 비어 있는지 본다. 남았으면 **누가 남겼는지 말하고** 비운다."""
    for 읽기, 비우기 in _전역들.values():
        if 읽기():
            비우기()

    yield

    남은 = {이름: 읽기() for 이름, (읽기, _) in _전역들.items() if 읽기()}
    for _, 비우기 in _전역들.values():
        비우기()

    if 남은:
        pytest.fail(
            f"테스트가 전역 상태를 남겼다: {남은}\n"
            "  뒤에 도는 모든 테스트가 이것을 물려받는다 (docs/infra.md 25.135).\n"
            "  연 실행은 `db.finish_batch_run` 으로 닫고, 일부러 남기는 검사라면\n"
            "  그 테스트 안에서 직접 비워라 (`tests/test_stale_runs.py` 가 그렇게 한다)."
        )


@pytest.fixture(autouse=True)
def _백테스트_위험_캐시를_비운다():  # noqa: ANN201
    """백테스트 리스크 지표 캐시(25.625)는 운영에선 `run` 이 비운다. 테스트는 `run` 없이 `build_pit_inputs` 를 부르기도 해
    **앞 테스트의 값이 섞일 수 있었다**(교차검증: 길이·첫날·마지막 값이 같고 중간만 다르면 앞 값이 나왔다). 채우는 것은
    정상이라 실패로 보지 않고 매번 비운다 (docs/infra.md 25.628)."""
    from batch.jobs import backtest as _bt

    _bt._위험_캐시.clear()
    yield
    _bt._위험_캐시.clear()


# ----------------------------------------------------------------------
# **테스트의 SQLite 는 외래키를 켠다** (docs/infra.md 25.226)
# ----------------------------------------------------------------------
#
# SQLite 는 연결마다 `PRAGMA foreign_keys` 가 꺼진 채 시작한다. 그래서 테스트용 메모리 DB 는 외래키를 한 번도
# 보지 않았고, 25.225(판 적이 있는 매수를 지우면 복기 행 때문에 외래키에 걸린다)를 어떤 테스트도 못 잡았다.
# 운영 DB 가 외래키를 강제하는지는 모른다 `[확인필요]` — 모르면 **더 엄한 쪽**으로 시험한다.
# 테스트마다 `sqlite3.connect` 를 따로 부르므로 여기서 한 번 감싼다.
import sqlite3 as _sqlite3  # noqa: E402

_원래_연결 = _sqlite3.connect


def _외래키를_켠_연결(*args: Any, **kwargs: Any) -> _sqlite3.Connection:
    연결 = _원래_연결(*args, **kwargs)
    연결.execute("PRAGMA foreign_keys = ON")
    return 연결


_sqlite3.connect = _외래키를_켠_연결  # type: ignore[assignment]

