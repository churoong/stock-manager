"""업종 모멘텀 — 종목선정 기법 발굴 루프 1회차 D (docs/factors.md 12.2, docs/infra.md 25.439).

같은 시장·같은 업종 종목들의 12-1 수익률(`momentum_12_1`) **동일가중 평균**이다.
Moskowitz & Grinblatt (1999) "Do Industries Explain Momentum?".

**팩터가 아니다.** 점수는 시장·업종별 z-score 로 정규화하므로(docs/factors.md 3장) 업종 안에서 모두 같은 이
값은 z=0 이 된다 — 팩터에 넣으면 아무 일도 안 한다. 그래서 **신호 조건**("업종 순위 상위 절반")으로만 쓴다.
지금은 백테스트 전략(`momentum_sector`)과 IC 로만 잰다. 실제 신호에는 켜는 기준을 넘기 전까지 넣지 않는다.

계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol

from batch.services.scoring import MIN_PEER_SIZE

METRIC = "momentum_12_1"

#: 업종 평균이 잡힌 종목 비율이 판정 문턱(`factor_ic.MIN_COVERAGE`) 아래일 때 결과에 붙이는 경고 (25.443)
WARN_LOW_COVERAGE = (
    "업종 모멘텀: 업종 평균이 잡힌 종목이 {pct:.0f}%({t}) — 60% 미만이라 조건이 거의 전부 통과한다."
    " momentum 과의 비교는 **판정 불가**다(업종 채움이 오르면 다시 잰다)"
)


class _Input(Protocol):
    stock_id: int
    market: str
    sector: str | None
    metrics: dict[str, float | None]


def sector_means(inputs: Iterable[_Input]) -> dict[tuple[str, str], float]:
    """(시장, 업종) → 12-1 수익률 평균. 값이 있는 종목이 `MIN_PEER_SIZE`(30) 미만인 업종은 뺀다.

    30 은 업종 z-score 의 최소 집단과 같은 값이다 — 새 문턱을 만들지 않는다(1회차 검증 2).
    업종이 없는 종목은 어느 업종에도 들지 않는다.
    """
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for i in inputs:
        v = i.metrics.get(METRIC)
        if i.sector and v is not None:
            values[(i.market, i.sector)].append(float(v))
    return {k: sum(vs) / len(vs) for k, vs in values.items() if len(vs) >= MIN_PEER_SIZE}


def top_half_sectors(means: dict[tuple[str, str], float]) -> set[tuple[str, str]]:
    """시장마다 업종 평균 순위 **상위 절반**(올림). 업종이 하나뿐인 시장은 그 업종이 든다."""
    by_market: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (market, sector), mean in means.items():
        by_market[market].append((mean, sector))
    out: set[tuple[str, str]] = set()
    for market, rows in by_market.items():
        rows.sort(reverse=True)
        keep = (len(rows) + 1) // 2
        out.update((market, sector) for _, sector in rows[:keep])
    return out


def passes(i: _Input, means: dict[tuple[str, str], float], top: set[tuple[str, str]]) -> bool:
    """조건을 통과하는가. **업종 평균을 낼 수 없는 종목(업종 없음·30종목 미만 업종)은 조건을 적용하지 않는다** —
    모르는 것으로 자르지 않는다(1회차 검증 1)."""
    key = (i.market, i.sector or "")
    if key not in means:
        return True
    return key in top


def stock_values(inputs: Iterable[_Input]) -> dict[int, float | None]:
    """종목마다 그 업종의 평균(IC 를 잴 때 쓴다). 업종 평균을 낼 수 없으면 None."""
    items = list(inputs)
    means = sector_means(items)
    return {i.stock_id: means.get((i.market, i.sector or "")) for i in items}


def coverage(inputs: Iterable[_Input], means: dict[tuple[str, str], float]) -> float:
    """업종 평균이 잡힌 종목의 비율(0~1). 후보가 없으면 0 (25.443)."""
    items = list(inputs)
    if not items:
        return 0.0
    return sum(1 for i in items if (i.market, i.sector or "") in means) / len(items)
