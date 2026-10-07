"""거래대금 급증 — 종목선정 기법 발굴 루프 3회차 B (docs/factors.md 12.2, docs/infra.md 25.738).

`ln(최근 20일 평균 거래대금 / 최근 60일 평균 거래대금)`. Gervais, Kaniel & Mingelgrin (2001) 의 거래량 충격 프리미엄.

**새 창을 만들지 않는다** (3회차 검증 2). 제안의 5일/50일은 이 저장소에 없던 숫자라 문턱 맞추기가 된다.
단기 신호의 "수급 유입" 조건(`signals.MA_SHORT_DAYS`·`MA_LONG_DAYS`, 20일 > 60일 × 1.2)이 이미 쓰면서
한 번도 검증하지 않은 비를 IC 로 잰다.
**거래량이 아니라 거래대금이다** (3회차 검증 1) — 국내 수정은 종가만 고치고 거래량은 원값이라
분할 직후 가짜 급증이 잡힌다.

**진단용이다.** IC 가 통과해도 팩터에 넣지 않는다. 쓰임은 수급 유입 조건의 근거를 확인하는 것이다.
계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations

import math

from batch.services.signals import MA_LONG_DAYS, MA_SHORT_DAYS, moving_average


def flow_surge(values: list[float | None]) -> float | None:
    """`values` 는 날짜 오름차순 거래대금(마지막이 기준일). 60일치가 모자라거나 평균이 0 이하면 None.

    한 날이라도 비면 `moving_average` 가 None 을 준다 — 거래정지일(0)은 값이 있어 평균을 낮출 뿐 ln(0) 이 되지 않는다.
    """
    짧은 = moving_average(values, MA_SHORT_DAYS)  # type: ignore[arg-type]
    긴 = moving_average(values, MA_LONG_DAYS)  # type: ignore[arg-type]
    if 짧은 is None or 긴 is None or 짧은 <= 0 or 긴 <= 0:
        return None
    return math.log(짧은 / 긴)
