"""**생성물을 저장소에 넣지 않는다** (docs/infra.md 25.116).

2026-09-22 에 `npx vitest run --coverage` 를 돌린 뒤 `git add -A` 로 커밋하면서
`web/coverage/` 200여 개가 통째로 들어갔다. 며칠 뒤 같은 세션의 다른 검사가
**그 안의 옛 소스 HTML** 을 물어 "고지 문장을 손으로 적은 곳이 있다" 고 잘못 말했다.

생성물은 소스보다 늦게 썩는다. 저장소에 남으면 **훑는 검사마다 거짓 양성**을 만들고,
프라이빗 저장소라 아무도 눈치채지 못한다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: 도구가 만드는 자리. 여기 있는 파일이 추적되면 안 된다
생성물 = ("web/coverage/", "web/.next/", "web/out/", "node_modules/", ".pytest_cache/", "htmlcov/")


def _추적중인_파일() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=뿌리, capture_output=True, text=True, check=False
    )
    return out.stdout.splitlines()


def test_읽어_냈다() -> None:
    """**0개를 훑고 통과하면 그물이 아니라 장식이다.**"""
    assert len(_추적중인_파일()) > 100, "git ls-files 를 못 읽었다"


def test_생성물이_추적되지_않는다() -> None:
    걸린것 = [f for f in _추적중인_파일() if any(f.startswith(d) or f"/{d}" in f for d in 생성물)]
    assert not 걸린것, (
        "도구가 만든 파일이 저장소에 들어 있다. .gitignore 에 넣고 지운다:\n  "
        + "\n  ".join(걸린것[:20])
    )


def test_gitignore_가_그_자리를_막는다() -> None:
    """지우기만 하면 다음 `git add -A` 가 도로 넣는다."""
    본문 = (뿌리 / ".gitignore").read_text(encoding="utf-8")
    for 자리 in ("web/coverage/", "web/.next/", "web/out/"):
        assert 자리 in 본문, f".gitignore 에 {자리} 가 없다"


def test_진단_SQL_은_무시되지_않는다_25_874() -> None:
    """`*.sql` 무시 규칙 때문에 7회차 진단 질의가 커밋되지 않은 채 "저장했다" 고 적혔다."""
    import subprocess
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parent.parent
    rc = subprocess.run(["git", "check-ignore", "-q", "--no-index", "scripts/diag/round7_financial_debt.sql"], cwd=뿌리).returncode
    assert rc == 1, "scripts/diag/*.sql 이 .gitignore 에 걸린다"
