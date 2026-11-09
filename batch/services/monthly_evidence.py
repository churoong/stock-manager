"""월 백테스트 판정판 기록 칸 — 꺼진 채(잇지 않음) 둔 조각
(docs/factors.md 12.45 "u2 판정판 기록 칸 + core_m 사전 등록").

- (a) `evidence` — 월 엔진엔 하루 기록(`DayLog`)이 없어 `result.rebalances` 로 기록 꼴을 만들고 `ce.evidence` 를
  **그대로** 부른다(씨앗·대조·묶음을 새로 짜지 않는다 — 검증 B).
  day = t, cutoff = `bt.decision_cutoff(dates, prices, t)`,
  held = 직전 리밸런스 weights 에서 w > 0, buys = 이번 weights 에서 w > 0(`dropped` 밖) − held.
  씨앗 시장코드는 **실행 시장**("virtual|KR|ref"·"virtual|US|ref") — 운영 `fill_controls(client, market)` 와 같다.
  `core_evidence` 의 기본값 "KR" 을 쓰지 않는다. 묶음 최소 날 수는 `ce.CORE_BLOCK_MIN_DAYS`(1)
- (b) `split_half` — `jobs/core_backtest.split_half` 와 같은 식을 services 로 옮겼다(jobs/backtest ↔ jobs/core_backtest
  순환 import 를 만들지 않게). `jobs/core_backtest.split_half` 는 이 함수를 가리키는 이름으로 남는다
- (c) `cohort_m` — 코호트 BH 의 m = max(시도 줄들 `luck.n_trials` 최댓값, 시도 줄 수). N 이 하나도 없으면 None
  (BH q 없음 — 줄 수로 떨어지지 않는다, 검증 B)

순수 함수 — DB·네트워크 없음. 아직 어느 실행에도 잇지 않았다(12월 판정 실행 뒤 잇는 커밋에서).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from batch.services import backtest as bt
from batch.services import core_evidence as ce
from batch.services import metrics as m

#: 결과 행 칸 `metrics_json.evidence.version` — `CALC_VERSION` 을 올리지 않고 칸 판만 단다(12.45 판 표시)
EVIDENCE_VERSION = 1
#: 씨앗 시장코드로 받는 실행 시장 — 그 밖은 `evidence_error`
MARKETS = ("KR", "US")


@dataclass(frozen=True)
class MonthLog:
    """`ce.first_buys`·`ce.evidence` 가 읽는 하루 기록 꼴 — day·cutoff·held·buys 만(`limits` 없음 → 낸 것 = 산 것)"""

    day: str
    cutoff: str
    held: frozenset[int] = field(default_factory=frozenset)
    buys: tuple[int, ...] = ()


def month_logs(rebalances: list[Any], dates: list[str], prices: dict[int, dict[str, float]]) -> list[MonthLog]:
    """리밸런스 → 기록 꼴. 첫 리밸런스의 held 는 비었다. 판단 기준일(`decision_cutoff`)이 없는 리밸런스는 결정 근거가
    없어 기록을 내지 않는다(그래도 그 weights 는 다음 리밸런스의 held 가 된다)"""
    out: list[MonthLog] = []
    held: frozenset[int] = frozenset()
    for r in rebalances:
        지금 = frozenset(int(s) for s, w in r.weights.items() if w > 0)
        빠짐 = {int(s) for s in (getattr(r, "dropped", None) or [])}
        cutoff = bt.decision_cutoff(dates, prices, r.date)
        if cutoff is not None:
            out.append(MonthLog(day=r.date, cutoff=cutoff, held=held, buys=tuple(sorted(지금 - held - 빠짐))))
        held = 지금
    return out


def evidence(
    rebalances: list[Any], dates: list[str], prices: dict[int, dict[str, float]], market: str,
    eligible_at: Any, index: dict[str, float],
) -> dict[str, Any]:  # fmt: skip
    """월 엔진 결과 하나 → `{"evidence": {...ce.evidence, "version": 1}}`,
    깨지면 `{"evidence_error": 까닭}`(던지지 않는다).

    `eligible_at(cutoff)` = 그날 시점 유니버스,
    `index` = 그 시장 기준 지수(`cb.BENCH_INDEX[market]`, 초과 묶음 기록만)"""
    try:
        if market not in MARKETS:
            raise ValueError(f"실행 시장은 KR·US — {market!r}")
        logs = month_logs(rebalances, dates, prices)
        e = ce.evidence(logs, ce.Closes(prices), eligible_at, index, market=market)
        return {"evidence": {**e, "version": EVIDENCE_VERSION}}
    except Exception as e:  # noqa: BLE001 — 칸 계산이 깨져도 실행은 성공으로 닫는다(12.45 실패)
        return {"evidence_error": f"{type(e).__name__}: {str(e)[:120]}"}


def split_half(curve: list[tuple[str, float]]) -> dict[str, Any]:
    """곡선을 날 수로 반 나눠 앞·뒤 반의 CAGR·MDD (docs/backtest.md 10장 "기간을 반으로 나눈 결과를 함께 싣는다").

    가운데 날은 두 반에 모두 든다(앞 반의 끝 = 뒤 반의 시작 — 뒤 반 수익이 그날 자본에서 시작한다).
    식은 docs/metrics.md 그대로(`metrics.cagr`·`max_drawdown`). 곡선이 셋보다 짧으면 비운다.
    `jobs/core_backtest.split_half` 에서 옮겼다(12.45 (b) — 같은 식, 출력이 비트까지 같다)"""
    if len(curve) < 3:
        return {}
    가운데 = len(curve) // 2
    out: dict[str, Any] = {}
    for 이름, 조각 in (("first", curve[: 가운데 + 1]), ("second", curve[가운데:])):
        점 = [m.PricePoint(date=date.fromisoformat(d), close=v) for d, v in 조각]
        out[이름] = {"start": 조각[0][0], "end": 조각[-1][0], "days": len(조각),
                    "cagr": m.cagr(점), "mdd": m.max_drawdown(점).mdd}  # fmt: skip
    return out


def _n(v: Any) -> int | None:
    """`luck.n_trials` 한 값 — 양의 정수만(bool·NaN·문자열은 없음으로)"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, float):
        if not math.isfinite(v) or v != int(v):
            return None
        v = int(v)
    return v if isinstance(v, int) and v > 0 else None


def cohort_m(n_trials: list[Any]) -> int | None:
    """코호트 BH 의 m — 같은 (시장, 코호트 1) 묶음의 **시도 줄마다** `luck.n_trials` 하나씩(없으면 None) 받는다.

    m = max(그 N 들의 최댓값, 시도 줄 수). 어느 줄에도 N 이 없으면(운 판정 실패) None — BH q 를 내지 않는다
    (줄 수로 떨어지지 않는다, 12.45 (c) 검증 B)"""
    ns = [n for v in n_trials if (n := _n(v)) is not None]
    if not ns:
        return None
    return max(max(ns), len(n_trials))
