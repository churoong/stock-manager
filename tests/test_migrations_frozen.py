"""**적용한 마이그레이션은 고치지 않는다** (docs/infra.md 25.168).

CLAUDE.md 스택 규칙에 이렇게 적혀 있다.

> 파일은 번호 순서로만 적용한다. 중간에 끼워 넣지 않는다.
> **이미 적용한 파일은 고치지 않는다. 새 파일을 추가한다**

그런데 지키는 장치가 없었다. `schema_migrations` 는 `version` 과 `applied_at` 만 담고
**내용의 지문을 남기지 않는다.** 그래서 이미 적용된 파일을 고치면 저장소와 운영 DB 가
갈라지고 — `apply_migrations` 는 이미 적용된 버전을 건너뛰므로 — **그 변경은 영영
운영에 닿지 않는다.** 아무 오류도 나지 않는다.

여기서 보는 것은 **git 이력**이다. 파일이 처음 들어온 커밋의 내용과 지금 내용을
견준다. 주석은 뺀다 — **주석을 고치는 것은 스키마를 고치는 것이 아니다.**
(실제로 `0025_health.sql` 이 한 번 그렇게 고쳐졌고, 스키마는 같았다.)

주석을 떼는 일은 `db._split_statements` 가 한다. **적용기가 쓰는 바로 그 함수다** —
다른 도구로 보면 더 너그럽거나 더 엄해져서 운영에서만 갈라진다
(25.0 「테스트가 진짜 길로 안 지난다」).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from batch.core import db

뿌리 = Path(__file__).resolve().parent.parent
마이그 = 뿌리 / "migrations"

#: 파일 이름 규칙. **`db.migration_files()` 의 glob 과 같아야 한다** — 이름이 이 모양이
#: 아니면 `apply_migrations` 가 조용히 건너뛴다(오류도 경고도 없다)
이름_무늬 = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (뿌리 / ".git").exists(),
    reason="git 저장소가 아니다 (내려받은 사본에서 돌릴 때)",
)


def _git(*args: str) -> str:
    결과 = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=뿌리,
        capture_output=True,
        text=True,
        check=False,
    )
    if 결과.returncode != 0:
        pytest.skip(f"git {' '.join(args)} 실패")
    return 결과.stdout


def _들어온_커밋(상대경로: str) -> str | None:
    """그 파일을 **처음 더한** 커밋. 이름이 바뀌었으면 따라간다."""
    줄 = _git("log", "--diff-filter=A", "--format=%H", "--follow", "--", 상대경로).split()
    return 줄[-1] if 줄 else None


def _문장들(글: str) -> list[str]:
    """주석을 떼고 공백을 눌러 문장 목록으로. 적용기와 같은 함수를 쓴다."""
    return [" ".join(s.split()) for s in db._split_statements(글)]


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다."""
    assert len(db.migration_files()) > 30, "마이그레이션을 너무 적게 찾았다"


@pytest.mark.parametrize("길", db.migration_files(), ids=lambda p: p.name)
def test_이름이_적용되는_모양이다(길: Path) -> None:
    """**이름이 어긋나면 조용히 건너뛴다.** 오류도 경고도 없이 그 표가 영영 안 생긴다."""
    assert 이름_무늬.match(길.name), (
        f"`{길.name}` 은 `db.migration_files()` 의 glob 에 안 걸린다 — 적용되지 않는다"
    )


def test_번호가_겹치지_않는다() -> None:
    """번호가 겹치면 **적용 순서가 파일 이름에 달린다.** 의존이 있으면 깨진다."""
    번호 = [p.name[:4] for p in db.migration_files()]
    겹침 = sorted({n for n in 번호 if 번호.count(n) > 1})
    assert not 겹침, f"번호가 겹친다: {겹침}"


@pytest.mark.parametrize("길", db.migration_files(), ids=lambda p: p.name)
def test_한번_들어온_스키마는_바뀌지_않았다(길: Path) -> None:
    """**이미 적용한 파일을 고치면 그 변경은 영영 운영에 닿지 않는다.**

    `apply_migrations` 가 이미 적용된 버전을 건너뛰기 때문이다. 저장소만 바뀌고
    DB 는 그대로인데, 코드는 저장소를 보고 "그 열이 있다" 고 믿는다.
    """
    상대 = str(길.relative_to(뿌리))
    커밋 = _들어온_커밋(상대)
    if 커밋 is None:
        pytest.skip(f"{상대} 는 아직 커밋되지 않았다 (새 마이그레이션)")

    옛글 = _git("show", f"{커밋}:{상대}")
    옛, 새 = _문장들(옛글), _문장들(길.read_text(encoding="utf-8"))

    assert 새 == 옛, (
        f"`{상대}` 의 SQL 이 처음 들어온 뒤로 바뀌었다 (주석은 뺀 비교다).\n"
        "이미 적용된 파일을 고치면 **그 변경은 운영 DB 에 영영 닿지 않는다** —\n"
        "새 마이그레이션 파일을 더해라 (CLAUDE.md 스택 규칙)"
    )


def test_주석만_고치는_것은_막지_않는다() -> None:
    """**왜 주석을 빼고 보는가.** 근거를 적어 두는 일까지 막으면 기록 규칙과 부딪힌다.

    `0025_health.sql` 이 실제로 그렇게 고쳐졌다 — `kind` 열의 설명을 줄 위로 옮기고
    `calendar-low` 의 뜻을 적었다(25.104). 스키마는 한 글자도 안 바뀌었다.
    """
    원본 = "-- 옛 설명\nCREATE TABLE t (\n  a TEXT NOT NULL  -- 설명\n);\n"
    고침 = "-- 새 설명\n-- 여러 줄로\nCREATE TABLE t (\n  a TEXT NOT NULL\n);\n"

    assert _문장들(원본) == _문장들(고침)


def test_스키마를_고치는_것은_잡는다() -> None:
    """그물이 실제로 무는지 본다. 위 비교가 늘 참이면 이 파일은 뜻이 없다."""
    원본 = "CREATE TABLE t (\n  a TEXT NOT NULL\n);\n"
    고침 = "CREATE TABLE t (\n  a TEXT\n);\n"

    assert _문장들(원본) != _문장들(고침)


def test_적용기와_같은_함수로_본다() -> None:
    """다른 도구로 주석을 떼면 운영에서만 갈라진다 (25.0 「테스트가 진짜 길로 안 지난다」)."""
    글 = (뿌리 / "batch" / "core" / "db.py").read_text(encoding="utf-8")
    적용부 = 글.split("def apply_migrations", 1)[1]

    assert "_split_statements(" in 적용부, "적용기가 다른 방법으로 문장을 나눈다 — 그물의 전제가 깨졌다"
