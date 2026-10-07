"""**2부에서 빠진 사유가 문서와 같은가** (docs/infra.md 25.190).

CLAUDE.md 가 이 규칙을 절대로 둔다.

> 2부는 1부의 부분집합이다. **1부에 있는데 2부에 없으면 사유를 반드시 적는다**

그 사유 목록의 정의처는 `docs/design.md` "두 부를 잇는 규칙" 표다 — 문서가 단일
정의처라는 기록 규칙 그대로다. 그런데 문서는 **넷**을 적고 코드는 **여섯**을 쓰고 있었다.
뒤의 둘(`총 투자가능금액 미설정`·`최소 주문 단위 미만`)은 2026-09 에 `services/report_picks`
안에 따로 생겼고, 문서의 "사유는 **이 중 하나다**" 는 그날부터 거짓이었다.

여기서 셋을 본다.

1. 상수와 문서 표가 **양방향으로** 같은가
2. 사유를 **글자로 박아** 넘기는 곳이 없는가 — 박으면 문서 대조를 통째로 지나친다
3. 적어 둔 사유가 실제로 **쓰이는가** — 안 쓰는 사유는 틀려도 아무도 모른다
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from batch.notify import report_sections as rs

뿌리 = Path(__file__).resolve().parent.parent
설계 = 뿌리 / "docs" / "design.md"


def 문서_사유() -> list[str]:
    """`docs/design.md` "두 부를 잇는 규칙" 표의 첫 칸들."""
    글 = 설계.read_text(encoding="utf-8")
    본문 = 글[글.index("**1부에 있는데 2부에 없으면 사유를 적는다.**") :]
    본문 = 본문[: 본문.index("\n\n표 밖에 있던 사유")]
    나온것 = re.findall(r"^\| ([^|]+?) \| ", 본문, flags=re.M)
    return [줄.strip() for 줄 in 나온것 if 줄.strip() not in {"사유", "---"}]


def test_문서에서_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다."""
    assert len(문서_사유()) >= 6, f"문서 표에서 {len(문서_사유())}개밖에 못 읽었다: {문서_사유()}"
    assert "섹터 상한 도달" in 문서_사유()


def test_상수를_한_곳에서_모았다() -> None:
    assert len(rs.EXCLUDED_REASONS) == len(set(rs.EXCLUDED_REASONS)), "같은 사유가 두 번 있다"
    assert len(rs.EXCLUDED_REASONS) >= 6


def test_코드의_사유가_문서에_다_있다() -> None:
    """**"이 중 하나다" 가 거짓이 되는 쪽.** 사용자가 문서에 없는 말을 화면에서 본다."""
    빠진것 = sorted(set(rs.EXCLUDED_REASONS) - set(문서_사유()))

    assert not 빠진것, (
        f"코드가 쓰는데 docs/design.md 표에 없는 사유: {빠진것}\n"
        "문서가 단일 정의처다(CLAUDE.md 기록 규칙). 표에 뜻과 함께 더하라"
    )


def test_문서의_사유가_코드에_다_있다() -> None:
    """반대 방향. 안 쓰는 사유가 표에 남으면 사용자가 오지 않을 말을 기다린다."""
    남는것 = sorted(set(문서_사유()) - set(rs.EXCLUDED_REASONS))

    assert not 남는것, f"문서 표에만 있는 사유: {남는것}"


def 사유를_넘기는_곳() -> list[tuple[str, int, str]]:
    """`Excluded(...)` 에 넘기는 사유 인자. (파일, 줄, 코드)."""
    나온것: list[tuple[str, int, str]] = []
    for 길 in sorted((뿌리 / "batch").rglob("*.py")):
        if "__pycache__" in str(길):
            continue
        for 마디 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
            if not isinstance(마디, ast.Call):
                continue
            이름 = 마디.func.attr if isinstance(마디.func, ast.Attribute) else getattr(마디.func, "id", "")
            if 이름 != "Excluded" or len(마디.args) < 3:
                continue
            나온것.append(
                (str(길.relative_to(뿌리)), 마디.lineno, ast.unparse(마디.args[2]))
            )
    return 나온것


def test_넘기는_곳을_찾아_냈다() -> None:
    assert len(사유를_넘기는_곳()) >= 4, sorted(사유를_넘기는_곳())


@pytest.mark.parametrize("자리", 사유를_넘기는_곳(), ids=lambda c: f"{c[0]}:{c[1]}")
def test_사유를_글자로_박지_않는다(자리: tuple[str, int, str]) -> None:
    """**박으면 문서 대조를 통째로 지나친다.**

    `Excluded(..., "무슨무슨 이유")` 라고 쓰면 위 두 검사가 그 말을 영영 못 본다.
    문서에 없는 말이 화면과 텔레그램에 뜨고 아무도 모른다.
    """
    파일, 줄, 코드 = 자리

    assert not 코드.startswith(("'", '"')), (
        f"{파일}:{줄} 가 사유를 글자로 박는다: {코드}\n"
        "`report_sections.EXCLUDED_*` 상수를 쓰고, 새 사유면 docs/design.md 표에도 더하라"
    )


def test_적어_둔_사유를_다_쓴다() -> None:
    """안 쓰는 사유는 **틀려도 아무도 모른다** (25.184 의 죽은 키와 같다)."""
    글 = "\n".join(
        길.read_text(encoding="utf-8")
        for 길 in sorted((뿌리 / "batch").rglob("*.py"))
        if "__pycache__" not in str(길) and 길.name != "report_sections.py"
    )
    이름들 = {
        이름
        for 이름 in dir(rs)
        if 이름.startswith("EXCLUDED_") and 이름 != "EXCLUDED_REASONS"
    }
    안쓰는것 = sorted(이름 for 이름 in 이름들 if 이름 not in 글)

    assert not 안쓰는것, f"정의만 하고 아무도 안 쓰는 사유: {안쓰는것}"


def test_다른_문서가_사유의_개수를_적지_않는다() -> None:
    """**개수를 적으면 사유가 늘 때 거짓이 된다** (docs/infra.md 25.205).

    `docs/signals.md` 3.3 이 "네 가지 중 하나" 라고 적고 있었다. 25.190 에서 여섯이 된 뒤에도 남았다.
    정의처는 `docs/design.md` 표 하나다. 다른 문서는 개수 없이 그 표를 가리킨다.
    `docs/infra.md` 는 지난 일을 적는 곳이라 뺀다.
    """
    수 = "(?:둘|셋|넷|다섯|여섯|일곱|여덟|두|세|네) ?가지"
    걸린것 = []
    for 길 in sorted((뿌리 / "docs").glob("*.md")):
        if 길.name in {"infra.md", "design.md"}:
            continue
        for 번호, 줄 in enumerate(길.read_text(encoding="utf-8").splitlines(), 1):
            if "사유" in 줄 and re.search(rf"{수} 중 하나|사유는 {수}", 줄):
                걸린것.append(f"{길.name}:{번호}: {줄.strip()}")

    assert not 걸린것, "제외 사유의 개수를 적은 문서:\n" + "\n".join(걸린것)


def test_설계서가_적은_개수가_맞다() -> None:
    """설계서는 개수를 적는다 — 여덟째가 더해진 뒤에도 "일곱 중 하나" 였다 (docs/infra.md 25.649)."""
    from batch.notify import report_sections as rs

    말 = {6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉", 10: "열"}[len(rs.EXCLUDED_REASONS)]
    글 = (뿌리 / "docs" / "design.md").read_text(encoding="utf-8")
    assert f"사유는 이 {말} 중 하나다" in 글
