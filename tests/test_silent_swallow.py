"""**조용히 삼키는 자리마다 사유가 있는가** (docs/infra.md 25.164).

웹 쪽은 `web/__tests__/notReadVsNone.test.ts` 가 본다(25.163). 파이썬은 안 봤었고,
열어 보니 같은 모양이 있었다 — 그리고 파이썬 쪽은 **빈 값이 숫자를 바꾼다.**

`scores.load_sentiments` 가 그랬다. 설명문은 "표가 없거나 값이 없으면 빈 dict" 인데
`except Exception` 은 뜻을 모른다. 한도에 걸린 날에도 빈 dict 이 되고, 그러면
`total_score` 가 **온 시장을 5팩터로 재정규화**한다(CLAUDE.md). 점수가 달라지는데
아무 데도 그 사실이 안 남았다.

여기서 막는 것은 **다음번**이다. `except Exception` 을 쓰면 둘 중 하나를 해야 한다 —
`db.표가_없나()` 로 가리거나, **왜 삼켜도 되는지**를 같은 줄에 적거나.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from batch.core import db

뿌리 = Path(__file__).resolve().parent.parent

#: 삼킴 표시(BLE001) 뒤에 붙는 사유가 이만큼은 돼야 한다. 빈 칸은 사유가 아니다
사유_최소길이 = 4

_사유무늬 = re.compile(r"#\s*noqa:\s*BLE001\s*[—\-–]\s*(.+)$")


def _소스들() -> list[Path]:
    나온것: list[Path] = []
    for 자리 in ("batch", "scripts"):
        나온것 += [
            f
            for f in sorted((뿌리 / 자리).rglob("*.py"))
            if "__pycache__" not in f.parts
        ]
    return 나온것


def _삼키는_곳() -> list[tuple[Path, int, str]]:
    """**말없이** 넘어가는 `except Exception` 만 고른다.

    삼킴이 아닌 것 둘은 뺀다.
      - 다시 던지는 것 (`raise` 가 몸통 어딘가에 있다)
      - **예외를 쓰는 것** — `as exc` 로 받아 로그에 남기거나 메시지에 실으면,
        무슨 일이 있었는지는 남는다. 사라지는 것이 없으니 사유를 물을 일도 없다
    """
    나온것: list[tuple[Path, int, str]] = []
    for 길 in _소스들():
        글 = 길.read_text(encoding="utf-8")
        줄들 = 글.splitlines()
        나무 = ast.parse(글)
        for 마디 in ast.walk(나무):
            if not isinstance(마디, ast.ExceptHandler):
                continue
            이름 = 마디.type
            넓은가 = isinstance(이름, ast.Name) and 이름.id in ("Exception", "BaseException")
            if not 넓은가:
                continue
            몸통 = list(ast.walk(ast.Module(body=마디.body, type_ignores=[])))
            if any(isinstance(n, ast.Raise) for n in 몸통):
                continue
            쓴다 = 마디.name is not None and any(
                isinstance(n, ast.Name) and n.id == 마디.name for n in 몸통
            )
            if 쓴다:
                continue
            나온것.append((길, 마디.lineno, 줄들[마디.lineno - 1]))
    return 나온것


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다 (25.0 「그물이 …」)."""
    assert len(_소스들()) > 40, "소스를 너무 적게 찾았다"
    assert len(_삼키는_곳()) > 10, "`except Exception` 을 하나도 못 찾았다 — 훑기가 깨졌다"


def test_삼키는_곳마다_사유가_있다() -> None:
    """**사유 없는 삼킴은 다음 사람이 지워도 되는지 모른다.**

    형식은 이 저장소가 이미 쓰던 것 그대로다 — `# noqa: BLE001 — <왜 삼켜도 되나>`.
    """
    없는것 = []
    for 길, 번호, 줄 in _삼키는_곳():
        m = _사유무늬.search(줄)
        if m is None or len(m.group(1).strip()) < 사유_최소길이:
            없는것.append(f"{길.relative_to(뿌리)}:{번호}")

    assert not 없는것, (
        "사유 없이 실패를 삼키는 자리가 있다:\n  " + "\n  ".join(없는것) + "\n"
        "`# noqa: BLE001 — <왜 삼켜도 되나>` 를 같은 줄에 적어라 (docs/infra.md 25.164)"
    )


class Test표가_없나:
    """웹의 `db.ifMissingTable` 과 **같은 판정**이어야 한다."""

    def test_표가_없으면_참(self) -> None:
        assert db.표가_없나(Exception("no such table: sentiment_scores"))
        assert db.표가_없나(Exception("SQL 실패: NO SUCH TABLE: x")), "대소문자를 가리지 않는다"

    @pytest.mark.parametrize(
        "말",
        [
            "blocked: upgrade your plan",
            "401 unauthorized",
            "daily row write limit exceeded",
            "connection reset",
        ],
    )
    def test_다른_실패는_거짓(self, 말: str) -> None:
        """이것들이 삼켜지면 화면·리포트가 "아직 없습니다" 라고 적는다 — 그것이 25.163·164 다."""
        assert not db.표가_없나(Exception(말))

    def test_두_언어가_같은_글자를_본다(self) -> None:
        글 = (뿌리 / "web" / "lib" / "db.ts").read_text(encoding="utf-8")
        assert "/no such table/i" in 글, "웹이 다른 잣대를 쓴다 — 두 언어가 갈라졌다"


class Test감성을_못_읽으면_말한다:
    """**빈 값이 숫자를 바꾸는 자리**라 조용히 넘어가면 안 된다."""

    @staticmethod
    def _터지는_클라이언트(말: str):
        class 터짐:
            def execute(self, *_a, **_k):
                raise RuntimeError(말)

        return 터짐()

    def test_표가_없으면_조용하다(self) -> None:
        from batch.jobs import scores

        값, 이유 = scores.load_sentiments(
            self._터지는_클라이언트("no such table: sentiment_scores"), "KR", "2026-09-23"
        )
        assert 값 == {}
        assert 이유 is None, "마이그레이션 전 DB 는 정상이다. 그때까지 시끄러우면 아무도 안 읽는다"

    def test_다른_실패는_이유를_돌려준다(self) -> None:
        from batch.jobs import scores

        값, 이유 = scores.load_sentiments(
            self._터지는_클라이언트("blocked: upgrade your plan"), "KR", "2026-09-23"
        )
        assert 값 == {}
        assert 이유 is not None and "blocked" in 이유

    def test_배치가_그_이유를_남기고_말한다(self) -> None:
        글 = (뿌리 / "batch" / "jobs" / "scores.py").read_text(encoding="utf-8")

        assert "sentiment_read_error" in 글, "실행 기록(step_log)에 남지 않으면 나중에 알 길이 없다"
        assert "5팩터로 재정규화했습니다" in 글, "사람이 보는 줄에도 나와야 한다"

    def test_매도_플래그도_같은_말을_한다(self) -> None:
        """**같은 판단을 하는 다른 자리** — 감성급락 플래그가 영영 안 뜨는 것을 알 수 있어야 한다."""
        글 = (뿌리 / "batch" / "jobs" / "sell_flags.py").read_text(encoding="utf-8")

        assert "감성을 읽지 못해 감성급락은 판정하지 않았습니다" in 글
        assert "표가_없나" in 글

    def test_따라잡기_알림도_가린다(self) -> None:
        """"신호 0건" 과 "못 읽었다" 는 다른 말이다."""
        글 = (뿌리 / "scripts" / "catchup_report.py").read_text(encoding="utf-8")

        assert "signals_read_error" in 글
        assert "신호 읽지 못함" in 글
