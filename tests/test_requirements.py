"""고정한 의존성이 실제로 쓰이는가 (`requirements.txt`).

**왜 있나.** 2026-09-20 에 `SQLAlchemy==2.0.54` 가 **아무 데서도 import 되지 않은 채**
넉 달 가까이 고정돼 있던 것을 찾았다. 주석에는 "DB. Step 1 부터 본격 사용" 이라고 적혀
있었는데, Alembic 을 포기하면서(docs/infra.md 2번) 끝내 쓰지 않았다. 주석만 남고 사실은
바뀐 것이다.

안 쓰는 고정이 왜 문제인가:

- **거짓말을 한다.** `CLAUDE.md` 가 "스키마의 단일 정의처는 SQLAlchemy 모델" 이라고
  적고 있었다. 다음 사람이 그 모델을 찾다가 없다는 걸 알고 나서야 사실을 안다
- 매 실행마다 받고 푼다. GitHub Actions 무료 분이 바닥난 지금(25.27) 초 단위도 아깝다
- 올릴 때마다 호환을 따져 봐야 한다. 쓰지도 않는 것을

그래서 **고정한 것은 쓰거나, 왜 쓰지 않는지 여기 적거나** 둘 중 하나다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
요구파일 = 뿌리 / "requirements.txt"

#: 배포 이름 → import 이름. 다른 것만 적는다
IMPORT_NAME = {
    "exchange-calendars": "exchange_calendars",
    "python-dotenv": "dotenv",
    "PyYAML": "yaml",
}

#: import 하지 않지만 남겨 두는 것. **이유를 적어야 들어올 수 있다.**
#: 이 칸이 늘어나면 한 줄씩 다시 물어봐야 한다 — "정말 필요한가"
직접_쓰지_않음 = {
    "numpy": "pandas·exchange-calendars 가 요구해 어차피 깔린다. 버전이 저절로 움직이면"
    " pandas 쪽이 깨질 수 있어 여기서 못 박는다",
}

찾는_곳 = ("batch", "scripts", "tests")


def 고정된_것들() -> list[str]:
    """`requirements.txt` 에 고정된 배포 이름들. 주석과 빈 줄은 건너뛴다."""
    이름들 = []
    for 줄 in 요구파일.read_text(encoding="utf-8").splitlines():
        줄 = 줄.strip()
        if not 줄 or 줄.startswith("#"):
            continue
        이름들.append(re.split(r"[=<>!~\[;]", 줄, maxsplit=1)[0].strip())
    return 이름들


def 어디선가_import_하는가(모듈: str) -> bool:
    패턴 = re.compile(rf"^\s*(?:from\s+{re.escape(모듈)}[\s.]|import\s+{re.escape(모듈)}\b)", re.M)
    for 디렉터리 in 찾는_곳:
        for 경로 in (뿌리 / 디렉터리).rglob("*.py"):
            if "__pycache__" in 경로.parts:
                continue
            if 패턴.search(경로.read_text(encoding="utf-8")):
                return True
    # `importorskip("yaml")` 처럼 문자열로 부르는 경우도 본다
    for 디렉터리 in 찾는_곳:
        for 경로 in (뿌리 / 디렉터리).rglob("*.py"):
            if "__pycache__" in 경로.parts:
                continue
            if f'"{모듈}"' in 경로.read_text(encoding="utf-8"):
                return True
    return False


def test_읽어_냈다() -> None:
    """파싱이 조용히 빈 목록을 내면 아래가 전부 통과한다. 가장 위험한 실패다."""
    고정 = 고정된_것들()

    assert len(고정) >= 5, f"requirements.txt 를 읽지 못했다: {고정}"
    assert "pandas" in 고정


@pytest.mark.parametrize("배포이름", 고정된_것들())
def test_고정한_것은_쓰거나_이유가_적혀_있다(배포이름: str) -> None:
    if 배포이름 in 직접_쓰지_않음:
        assert 직접_쓰지_않음[배포이름], "이유가 비어 있다"
        return

    모듈 = IMPORT_NAME.get(배포이름, 배포이름)

    assert 어디선가_import_하는가(모듈), (
        f"{배포이름} 을 고정했는데 import 하는 곳이 없다."
        " 지우거나, 남길 이유를 tests/test_requirements.py 의 `직접_쓰지_않음` 에 적는다"
    )


def test_SQLAlchemy_는_돌아오지_않는다() -> None:
    """지운 것이 슬그머니 돌아오지 않게 못 박는다 (docs/infra.md 25.36).

    Alembic 을 쓰지 않기로 한 이상 SQLAlchemy 를 넣을 이유가 없다. 넣고 싶어지면
    먼저 `batch/core/db.py` 머리말의 판단부터 뒤집어야 한다.
    """
    본문 = 요구파일.read_text(encoding="utf-8")
    고정 = [이름.lower() for 이름 in 고정된_것들()]

    assert "sqlalchemy" not in 고정
    assert "alembic" not in 고정
    # 지웠다는 사실과 이유는 파일에 남아 있어야 한다. 안 그러면 다음 사람이 다시 넣는다
    assert "SQLAlchemy 는 쓰지 않는다" in 본문


def test_CLAUDE_md_가_Alembic_을_쓴다고_말하지_않는다() -> None:
    """문서와 코드가 어긋난 채로 두지 않는다 (CLAUDE.md 기록 규칙).

    2026-09-20 까지 CLAUDE.md 는 세 군데에서 사실과 다르게 적고 있었다 —
    "스키마 변경은 Alembic으로 관리", "단일 정의처는 SQLAlchemy 모델",
    "마이그레이션: alembic upgrade head".
    """
    본문 = (뿌리 / "CLAUDE.md").read_text(encoding="utf-8")

    assert "alembic upgrade head" not in 본문
    assert "python -m batch.jobs.migrate" in 본문
    # Alembic 을 언급하는 줄은 "쓰지 않는다" 를 함께 말해야 한다
    for 줄 in 본문.splitlines():
        if "Alembic" in 줄:
            assert "쓰지 않는다" in 줄, f"Alembic 을 쓴다고 읽힌다: {줄.strip()[:60]}"
