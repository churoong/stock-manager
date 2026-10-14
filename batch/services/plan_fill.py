"""매수 구간·3회 분할 계획의 체결 성적 — 기록 전용 진단 (docs/signals.md 10.2, docs/factors.md 12.22 P3, infra 25.1327).

신호 성적표(10장)는 모든 신호를 **다음 거래일 종가**에 산 것으로 잰다. 그런데 신호는 사용자에게 "이 구간에서
세 번 나눠 사라" 고 한다(CLAUDE.md 매매 규칙). 그 계획을 따랐다면 어땠는지는 한 번도 잰 적이 없다. 지정가처럼
기다렸다 사는 방식은 **떨어질 때 더 잘 체결된다**(역선택 — Linnainmaa 2010 *JF* `[확인필요]`; Harris·Hasbrouck 1996).

규칙 (검증 둘의 조건, 결과 보기 전에 고정 — 바꾸지 않는다)
- 분할 가격은 **신호에 저장된 `tranche_plan`** 을 목표·손절과 같은 배수(`outcomes.unit_ratio`)로 지금 단위에 옮긴다.
  오늘의 `tranche_plan()` 으로 다시 만들지 않는다(호가 규칙이 바뀌었다, 25.657). 여유 계수 없음
- 회차 i 는 진입일(기준일 다음 거래일)부터 `HORIZON_WINDOW`(60) 거래일 안에서 **종가 ≤ 그 가격**인 첫날 체결.
  체결가는 그 회차 가격(지정가 — 종가보다 나쁘게 잡는 보수 쪽). 종가만 보므로 장중 저가로 찼을 몫을 놓친다
  — 체결률은 하한
- 계획 수익 = Σ 체결된 회차 비중 × (끝 가격 ÷ 체결가 − 1). 안 찬 몫은 현금 0. 끝 가격 = 성적표 60일 수익이
  정해진 값(진입 종가 × (1 + `rets[60]`)) — 상장폐지·끊김의 청산 규칙(25.696)도 그대로 따른다.
  60일 수익이 없으면 재지 않는다
- 견줄 값은 같은 신호의 다음 날 종가 진입 60일 수익(`rets[60]`). 5·20일 계획 수익은 내지 않는다
  (늦게 찬 회차에 뜻이 없다)
- 표본: 기간마다 `MIN_N` 미만이면 n 만, `CONCLUSIVE_N` 미만이면 "단정하지 않음". 이어 나온 신호는 독립이 아니다(10장) —
  서로 다른 종목 수를 함께 적는다
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

from batch.services import calibration as cb
from batch.services import outcomes as oc

#: 성적표 집계와 같은 하한 (`outcomes.summarize` min_n)
MIN_N = 5
#: 이보다 적으면 "단정하지 않음" — 점수 보정표(10.1)의 문턱을 그대로 빌린다(새 문턱을 만들지 않는다)
CONCLUSIVE_N = cb.CAUTION_N


@dataclass
class PlanFill:
    stock_id: int
    horizon: str
    filled: list[bool]  # 회차별 체결 여부 (1·2·3차)
    plan_ret: float  # 안 찬 몫은 현금 0
    deployed: float  # 체결된 비중 합 (0~1)
    naive_ret: float  # 다음 날 종가 진입 60일 수익


def tranches_of(raw: Any) -> list[tuple[float, float]]:
    """저장된 분할 계획 → [(비중, 가격)]. 깨졌거나 비중·가격이 없으면 빈 목록."""
    try:
        plan = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    out: list[tuple[float, float]] = []
    for t in plan or []:
        if not isinstance(t, dict):
            return []
        ratio, price = t.get("ratio"), t.get("price")
        if not isinstance(ratio, int | float) or not isinstance(price, int | float) or ratio <= 0 or price <= 0:
            return []
        out.append((float(ratio), float(price)))
    return out


def evaluate(
    signal: dict[str, Any], outcome: oc.Outcome, closes: list[tuple[str, float]],
    raw_closes: dict[str, float] | None = None,
) -> PlanFill | None:
    """신호 하나의 계획 체결 성적. 60일 수익이 없거나 계획을 읽지 못하면 None."""
    plan = tranches_of(signal.get("tranche_plan"))
    naive = outcome.rets.get(oc.HORIZON_WINDOW)
    if not plan or naive is None or outcome.entry_date is None or not outcome.entry_close:
        return None
    dates = [d for d, _c in closes]
    start = bisect_right(dates, outcome.as_of_date)  # 진입일 — 성적표와 같은 자리
    window = closes[start : start + oc.HORIZON_WINDOW]
    k = oc.unit_ratio(signal, closes, raw_closes, outcome.entry_date, outcome.entry_close)
    end = outcome.entry_close * (1 + naive)
    filled: list[bool] = []
    plan_ret = deployed = 0.0
    for ratio, price in plan:
        limit = price * k
        hit = any(c <= limit for _d, c in window)
        filled.append(hit)
        if hit:
            plan_ret += ratio * (end / limit - 1)
            deployed += ratio
    return PlanFill(int(signal["stock_id"]), str(signal["horizon"]), filled, plan_ret, deployed, naive)


def summarize(fills: list[PlanFill]) -> list[dict[str, Any]]:
    """기간마다 체결률(회차별)·계획 수익·투입한 돈의 수익·다음 날 진입 수익. step_log 에 그대로 적는다."""
    out: list[dict[str, Any]] = []
    for horizon in sorted({f.horizon for f in fills}):
        g = [f for f in fills if f.horizon == horizon]
        n = len(g)
        row: dict[str, Any] = {"horizon": horizon, "n": n, "stocks": len({f.stock_id for f in g}),
                               "conclusive": n >= CONCLUSIVE_N}  # fmt: skip
        if n >= MIN_N:
            회차 = max(len(f.filled) for f in g)
            row["fill_rate"] = [round(sum(1 for f in g if len(f.filled) > i and f.filled[i]) / n, 4)
                                for i in range(회차)]  # fmt: skip
            row["plan_ret"] = round(sum(f.plan_ret for f in g) / n, 4)
            투입 = [f for f in g if f.deployed > 0]
            row["deployed_ret"] = round(sum(f.plan_ret / f.deployed for f in 투입) / len(투입), 4) if 투입 else None
            row["naive_ret"] = round(sum(f.naive_ret for f in g) / n, 4)
        out.append(row)
    return out
