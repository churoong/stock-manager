"""정의처 상수를 숫자로 다시 적은 사본 찾기 — AST 로 (docs/infra.md 25.1757).

25.1753~25.1756 은 줄 정규식으로 막았는데, 교차검증마다 새 꼴이 빠져나갔다(`float | None = 30.0`, `Final[float]`,
`(\n 0.30\n)` 처럼 줄을 넘긴 값, `0.1` 같은 비율 꼴, 줄을 넘긴 두 번째 인자). 줄이 아니라 구문 나무를 보면 꼴이 바뀌어도
같은 노드다. 두 가지를 본다:

- `사본_대입`: 이름이 정해진 사본(`KR_PRICE_LIMIT` 등)에 숫자 상수가 섞인 값을 대입·주석 대입한 자리
- `상한_숫자`: 비중 상한 기본값을 숫자로 다시 적은 자리 — 상한 이름에 바로 묶인 **강한 자리**는 정의처와 다른 값까지 모든 숫자,
  상한을 언급하는 문장·복합 문장 머리의 **느슨한 자리**는 정의처 기본값과 그 비율 꼴만(25.1758·25.1759). 함수 docstring 을 본다

알고 두는 한계(25.1759): 사본 이름 목록·상한 이름 정규식은 손으로 적는다(새 이름은 못 잡는다), 상한 인자를 **위치 인자**로 넘긴
호출(`build_portfolio(a, b, 25.0)`)과 식 속 다른 값(`max_stock_pct = min(x, 15)`)은 보지 않는다, 정의처 파일은 통째로 뺀다.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from batch.core import settings_range as sr

뿌리 = Path(__file__).resolve().parent.parent

#: 비중 상한을 가리키는 이름 — 설정 키·인자 이름·정의처 별칭·지역 변수(`섹터상한`). 느슨한 자리에 쓴다
#: 뒤 글자를 막는다 — `analyze_extra.MAX_STOCKS = 30`(분석 종목 수 상한)이 `max_stock` 으로 걸렸다
상한_이름 = re.compile(r"(?i)max_(weight_per_(stock|sector)|(stock|sector)_pct)(?![a-z0-9])|상한")
#: 강한 자리에 쓰는 좁은 이름 — 한글 `상한` 단독은 `상한가`·행 조회 상한 같은 남의 상한에도 맞아 모든 숫자를 잡으면 시끄럽다(25.1759)
강한_이름 = re.compile(r"(?i)max_(weight_per_(stock|sector)|(stock|sector)_pct)(?![a-z0-9])|(종목|섹터|업종|비중)상한")
#: 느슨한 규칙(상한을 언급하는 문장 아무 데나 있는 숫자)에 쓰는 값 — 정의처 기본값과 그 비율 꼴에서 끌어온다(25.1758 —
#: 손으로 `{10, 30}` 을 적어 두면 정의처를 바꾼 날 옛 값만 찾는다)
상한_값 = frozenset({sr.DEFAULT_MAX_STOCK_PCT, sr.DEFAULT_MAX_SECTOR_PCT,
                    sr.DEFAULT_MAX_STOCK_PCT / 100, sr.DEFAULT_MAX_SECTOR_PCT / 100})  # fmt: skip
#: 복합 문장의 머리 — 몸통은 넓어 남의 숫자까지 묶이니 보지 않되, 조건·반복 대상·with 항목·데코레이터는 본다(25.1758 —
#: 25.1757 이 복합 문장을 통째로 빼서 `if (… "max_weight_per_sector" … or 30) …` 를 놓쳤다)
_머리 = ("test", "iter", "target", "items", "subject", "decorator_list", "returns")
_복합 = (ast.If, ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try, ast.Match, ast.FunctionDef,
         ast.AsyncFunctionDef, ast.ClassDef, *((ast.TryStar,) if hasattr(ast, "TryStar") else ()))  # fmt: skip


def _숫자(n: ast.AST, 값: frozenset | None = None) -> bool:
    return any(isinstance(c, ast.Constant) and type(c.value) in (int, float) and (값 is None or c.value in 값)
               for c in ast.walk(n))  # fmt: skip


def _언급(n: ast.AST, 이름: re.Pattern = 상한_이름) -> bool:
    return any(이름.search(x) for x in _이름들(n))


def _상수들(v: ast.AST | None, 또는: bool = False) -> Iterable[tuple[ast.Constant, bool]]:
    """값 자리의 숫자 상수 — `-5`·`(\n 30.0\n)`·`float(15)`·`a or 15`·`15 if … else a`·`(15, 25)` 를 풀어 본다. 식 속 숫자
    (`min(a, 5)`)는 보지 않는다 — 계산의 일부라 사본이 아니다. 둘째 값은 `or` 의 직접 피연산자인가(`… or 100` = 제한 없음)"""
    if isinstance(v, ast.UnaryOp):
        yield from _상수들(v.operand, 또는)
    elif isinstance(v, ast.Constant) and type(v.value) in (int, float):
        yield v, 또는
    elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in ("float", "int") and len(v.args) == 1:
        yield from _상수들(v.args[0], 또는)
    elif isinstance(v, ast.BoolOp) and isinstance(v.op, ast.Or):
        for x in v.values:
            yield from _상수들(x, True)
    elif isinstance(v, ast.IfExp):
        yield from _상수들(v.body)
        yield from _상수들(v.orelse)
    elif isinstance(v, (ast.Tuple, ast.List)):
        for x in v.elts:
            yield from _상수들(x)


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
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and n.value is not None:
            과녁, 값 = [n.target], n.value
        else:
            continue
        if any(x in 이름 for t in 과녁 for x in _이름들(t)) and _숫자(값):
            줄.append(n.lineno)
    return 줄


def 상한_숫자(글: str) -> list[int]:
    """비중 상한 기본값을 숫자로 다시 적은 줄 번호. 두 규칙:

    - 강한 자리 — 상한 이름(`강한_이름`)에 바로 묶인 값은 0("배분하지 않음", `jobs/daily`) 말고 **모든 숫자**(정의처와 다른 값은
      같은 값보다 해롭다, 25.1758): 그 이름의 인자 기본값·키워드 인자·대입(튜플·`:=` 포함), 설정 키를 직접 받는 호출의
      다른 인자·키워드 인자(`get_setting(c, "max_…", default=20)`), 설정 키 사전 값, `상한 or 15`, `15 if 상한 … else …`.
      100 은 `… or 100`(제한 없음, `services/paper`) 자리에서만 뺀다 — 설정 기본값 100 은 상한을 없애는 가장 해로운 값이다(25.1759)
    - 느슨한 자리 — 상한을 언급하는 단순 문장·복합 문장 머리·인자 기본값 식 어디든 정의처 기본값(그 비율 꼴 포함)이 있으면.
      줄을 넘긴 `_담다(get_setting_in_range(…, "max_weight_per_stock", …), 10.0)`·`= float(10) / 100` 이 여기서 잡힌다
    """
    줄: set[int] = set()

    def 강(값: ast.AST | None) -> None:
        for c, 또는 in _상수들(값):
            if c.value != 0 and not (또는 and c.value == 100):
                줄.add(c.lineno)

    def 키(x: ast.AST) -> bool:
        return isinstance(x, ast.Constant) and isinstance(x.value, str) and bool(강한_이름.search(x.value))

    for n in ast.walk(ast.parse(글)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = n.args
            위치 = a.posonlyargs + a.args
            for x, d in [*zip(위치[len(위치) - len(a.defaults):], a.defaults, strict=True),
                         *zip(a.kwonlyargs, a.kw_defaults, strict=True)]:  # fmt: skip
                if d is not None and 상한_이름.search(x.arg):
                    강(d) if 강한_이름.search(x.arg) else None
                    if _숫자(d, 상한_값):  # 인자 기본값 식 속 정의처 값(`float(10) / 100`) — 25.1757 이 잡던 꼴
                        줄.add(d.lineno)
        elif isinstance(n, ast.keyword) and n.arg and 강한_이름.search(n.arg):
            강(n.value)
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            과녁 = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(_언급(t, 강한_이름) for t in 과녁):
                강(n.value)
        elif isinstance(n, ast.Call) and (any(키(x) for x in n.args) or any(키(k.value) for k in n.keywords)):
            for x in [*n.args, *(k.value for k in n.keywords)]:
                강(x)
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values, strict=True):
                if k is not None and 키(k):
                    강(v)
        elif (isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or) and any(_언급(v, 강한_이름) for v in n.values)) or (
                isinstance(n, ast.IfExp) and any(_언급(v, 강한_이름) for v in (n.test, n.body, n.orelse))):  # fmt: skip
            강(n)
        # 느슨한 자리 — 단순 문장은 통째로, 복합 문장은 머리만
        if isinstance(n, _복합):
            머리 = [x for f in _머리 for x in (lambda v: v if isinstance(v, list) else [v])(getattr(n, f, None)) if x]
            if any(_언급(x) for x in 머리) and any(_숫자(x, 상한_값) for x in 머리):
                줄.add(n.lineno)
        elif isinstance(n, ast.stmt) and _언급(n) and _숫자(n, 상한_값):
            줄.add(n.lineno)
    return sorted(줄)


def 훑기(찾기, 빼기: Iterable[str] = ()) -> list[str]:
    """batch/ 아래 모든 .py 에 `찾기(글)` 을 돌려 `경로:줄` 목록 — 정의처는 이름이 아니라 경로로 뺀다(25.1756)"""
    빼기 = set(빼기)
    파일 = sorted((뿌리 / "batch").rglob("*.py"))
    # 경로가 빗나가 하나도 못 읽으면 "사본 0" 으로 통과한다(25.1757 교차검증 [참고])
    assert len(파일) > 100 and 뿌리 / "batch" / "jobs" / "paper.py" in 파일, "batch/ 를 못 읽었다"
    return [f"{p}:{k}" for f in 파일
            if (p := str(f.relative_to(뿌리))) not in 빼기 for k in 찾기(f.read_text(encoding="utf-8"))]  # fmt: skip
