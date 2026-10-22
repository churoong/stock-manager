"""무작위 대조(R1·R2)·가상 대조의 고르기 규칙 지문 (docs/paper.md 2.1.2·10.1, docs/infra.md 25.1700).

R1·R2 는 판 1 로 고정이고 관문 "R 대비"·"가상 대조"(25.1697)의 기준선이다. 그런데 고르는 코드(`random_substitute`·
`random_order`)와 가상 대조(`control_picks`·`control_pct`·`fill_controls`)는 부서 판단식 지문(25.1488) 밖이라, 관문 구간
중간에 기준선이 조용히 바뀔 수 있었다(H·R 감사 [권고] 4 — 10-21 25.1629 가 R 의 묵음 잣대를 H 판 2 관문 구간 안에서
바꿈. 그때는 위험 점검과 대칭이라 괜찮았다).

이 시험은 그 함수들과 상수의 **논리**(주석·독스트링을 뺀 토큰 흐름) 지문을 고정한다. 깨지면:
- 고르는 결과를 바꾸지 않으면(이름·정리) 지문만 새 값으로
- 바꾸면 그 날짜와 까닭을 docs/infra.md 에 적고 docs/paper.md 2.1.2 의 R 판 이력에 한 줄 — 관문은 그 날 뒤 구간만
  믿을 수 있다. R 의 `rule_version` 을 올리는 것은 마이그레이션이라 하나씩(handoff)
"""

from __future__ import annotations

import ast
import hashlib
import io
import textwrap
import tokenize
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: (파일, 이름) → 마지막으로 확인한 논리 지문 (2026-10-22 25.1700 고정, 파이썬 3.12). 25.1708: R 전용 후보 풀(`UNIVERSE_SQL`)·
#: 든 대체 종목 잇기(`held_substitutes`·`SUBSTITUTE_ORDERS_SQL`)·가상 대조 묵음(`CONTROL_STALE_DAYS`) 더함(25.1700
#: 교차검증 [권고]). `run_step` 안의 호출 인자(`today=`·`cooldown=`)는 함수 전체를 묶어야 해 넣지 않았다 — 알고 둔 빈틈
고정 = {
    ("batch/services/paper.py", "random_order"): "f7370bfc4208",
    ("batch/services/paper.py", "control_picks"): "d76daecaa849",
    ("batch/services/paper.py", "control_pct"): "9c5aa9661c4e",
    ("batch/services/paper.py", "VIRTUAL_CONTROLS"): "e59cf48b2ae0",
    ("batch/services/paper.py", "VIRTUAL_MIN_CONTROLS"): "e0862af57f0d",
    ("batch/jobs/paper.py", "random_substitute"): "df86481c3705",
    ("batch/jobs/paper.py", "RANDOM_MAX_TRIES"): "efd2a89fda10",
    ("batch/jobs/paper.py", "fill_controls"): "d1d01ee66425",
    ("batch/jobs/paper.py", "_대조_모집단"): "dd18c105555f",
    ("batch/jobs/paper.py", "PENDING_CONTROL_SQL"): "7a0e5836f96f",
    ("batch/jobs/paper.py", "SNAPSHOT_AT_SQL"): "fa0e7436b8da",
    ("batch/jobs/paper.py", "UNIVERSE_AT_SQL"): "5a07b84d5e50",
    ("batch/jobs/paper.py", "CLOSE_ON_OR_BEFORE_SQL"): "bfd792951af1",
    ("batch/jobs/paper.py", "UNIVERSE_SQL"): "ce77214ad6b5",
    ("batch/jobs/paper.py", "SUBSTITUTE_ORDERS_SQL"): "43a1dcf4a8fb",
    ("batch/jobs/paper.py", "held_substitutes"): "824ba41940b8",
    ("batch/jobs/paper.py", "CONTROL_STALE_DAYS"): "504632c8fea2",
}


def _조각(path: Path, 이름: str) -> str:
    글 = path.read_text(encoding="utf-8")
    for n in ast.parse(글).body:
        if isinstance(n, ast.FunctionDef) and n.name == 이름:
            # 데코레이터(`@cache` 등)도 판단을 바꾼다 — 구문 조각에 들지 않아 따로 붙인다(25.1708)
            장식 = "".join(f"@{ast.get_source_segment(글, d)}\n" for d in n.decorator_list)
            return 장식 + textwrap.dedent(ast.get_source_segment(글, n) or "")
        if isinstance(n, ast.Assign | ast.AnnAssign):
            표적 = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id == 이름 for t in 표적):
                return ast.get_source_segment(글, n) or ""
    raise AssertionError(f"{path.name} 에 {이름} 없음")


def 논리_지문(글: str) -> str:
    """주석·독스트링·줄바꿈·들여쓰기 폭을 뺀 토큰 흐름의 sha256 앞 12자 (`test_dept_logic_frozen_1488` 과 같은 방식)"""
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
            건너뜀 = True
            continue
        if 건너뜀 and t.type == tokenize.NEWLINE:
            건너뜀 = False
            continue
        건너뜀 = False
        조각.append(f"{tokenize.tok_name[t.type]}:{'' if t.type in (tokenize.INDENT, tokenize.DEDENT) else t.string}")
    return hashlib.sha256("\n".join(조각).encode()).hexdigest()[:12]


def test_r_and_control_logic_fingerprint_unchanged() -> None:
    지금 = {k: 논리_지문(_조각(뿌리 / k[0], k[1])) for k in 고정}
    바뀜 = {f"{k[0]}:{k[1]}": v for k, v in 지금.items() if 고정[k] != v}
    assert not 바뀜, f"R·가상 대조 고르기 규칙이 바뀜 — 모듈 독스트링 절차를 따르고 지문을 새 값으로: {바뀜}"


def test_fingerprint_ignores_comments_and_catches_logic() -> None:
    원 = "def f(x):\n    \"\"\"설명\"\"\"\n    return x + 1  # 하나\n"
    assert 논리_지문(원) == 논리_지문("def f(x):\n    # 다른 주석\n    return x + 1\n")
    assert 논리_지문(원) != 논리_지문("def f(x):\n    return x + 2\n")
