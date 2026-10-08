"""**배치가 읽기 실패를 빈 값으로 삼키지 않는다** (docs/infra.md 25.164·25.219~25.224).

`except Exception: return []` 는 "표가 아직 없다" 를 넘기려고 붙이지만 **아무 실패나 다 삼킨다.** 한도·인증 실패도
빈 값이 되고, 그 빈 값이 **점수를 바꾸거나(25.221) 권장 금액을 키운다(25.219).** 표가 없을 때만 넘기려면
`db.표가_없나(exc)` 로 가르고, 그 밖의 실패는 경고·실행 기록으로 **말한다.**

25.164 에서 고친 뒤에도 같은 자리가 여섯 곳 더 나왔다 — 손으로 찾으면 남는다. 웹의 같은 그물은
`web/__tests__/noSwallowedReads.test.ts`.
"""

from __future__ import annotations

import ast
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: (파일, 함수) → 빈 값으로 가도 되는 사유. **사유 없는 예외는 두지 않는다**
면제: dict[tuple[str, str], str] = {
    ("batch/core/entry.py", "deferred_by_read_budget"): "읽기 진도 문(25.1031)이다. 재지 못하면 들어간다 — 재는 일이 작업을"
    " 멈추게 하면 안 된다(25.886 문의 원칙). 경고 로그를 남긴다",
    ("batch/core/client.py", "read_return_marker"): "Turso 복귀 표시(운영 표시, 25.12). 못 읽으면 '표시 없음' 이 곧 안전한 쪽이다",
    ("batch/core/d1.py", "database_size"): "모르면 None — 0 으로 두면 '비어 있다' 가 된다(25.30). 부르는 쪽이 None 을 '모름' 으로 적는다",
    ("batch/core/db.py", "_record_rows"): "쓰기 카운터 갱신이다. 카운터가 본 작업을 막으면 안 되고, 경고 로그를 남긴다",
    ("batch/core/db.py", "d1_writes_today"): "모르면 None — 0 과 구별한다(25.30)",
    ("batch/jobs/daily.py", "signal_age_sessions"): "DB 읽기가 아니라 거래소 달력 계산이다. 모르면 None(나이 모름) — 배분을 막지 않고 경고 줄은 따로 있다 (25.820)",
    ("batch/core/db.py", "db_size_note"): "리포트에 붙일 **말 한 줄**이다. 말하는 일이 배치를 죽이면 안 된다",
    ("batch/core/db.py", "d1_reads_today"): "모르면 None — 0 과 구별한다(25.30)",
    ("batch/core/db.py", "remaining_read_budget_or_none"): "모르면 None — 진도 문이 '모름' 과 '다 남음' 을 가른다(25.886)",
    ("batch/jobs/backtest.py", "load_benchmark_closes"): "백테스트는 사람이 돌려 읽는다. 경고 로그가 남는다(25.221 알고 두는 것)",
    ("batch/notify/telegram.py", "discover_chat_ids"): "설정을 돕는 **후보 목록**이다(25.392). 못 찾아도 '보내지 않는다' 는 결론은 같고 오류 문구에서 후보만 빠진다",
    ("batch/jobs/backtest.py", "load_pit_dividends"): "백테스트의 배당 이력이다. 사람이 돌려 읽고 경고 로그가 남는다(25.221)",
    ("batch/services/adjust.py", "_빠진_거래일"): "DB 읽기가 아니라 달력 계산이다. 못 세면 None 이 '날짜 간격 규칙으로 물러난다'는 뜻이다(25.569)",
}


def 삼키는_자리() -> list[tuple[str, str, int]]:
    """(파일, 함수, 줄). `except Exception` 이 빈 목록·빈 사전·None 을 돌려주면서 표 없음을 가르지도, 말하지도 않는 자리."""
    나온것: list[tuple[str, str, int]] = []
    for 길 in sorted((뿌리 / "batch").rglob("*.py")):
        if "__pycache__" in str(길):
            continue
        상대 = str(길.relative_to(뿌리))
        for 함수 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
            if not isinstance(함수, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for 마디 in ast.walk(함수):
                if not (isinstance(마디, ast.ExceptHandler) and isinstance(마디.type, ast.Name)
                        and 마디.type.id == "Exception"):  # fmt: skip
                    continue
                본문 = ast.unparse(ast.Module(body=마디.body, type_ignores=[]))
                if "표가_없나" in 본문 or "raise" in 본문 or "append" in 본문:
                    continue
                빈값 = any(
                    isinstance(r, ast.Return)
                    and (
                        (isinstance(r.value, ast.List) and not r.value.elts)
                        or (isinstance(r.value, ast.Dict) and not r.value.keys)
                        or (isinstance(r.value, ast.Constant) and r.value.value is None)
                    )
                    for r in ast.walk(ast.Module(body=마디.body, type_ignores=[]))
                )
                if 빈값:
                    나온것.append((상대, 함수.name, 마디.lineno))
    return 나온것


def test_훑기가_실제로_문다() -> None:
    """미끼 — 면제 자리를 실제로 찾아 내는지. 0개면 아래가 공짜로 통과한다."""
    찾은것 = {(f, fn) for f, fn, _ in 삼키는_자리()}
    assert set(면제) <= 찾은것, sorted(set(면제) - 찾은것)


def test_면제_말고는_없다() -> None:
    걸린것 = [f"{f}:{줄} ({fn})" for f, fn, 줄 in 삼키는_자리() if (f, fn) not in 면제]
    assert not 걸린것, (
        "읽기 실패를 빈 값으로 삼킨다 — 표가 없을 때만 넘기려면 `db.표가_없나(exc)` 로 가르고,\n"
        "그 밖의 실패는 경고·실행 기록으로 말하라:\n  " + "\n  ".join(걸린것)
    )


def test_면제에는_사유가_있다() -> None:
    assert all(len(사유) > 5 for 사유 in 면제.values())
