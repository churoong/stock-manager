"""신선도 판정의 **잣대가 어디서 왔나** (docs/infra.md 25.140).

`/status` 의 신선도 칸마다 "며칠이면 늦은 것인가" 가 다르다. 그 잣대의 근거는 둘이다.

1. **배치의 실측 상수** — 거래일 간격(`metrics.MAX_SESSION_GAP_DAYS`)·환율 묵음
   (`fx.MAX_STALE_DAYS`). 웹에 사본이 있으므로 갈라지면 화면이 거짓말을 한다
   (25.0 「한 규칙이 두 곳에 있다」)
2. **그 칸을 채우는 예약의 주기** — 주 1회짜리를 거래일 잣대로 재면 매주 빨개지고,
   월 1회짜리를 주간 잣대로 재도 마찬가지다. 거짓 경보가 쌓이면 사람은 화면을 안 읽는다

여기서는 둘 다 대조한다. **워크플로의 cron 이 정의처**이고 `every` 는 그 사본이다.
어느 칸을 어느 워크플로가 채우는지는 `tests/test_schedule_visible.py` 의 `보는곳` 이
이미 적고 있다 — 같은 표를 두 번 적지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_schedule_visible import 보는곳

뿌리 = Path(__file__).resolve().parent.parent
HEALTH = (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
워크플로 = 뿌리 / ".github" / "workflows"


def _상수(이름: str) -> int:
    m = re.search(rf"export const {이름} = (\d+);", HEALTH)
    assert m, f"health.ts 에서 {이름} 을(를) 못 찾았다"
    return int(m.group(1))


def 신선도_주기() -> dict[str, str]:
    """`FRESHNESS` 의 키 → `every`."""
    몸통 = HEALTH.split("export const FRESHNESS", 1)[1].split(" = [", 1)[1].split("\n];", 1)[0]
    깨끗 = "\n".join(줄 for 줄 in 몸통.splitlines() if not 줄.strip().startswith("//"))
    return dict(re.findall(r'key: "([a-z_]+)".*?every: "([a-z]+)"', 깨끗))


def 첫_크론(파일: str) -> str | None:
    for 줄 in (워크플로 / 파일).read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*-\s*cron:\s*"([^"]+)"', 줄)
        if m:
            return m.group(1)
    return None


def 크론의_주기(식: str) -> str:
    """cron 다섯 칸에서 주기를 읽는다. **여기가 판정의 근거다.**

    월이 지정돼 있으면 한 해에 몇 번(`year`), 날짜가 지정돼 있으면 달마다(`month`),
    요일이 닷새 이상이면 사실상 매일(`session`), 요일 한둘이면 주마다(`week`).
    """
    _, _, dom, mon, dow = 식.split()
    if mon != "*":
        return "year"
    if dom != "*":
        return "month"
    if dow == "*":
        return "session"
    날 = set()
    for 조각 in dow.split(","):
        if "-" in 조각:
            처음, 끝 = (int(x) for x in 조각.split("-"))
            날 |= set(range(처음, 끝 + 1))
        else:
            날.add(int(조각))
    return "session" if len(날) >= 5 else "week"


주기 = 신선도_주기()


def test_읽어_냈다() -> None:
    """정규식이 빗나가면 아래가 전부 공짜로 통과한다."""
    assert len(주기) >= 20, f"FRESHNESS 에서 {len(주기)}칸밖에 못 읽었다"
    assert len(보는곳) >= 10, "워크플로↔칸 표가 비었다"


def test_거래일_간격은_배치의_실측값과_같다() -> None:
    """XKRX 최장 휴장 간격. 웹이 더 짧게 잡으면 **연휴마다 멀쩡한 칸이 빨개진다**."""
    from batch.services import metrics

    assert _상수("MAX_SESSION_GAP_DAYS") == metrics.MAX_SESSION_GAP_DAYS


def test_환율_묵음_일수가_배치와_같다() -> None:
    """배치는 이 값으로 "환율이 묵었습니다" 를 적는다(25.136). 화면이 다른 수를 쓰면 안 된다."""
    from batch.services import fx

    assert _상수("FX_STALE_DAYS") == fx.MAX_STALE_DAYS


@pytest.mark.parametrize("파일", sorted(파일 for 파일, (갈래, _) in 보는곳.items() if 갈래 == "freshness"))
def test_잣대가_그_예약의_주기와_맞는다(파일: str) -> None:
    """**정의처는 워크플로의 cron 이다.** 예약을 주 1회에서 월 1회로 바꾸면 여기가 깨진다.

    안 깨지면 어떻게 되나: 월 1회로 바꾼 칸을 주간 잣대로 재서 **매달 셋째 주마다
    노랗다가 빨개진다.** 고칠 것이 없는 경보가 매달 뜨면 사람은 이 화면을 닫는다.
    """
    _, 키 = 보는곳[파일]
    식 = 첫_크론(파일)
    assert 식, f"{파일} 에 cron 이 없다 — `보는곳` 이 낡았나"

    assert 주기[키] == 크론의_주기(식), (
        f"`{키}` 의 잣대는 `{주기[키]}` 인데 `{파일}` 의 예약은 `{식}`"
        f"({크론의_주기(식)}) 이다.\n"
        "예약을 바꿨으면 health.ts 의 `every` 도 함께 고쳐라 (docs/infra.md 25.140)"
    )


def test_미국_칸은_D1_에서_쉬는_것으로_표시돼_있다() -> None:
    """**쉬기로 한 것을 고장이라고 부르지 않는다** (25.14).

    D1 에는 미국 데이터가 없다(25.11). 무응답 감시는 이것을 이미 알지만(`dueAlerts` 의
    `markets`) 화면은 몰랐다 — 그래서 25.140 에서 칸에 표시를 달았다.
    """
    몸통 = HEALTH.split("export const FRESHNESS", 1)[1].split(" = [", 1)[1].split("\n];", 1)[0]
    미국칸 = re.findall(r'key: "([a-z_]*_us)"[^\n]*', 몸통)
    assert len(미국칸) >= 6, f"미국 칸을 {len(미국칸)}개밖에 못 찾았다"

    안달린것 = [
        키
        for 키 in 미국칸
        if "restsOnD1: true" not in next(줄 for 줄 in 몸통.splitlines() if f'key: "{키}"' in 줄)
    ]
    assert not 안달린것, (
        f"미국 칸인데 `restsOnD1` 이 없다: {안달린것}.\n"
        "D1 운영 중에 빨갛게 칠하면 거짓 경보다 (docs/infra.md 25.14)"
    )
