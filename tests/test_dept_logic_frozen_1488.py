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
import io
import tokenize
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: 파일 → 마지막으로 확인한 논리 지문 (2026-10-16 25.1488 고정 — 2026-10-18 25.1530 에서 토큰 지문으로 다시 셈, 두 파일은
#: 25.1485 뒤로 바뀌지 않았다. 3.12.3·3.13.16 같은 값, 3.11 은 f-문자열 토큰이 달라 다르다 — 이 저장소는 3.12).
#: 2026-10-20 25.1576 갱신 — company.py 는 주간 복기의 부결 판정만(판단 그대로, 절차 1), holding.py 는 기준 평균을 표본
#: 가중으로(판단 바뀜, 절차 2 — 0088 에서 H 판 2·A·C·D·E 판 3)
고정 = {
    "batch/services/company.py": "f6b76cae6982",
    "batch/services/holding.py": "2f3a2bd5e7fe",
}


def 논리_지문(path: Path) -> str:
    """주석·독스트링·줄바꿈·들여쓰기 폭을 뺀 **토큰 흐름**의 sha256 앞 12자 — 글 다듬기로는 바뀌지 않고 식·상수를 바꾸면 바뀐다.

    예전에는 `ast.dump` 를 해시했는데 그 출력은 파이썬 패치 판마다 달라(2026-10-18 새 컨테이너 3.12.3 에서 코드 그대로인데 깨짐)
    거짓 경보가 났다(docs/infra.md 25.1530). 토큰 종류와 글자는 같은 소판(3.12) 안에서 바뀌지 않는다
    """
    글 = path.read_text(encoding="utf-8")
    독스트링: set[tuple[int, int]] = set()
    for n in ast.walk(ast.parse(글)):
        if (isinstance(n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
                and isinstance(n.body[0].value.value, str)):  # fmt: skip
            독스트링.add((n.body[0].lineno, n.body[0].col_offset))
    조각: list[str] = []
    건너뜀 = False
    for t in tokenize.generate_tokens(io.StringIO(글).readline):
        if t.type in (tokenize.COMMENT, tokenize.NL, tokenize.ENDMARKER):
            continue
        if t.type == tokenize.STRING and t.start in 독스트링:
            건너뜀 = True  # 독스트링과 그 뒤 줄 끝을 뺀다
            continue
        if 건너뜀 and t.type == tokenize.NEWLINE:
            건너뜀 = False
            continue
        건너뜀 = False
        조각.append(f"{tokenize.tok_name[t.type]}:{'' if t.type in (tokenize.INDENT, tokenize.DEDENT) else t.string}")
    return hashlib.sha256("\n".join(조각).encode()).hexdigest()[:12]


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
