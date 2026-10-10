"""표시용 반올림 — **웹 `toFixed` 와 같은 규칙** (docs/infra.md 25.1105).

파이썬 서식(`f"{x:.0f}"`)과 `round()` 는 .5 를 짝수 쪽으로 보낸다(72.5 → 72, 2.25 → 2.2). 웹은 `toFixed` 라 올린다
(73, 2.3). 점수는 소수 1자리로 저장돼 x.5 가 흔해, 같은 종목이 텔레그램 팩터 줄 72·근거 문장 73·웹 73 으로 갈렸다.
`toFixed` 는 이진수 값 그대로에서 가장 가까운 쪽, 같으면 절댓값이 큰 쪽이다 —
`Decimal(float)` + `ROUND_HALF_UP` 과 같다.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def half_up(value: float, digits: int = 0) -> float:
    """`toFixed(digits)` 와 같은 값. 결과의 −0 은 0 으로."""
    q = Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return float(q) + 0.0


def fixed(value: float, digits: int = 0, sign: bool = False) -> str:
    """`toFixed(digits)` 글자. `sign` 이면 + 를 붙인다("-0" 은 "0"/"+0")."""
    v = half_up(value, digits)
    return f"{v:+.{digits}f}" if sign else f"{v:.{digits}f}"
