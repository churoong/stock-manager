"""순주식발행 — 종목선정 기법 발굴 루프 1회차 B (docs/factors.md 12.2, docs/infra.md 25.446).

순주식발행 = ln(당해 발행주식수 / 전년 발행주식수). 유상증자·전환·주식보상으로 늘면 +, 자사주 소각으로 줄면 −.
방향은 −(적게 찍을수록 좋다). Pontiff & Woodgate (2008), *Journal of Finance*; Daniel & Titman (2006).
한국 실증(JRFM 2025)은 효과가 발행 쪽에서 나온다고 보고했다 — 국내 자사주는 소각하지 않으면 주식수가 안 준다.

**분할·병합은 발행이 아니다.** 주식수 이력이 분할 조정되어 있지 않으므로(SEC dei 는 그날의 실제 수)
비율이 `SPLIT_RATIO` 배 이상 뛰거나 그 역수 이하로 줄면 분할·병합으로 보고 NULL 로 둔다(1회차 검증 1 제안).
진짜 두 배 증자도 이 값으로 잘린다 — 드물고, 잘못 넣는 쪽(분할을 대량 발행으로)이 더 나쁘다.

**지금은 미국만** — 국내 주식수 이력을 담을 곳이 없다(docs/factors.md 12.1). 계산만 한다.
"""

from __future__ import annotations

import math

#: 이 배수 이상 뛰면 분할·병합으로 본다 (2:1 분할이 가장 흔한 작은 분할이다)
SPLIT_RATIO = 1.9


def net_issuance(shares: float | None, shares_prev: float | None, *, split_adjusted: bool = False) -> float | None:
    """ln(당해 / 전년). 하나라도 없거나 0 이하이면 None.

    `split_adjusted=True` 는 **같은 공시의 당기·전기 값**처럼 분할이 소급 조정된 쌍이다 — 문턱으로 자르지 않는다
    (진짜 두 배 증자도 산다). 조정되지 않은 쌍이면 `SPLIT_RATIO` 로 분할·병합을 거른다 — 다만 3:2 분할(1.5배)은
    이 문턱을 빠져나간다(25.450, 교차검증). 그래서 백테스트는 조정된 쌍만 쓴다.
    """
    if shares is None or shares_prev is None or shares <= 0 or shares_prev <= 0:
        return None
    ratio = float(shares) / float(shares_prev)
    if not split_adjusted and (ratio >= SPLIT_RATIO or ratio <= 1 / SPLIT_RATIO):
        return None
    return math.log(ratio)
