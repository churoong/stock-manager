"""비슷한 국면 — 그 종목 자신의 과거에서 지금과 같은 상태였던 날들의 그 뒤 수익
(docs/analysis.md 13장, docs/infra.md 25.1039).

CAPM(10장)은 시장과 베타만 본다. 이것은 **그 종목이 겪은 일**에서 나온 숫자다 — 두꺼운 꼬리·되돌림·추세가 그대로 담긴다.

상태는 둘이다(둘 다 모멘텀 팩터 원값과 같은 정의 — 일일 의견이 오늘 상태를 거기서 고른다):
  r3    3개월 수익률 = 종가 ÷ 63거래일 전 종가 − 1   (`scoring.momentum_from_points` 의 `momentum_3m`)
  prox  52주 고점 근접 = 종가 ÷ 최근 252거래일 최고 종가   (`scoring.high_52w_proximity`)
그 종목 자신의 이력에서 각각 삼분위로 나눠 3 × 3 = 9 칸.
칸마다 1·3·6·12개월(21·63·126·252거래일) 뒤 수익률의 분포를 낸다.
계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

import statistics
from collections import deque
from typing import Any

#: 기간(개월) → 거래일
HORIZON_DAYS = {1: 21, 3: 63, 6: 126, 12: 252}
#: 상태를 재는 거래일 — 모멘텀 팩터와 같다
R3_DAYS = 63
HIGH_DAYS = 252
#: 한 칸의 분포를 말할 최소 일수. 앞으로 수익의 창이 겹쳐 날마다 독립이 아니므로 넉넉히 잡는다 —
#: 한 달(21거래일) 창이면 30일은 서로 다른 한 달이 겨우 두 번쯤이다. 그보다 적으면 칸을 비운다
MIN_DAYS = 30
R3_LABEL = ("3개월 수익률 하위 1/3", "3개월 수익률 중간", "3개월 수익률 상위 1/3")
PROX_LABEL = ("52주 고점에서 먼 쪽", "52주 고점에서 중간", "52주 고점 가까이")


def _q(sorted_values: list[float], p: float) -> float:
    """분위(선형 보간). 값은 정렬돼 있어야 한다."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def dist(values: list[float]) -> dict[str, float] | None:
    """수익률 목록의 분포 요약. 비면 None."""
    if not values:
        return None
    v = sorted(values)
    return {
        "n": len(v),
        "median": round(_q(v, 0.5), 4),
        "p05": round(_q(v, 0.05), 4),
        "p16": round(_q(v, 0.16), 4),
        "p84": round(_q(v, 0.84), 4),
        "p95": round(_q(v, 0.95), 4),
        "up": round(sum(1 for x in v if x > 0) / len(v), 4),
    }


def bucket(r3: float, prox: float, edges: dict[str, list[float]]) -> str:
    """상태 → 칸 이름 "i-j" (i: 3개월 수익률 삼분위, j: 52주 고점 근접 삼분위, 0 이 낮은 쪽)."""
    a, b = edges["r3"]
    c, d = edges["prox"]
    return f"{0 if r3 <= a else 1 if r3 <= b else 2}-{0 if prox <= c else 1 if prox <= d else 2}"


def label(key: str) -> str:
    i, j = (int(x) for x in key.split("-"))
    return f"{R3_LABEL[i]} · {PROX_LABEL[j]}"


def table(dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """그 종목의 국면표. 날짜 오름차순 종가(수정주가). 상태를 낼 날이 모자라면 None."""
    n = len(closes)
    상태: list[tuple[int, float, float]] = []
    # 252일 최고값을 미는 창(단조 덱)으로 — 종목마다 1,250일 × 252 를 다시 훑지 않는다
    덱: deque[int] = deque()
    for i in range(n):
        while 덱 and closes[덱[-1]] <= closes[i]:
            덱.pop()
        덱.append(i)
        if 덱[0] <= i - HIGH_DAYS:
            덱.popleft()
        if i < max(R3_DAYS, HIGH_DAYS - 1) or closes[i - R3_DAYS] <= 0 or closes[덱[0]] <= 0:
            continue
        상태.append((i, closes[i] / closes[i - R3_DAYS] - 1, closes[i] / closes[덱[0]]))
    if len(상태) < MIN_DAYS * 3:
        return None
    r3s = statistics.quantiles([s[1] for s in 상태], n=3)
    proxs = statistics.quantiles([s[2] for s in 상태], n=3)
    edges = {"r3": [round(x, 6) for x in r3s], "prox": [round(x, 6) for x in proxs]}
    칸들: dict[str, dict[str, list[float]]] = {}
    날수: dict[str, int] = {}
    국면: dict[str, int] = {}
    전체: dict[str, list[float]] = {str(m): [] for m in HORIZON_DAYS}
    앞칸 = None
    for i, r3, prox in 상태:
        k = bucket(r3, prox, edges)
        날수[k] = 날수.get(k, 0) + 1
        if k != 앞칸:
            국면[k] = 국면.get(k, 0) + 1
        앞칸 = k
        for m, d in HORIZON_DAYS.items():
            if i + d < n and closes[i] > 0:
                r = closes[i + d] / closes[i] - 1
                칸들.setdefault(k, {}).setdefault(str(m), []).append(r)
                전체[str(m)].append(r)
    buckets: dict[str, Any] = {}
    for k, 기간 in 칸들.items():
        h = {m: dist(v) for m, v in 기간.items() if len(v) >= MIN_DAYS}
        if h:
            buckets[k] = {"days": 날수.get(k, 0), "episodes": 국면.get(k, 0), "h": h}
    return {
        "since": dates[상태[0][0]], "until": dates[-1], "edges": edges, "buckets": buckets,
        "all": {m: dist(v) for m, v in 전체.items() if len(v) >= MIN_DAYS},
        "current": bucket(상태[-1][1], 상태[-1][2], edges),
        # 마지막 날의 상태 값 — 모멘텀 원값과 같은 정의인지 테스트가 이 값으로 견준다
        "state": {"r3": round(상태[-1][1], 6), "prox": round(상태[-1][2], 6)},
    }


def pick(t: dict | None, r3: float | None, prox: float | None) -> dict | None:
    """오늘 상태의 칸 — 일일 의견이 모멘텀 원값으로 고른다. 칸이 없거나(표본 모자람) 상태를 모르면 None."""
    if not t or not isinstance(r3, (int, float)) or not isinstance(prox, (int, float)):
        return None
    k = bucket(r3, prox, t["edges"])
    칸 = (t.get("buckets") or {}).get(k)
    if not 칸:
        return {"key": k, "label": label(k), "empty": True, "since": t.get("since"), "until": t.get("until")}
    horizons = []
    for m in sorted(HORIZON_DAYS):
        d = (칸.get("h") or {}).get(str(m))
        if d:
            horizons.append({"months": m, **d, "base": (t.get("all") or {}).get(str(m))})
    return {"key": k, "label": label(k), "days": 칸.get("days"), "episodes": 칸.get("episodes"),
            "since": t.get("since"), "until": t.get("until"), "horizons": horizons}  # fmt: skip
