"""연간 보고서 코드가 **한 곳에서만 나오는가** (docs/infra.md 25.143).

DART 의 사업보고서 코드 `11011` 은 `financials` · `financial_snapshots` 를 읽는 거의 모든
질의의 필터다. 2026-09-23 까지 이 다섯 글자가 **아홉 군데**에 따로 적혀 있었다 —
일곱은 각자의 `ANNUAL_REPORT_CODE`·`ANNUAL` 상수, 둘은 SQL 안의 **날글자**였다.

날글자 쪽이 특히 나쁘다. 상수 이름으로 찾을 수 없어서, 코드를 바꿀 일이 생겼을 때
`ANNUAL_REPORT_CODE` 를 훑는 사람의 눈에 **안 보인다.** 25.0 「한 규칙이 두 곳에 있다」 —
이 저장소에서 가장 여러 번 나온 모양이고, 사본이 아홉인 것은 처음이다.

**값이 안 바뀔 텐데 왜 묶나.** 바뀌는 것이 값만은 아니다. "연간 재무를 어떻게 고르나" 는
규칙이고, 그 규칙에 한 마디(예: 미국은 다른 코드)가 붙는 날 아홉 곳을 다 찾아야 한다.
지금 묶어 두면 그날 한 곳만 고친다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
코드 = "11011"

#: 여기에만 날글자가 있어야 한다
정의처 = "batch/sources/dart.py"

#: 훑을 곳. 테스트 fixture 는 일부러 뺀다 — 거기서는 "그 코드의 행" 을 만드는 것이 목적이다
훑을곳 = ("batch", "scripts")


def _문자열_상수(길: Path) -> list[tuple[int, str]]:
    """**설명문과 주석을 뺀** 문자열 상수들.

    주석을 안 걸러 여러 번 헛짚었다(25.105·25.109·25.112·25.116·25.130·25.139).
    `ast` 는 `#` 주석을 애초에 안 담지만 **모듈·함수 설명문은 담는다** — 그것을 뺀다.
    """
    나무 = ast.parse(길.read_text(encoding="utf-8"))
    설명문: set[int] = set()
    for 마디 in ast.walk(나무):
        if isinstance(마디, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            첫 = 마디.body[0] if 마디.body else None
            if isinstance(첫, ast.Expr) and isinstance(첫.value, ast.Constant):
                설명문.add(id(첫.value))
    return [
        (마디.lineno, 마디.value)
        for 마디 in ast.walk(나무)
        if isinstance(마디, ast.Constant)
        and isinstance(마디.value, str)
        and id(마디) not in 설명문
    ]


def 날글자가_있는_곳() -> dict[str, list[int]]:
    나온것: dict[str, list[int]] = {}
    for 자리 in 훑을곳:
        for 길 in sorted((뿌리 / 자리).rglob("*.py")):
            if "__pycache__" in str(길):
                continue
            줄들 = [줄 for 줄, 값 in _문자열_상수(길) if 코드 in 값]
            if 줄들:
                나온것[str(길.relative_to(뿌리))] = 줄들
    return 나온것


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다."""
    전체 = sum(1 for 자리 in 훑을곳 for _ in (뿌리 / 자리).rglob("*.py"))
    assert 전체 > 50, f"파이썬 파일을 {전체}개밖에 못 찾았다"
    assert 날글자가_있는_곳(), "정의처의 날글자조차 못 찾았다 — 훑기가 고장 났다"


def test_날글자는_정의처에만_있다() -> None:
    """새로 적으면 여기서 걸린다. **묶어 두는 값어치는 다음 사람이 받는다.**"""
    찾음 = 날글자가_있는_곳()
    딴곳 = {k: v for k, v in 찾음.items() if k != 정의처}

    assert not 딴곳, (
        f"연간 보고서 코드 `{코드}` 가 정의처 밖에 적혀 있다: {딴곳}\n"
        f"`from batch.sources import dart` 뒤 `dart.ANNUAL_REPORT_CODE` 를 써라.\n"
        "SQL 안에 박으면 상수 이름으로 찾을 수 없다 (docs/infra.md 25.143)"
    )


def test_정의처의_값이_실제로_연간이다() -> None:
    """**상수가 판정에 쓰이는지를 본다** (25.108 의 교훈). 이름만 `ANNUAL` 이면 장식이다."""
    from batch.sources import dart

    assert dart.ANNUAL_REPORT_CODE in dart.REPORT_CODES
    이름, 주기 = dart.REPORT_CODES[dart.ANNUAL_REPORT_CODE]
    assert 주기 == "A", f"`{dart.ANNUAL_REPORT_CODE}` 는 연간이 아니다 ({이름}, {주기})"


@pytest.mark.parametrize(
    ("모듈", "이름"),
    [
        ("batch.jobs.scores", "ANNUAL_REPORT_CODE"),
        ("batch.jobs.signals", "ANNUAL_REPORT_CODE"),
        ("batch.jobs.backtest", "ANNUAL_REPORT_CODE"),
        ("batch.jobs.valuation_bands", "ANNUAL_REPORT_CODE"),
        ("batch.jobs.us_financials", "ANNUAL_REPORT_CODE"),
        ("batch.jobs.accumulation", "ANNUAL"),
        ("batch.sources.dart_dividends", "ANNUAL"),
    ],
)
def test_각_모듈이_같은_값을_본다(모듈: str, 이름: str) -> None:
    """이름은 저마다 다르게 두되 **값은 한 곳에서 온다**.

    이름까지 통일하지 않은 이유: 이 절의 목적은 값의 단일 정의처이지 이름 고르기가 아니다.
    한 번에 한 가지만 바꾼다.
    """
    import importlib

    from batch.sources import dart

    m = importlib.import_module(모듈)
    assert getattr(m, 이름) == dart.ANNUAL_REPORT_CODE


def test_미국_재무도_같은_코드로_적힌다() -> None:
    """**이상해 보이는 배선을 검사로 못 박는다.**

    SEC 10-K 를 넣는 `jobs/us_financials` 가 DART 의 코드를 일부러 쓴다. 그래야
    `financials` 를 읽는 한 벌의 질의가 두 나라를 함께 덮는다. 모르고 고치면
    미국 재무가 모든 읽기에서 조용히 사라진다 — 오류 없이, 빈칸으로.
    """
    from batch.jobs import us_financials
    from batch.sources import dart

    assert us_financials.ANNUAL_REPORT_CODE == dart.ANNUAL_REPORT_CODE
