"""어느 DB 를 쓸지 정하는 표 — 파이썬 쪽 (`batch/core/client.decide`).

표 자체는 `tests/fixtures/db_backend_decision.json` 에 있다. **웹도 같은 파일을 읽는다**
(`web/__tests__/dbBackend.test.ts`). 왜 그렇게 했는지는 그 파일 머리말에 적었다 —
요약하면, 2026-09-21 까지 두 테스트가 같은 표를 **손으로 베껴** 갖고 있었고, 한쪽을
고치면 다른 쪽은 옛 표를 지키며 조용히 통과했다.

**어긋나면 무슨 일이 나나.** 배치는 쓰고 웹은 읽는다. 둘이 다른 DB 를 고르면 배치가
넣은 데이터를 웹이 엉뚱한 곳에서 찾는다. 화면은 "데이터가 없다" 고 말하고, 오류도 안
나고, 아무도 이유를 모른다. **지금처럼 DB 를 옮기는 중일 때 가장 위험한 고장이다.**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch.core import client as backend

표파일 = Path(__file__).resolve().parent / "fixtures" / "db_backend_decision.json"
표 = json.loads(표파일.read_text(encoding="utf-8"))["표"]


def 이름(행: dict) -> str:
    return f"복귀={행['returned']} 살아있음={행['turso_ok']} 한도={행['quota_blocked']} → {행['backend']}"


def test_표를_읽어_냈다() -> None:
    """읽기가 조용히 빈 목록을 내면 아래가 0번 돌고 전부 통과한다."""
    assert len(표) == 12, f"조합 3×2×2 를 다 적어야 한다. 지금 {len(표)}줄"

    조합 = {(행["returned"], 행["turso_ok"], 행["quota_blocked"]) for 행 in 표}
    assert len(조합) == 12, "같은 조합이 두 번 적혀 있다"


@pytest.mark.parametrize("행", 표, ids=이름)
def test_판정표(행: dict) -> None:
    답 = backend.decide(행["turso_ok"], 행["quota_blocked"], 행["returned"])

    assert 답 == 행["backend"], 행["왜"]


def test_돌려주는_값은_둘뿐이다() -> None:
    """`TURSO`·`D1` 상수와 실제로 같은 글자인가. 오타 하나면 어디서도 안 맞는다."""
    답들 = {backend.decide(행["turso_ok"], 행["quota_blocked"], 행["returned"]) for 행 in 표}

    assert 답들 <= {backend.TURSO, backend.D1}
    assert (backend.TURSO, backend.D1) == ("turso", "d1")


def test_모르는_경우에만_Turso_를_살펴본다() -> None:
    """D1 을 읽어 답을 안 경우에는 Turso 상태가 판정을 바꾸지 못해야 한다.

    바꾼다면 `resolved_backend()` 가 Turso 를 찔러 보지 않고 ok=False 를 넘기는 지금
    구조에서 **틀린 답**이 나온다 (web/lib/db.resolveBackend 도 같은 구조다).
    """
    for returned in (True, False):
        답들 = {
            backend.decide(ok, blocked, returned)
            for ok in (True, False)
            for blocked in (True, False)
        }
        assert len(답들) == 1, f"복귀={returned} 인데 Turso 상태에 따라 답이 갈린다: {답들}"
