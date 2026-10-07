"""`docs/infra.md` 25.0 의 목록이 **제목과 어긋나지 않는지** (docs/infra.md 25.0).

25절이 68개로 불어나 목록을 만들었다. 그런데 손으로 적은 목록은 **반드시 낡는다** —
새 절을 더하면서 목록을 고치는 것을 잊는다. 그러면 목록이 있어도 거기 없는 절이 생기고,
"목록에 없으니 그런 기록은 없겠지" 로 읽힌다. **없는 것보다 나쁘다.**

그래서 제목에서 직접 뽑아 대조한다. 어긋나면 이 테스트가 어느 줄인지 알려 준다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent
문서 = 뿌리 / "docs" / "infra.md"
글 = 문서.read_text(encoding="utf-8")

#: `### 25.7 제목` 에서 (번호, 제목). 25.0 자신은 목록이라 뺀다
제목들 = [(n, t.replace("**", "").strip()) for n, t in re.findall(r"^### (25\.\d+) (.+)$", 글, re.M) if n != "25.0"]


def 목록행() -> list[tuple[str, str]]:
    """25.0 의 '전체 목록' 표에서 (번호, 무엇)."""
    조각 = 글.split("#### 전체 목록", 1)[1].split("\n### ", 1)[0]
    return [
        (m.group(1), m.group(2).strip())
        for m in re.finditer(r"^\|\s*(25\.\d+)\s*\|\s*(.+?)\s*\|$", 조각, re.M)
    ]


def test_읽어_냈다() -> None:
    """정규식이 빗나가 0개면 아래가 전부 공짜로 통과한다."""
    assert len(제목들) >= 60
    assert len(목록행()) >= 60


def test_목록이_제목과_같다() -> None:
    빠짐 = [f"{n} {t}" for n, t in 제목들 if n not in {행[0] for 행 in 목록행()}]
    남음 = [n for n, _ in 목록행() if n not in {x[0] for x in 제목들}]

    assert not 빠짐, "25.0 목록에 없는 절이 있다. 아래를 더하라:\n  " + "\n  ".join(빠짐)
    assert not 남음, f"25.0 목록이 없는 절을 가리킨다: {남음}"


def test_목록의_설명이_제목_그대로다() -> None:
    """설명을 따로 쓰면 제목과 갈라진다 — 25.0 이 경고하는 바로 그 모양이다."""
    제목맵 = dict(제목들)
    어긋남 = [f"{n}: 목록 {설명!r} ≠ 제목 {제목맵[n]!r}" for n, 설명 in 목록행() if 설명 != 제목맵.get(n)]

    assert not 어긋남, "\n  ".join(어긋남)


def test_번호가_겹치지_않는다() -> None:
    번호 = [n for n, _ in 제목들]

    assert len(번호) == len(set(번호)), f"겹치는 번호: {[n for n in 번호 if 번호.count(n) > 1]}"


def test_되풀이된_모양_표가_가리키는_절이_모두_있다() -> None:
    """모양 표는 손으로 쓴다. 가리키는 절이 없으면 읽는 사람을 헛걸음시킨다."""
    조각 = 글.split("#### 되풀이된 모양", 1)[1].split("#### 전체 목록", 1)[0]
    가리킨것 = set(re.findall(r"25\.\d+", 조각))
    있는것 = {n for n, _ in 제목들}

    assert 가리킨것, "모양 표에서 절 번호를 하나도 못 찾았다"
    assert 가리킨것 <= 있는것, f"없는 절을 가리킨다: {sorted(가리킨것 - 있는것)}"


# ----------------------------------------------------------------------
# 인계 메모가 가리키는 범위 (2026-09-21, docs/infra.md 25.0)
# ----------------------------------------------------------------------
#
# **문이 낡으면 목록이 낡는 것보다 나쁘다.** `docs/handoff.md` 는 다음 세션이 여는 문이고
# (CLAUDE.md 기록 규칙: "이 파일만 읽고도 이어서 작업할 수 있어야 한다"), 거기에
# "찾은 것은 infra.md 25.32~25.86 에 있다" 처럼 **범위**를 적어 두었다.
#
# 새 절을 더하면서 그 범위를 안 고치면, 다음 사람은 **범위 밖의 절을 아예 안 본다.**
# 목록이 낡은 것과 같은 병인데(25.0), 이쪽은 읽는 사람이 "여기까지가 전부" 라고 믿는다.

인계 = (뿌리 / "docs" / "handoff.md").read_text(encoding="utf-8")


def 마지막_절() -> str:
    """지금 infra.md 에 있는 가장 큰 25.N."""
    return max(제목들, key=lambda x: int(x[0].split(".")[1]))[0]


def test_인계_메모가_범위를_적어_둔다() -> None:
    """0개면 아래가 공짜로 통과한다."""
    assert re.search(r"25\.\d+\s*~\s*25\.\d+", 인계), "인계 메모에 infra 범위 표기가 없다 — 모양이 바뀌었나"


def test_범위의_끝이_실제_마지막_절이다() -> None:
    끝 = [m.group(1) for m in re.finditer(r"25\.\d+\s*~\s*(25\.\d+)", 인계)]
    마지막 = 마지막_절()

    assert 끝, "범위 표기를 못 읽었다"
    assert all(e == 마지막 for e in 끝), (
        f"인계 메모가 가리키는 범위의 끝이 {sorted(set(끝))} 인데 실제 마지막 절은 {마지막} 이다.\n"
        "다음 사람은 **범위 밖의 절을 아예 안 본다.** docs/handoff.md 의 범위를 고쳐라"
    )
