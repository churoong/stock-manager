"""배당 중단 — 종목선정 기법 발굴 루프 2회차 A (docs/factors.md 12.2, docs/infra.md 25.479).

가장 최근 사업연도의 현금배당총액이 0 인데 그 전 해는 0 보다 컸으면 "중단". Michaely, Thaler & Womack (1995).
**이진값만** 쓴다 — 연속 변화량·총액 삭감은 자사주·특별배당으로 거짓이 난다(2회차 검증 2).

계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations


def omitted(history: list[tuple[str, int, float]], cutoff: str) -> bool | None:
    """`history` 는 (접수일, 사업연도, 현금배당총액) 접수일 순. 기준일까지 접수된 것만 본다.

    같은 사업연도는 뒤에 접수된 것이 이긴다. 최근 해 Y 나 Y−1 을 모르면 None."""
    알려진: dict[int, float] = {}
    for as_of, fiscal_year, total in history:
        if as_of > cutoff:
            continue
        알려진[int(fiscal_year)] = float(total or 0)
    if not 알려진:
        return None
    y = max(알려진)
    if y - 1 not in 알려진:
        return None
    return 알려진[y - 1] > 0 and 알려진[y] == 0
