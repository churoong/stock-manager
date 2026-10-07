"""코드가 가리키는 문서 절이 실제로 있는가.

**왜 있나.** 이 저장소에는 `docs/infra.md 25.14` 같은 참조가 **561개** 있다(2026-09-21 기준).
그것이 이 프로젝트의 **길 안내판**이다 — 기록 규칙이 "판단의 근거는 대화가 아니라 파일에
있어야 한다" 고 말하는데, 파일을 찾아가는 길이 그 참조다.

안내판이 없는 곳을 가리키면 다음 세션은 **없는 절을 찾느라 시간을 버린다.** 그리고 그것이
틀렸다는 것을 알기까지가 오래 걸린다 — 번호가 그럴듯해 보이기 때문이다.

실제로 하나가 끊겨 있었다. `web/app/api/recommend/route.ts` 가 인계 메모의 3.5 절을
가리켰는데, 인계 메모가 다시 쓰이면서 그 절이 사라졌다.

그래서 규칙이 둘이다.

1. 가리킨 절은 **있어야 한다**
2. **인계 메모(`handoff.md`)는 절 번호로 가리키지 않는다.** 매 세션 다시 쓰이는 문서다.
   거기 있는 내용이 오래 남을 것이면 `docs/` 의 제자리 문서로 옮기고 그쪽을 가리킨다
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
문서방 = 뿌리 / "docs"

#: `docs/infra.md 25.14` · `docs/infra.md 17절` · `docs/signals.md 3.5-2` 를 모두 잡는다
참조패턴 = re.compile(r"docs/([a-z_-]+\.md)\s+(\d+(?:\.\d+)*)")

#: `## 25.14 제목` · `## 🔴🔴 0. 제목` — 앞에 이모지나 기호가 붙어도 번호를 찾는다
제목패턴 = re.compile(r"^#{1,6}\s+[^\w\d]*\s*(\d+(?:\.\d+)*)", re.M)

훑는_확장자 = (".py", ".ts", ".tsx", ".yml", ".md", ".json")

#: 끊긴 채로 두기로 한 것. **이유를 적어야 들어올 수 있다** (지금은 비어 있어야 정상이다)
봐주는_참조: dict[tuple[str, str], str] = {}


def 훑을_파일들() -> list[Path]:
    out = [
        p
        for p in 뿌리.rglob("*")
        if p.is_file()
        and p.suffix in 훑는_확장자
        and not {"node_modules", "__pycache__", ".git", ".next"} & set(p.parts)
        # **자기 자신은 뺀다.** 규칙을 설명하는 글에는 규칙이 금지하는 모양이 그대로 나온다
        and p != Path(__file__).resolve()
    ]
    return sorted(out)


def 있는_절() -> dict[str, set[str]]:
    return {
        md.name: set(제목패턴.findall(md.read_text(encoding="utf-8")))
        for md in sorted(문서방.glob("*.md"))
    }


def 끊긴_참조() -> dict[tuple[str, str], list[str]]:
    """(문서, 절) → 그렇게 가리킨 파일들."""
    절 = 있는_절()
    끊김: dict[tuple[str, str], list[str]] = defaultdict(list)
    for 경로 in 훑을_파일들():
        for 문서, 번호 in 참조패턴.findall(경로.read_text(encoding="utf-8", errors="replace")):
            if 문서 not in 절:
                끊김[(문서, 번호)].append(f"{경로.relative_to(뿌리)} (문서 자체가 없다)")
            elif 번호 not in 절[문서] and (문서, 번호) not in 봐주는_참조:
                끊김[(문서, 번호)].append(str(경로.relative_to(뿌리)))
    return dict(끊김)


def test_훑을_것이_있다() -> None:
    """훑기가 조용히 0개를 내면 아래가 무조건 통과한다."""
    파일들 = 훑을_파일들()
    절 = 있는_절()

    assert len(파일들) > 150, f"{len(파일들)}개만 훑었다"
    assert "infra.md" in 절 and len(절["infra.md"]) > 30, "infra.md 의 절 제목을 못 읽었다"
    assert "0" in 절.get("todo-user.md", set()), "이모지가 붙은 제목을 못 읽는다"


def test_참조를_실제로_찾아낸다() -> None:
    """거르개가 아무것도 안 잡으면 이 파일 전체가 장식이 된다."""
    총수 = sum(
        len(참조패턴.findall(p.read_text(encoding="utf-8", errors="replace"))) for p in 훑을_파일들()
    )

    assert 총수 > 300, f"참조를 {총수}개만 찾았다 — 거르개가 망가졌다"


def test_가리킨_절이_모두_있다() -> None:
    끊김 = 끊긴_참조()

    보고 = "\n".join(
        f"  docs/{문서} {번호}  ← {', '.join(sorted(set(곳))[:3])}"
        for (문서, 번호), 곳 in sorted(끊김.items())
    )
    assert not 끊김, (
        "없는 문서 절을 가리킨다. 다음 사람이 그것을 찾느라 시간을 버린다"
        f" (docs/infra.md 25.46):\n{보고}"
    )


def test_인계_메모를_절_번호로_가리키지_않는다() -> None:
    """`handoff.md` 는 **매 세션 다시 쓰인다.** 절 번호가 살아남지 않는다.

    2026-09-21 에 실제로 끊겨 있던 참조가 이것이었다. 문서 안에서(`docs/*.md`) 서로
    가리키는 것은 함께 고쳐지므로 두고, **코드에서** 가리키는 것만 막는다.
    """
    범인 = [
        str(p.relative_to(뿌리))
        for p in 훑을_파일들()
        if p.suffix != ".md"
        and re.search(r"docs/handoff\.md\s+\d", p.read_text(encoding="utf-8", errors="replace"))
    ]

    assert not 범인, (
        "코드가 인계 메모를 절 번호로 가리킨다. 그 절은 다음 세션에 사라진다."
        " 오래 남을 내용이면 docs/ 의 제자리 문서로 옮기고 그쪽을 가리킨다:\n  "
        + "\n  ".join(범인)
    )


@pytest.mark.parametrize("열쇠", sorted(봐주는_참조))
def test_봐주는_참조는_이유가_적혀_있다(열쇠: tuple[str, str]) -> None:
    assert 봐주는_참조[열쇠].strip(), f"{열쇠} 의 이유가 비어 있다"


# ----------------------------------------------------------------------
# 사용자 할 일 목록의 머리말이 본문과 맞는가 (2026-09-21, docs/infra.md 25.88)
# ----------------------------------------------------------------------
#
# `docs/todo-user.md` 는 **사용자가 보고 실제로 행동하는** 유일한 문서다. 맨 위에
# "지금 할 일은 N 개입니다" 라고 요약해 두었는데, 항목의 표시(🔴)를 고치면서 그 줄을
# 잊으면 **요약과 본문이 다른 말을 한다.** 사람은 맨 위만 읽고 덮는다.
#
# 실제로 2026-09-21 에 그렇게 썼다 — 머리말에 "1번 하나" 라고 적어 놓고 본문에는 🔴 가 둘이었다.

TODO = 뿌리 / "docs" / "todo-user.md"


def _빨강() -> list[str]:
    글 = TODO.read_text(encoding="utf-8")
    return [줄 for 줄 in 글.splitlines() if 줄.startswith("## 🔴")]


def test_할_일_목록을_읽어_냈다() -> None:
    """표시가 하나도 안 잡히면 아래가 공짜로 통과한다."""
    글 = TODO.read_text(encoding="utf-8")

    assert len([줄 for 줄 in 글.splitlines() if 줄.startswith("## ")]) >= 5


def test_머리말의_개수가_본문과_같다() -> None:
    """맨 위 요약과 🔴 항목 수가 어긋나면 사람이 잘못 안다."""
    글 = TODO.read_text(encoding="utf-8")
    # **수관형사도 넣는다.** 한국어는 단위명사 앞에서 "둘" 이 아니라 "두 개" 다 —
    # 이 사전에 "두" 가 없어 테스트가 0 으로 읽고 엉뚱하게 실패했다 (2026-09-21)
    한글수 = {
        "하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3,
        "넷": 4, "네": 4, "다섯": 5, "여섯": 6,
    }  # fmt: skip

    m = re.search(r"지금 사람이 할 일은 🔴? ?(\S+?) ?(?:개|가지)?입니다", 글)
    assert m, "머리말에 '지금 사람이 할 일은 …' 요약이 없다 — 모양이 바뀌었나"

    적힌수 = 한글수.get(m.group(1)) or int(re.sub(r"\D", "", m.group(1)) or 0)
    assert 적힌수 == len(_빨강()), (
        f"머리말은 {적힌수}개라는데 본문의 🔴 는 {len(_빨강())}개다:\n  "
        + "\n  ".join(_빨강())
        + "\n사람은 맨 위만 읽고 덮는다"
    )


def test_끝난_것이_아직_빨강으로_남아_있지_않다() -> None:
    글 = TODO.read_text(encoding="utf-8")
    끝난것 = 글.split("## ✅ 끝난 것", 1)[-1]
    번호 = {re.search(r"\*\*(\d+)번", x).group(1) for x in re.findall(r"\*\*\d+번", 끝난것)} if "번" in 끝난것 else set()

    겹침 = [줄 for 줄 in _빨강() if any(f"## 🔴 {n}." in 줄 for n in 번호)]

    assert not 겹침, f"끝났다고 적어 놓고 아직 🔴 인 항목: {겹침}"
