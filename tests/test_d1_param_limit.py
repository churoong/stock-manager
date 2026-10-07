"""D1 파라미터 한도를 **두 언어가 같은 값으로** 안다 (docs/infra.md 25.90).

D1 은 질의 하나에 바인딩 파라미터 **100개**까지 받는다. 넘으면 `too many SQL variables` 로
거절하는데, 그 문구만으로는 **어느 질의인지 알 수 없다.**

배치(`batch/core/d1.py`)는 그 한도를 알고 적재를 여러 문장으로 나눈다. 그런데 **웹은
그냥 보내고 있었다** — 같은 D1 을 쓰면서 한쪽만 알고 있었다. 25.0 의 "한 규칙이 두 곳에
있다" 와 반대 모양이다: 한 규칙이 **한 곳에만** 있어서, 다른 곳이 그것을 모른다.

파이썬이 단일 정의처다. 여기서 웹 소스를 읽어 대 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

from batch.core import d1

뿌리 = Path(__file__).resolve().parent.parent
D1_TS = (뿌리 / "web" / "lib" / "d1.ts").read_text(encoding="utf-8")


def test_읽어_냈다() -> None:
    assert "export const MAX_PARAMS" in D1_TS, "웹 D1 어댑터에 한도 상수가 없다 — 모양이 바뀌었나"


def test_두_언어가_같은_한도를_안다() -> None:
    m = re.search(r"export const MAX_PARAMS = (\d+);", D1_TS)
    assert m, "웹의 MAX_PARAMS 를 못 읽었다"

    assert int(m.group(1)) == d1.MAX_PARAMS, (
        f"D1 파라미터 한도가 갈라졌다: 파이썬 {d1.MAX_PARAMS} · 웹 {m.group(1)}.\n"
        "같은 D1 을 쓰는데 한쪽만 다른 값을 믿으면, 그쪽이 조용히 거절당한다"
    )


def test_웹이_보내기_전에_검사한다() -> None:
    """**보내고 나서 D1 의 문구를 받는 것과 다르다.** 어느 질의였는지는 여기서만 알 수 있다."""
    보내는곳 = D1_TS.split("export async function d1Batch", 1)[1].split("const response = await fetch", 1)[0]

    assert "파라미터_한도검사(statements)" in 보내는곳, "검사가 전송 앞에 없다"


def test_오류에_어느_질의인지_담는다() -> None:
    몸통 = D1_TS.split("function 파라미터_한도검사", 1)[1].split("export async function", 1)[0]

    assert "s.sql.slice" in 몸통, "질의를 안 적으면 D1 의 원래 문구와 다를 게 없다"
    assert "문장을 나눠 보내세요" in 몸통, "무엇을 하라는지 없다"


def test_배치는_나눠_보낸다() -> None:
    """한도를 아는 쪽이 실제로 그 지식을 쓰는지. 안 쓰면 상수만 있고 장치가 없는 것이다."""
    본문 = (뿌리 / "batch" / "core" / "d1.py").read_text(encoding="utf-8")

    assert "MAX_PARAMS" in 본문.split("def ", 1)[1], "상수를 선언만 하고 아무 데도 안 쓴다"


# ----------------------------------------------------------------------
# 자료 크기를 따라가는 파라미터 (2026-09-22, docs/infra.md 25.107)
# ----------------------------------------------------------------------


class Test목록_질의는_반드시_나눠_보낸다:
    """`IN (?, ?, …)` 의 길이가 **자료 크기를 따라가면** 언젠가 100을 넘는다.

    `core/d1.split_for_params` 는 그런 질의를 **일부러 안 쪼갠다** — `IN` 을 쪼개면 뜻이
    달라지고, 조용히 반쪽만 도는 것이 가장 나쁘기 때문이다. 그래서 **부르는 쪽이** 나눠야 한다.

    2026-09-22 에 `jobs/accumulation` 에서 하나가 나왔다. 판정 종목 전부를
    `NOT IN (?, …)` 으로 넘겨 국내 879·미국 1,800 개가 그대로 붙었다. D1 에서 아직 한 번도
    안 돌아 본 작업이라(월 1회, 다음 예정 10-06) **아무도 몰랐다.**
    """

    #: 나누지 않아도 되는 자리와 **사유**. 사유 없는 예외는 두지 않는다
    예외: dict[str, str] = {}

    @staticmethod
    def _고정길이(fn, 이름: str) -> bool:
        """그 이름의 길이가 **자료를 안 따라가는가.**

        둘이다. 모듈 상수(대문자)에서 온 목록이거나, 이 함수 안에서 **글자로 적은**
        튜플·리스트를 담은 이름이다(예: `stress.store` 의 `row` = 열 개수만큼 고정).
        """
        import ast

        if 이름.isupper() or (이름.startswith("_") and 이름.lstrip("_").isupper()):
            return True
        for n in ast.walk(fn):
            if (
                isinstance(n, ast.Assign)
                and isinstance(n.value, (ast.Tuple, ast.List))
                and any(isinstance(t, ast.Name) and t.id == 이름 for t in n.targets)
            ):
                return True
        return False

    @staticmethod
    def _나눴나(본문: str) -> bool:
        import re

        return "in_chunk" in 본문 or bool(re.search(r"range\(0, len\(", 본문))

    def _새는것(self) -> tuple[list[str], int]:
        """**부르는 쪽이 나눠도 된다.**

        `adjust_kr.load_days` 는 자기 안에서 안 나누지만 `run()` 이 `CHUNK_STOCKS = 20` 씩
        잘라서 부른다. 그것도 옳은 모양이라 같은 모듈의 호출부까지 본다.

        **알고 두는 한계**: 잘라 보내는 크기가 실제로 100 아래인지는 못 본다. 그건
        `tests/test_correlation_sizing.py` 처럼 자리마다 따로 재는 쪽이 맞다.
        """
        import ast
        import re

        찾음 = 0
        새는것: list[str] = []
        자리표 = re.compile(r"""\[["']\?["']\]\s*\*\s*len\((\w+)\)|["']\?["']\s+for\s+_\w*\s+in\s+(\w+)""")
        for path in sorted((뿌리 / "batch").rglob("*.py")):
            나무 = ast.parse(path.read_text(encoding="utf-8"))
            fns = {n.name: n for n in ast.walk(나무) if isinstance(n, ast.FunctionDef)}
            for 함수명, fn in sorted(fns.items()):
                본문 = ast.unparse(fn)
                이름들 = [a or b for a, b in 자리표.findall(본문)]
                이름들 = [n for n in 이름들 if not self._고정길이(fn, n)]
                if not 이름들:
                    continue
                찾음 += 1
                키 = f"{path.name}::{함수명}"
                if 키 in self.예외 or self._나눴나(본문):
                    continue
                부른쪽 = [
                    ast.unparse(other)
                    for 다른이름, other in fns.items()
                    if 다른이름 != 함수명
                    and any(
                        isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == 함수명
                        for c in ast.walk(other)
                    )
                ]
                if 부른쪽 and all(self._나눴나(t) for t in 부른쪽):
                    continue
                새는것.append(f"{키} (줄 {fn.lineno}) — {', '.join(sorted(set(이름들)))}")
        return 새는것, 찾음

    def test_목록을_만드는_질의는_나누거나_사유를_적는다(self) -> None:
        새는것, _ = self._새는것()
        assert not 새는것, (
            f"목록 길이가 자료를 따라가는 질의가 있다. D1 은 질의당 파라미터 {d1.MAX_PARAMS}개까지다.\n"
            "  `core/d1.split_for_params` 는 `IN` 목록을 일부러 안 쪼갠다 — 부르는 쪽이 나눠야 한다:\n  "
            + "\n  ".join(새는것)
        )

    def test_예외에_사유가_있다(self) -> None:
        assert all(self.예외.values())

    def test_훑기가_실제로_함수를_찾았다(self) -> None:
        """**읽어 냈는지 먼저 센다.** 0개를 훑고 통과하면 그물이 아니라 장식이다."""
        _, 찾음 = self._새는것()
        assert 찾음 >= 5, f"목록을 만드는 함수를 {찾음}개밖에 못 찾았다 — 표기가 바뀌었나"
