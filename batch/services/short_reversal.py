"""단기 반전 — 종목선정 기법 발굴 루프 2회차 E (docs/factors.md 12.2, docs/infra.md 25.475).

최근 21거래일 수익률의 반대 부호. Jegadeesh (1990), Lehmann (1990).

**진단용이다.** IC 가 통과해도 팩터·신호에 넣지 않는다 —
모멘텀 팩터의 3·6개월 수익률이 최근 한 달과 부딪히는지 보는 근거로만 쓴다.
창은 `scoring.MOMENTUM_SKIP_DAYS` 를 그대로 쓴다(새 숫자를 만들지 않는다).

계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations

from batch.services.scoring import MOMENTUM_SKIP_DAYS


def reversal_1m(closes: list[float]) -> float | None:
    """`closes` 는 날짜 오름차순(마지막이 기준일 종가). 거래일이 모자라거나 기준점이 0 이하면 None."""
    if len(closes) <= MOMENTUM_SKIP_DAYS:
        return None
    base = closes[-1 - MOMENTUM_SKIP_DAYS]
    if base is None or base <= 0 or closes[-1] is None:
        return None
    return -(closes[-1] / base - 1)
