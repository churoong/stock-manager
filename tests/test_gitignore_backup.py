"""백업 파일이 **저장소에 들어올 수 없는가** (docs/infra.md 25.159).

CLAUDE.md 의 절대 규칙 하나가 이렇다.

> **코드 저장소는 공개, 데이터와 운영 출력은 비공개.** 시세·재무·보유 데이터는 DB 에만 두고 저장소에 넣지 않는다

2026-10-07 부터 코드 저장소가 **공개**다(docs/public-repo.md). 그래서 **무엇이 들어 있나**를 이 검사가 지킨다.

`scripts/backup_db.py --out backup/` 이 만드는 파일에는 매매 기록·배당·설정·관심 종목과
한국거래소·야후에서 받은 시세·재무가 **통째로** 들어 있다. 그리고 `docs/backup.md` 2장이
**로컬에서 손으로 받는 법**을 안내한다 — 드문 길이 아니다.

`git add -A` 한 번이면 그것이 이력에 박힌다. **지워도 이력에는 남는다.**

여기서 보는 것은 `.gitignore` 의 글자가 아니라 **git 이 실제로 무시하는가** 다
(`git check-ignore`). 규칙을 적어 두고 오타 하나로 안 걸리는 일을 막는다 —
25.151 에서 배운 것: 함수가 옳은 것과 부르는 곳이 옳은 것은 다른 일이다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent

#: 들어오면 안 되는 길. `backup_db.py` 설명문과 `backup.yml` 이 쓰는 경로 그대로다
막아야_할_것 = [
    "backup/stock-manager-essential-2026-09-23.sql.gz",
    "backup/stock-manager-full-2026-09-23.sql.gz",
    "내려받은-백업.sql.gz",
    "web/backup.sql.gz",
    # 압축을 풀면 `.sql` 이다 (2026-09-23 덧). `*.sql.gz` 만으로는 안 걸린다
    "내려받은-백업.sql",
    "backup/stock-manager-essential-2026-09-23.sql",
    "restore.sql",
]

#: 반대로 **막히면 안 되는** 것. 너무 넓게 막으면 스키마가 사라진다
막으면_안_되는_것 = [
    "migrations/0001_initial.sql",
    "migrations/0021_sentiment.sql",
    # **아직 없는 마이그레이션도 더할 수 있어야 한다.** 되살리기 규칙이
    # `!migrations/*.sql` 이라 여기서 그것을 못 박는다
    "migrations/0099_아직_없는_것.sql",
]


def _git가_무시하나(상대경로: str) -> bool:
    """`git check-ignore` 는 실제 판정이다. `.gitignore` 를 손으로 파싱하지 않는다."""
    결과 = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-q", "--no-index", 상대경로],  # noqa: S607
        cwd=뿌리,
        capture_output=True,
        check=False,
    )
    return 결과.returncode == 0


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (뿌리 / ".git").exists(),
    reason="git 저장소가 아니다 (내려받은 사본에서 돌릴 때)",
)


@pytest.mark.parametrize("길", 막아야_할_것)
def test_백업_파일은_커밋할_수_없다(길: str) -> None:
    assert _git가_무시하나(길), (
        f"`{길}` 이 무시되지 않는다. `git add -A` 한 번에 매매 기록과 시세가 이력에 박힌다"
        " — 지워도 이력에는 남는다 (docs/infra.md 25.159)"
    )


@pytest.mark.parametrize("길", 막으면_안_되는_것)
def test_스키마는_막지_않는다(길: str) -> None:
    """`*.sql` 을 통째로 막으면 **스키마의 단일 정의처**가 사라진다 (CLAUDE.md)."""
    assert not _git가_무시하나(길), f"`{길}` 까지 무시하고 있다 — 너무 넓게 막았다"


def test_백업이_실제로_그_경로에_쓴다() -> None:
    """**막는 길과 쓰는 길이 같아야 한다.** 다른 데 쓰면 막아 둔 뜻이 없다."""
    글 = (뿌리 / "scripts" / "backup_db.py").read_text(encoding="utf-8")
    워크플로 = (뿌리 / ".github" / "workflows" / "backup.yml").read_text(encoding="utf-8")

    assert "--out backup/" in 글, "설명문이 안내하는 경로가 바뀌었다 — 무시 규칙도 함께 보라"
    assert "--out backup" in 워크플로
    assert ".sql.gz" in 글


def test_이력에_백업이_들어간_적이_없다() -> None:
    """**한 번 박히면 지워도 남는다.** 지금까지 깨끗한지 확인해 둔다."""
    결과 = subprocess.run(  # noqa: S603
        ["git", "log", "--all", "--name-only", "--pretty=format:"],  # noqa: S607
        cwd=뿌리,
        capture_output=True,
        text=True,
        check=False,
    )
    if 결과.returncode != 0:
        pytest.skip("git log 를 읽지 못했다")

    걸린것 = sorted(
        {
            줄.strip()
            for 줄 in 결과.stdout.splitlines()
            if 줄.strip().endswith(".sql.gz") or 줄.strip().startswith("backup/")
        }
    )

    assert not 걸린것, f"이력에 백업 파일이 있다: {걸린것}"
