"""기본 점검 묶음이 운영 DB 에서 실제로 도는가 (scripts/db_status.py).

**왜 필요한가.** 이 묶음은 폰·클라우드 세션이 DB 를 보는 유일한 창이다(docs/infra.md 25.17).
그런데 질의가 틀려도 **돌려 보기 전에는 모른다** — Actions 를 한 번 돌리고 이슈 코멘트를 읽어야
알게 되니, 오타 하나에 몇 분과 Actions 분이 날아간다. 실제로 2026-09-20 에 점검 질의가
`too many terms in compound SELECT` 로 거절당했다(D1 이 `UNION ALL` 항 수를 좁게 제한한다).

여기서는 마이그레이션으로 세운 진짜 스키마에 대고 **모든 점검을 실행해 본다.** 표 이름이나
열 이름이 바뀌면 이 테스트가 먼저 깨진다.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.db_status import CHECKS, is_read_only  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture(scope="module")
def 스키마() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    # batch/core/db.py 의 apply_migrations 가 코드로 만드는 표. .sql 에는 없다
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):
            con.executescript(path.read_text(encoding="utf-8"))
    return con


@pytest.mark.parametrize("제목, sql", CHECKS, ids=lambda v: v[:24] if isinstance(v, str) else "")
def test_점검_질의가_실제_스키마에서_돈다(제목: str, sql: str, 스키마: sqlite3.Connection) -> None:
    스키마.execute(sql).fetchall()


@pytest.mark.parametrize("제목, sql", CHECKS, ids=lambda v: v[:24] if isinstance(v, str) else "")
def test_점검_질의는_읽기_전용이다(제목: str, sql: str) -> None:
    """가드가 자기 묶음을 거절하면 기본 실행이 통째로 거절 메시지만 낸다."""
    assert is_read_only(sql), f"{제목} 이 읽기 전용 가드에 걸린다"


def test_D1_의_합성_SELECT_제한에_걸리지_않는다() -> None:
    """D1 은 `UNION ALL` 항 수를 좁게 제한한다 (infra 25.5). 여러 값을 한 줄에 보고 싶으면
    세로로 잇지 말고 스칼라 서브쿼리를 가로로 늘어놓는다."""
    for 제목, sql in CHECKS:
        assert sql.upper().count(" UNION ") <= 2, (
            f"{제목}: UNION 을 여러 번 쓴다 — D1 이 'too many terms in compound SELECT' 로 거절한다."
            " 스칼라 서브쿼리를 가로로 늘어놓으세요"
        )


def test_나라별_채움이_맨_앞이다() -> None:
    """아침마다 가장 먼저 보는 숫자다. 뒤로 밀리면 긴 출력에 묻힌다."""
    assert "나라별" in CHECKS[0][0]


#: 수백만 행 표. 조건 없이 통째로 세면 한 번에 Turso 읽기 1,353만 행이었다 (docs/infra.md 25.980)
큰_표 = ("prices", "financials", "factors", "scores", "signals", "performance_metrics")


@pytest.mark.parametrize("제목, sql", CHECKS, ids=lambda v: v[:24] if isinstance(v, str) else "")
def test_기본_묶음은_큰_표를_통째로_세지_않는다(제목: str, sql: str) -> None:
    """기본 묶음은 하루 몇 번 돈다. 큰 표 전체 COUNT 하나가 묶음 나머지의 250배를 읽었다."""
    for 표 in 큰_표:
        assert not re.search(rf"COUNT\((\*|DISTINCT \w+)\) FROM {표}\s*\)", sql), (
            f"{제목}: {표} 를 조건 없이 센다 — 읽기 예산을 크게 쓴다. `--tables` 로 옮기세요"
        )


def test_나라별_점검이_있다() -> None:
    """복귀 뒤 두 나라가 한 숫자에 섞이지 않게 (docs/infra.md 25.75).

    예전 "따라잡기 진행"(25.980 에서 뺐다)은 나라를 가리지 않고 셌다. 국내만 돌릴 때는 그게 곧 국내 숫자지만,
    미국이 살아나면 "점수 1,800" 이 국내 900 + 미국 900 인지 알 수 없게 된다.
    """
    제목들 = [제목 for 제목, _ in CHECKS]

    assert any("나라별" in 제목 for 제목 in 제목들), "나라를 가려 보는 점검이 사라졌다"


def test_나라별_점검이_정말_나라로_가른다() -> None:
    """제목만 '나라별' 이고 실제로 안 가르면 아무 쓸모가 없다."""
    sql = next(sql for 제목, sql in CHECKS if "나라별" in 제목)

    assert "GROUP BY s.country" in sql
    assert sql.count("country") >= 6, "나라 조건이 몇 군데밖에 없다 — 어떤 칸은 여전히 섞인다"


def test_나라별_점검이_두_나라를_따로_센다(스키마: sqlite3.Connection) -> None:
    """실제 스키마에 국내·미국을 한 줄씩 넣고 **두 줄이 나오는지** 본다."""
    for i, country in ((1, "KR"), (2, "US")):
        스키마.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'M', ?, 'KRW', 'active', 't', 't')",
            [i, f"T{i}", country],
        )
    sql = next(sql for 제목, sql in CHECKS if "나라별" in 제목)

    행 = 스키마.execute(sql).fetchall()

    assert [r[0] for r in 행] == ["KR", "US"]
    assert all(r[1] == 1 for r in 행), "나라마다 제 종목만 세야 한다"


def test_묶음이_비어_있지_않다() -> None:
    assert len(CHECKS) >= 10
