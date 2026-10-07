"""docs/intraday.md 의 호출 횟수가 같은 문서 4장 표와 코드에서 셈한 값과 같은지 (docs/infra.md 25.346).

전에는 "하루 약 84 + 100 + 1 회" 로 장중만 셌고(뉴스·감시 빠짐, 미국 창은 102번), DART 는 "종목당 78번" 이라
폐장 뒤 유예(`CLOSE_GRACE_MINUTES`)를 빼고 셌다. 숫자만 적어 두면 표나 코드가 바뀔 때 아무도 모른다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "intraday.md").read_text(encoding="utf-8")


def _분(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _횟수(시작: str, 끝: str, 주기: int) -> int:
    a, b = _분(시작), _분(끝)
    if b < a:
        b += 24 * 60
    return (b - a) // 주기 + 1


def _표_줄(이름: str) -> str:
    return next(line for line in DOC.splitlines() if line.startswith(f"| {이름} |"))


def test_장중_횟수가_4장_표와_같다() -> None:
    국내 = re.search(r"매 (\d+)분, (\d\d:\d\d)~(\d\d:\d\d)", _표_줄("국내 장중"))
    미국 = re.search(r"매 (\d+)분, (\d\d:\d\d)~(\d\d:\d\d)", _표_줄("미국 장중"))
    assert 국내 and 미국
    kr = _횟수(국내[2], 국내[3], int(국내[1]))
    us = _횟수(미국[2], 미국[3], int(미국[1]))
    assert (kr, us) == (84, 102)
    assert f"국내 장중 {kr}" in DOC and f"미국 장중 {us}" in DOC
    total = kr + us + 1 + 24 + 24 + 24  # 미국 뉴스 매시 (25.880, 전에는 1분이라 1440)
    assert f"**{total:,}회**" in DOC


def test_DART_종목당_횟수는_폐장_유예를_포함한다() -> None:
    src = (ROOT / "web" / "lib" / "intraday.ts").read_text(encoding="utf-8")
    grace = int(re.search(r"CLOSE_GRACE_MINUTES = (\d+)", src)[1])  # type: ignore[index]
    # 09:00 개장 ~ 15:30 폐장 + 유예. 크론이 정각보다 조금 늦게 닿으므로 끝 시각 그 자체는 빼고 센다
    끝 = _분("15:30") + grace
    횟수 = len([m for m in range(_분("09:00"), _분("15:55") + 1, 5) if m < 끝])
    assert f"종목당 하루 최대 {횟수}번" in DOC
