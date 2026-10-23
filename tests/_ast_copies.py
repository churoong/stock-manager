"""정의처 상수를 숫자로 다시 적은 사본 찾기 — AST 로 (docs/infra.md 25.1757).

25.1753~25.1756 은 줄 정규식으로 막았는데, 교차검증마다 새 꼴이 빠져나갔다(`float | None = 30.0`, `Final[float]`,
`(\n 0.30\n)` 처럼 줄을 넘긴 값, `0.1` 같은 비율 꼴, 줄을 넘긴 두 번째 인자). 줄이 아니라 구문 나무를 보면 꼴이 바뀌어도
같은 노드다. 두 가지를 본다:

- `사본_대입`: 이름이 정해진 사본(`KR_PRICE_LIMIT` 등)에 숫자 상수가 섞인 값을 대입·주석 대입한 자리
- `상한_숫자`: 비중 상한 이름(`max_weight_per_stock`·`max_sector_pct`·`DEFAULT_MAX_*`·`…상한`)을 언급하는 단순 문장이나
  함수 인자 기본값에 상한 기본값 숫자(10·30 과 그 비율 꼴 0.1·0.3)가 있는 자리. 0 은 "배분하지 않음"(`jobs/daily`)이라 뺀다
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: 비중 상한을 가리키는 이름 — 설정 키·인자 이름·정의처 별칭·지역 변수(`섹터상한`)
#: 뒤 글자를 막는다 — `analyze_extra.MAX_STOCKS = 30`(분석 종목 수 상한)이 `max_stock` 으로 걸렸다
상한_이름 = re.compile(r"(?i)max_(weight_per_(stock|sector)|(stock|sector)_pct)(?![a-z0-9])|상한")
#: 상한 기본값과 그 비율 꼴 — `settings_range.DEFAULT_MAX_STOCK_PCT`·`DEFAULT_MAX_SECTOR_PCT`
상한_값 = frozenset({10, 30, 0.1, 0.3})


def _숫자(n: ast.AST, 값: frozenset | None = None) -> bool:
    return any(isinstance(c, ast.Constant) and type(c.value) in (int, float) and (값 is None or c.value in 값)
               for c in ast.walk(n))  # fmt: skip


def _이름들(n: ast.AST) -> Iterable[str]:
    for c in ast.walk(n):
        if isinstance(c, ast.Name):
            yield c.id
        elif isinstance(c, ast.Attribute):
            yield c.attr
        elif isinstance(c, (ast.arg, ast.keyword)) and c.arg:
            yield c.arg
        elif isinstance(c, ast.Constant) and isinstance(c.value, str):
            yield c.value


def 사본_대입(글: str, 이름: Iterable[str]) -> list[int]:
    """`이름` 에 숫자 상수가 섞인 값을 대입한 줄 번호 — `X = 0.30`·`X: Final[float] = 0.30`·`X = (\\n 0.30\\n)`·`X = 1 - 0.7`"""
    이름 = set(이름)
    줄 = []
    for n in ast.walk(ast.parse(글)):
        if isinstance(n, ast.Assign):
            과녁, 값 = n.targets, n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            과녁, 값 = [n.target], n.value
        else:
            continue
        if any(x in 이름 for t in 과녁 for x in _이름들(t)) and _숫자(값):
            줄.append(n.lineno)
    return 줄


def 상한_숫자(글: str) -> list[int]:
    """비중 상한 기본값을 숫자로 다시 적은 줄 번호 — 인자 기본값·키워드 인자·`get(…) or 10`·`10 if 상한 is None`·
    `_담다(get_setting_in_range(…, "max_weight_per_stock", …), 10.0)`·사전 값 모두 같은 규칙(언급 + 숫자)으로 잡힌다"""
    줄 = []
    for n in ast.walk(ast.parse(글)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = n.args
            위치 = a.posonlyargs + a.args
            쌍 = [*zip(위치[len(위치) - len(a.defaults):], a.defaults, strict=True),
                 *zip(a.kwonlyargs, a.kw_defaults, strict=True)]
            줄 += [d.lineno for x, d in 쌍 if d is not None and 상한_이름.search(x.arg) and _숫자(d, 상한_값)]
        # 단순 문장만 통째로 — 복합 문장(if·for·def)은 몸통이 넓어 엉뚱한 숫자까지 묶인다
        elif isinstance(n, ast.stmt) and not hasattr(n, "body"):
            if any(상한_이름.search(x) for x in _이름들(n)) and _숫자(n, 상한_값):
                줄.append(n.lineno)
    return 줄


def 훑기(찾기, 빼기: Iterable[str] = ()) -> list[str]:
    """batch/ 아래 모든 .py 에 `찾기(글)` 을 돌려 `경로:줄` 목록 — 정의처는 이름이 아니라 경로로 뺀다(25.1756)"""
    빼기 = set(빼기)
    return [f"{p}:{k}" for f in sorted((뿌리 / "batch").rglob("*.py"))
            if (p := str(f.relative_to(뿌리))) not in 빼기 for k in 찾기(f.read_text(encoding="utf-8"))]  # fmt: skip
