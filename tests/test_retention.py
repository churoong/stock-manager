"""**보존 정책이 없다** — 그 사실을 붙들어 둔다 (docs/infra.md 25.131).

`DELETE FROM` 이 스물세 표에 있어서 "지운다" 로 보인다. 그런데 전부 **다시 넣기 전에
같은 것을 지우는** 문장이다 — `WHERE as_of_date = ?`, `WHERE market = ?` 꼴이다.
멱등성을 위한 것이지 **지난 것을 잘라 내는 것이 아니다.**

시간으로 자르는 곳은 **둘뿐**이다(아래 `시간으로_자르는_곳`). 나머지는 전부 쌓인다.

왜 이것이 문제인가: D1 무료 플랜의 **500MB 는 리셋이 없는 한도**다(25.123). 하루 쓰기·
읽기는 자정에 풀리지만 용량은 지우기 전까지 안 풀린다. 차면 쓰기가 전부 거부되고,
**그때 "무엇을 지울까" 를 정해야 한다.**

**여기서 보존 기간을 정하지 않는다.** 얼마를 지워야 하는지는 무엇이 얼마나 찼는지를 보고
정할 일이고, 그 수를 아직 아무도 잰 적이 없다(`scripts/db_status.py --tables`). 어림으로
정한 보존 기간은 지워서는 안 될 것을 지운다. 여기서 하는 일은 **사실을 고정해 두는 것**이다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: **시간으로 오래된 행을 잘라 내는 유일한 자리들.** 새로 생기면 여기 적고 25.131 을 고친다.
#: 값은 "무엇을 얼마나 남기나".
시간으로_자르는_곳 = {
    "signal_checks": "그날 근거표만 남긴다. 지난 날짜는 신호 화면이 보지 않는다"
    " (`jobs/signals`, `as_of_date < ?`)",
    "login_attempts": "로그인 시도 제한 창만큼만 남긴다"
    " (`web/lib/loginGuard.ts`, `attempted_at < ?`)",
}

_훑을곳 = ("batch", "web/lib", "web/app", "scripts")
_지우기 = re.compile(r"DELETE\s+FROM\s+(\w+)\s+WHERE\s+([^\"']{0,200})", re.I)
#: 날짜·시각 열과 **부등호** 비교. `= ?` 는 같은 키를 다시 넣으려는 것이라 자르기가 아니다
_시간비교 = re.compile(r"(date|_at|year)\w*\s*<", re.I)


def _소스들():
    for 자리 in _훑을곳:
        for 길 in sorted((뿌리 / 자리).rglob("*")):
            if 길.suffix in (".py", ".ts") and "node_modules" not in str(길):
                yield 길


def 시간으로_자르는_표() -> dict[str, set[str]]:
    나온것: dict[str, set[str]] = {}
    for 길 in _소스들():
        본문 = 길.read_text(encoding="utf-8", errors="ignore")
        for m in _지우기.finditer(본문):
            if _시간비교.search(m.group(2)):
                나온것.setdefault(m.group(1).lower(), set()).add(str(길.relative_to(뿌리)))
    return 나온것


def test_읽어_냈다() -> None:
    """**0개면 아래가 공짜로 통과한다.** 훑기가 조용히 비면 그물이 아니다."""
    전체 = sum(1 for _ in _소스들())
    assert 전체 > 100, f"소스를 {전체}개밖에 못 찾았다 — 훑을 곳이 바뀌었나"


def test_시간으로_자르는_곳은_적어_둔_둘뿐이다() -> None:
    """새 보존 정책이 생기면 **여기와 25.131 을 함께** 고치게 한다.

    이 검사가 깨지는 날은 둘 중 하나다 — 보존 정책을 하나 만들었거나(좋은 일, 적어라),
    아니면 같은 키를 지우는 문장을 시간 비교로 잘못 적었거나.
    """
    찾음 = 시간으로_자르는_표()
    새것 = sorted(set(찾음) - set(시간으로_자르는_곳))
    사라짐 = sorted(set(시간으로_자르는_곳) - set(찾음))

    assert not 새것, (
        "오래된 행을 자르는 자리가 새로 생겼다. `시간으로_자르는_곳` 에 **무엇을 얼마나 남기는지**"
        f" 적고 docs/infra.md 25.131 을 고쳐라: {[(t, sorted(찾음[t])) for t in 새것]}"
    )
    assert not 사라짐, f"적어 둔 보존 정책이 코드에서 사라졌다: {사라짐}"


def test_사유가_비어_있지_않다() -> None:
    for 표, 사유 in 시간으로_자르는_곳.items():
        assert len(사유) > 20, f"{표} 의 보존 사유가 너무 짧다"


def test_무엇이_찼는지_볼_수_있다() -> None:
    """**재는 장치가 있어야 정할 수 있다** (25.123 의 게이지와 짝이다).

    용량 게이지는 "얼마나 찼나" 만 말한다. `--tables` 가 "무엇이 채웠나" 를 말한다.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("db_status", 뿌리 / "scripts" / "db_status.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    점검 = m.행수_점검(m.자라는표)
    assert 점검, "표별 행 수 점검이 비었다"
    assert all(m.is_read_only(sql) for _, sql in 점검), "행 수 점검이 읽기 전용이 아니다"
    # `prices` 가 가장 큰 표다. 빠지면 재는 뜻이 없다
    assert "prices" in m.자라는표


def test_자라는표_목록이_실제_표다() -> None:
    """오타로 적어 두면 그 항목만 조용히 오류로 찍히고, 사람은 0 으로 읽는다."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("db_status", 뿌리 / "scripts" / "db_status.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    본문 = "\n".join(
        길.read_text(encoding="utf-8") for 길 in sorted((뿌리 / "migrations").glob("*.sql"))
    )
    있는표 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", 본문))
    assert 있는표, "마이그레이션에서 표를 못 읽었다"

    유령 = sorted(set(m.자라는표) - 있는표)
    assert not 유령, f"없는 표를 세려 한다: {유령}"
    겹침 = len(m.자라는표) - len(set(m.자라는표))
    assert 겹침 == 0, "같은 표를 두 번 센다"
