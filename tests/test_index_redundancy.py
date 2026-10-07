"""겹치는 인덱스가 새로 생기지 않게 막는다 (docs/infra.md 25.25, migrations/0039·0040).

**왜 이것이 돈 문제인가.** D1 은 쓰기를 셀 때 인덱스까지 센다. 한 행을 넣으면
`1 + 인덱스 수` 만큼 하루 한도(10만 행)를 태운다. 그래서 **쓸모없는 인덱스 하나가 곧
채우는 속도**다. 2026-09-20 에 `prices` 에서 하나, 다른 표에서 아홉을 더 찾아 지웠다.

**무엇을 겹친다고 보나.** 어떤 인덱스의 열 목록이 같은 표의 다른 인덱스의 **접두사**면
겹친다. `(a, b)` 로 답할 수 있는 질의는 `(a, b, c)` 로도 똑같이 답한다 — 앞에서부터 같은
제약을 쓰기 때문이다. 덮개(covering) 여부도 달라지지 않는다. 대가는 인덱스가 조금 더 큰
것뿐이다. 대부분 `UNIQUE (…)` 를 걸어 두고 그 앞부분에 인덱스를 또 만들어 생긴다.

**이 테스트는 값을 박아 두지 않는다.** 표를 더하든 인덱스를 더하든 규칙만 지키면 통과한다.
새로 겹치는 것을 만들면 그 자리에서 이름을 대며 실패한다.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

#: 겹치는데도 일부러 남겨 둔 것이 생기면 여기에 사유와 함께 적는다. 지금은 없다.
허용 = {}  # type: dict[str, str]


def _스키마() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):  # D1 전용/이미 있는 것은 넘어간다
            con.executescript(path.read_text(encoding="utf-8"))
    return con


def _표별_인덱스(con: sqlite3.Connection) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    결과: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    표들 = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for 표 in 표들:
        목록 = []
        for row in con.execute(f"PRAGMA index_list('{표}')"):
            이름 = row[1]
            열 = tuple(c[2] for c in con.execute(f"PRAGMA index_info('{이름}')"))
            목록.append((이름, 열))
        결과[표] = 목록
    return 결과


def 겹치는_인덱스() -> list[tuple[str, str, tuple, str, tuple]]:
    """(표, 겹치는 인덱스, 그 열, 대신 쓸 인덱스, 그 열) 목록."""
    con = _스키마()
    try:
        찾은것: list[tuple[str, str, tuple, str, tuple]] = []
        for 표, 목록 in _표별_인덱스(con).items():
            for 이름1, 열1 in 목록:
                for 이름2, 열2 in 목록:
                    if 이름1 == 이름2 or 이름1 in 허용:
                        continue
                    같은길이_자동 = len(열1) == len(열2) and 이름1.startswith("sqlite_autoindex_")
                    if len(열1) <= len(열2) and 열2[: len(열1)] == 열1 and not 같은길이_자동:
                        찾은것.append((표, 이름1, 열1, 이름2, 열2))
                        break
        return 찾은것
    finally:
        con.close()


def test_겹치는_인덱스가_없다() -> None:
    겹침 = 겹치는_인덱스()
    메시지 = "\n".join(
        f"  {표}: {n1}{list(c1)} 는 {n2}{list(c2)} 의 접두사다 — 지우면 쓰기가 한 번 준다"
        for 표, n1, c1, n2, c2 in 겹침
    )
    assert not 겹침, (
        "겹치는 인덱스가 있다. D1 은 쓰기를 셀 때 인덱스까지 세므로 이것이 곧 채우는 속도다"
        f" (docs/infra.md 25.25)\n{메시지}"
    )


def test_검사가_헛돌지_않는다() -> None:
    """표와 인덱스를 실제로 읽고 있는지. 스키마를 못 세우면 '겹침 0' 으로 조용히 통과한다."""
    con = _스키마()
    표별 = _표별_인덱스(con)
    con.close()
    assert len(표별) > 40, f"표를 {len(표별)}개밖에 못 읽었다 — 마이그레이션 적용이 깨졌다"
    assert sum(len(v) for v in 표별.values()) > 50, "인덱스를 거의 못 읽었다"


def test_접두사_규칙이_실제로_잡는다() -> None:
    """일부러 겹치는 인덱스를 만들면 잡히는지. 규칙이 헛돌면 이 테스트가 먼저 깨진다."""
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE t (a, b, c, UNIQUE (a, b, c));"
        "CREATE INDEX idx_t_ab ON t (a, b);"
    )
    목록 = _표별_인덱스(con)["t"]
    con.close()
    이름들 = {n for n, _ in 목록}
    assert "idx_t_ab" in 이름들
    짧은 = next(c for n, c in 목록 if n == "idx_t_ab")
    긴 = next(c for n, c in 목록 if n.startswith("sqlite_autoindex_"))
    assert 긴[: len(짧은)] == 짧은, "접두사 판정이 잘못됐다"
