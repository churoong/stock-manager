"""한 모듈 안에 같은 이름의 최상위 함수·클래스를 두 번 두지 않는다 (docs/infra.md 25.566).

25.564 가 `jobs/sell_flags._criteria_of` 를 하나 더 만들어 뒤의 정의에 덮였고, 확인한 플래그가 매 배치 풀렸다.
ruff F811 은 앞의 이름이 쓰인 뒤라 잡지 않았다 — 파이썬은 조용히 뒤의 것을 쓴다.
"""

from __future__ import annotations

import ast
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent


def 겹친_정의(위치: Path | None = None) -> list[str]:
    나온것: list[str] = []
    for 길 in sorted((위치 or 뿌리 / "batch").rglob("*.py")):
        if "__pycache__" in str(길):
            continue
        본 = set()
        for 마디 in ast.parse(길.read_text(encoding="utf-8")).body:
            if isinstance(마디, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if 마디.name in 본:
                    나온것.append(f"{길.name}:{마디.lineno} {마디.name}")
                본.add(마디.name)
    return 나온것


def test_최상위_정의가_겹치지_않는다() -> None:
    assert 겹친_정의() == []


def test_훑기가_실제로_문다(tmp_path: Path) -> None:
    """미끼 — 겹친 정의를 **같은 함수로** 실제로 찾는지 (25.568, 교차검증: 예전 미끼는 로직을 따로 다시 썼다)."""
    (tmp_path / "m.py").write_text("def a():\n    return 1\n\n\ndef a():\n    return 2\n", encoding="utf-8")
    assert 겹친_정의(tmp_path) == ["m.py:5 a"]
