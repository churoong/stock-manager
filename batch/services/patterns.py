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


#: 시장 국면 (docs/analysis.md 29장, 25.1054) — 기준 지수가 200거래일 단순이동평균 아래면 약세.
#: 정의는 추세 필터(`trend.regime_from_closes`, signals.md 3.5)와 같다 — 오늘 국면을 그 함수로 고르기 때문이다
REGIME_SMA_DAYS = 200
REGIME_LABEL = {"bull": "시장 강세(지수 200일선 위)", "bear": "시장 약세(지수 200일선 아래)"}


def index_regimes(index: dict[str, float]) -> dict[str, str]:
    """지수 {날짜: 종가} → {날짜: "bull"|"bear"}. 그날까지 최근 200개 종가의 평균과 견준다(200개가 모이기 전 날은 없음).

    나라마다 한 번만 부른다(주간 작업이 캐시) — 날마다 창을 새로 더해
    `trend.regime_from_closes` 와 소수 끝까지 같게 한다."""
    날 = sorted(d for d, c in index.items() if c and c > 0)
    값 = [index[d] for d in 날]
    out: dict[str, str] = {}
    for i in range(REGIME_SMA_DAYS - 1, len(날)):
        창 = 값[i - REGIME_SMA_DAYS + 1 : i + 1]
        out[날[i]] = "bear" if 값[i] < sum(창) / len(창) else "bull"
    return out


def table(dates: list[str], closes: list[float], regimes: dict[str, str] | None = None) -> dict[str, Any] | None:
    """그 종목의 국면표. 날짜 오름차순 종가(수정주가). 상태를 낼 날이 모자라면 None.

    regimes(`index_regimes`)를 주면 같은 칸을 시장 강세·약세로 한 번 더 나눈 `by_regime` 도 낸다(29장)."""
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
    시장칸: dict[str, dict[str, dict[str, list[float]]]] = {}
    시장날수: dict[str, int] = {}
    앞칸 = None
    for i, r3, prox in 상태:
        k = bucket(r3, prox, edges)
        g = (regimes or {}).get(str(dates[i]))
        if g:
            시장날수[f"{g}/{k}"] = 시장날수.get(f"{g}/{k}", 0) + 1
        날수[k] = 날수.get(k, 0) + 1
        if k != 앞칸:
            국면[k] = 국면.get(k, 0) + 1
        앞칸 = k
        for m, d in HORIZON_DAYS.items():
            if i + d < n and closes[i] > 0:
                r = closes[i + d] / closes[i] - 1
                칸들.setdefault(k, {}).setdefault(str(m), []).append(r)
                전체[str(m)].append(r)
                if g:
                    시장칸.setdefault(f"{g}/{k}", {}).setdefault(str(m), []).append(r)
    buckets: dict[str, Any] = {}
    for k, 기간 in 칸들.items():
        h = {m: dist(v) for m, v in 기간.items() if len(v) >= MIN_DAYS}
        if h:
            buckets[k] = {"days": 날수.get(k, 0), "episodes": 국면.get(k, 0), "h": h}
    by_regime: dict[str, dict[str, Any]] = {}
    for gk, 기간 in 시장칸.items():
        h = {m: dist(v) for m, v in 기간.items() if len(v) >= MIN_DAYS}
        if h:
            g, k = gk.split("/")
            by_regime.setdefault(g, {})[k] = {"days": 시장날수.get(gk, 0), "h": h}
    extra = {"by_regime": by_regime} if regimes is not None else {}
    return {
        **extra,
        "since": dates[상태[0][0]], "until": dates[-1], "edges": edges, "buckets": buckets,
        "all": {m: dist(v) for m, v in 전체.items() if len(v) >= MIN_DAYS},
        "current": bucket(상태[-1][1], 상태[-1][2], edges),
        # 마지막 날의 상태 값 — 모멘텀 원값과 같은 정의인지 테스트가 이 값으로 견준다
        "state": {"r3": round(상태[-1][1], 6), "prox": round(상태[-1][2], 6)},
    }


def pick(t: dict | None, r3: float | None, prox: float | None, regime: str | None = None) -> dict | None:
    """오늘 상태의 칸 — 일일 의견이 모멘텀 원값으로 고른다. 칸이 없거나(표본 모자람) 상태를 모르면 None.

    regime(오늘 시장 국면 "bull"|"bear")을 주고 표에 `by_regime` 이 있으면 같은 칸·같은 시장 국면의 분포를
    `regime` 으로 덧붙인다(29장). 표본이 모자라면 {"empty": True} — 칸 전체 분포는 그대로 둔다."""
    if not t or "edges" not in t or not isinstance(r3, (int, float)) or not isinstance(prox, (int, float)):
        return None  # 하락장 성적만 있는 행(국면표를 못 낸 짧은 이력)도 여기서 걸러진다
    k = bucket(r3, prox, t["edges"])
    칸 = (t.get("buckets") or {}).get(k)
    if not 칸:
        return {"key": k, "label": label(k), "empty": True, "since": t.get("since"), "until": t.get("until")}
    horizons = []
    for m in sorted(HORIZON_DAYS):
        d = (칸.get("h") or {}).get(str(m))
        if d:
            horizons.append({"months": m, **d, "base": (t.get("all") or {}).get(str(m))})
    out = {"key": k, "label": label(k), "days": 칸.get("days"), "episodes": 칸.get("episodes"),
           "since": t.get("since"), "until": t.get("until"), "horizons": horizons}  # fmt: skip
    if regime in REGIME_LABEL and isinstance(t.get("by_regime"), dict):
        시장 = (t["by_regime"].get(regime) or {}).get(k)
        if 시장:
            out["regime"] = {"state": regime, "label": REGIME_LABEL[regime], "days": 시장.get("days"),
                             "horizons": [{"months": m, **시장["h"][str(m)]} for m in sorted(HORIZON_DAYS)
                                          if (시장.get("h") or {}).get(str(m))]}  # fmt: skip
        else:
            out["regime"] = {"state": regime, "label": REGIME_LABEL[regime], "empty": True}
    return out


#: 하락장 성적 — 시장(기준 지수)이 가장 나빴던 달의 수 (docs/analysis.md 28장)
WORST_MONTHS = 5
#: 하락장 성적을 낼 최소 겹치는 달 수 — 1년이 안 되면 "가장 나빴던 달" 이 한 국면이다
MIN_MONTHS = 12


def _month_ends(dates: list[str], closes: list[float]) -> dict[str, float]:
    끝: dict[str, float] = {}
    for d, c in zip(dates, closes, strict=False):
        if c and c > 0:
            끝[str(d)[:7]] = c  # 날짜 오름차순이라 그달 마지막 종가가 남는다
    return 끝


def stress(dates: list[str], closes: list[float], index: dict[str, float]) -> dict | None:
    """하락장 성적 (docs/analysis.md 28장, 25.1053). index = 기준 지수 {날짜: 종가}.

    - 달마다(그달 마지막 종가) 수익률을 종목·지수 함께 내고, 지수가 가장 나빴던 `WORST_MONTHS` 달의 둘을 나란히
    - 하락일 베타·상승일 베타: 지수 일간 수익률이 음수인 날만·양수인 날만으로 잰 기울기(공분산 ÷ 분산)
    겹치는 달이 `MIN_MONTHS` 미만이면 None. 문턱 없음 — 사실만."""
    종목달 = _month_ends(dates, closes)
    지수달 = _month_ends(sorted(index), [index[d] for d in sorted(index)])
    달들 = sorted(set(종목달) & set(지수달))
    if len(달들) < MIN_MONTHS + 1:
        return None
    rows = []
    for a, b in zip(달들, 달들[1:], strict=False):
        rows.append({"month": b, "index": 지수달[b] / 지수달[a] - 1, "stock": 종목달[b] / 종목달[a] - 1})
    worst = sorted(rows, key=lambda r: r["index"])[:WORST_MONTHS]
    worse = sum(1 for r in worst if r["stock"] < r["index"])
    out: dict[str, Any] = {
        "months": len(rows), "worst": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
                                        for r in sorted(worst, key=lambda r: r["month"])],
        "avg_index": round(sum(r["index"] for r in worst) / len(worst), 4),
        "avg_stock": round(sum(r["stock"] for r in worst) / len(worst), 4), "worse": worse,
    }  # fmt: skip
    # 하락일·상승일 베타 — 같은 날 둘 다 있는 날만
    일 = [(d, c) for d, c in zip(dates, closes, strict=False) if str(d) in index and c and c > 0]
    쌍 = [(c1 / c0 - 1, index[str(d1)] / index[str(d0)] - 1) for (d0, c0), (d1, c1) in zip(일, 일[1:], strict=False)
         if index[str(d0)] > 0]  # fmt: skip
    for 이름, 고름 in (("down_beta", lambda m: m < 0), ("up_beta", lambda m: m > 0)):
        xs = [(s, m) for s, m in 쌍 if 고름(m)]
        if len(xs) >= 30:
            ms = sum(m for _, m in xs) / len(xs)
            ss = sum(s for s, _ in xs) / len(xs)
            var = sum((m - ms) ** 2 for _, m in xs)
            out[이름] = round(sum((s - ss) * (m - ms) for s, m in xs) / var, 3) if var > 0 else None
    return out
