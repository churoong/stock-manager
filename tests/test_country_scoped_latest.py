"""**"가장 최근 기준일" 은 나라 안에서 잡는다** (docs/infra.md 25.61·25.111·25.112).

나라마다 배치가 따로 돈다. 국내 09-16, 미국 09-15 처럼 기준일이 어긋나는 것이 정상이고,
지금은 미국이 통째로 쉬고 있다(25.14). 표 **전체**에서 `MAX` 를 잡으면 늦은 쪽이

* INNER JOIN 이면 **통째로 빠지고**
* LEFT JOIN 이면 값이 **전부 NULL** 이 되고 (화면은 "아직 없나 보다" 로 보인다)
* 세는 질의면 **0** 이 나온다 (되살아나는 날 자동 점검이 거짓 실패를 알린다)

세 번 되풀이돼서 그물을 건다. 웹 쪽 짝은 `web/__tests__/appSql.test.ts` 의
「최신 기준일은 나라별로 잡는다」 다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: 조건 없이 잡아도 되는 표와 **사유**. 사유 없는 예외는 두지 않는다
예외: dict[str, str] = {
    "sell_flags": (
        "매도 플래그는 한 번에 모든 나라를 판정한다 (batch/jobs/sell_flags.run 은 market=None)."
        " 나라별 날짜가 애초에 안 생긴다"
    ),
}

#: `(SELECT MAX(as_of_date) FROM 표)` 처럼 **조건 없이 닫히는** 것
_맨것 = re.compile(
    r"\(\s*SELECT\s+MAX\(\s*\w*\.?(?:as_of_date|snapshot_date)\s*\)\s+FROM\s+(\w+)\s*\)",
    re.IGNORECASE,
)


def _문자열들(본문: str) -> list[str]:
    """코드에 **실제로 쓰이는** SQL 문자열만. 주석과 설명문(docstring)은 뺀다.

    처음에는 파일 글자를 통째로 훑었는데 `batch/core/db.latest_snapshot_sql` 의 설명문이
    걸렸다 — 거기에는 **"예전에는 이렇게 썼다"** 는 옛 모양이 적혀 있다. 고치라고 적어 둔
    글을 그물이 무는 셈이다. **엉뚱한 것을 물면 사람이 그물을 끈다**(25.98).

    파이썬이 이어 붙인 문자열(`"a" "b"`)을 파싱 때 한 덩어리로 만들어 주므로,
    여러 줄로 나눠 적은 SQL 도 그대로 한 값으로 나온다.
    """
    import ast

    나무 = ast.parse(본문)
    설명문: set[int] = set()
    for n in ast.walk(나무):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            첫 = n.body[0] if n.body else None
            if isinstance(첫, ast.Expr) and isinstance(첫.value, ast.Constant) and isinstance(첫.value.value, str):
                설명문.add(id(첫.value))
    return [
        n.value
        for n in ast.walk(나무)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in 설명문
    ]


def _훑기() -> tuple[list[str], int]:
    걸린것: list[str] = []
    본것 = 0
    for 폴더 in ("batch", "scripts"):
        for path in sorted((뿌리 / 폴더).rglob("*.py")):
            for 값 in _문자열들(path.read_text(encoding="utf-8")):
                for m in _맨것.finditer(값):
                    본것 += 1
                    if m.group(1).lower() not in 예외:
                        걸린것.append(f"{path.relative_to(뿌리)}: {m.group(0)[:90]}")
    return 걸린것, 본것


def test_예외에_사유가_있다() -> None:
    assert all(len(v) > 20 for v in 예외.values())


def test_읽어_냈다() -> None:
    """**0개를 훑고 통과하면 그물이 아니라 장식이다.**"""
    _, 본것 = _훑기()
    assert 본것 >= 1, "이런 모양을 하나도 못 찾았다 — 이어 붙인 SQL 을 못 읽고 있나"


def test_기준일을_표_전체에서_잡지_않는다() -> None:
    걸린것, _ = _훑기()
    assert not 걸린것, (
        "기준일을 표 전체에서 잡았다. 나라 안에서 잡거나 예외에 사유를 적는다:\n  " + "\n  ".join(걸린것)
    )
