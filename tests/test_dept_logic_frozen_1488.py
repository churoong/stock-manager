"""부서·회의·지주사 판단식 지문 (docs/company.md 11장, docs/infra.md 25.1488).

`company.charter_hash` 는 헌장 dict 만 해시한다 — 부서 판단식(`company.py`)이나 H 의 가중 식(`holding.py`)을 **코드로** 바꾸면
회사 지문·부서 성적표 열쇠·H 의 0083 고정 지문이 그대로라 판 2·H·KIS모의투자 기록이 바뀌기 전후로 조용히 섞인다(30회차 검증 B).
이 시험은 두 파일의 **논리**(주석·독스트링을 뺀 구문 트리) 지문을 고정한다. 깨지면 바꾼 것이 판단을 바꾸는지 보고:
- 판단을 바꾸지 않으면(이름·정리) 지문만 새 값으로
- 판단을 바꾸면 docs/company.md 11장 절차(판 올림 마이그레이션·H 지문 갱신·infra 기록)를 같은 커밋에서 하고 지문을 새 값으로
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: 파일 → 마지막으로 확인한 논리 지문 (2026-10-16, 25.1488)
고정 = {
    "batch/services/company.py": "cbbbea47b535",
    "batch/services/holding.py": "84e36990d5c5",
}


def 논리_지문(path: Path) -> str:
    """주석·독스트링을 뺀 구문 트리의 sha256 앞 12자 — 글 다듬기로는 바뀌지 않고 식·상수를 바꾸면 바뀐다."""
    트리 = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(트리):
        if (isinstance(n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
                and isinstance(n.body[0].value.value, str)):  # fmt: skip
            n.body = n.body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.dump(트리, annotate_fields=False).encode()).hexdigest()[:12]


def test_dept_logic_fingerprint_unchanged() -> None:
    지금 = {p: 논리_지문(뿌리 / p) for p in 고정}
    assert 지금 == 고정, (
        "부서·회의·지주사 판단식이 바뀌었다 — 판단이 바뀌면 docs/company.md 11장 절차(판 올림·H 지문·infra)를 먼저, "
        f"그다음 이 시험의 지문을 새 값으로: {지금}"
    )


def test_fingerprint_ignores_comments_but_not_logic(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('"""글."""\nX = 1  # 주석\n\n\ndef f():\n    """글."""\n    return X\n', encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text('"""다른 글."""\nX = 1\n\n\ndef f():\n    return X\n', encoding="utf-8")
    c = tmp_path / "c.py"
    c.write_text('X = 2\n\n\ndef f():\n    return X\n', encoding="utf-8")
    assert 논리_지문(a) == 논리_지문(b) != 논리_지문(c)
