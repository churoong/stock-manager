"""한도 오류를 알아보는가 — 파이썬 쪽, 그리고 웹과 같은 답을 내는가.

문구 표는 `tests/fixtures/db_quota_messages.json` 에 있고 **웹도 같은 파일을 읽는다**
(`web/__tests__/dbQuota.test.ts`). 25.42 에서 판정표에 했던 것과 같은 방식이다.

**왜 이것이 중요한가.** "한도에 걸렸다" 는 고장이 아니라 **기다리면 풀리는 상태**다.
두 언어가 이 판정을 다르게 하면:

- 배치는 한도를 못 알아보고 **실패로 끝난다** → 매일 실패 알림이 쏟아지고 진짜 고장이 묻힌다
- 웹은 한도를 "다른 장애" 로 읽고 **Turso 로 간다** → 막힌 DB 를 읽는다
  (`probeTurso()` 의 `blocked` 가 `db_backend_decision.json` 의 입력이다)

여기서는 **분류**를 맞춘다. 돌려주는 한국어 문장은 두 언어가 조금 다르다(괄호·줄바꿈).
사람이 읽는 문장을 글자까지 묶으면 고치기가 번거로워지고, 정작 중요한 것은 분류다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from batch.core import client as backend
from batch.core import db
from batch.core.turso import TursoError

뿌리 = Path(__file__).resolve().parent.parent
표파일 = Path(__file__).resolve().parent / "fixtures" / "db_quota_messages.json"
표 = json.loads(표파일.read_text(encoding="utf-8"))["표"]

#: 분류 → 돌려주는 문장에 반드시 들어 있어야 할 말. 두 언어가 같이 지킨다
표시 = {"d1_daily": "D1 하루 쓰기 한도", "turso_monthly": "Turso 월 한도"}


def 이름(행: dict) -> str:
    return f"{행['쪽']}: {행['문구'][:45] or '(빈 문구)'}"


def test_표를_읽어_냈다() -> None:
    """읽기가 조용히 빈 목록을 내면 아래가 0번 돌고 전부 통과한다."""
    assert len(표) >= 10
    assert {행["쪽"] for 행 in 표} == {"d1_daily", "turso_monthly", None}


def test_표가_지어낸_문구로_채워지지_않는다() -> None:
    """`실측: false` 는 **아직 받아 본 적 없는 모양**이다. 규칙의 가지를 붙들려고 두지만,
    이것이 늘어나면 표가 "우리가 상상한 오류" 목록이 되고 진짜 응답이 바뀌어도 모른다.
    """
    지어낸것 = [행 for 행 in 표 if not 행["실측"]]

    assert len(지어낸것) <= 2, (
        f"실측 아닌 줄이 {len(지어낸것)}개다. 늘리기 전에 그 문구를 정말 받아 봤는지 먼저 본다"
    )


@pytest.mark.parametrize("행", 표, ids=이름)
def test_문구를_제대로_가른다(행: dict) -> None:
    답 = db.quota_reason(행["문구"])

    if 행["쪽"] is None:
        assert 답 is None, f"{행['왜']} — 한도로 읽으면 안 된다"
    else:
        assert 답 is not None, f"{행['왜']} — 한도를 못 알아봤다"
        assert 표시[행["쪽"]] in 답


@pytest.mark.parametrize("행", [행 for 행 in 표 if 행["쪽"]], ids=이름)
def test_예외로_와도_알아본다(행: dict) -> None:
    """`guard()` 는 **잡은 예외**를 넘긴다. 문자열만 받으면 거기서 못 알아본다."""
    assert db.quota_reason(TursoError(행["문구"])) is not None


def test_대소문자를_가리지_않는다() -> None:
    # 바깥 서비스가 문구의 대소문자를 바꾼 적이 있다. 그것 하나로 건너뜀이 실패가 되면 안 된다
    assert db.quota_reason("EXCEEDED D1'S FREE TIER DAILY ROW WRITE LIMIT") is not None
    assert db.quota_reason("OPERATION WAS BLOCKED: SQL READ OPERATIONS ARE FORBIDDEN") is not None


class Test언어를_건너뛰는_이름들:
    """두 언어가 **같은 글자**를 써야만 동작하는 것들. 오타 하나면 조용히 멈춘다."""

    def test_복귀_표시_열쇠가_같다(self) -> None:
        """배치가 이 열쇠로 적고 웹이 이 열쇠로 읽는다. 다르면 **자동 복귀가 영영 안 된다.**

        웹은 표시를 못 찾아 계속 D1 을 읽고, 아무 오류도 나지 않는다 (docs/infra.md 25.12).
        """
        소스 = (뿌리 / "web" / "lib" / "db.ts").read_text(encoding="utf-8")
        웹값 = re.search(r'export const RETURN_MARKER = "([^"]+)"', 소스)

        assert 웹값, "web/lib/db.ts 에서 RETURN_MARKER 를 찾지 못했다"
        assert 웹값.group(1) == backend.RETURN_MARKER

    def test_열쇠가_settings_에_들어갈_모양이다(self) -> None:
        # settings 화면은 아는 열쇠만 보여 주고 나머지는 버린다(web/app/api/settings/route.ts).
        # 운영용 열쇠가 사용자 설정과 겹치면 화면이 그것을 설정으로 읽는다
        assert backend.RETURN_MARKER.startswith("db_")
