"""화면의 "오래됨" 문턱을 **달력에서 다시 재어** 대조한다 (docs/infra.md 25.94).

왜 있나: 그 값의 근거가 두 번 낡았다.

1. 처음 주석은 "스코어·신호 배치는 지금 주 1회다" 였다. **Step 9 에서 일일 배치로
   들어오면서 그 전제가 없어졌는데** 주석도 값도 그대로였다
2. 그 사이 종목 상세 화면은 **4 를 손으로 박아** 썼다. 한 규칙이 두 곳에 생겼고
   두 화면이 같은 값을 다르게 판정했다 (docs/infra.md 25.0 첫 줄의 모양)

이제 이 값을 정하는 것은 배치 주기가 아니라 **휴장이 얼마나 길어질 수 있는가** 다.
배치는 거래일마다 돈다 — 기준일이 오래된 것은 배치가 죽어서가 아니라 장이 안 열려서일
수 있다. 그래서 **어림이 아니라 실측**이어야 하고, 달력이 바뀌면 여기가 먼저 깨져야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent

#: 재는 구간. 10년이면 설·추석이 주말에 붙는 경우가 여러 번 들어온다
START, END = "2016-01-01", "2026-12-31"

#: 이 앱이 보는 두 시장 (`batch/core/calendar.py`)
거래소 = {"KR": "XKRX", "US": "XNYS"}


def 문턱() -> int:
    """`web/lib/recommend.ts` 의 `STALE_AFTER_DAYS`. **웹이 단일 정의처다.**"""
    본문 = (뿌리 / "web" / "lib" / "recommend.ts").read_text(encoding="utf-8")
    찾음 = re.search(r"export const STALE_AFTER_DAYS\s*=\s*(\d+)", 본문)
    assert 찾음, "STALE_AFTER_DAYS 를 못 읽었다 — 이름이 바뀌었으면 이 테스트도 고쳐라"
    return int(찾음.group(1))


def 상태_문턱() -> int:
    """`web/lib/health.ts` 의 `MAX_AS_OF_AGE_DAYS` — 상태 화면의 거래일 칸 문턱 (docs/infra.md 25.242)."""
    본문 = (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
    찾음 = re.search(r"export const MAX_AS_OF_AGE_DAYS\s*=\s*(\d+)", 본문)
    assert 찾음, "MAX_AS_OF_AGE_DAYS 를 못 읽었다"
    return int(찾음.group(1))


def 최장_나이(code: str) -> tuple[int, str, str]:
    """**기준일 값의 최장 나이**와 그 두 날 (docs/infra.md 25.242).

    기준일은 아침 배치가 넘기는 **직전 거래일**이라 한 세션 늦다. 연속한 세 거래일 Z·A·B 에서 B 아침 배치 직전의
    값은 Z 이므로 나이는 `B − Z` — 연속 세 거래일의 폭이다. 25.242 전까지 이 파일은 `간격 − 1` 로 쟀다(틀렸다).
    """
    xcals = pytest.importorskip("exchange_calendars")
    cal = xcals.get_calendar(code, start=START, end=END)
    날 = [d.date() for d in cal.sessions]
    최악, 언제 = 0, ("", "")
    for z, b in zip(날, 날[2:], strict=False):
        if (b - z).days > 최악:
            최악, 언제 = (b - z).days, (z.isoformat(), b.isoformat())
    return 최악, *언제


def 최장_간격(code: str) -> tuple[int, str, str]:
    """거래일 **사이**의 최장 일수와 그 두 날. 휴장이 이어진 길이다."""
    xcals = pytest.importorskip("exchange_calendars")
    cal = xcals.get_calendar(code, start=START, end=END)
    날 = [d.date() for d in cal.sessions]
    assert len(날) > 2000, f"{code} 거래일을 {len(날)}개밖에 못 읽었다 — 아래가 공짜로 통과한다"
    최악, 언제 = 0, ("", "")
    for 앞, 뒤 in zip(날, 날[1:], strict=False):
        간격 = (뒤 - 앞).days
        if 간격 > 최악:
            최악, 언제 = 간격, (앞.isoformat(), 뒤.isoformat())
    return 최악, *언제


def test_문턱이_최악의_휴장을_견딘다() -> None:
    """**늑대야 하고 외치지 않는다.**

    값의 최장 나이는 **연속 세 거래일의 폭**이다(`최장_나이`, 25.242 — 예전 전제 `간격 − 1` 은 틀렸다). 문턱이 그보다 작으면 **정상 휴장에도
    노란 줄**이 뜨고, 노란 줄이 예사가 되면 진짜로 멈춘 날에도 아무도 안 본다.
    """
    for 이름, 한도 in (("recommend.STALE_AFTER_DAYS", 문턱()), ("health.MAX_AS_OF_AGE_DAYS", 상태_문턱())):
        for 나라, 코드 in 거래소.items():
            나이, 앞, 뒤 = 최장_나이(코드)
            assert 나이 <= 한도, (
                f"{나라}({코드})는 {앞} 기준 값이 {뒤} 아침 배치 전까지 남아 {나이}일이 된 적이 있다."
                f" {이름} 은 {한도}일이다 — 정상 휴장에 화면이 '오래됨' 을 띄운다"
            )


def test_문턱이_쓸데없이_넉넉하지는_않다() -> None:
    """반대쪽도 본다. 너무 크면 **정말 멈춘 날에도 아무 말을 안 한다.**"""
    한도 = max(문턱(), 상태_문턱())
    최악 = max(최장_나이(코드)[0] for 코드 in 거래소.values())

    assert 한도 <= 최악 + 3, (
        f"문턱 {한도}일은 최장 값 나이 {최악}일보다 너무 넉넉하다."
        " 배치가 며칠 멈춰도 화면이 멀쩡해 보인다"
    )


def test_실측값이_주석에_적혀_있다() -> None:
    """**상수에는 근거를 붙인다** (CLAUDE.md 기록 규칙). 숫자만 있으면 바꿔도 되는지 모른다."""
    본문 = (뿌리 / "web" / "lib" / "recommend.ts").read_text(encoding="utf-8")
    머리 = 본문[: 본문.index("export const STALE_AFTER_DAYS")]

    assert "exchange_calendars" in 머리, "무엇으로 쟀는지 적혀 있어야 한다"
    for 코드 in 거래소.values():
        assert 코드 in 머리, f"{코드} 의 실측이 안 적혀 있다"
    for 코드 in 거래소.values():
        나이 = 최장_나이(코드)[0]
        assert f"{나이}일" in 머리, f"{코드} 의 최장 값 나이 {나이}일이 주석에 없다 (docs/infra.md 25.242)"
    for 코드 in 거래소.values():
        간격 = 최장_간격(코드)[0]
        assert f"{간격}일" in 머리, (
            f"{코드} 의 최장 간격이 지금 {간격}일인데 주석에 그 숫자가 없다."
            " 달력이 바뀌었으면 주석과 값을 함께 고쳐라"
        )


def test_손으로_박은_값이_없다() -> None:
    """**한 규칙이 두 곳에 있으면 한 곳만 고쳐진다** (docs/infra.md 25.0)."""
    새는것: list[str] = []
    for path in sorted((뿌리 / "web" / "components").glob("*.tsx")):
        for 번호, 줄 in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            찾음 = re.search(r"staleAfterDays=\{(\d+)\}", 줄)
            if 찾음:
                새는것.append(f"{path.name}:{번호} staleAfterDays={{{찾음.group(1)}}}")

    assert not 새는것, (
        "숫자를 손으로 박은 자리가 있다. 이름 붙인 상수를 넘겨라 —\n  " + "\n  ".join(새는것)
    )


def test_이름_붙인_상수들을_실제로_넘기고_있다() -> None:
    """위 테스트가 **아무것도 못 찾아도** 통과하므로, 쓰이고는 있는지 함께 본다."""
    쓰임 = [
        줄
        for path in sorted((뿌리 / "web" / "components").glob("*.tsx"))
        for 줄 in path.read_text(encoding="utf-8").splitlines()
        if "staleAfterDays=" in 줄
    ]

    assert len(쓰임) >= 4, f"근거표를 그리는 자리를 {len(쓰임)}개밖에 못 찾았다"
