"""핵심 H 백테스트의 결정 단위 판정 — 매수마다 같은 날 유니버스 무작위 대조 안 분위
(docs/backtest.md 10장 조각 1e, docs/infra.md 25.1542).

CAGR 하나로는 수년이어도 운과 실력을 가르기 어렵다 — 결정마다 같은 날 무작위 종목과 견주면 시장 전체·같은 날 공통
움직임이 빠진다(docs/paper.md 10.1). 가상모의투자 가상 대조(`pp.control_picks`·`pp.control_pct`, docs/company.md 6장)와
**같은 함수**를 쓴다 — 대조 종목은 "virtual|KR|기준일" 씨앗으로 그날 시점 유니버스에서 20,
성과는 기준일 뒤 20번째 거래일 종가.

- 기준일 = 결정의 cutoff(그 결정이 본 마지막 종가 날) — 운영 결정 원장의 기준 종가(신호 행 종가)와 같은 뜻
- 매수 = 변형의 하루 기록 `DayLog.buys` 가운데 **에피소드 첫 매수**(같은 종목이 `pp.ATTRIBUTION_EPISODE_DAYS` 안에
  되풀이되면 처음만 — 분할 매수·다시 사기가 같은 판단을 여러 번 세지 않게)
- 날마다 분위 평균 → 28일 묶음(`pp._묶음`, 묶음 안 날 `pp.EVIDENCE_BLOCK_MIN_DAYS` 이상) → `pp.block_bound` 한쪽 하한.
  무작위면 50. 같은 묶음으로 20 거래일 KOSPI 대비 초과 평균도 내 짝(H−G 선정·G−B 매도) 차이를 본다
- 순수 함수 — DB·네트워크 없음. 자료는 실행이 이미 메모리에 든 수정종가·지수(읽기 0)
"""

from __future__ import annotations

import bisect
import math
from datetime import date
from typing import Any

from batch.services import paper as pp

#: 짝 비교 — (앞 − 뒤). H−G = 합의 선정의 몫, G−B = 추적 매도의 몫(docs/backtest.md 10장 변형 표와 같은 뜻)
CORE_PAIRS: tuple[tuple[str, str], ...] = (("core_h", "core_g"), ("core_g", "core_b"))


class Closes:
    """종목 → 정렬된 (날짜, 종가). 기준일 종가와 그 뒤 n 번째 종가를 이분 탐색으로."""

    def __init__(self, prices: dict[int, dict[str, float]]) -> None:
        self._raw = prices
        self._rows: dict[int, list[tuple[str, float]]] = {}

    def rows(self, sid: int) -> list[tuple[str, float]]:
        r = self._rows.get(sid)
        if r is None:
            r = sorted((d, float(c)) for d, c in (self._raw.get(sid) or {}).items() if c is not None and float(c) > 0)
            self._rows[sid] = r
        return r

    def forward(self, sid: int, ref: str, n: int) -> tuple[float, str] | None:
        """기준일 종가 대비 그 뒤 n 번째 거래일 종가 수익률(%)과 그 날짜. 기준일에 종가가 없거나 n 개가 안 차면 None"""
        r = self.rows(sid)
        k = bisect.bisect_left(r, (ref, -math.inf))
        if k >= len(r) or r[k][0] != ref or k + n >= len(r):
            return None
        return (r[k + n][1] / r[k][1] - 1) * 100, r[k + n][0]


def _bench(index: dict[str, float]) -> tuple[list[str], list[float]]:
    날 = sorted(index)
    return 날, [float(index[d]) for d in 날]


def _upto(days: list[str], vals: list[float], d: str) -> float | None:
    k = bisect.bisect_right(days, d)
    return vals[k - 1] if k else None


def first_buys(logs: list[Any]) -> list[tuple[str, int]]:
    """(기준일 cutoff, 종목) — 에피소드 첫 매수만. 하루 기록(`DayLog`)의 `buys` 열쇠를 날 순으로"""
    마지막: dict[int, date] = {}
    out: list[tuple[str, int]] = []
    for log in sorted(logs, key=lambda x: x.day):
        d = date.fromisoformat(str(log.day)[:10])
        for sid in sorted(log.buys):
            if sid in 마지막 and (d - 마지막[sid]).days < pp.ATTRIBUTION_EPISODE_DAYS:
                continue
            마지막[sid] = d
            out.append((str(log.cutoff), int(sid)))
    return out


def evidence(
    logs: list[Any], closes: Closes, eligible_at: Any, kospi: dict[str, float], market: str = "KR",
) -> dict[str, Any]:  # fmt: skip
    """변형 하나의 결정 단위 판정 → {buys, scored, no_control, days, n_blocks, mean, se, lower, blocks,
    excess_blocks}.

    `eligible_at(cutoff)` = 그날 시점 유니버스(종목 id 집합), `kospi` = {날짜: 지수}.
    매수 종목의 20 거래일 성과가 아직 안 찼으면
    (끝 20일) 빼고 센다. 대조가 `pp.VIRTUAL_MIN_CONTROLS` 보다 적게 찼으면 `no_control`
    """
    n = pp.ATTRIBUTION_HORIZON
    bd, bv = _bench(kospi)
    산 = first_buys(logs)
    날별: dict[str, list[float]] = {}
    초과묶음: dict[int, list[float]] = {}
    점수, 대조없음 = 0, 0
    for ref, sid in 산:
        f = closes.forward(sid, ref, n)
        if f is None:
            continue
        ret, 끝날 = f
        a, b = _upto(bd, bv, ref), _upto(bd, bv, 끝날)
        if a and b:
            초과묶음.setdefault(pp._묶음(ref), []).append(ret - (b / a - 1) * 100)
        대조 = pp.control_picks(sorted(eligible_at(ref)), market, ref, sid)
        rets = [x[0] for c in 대조 if (x := closes.forward(c, ref, n)) is not None]
        q = pp.control_pct(ret, rets)
        if q is None:
            대조없음 += 1
            continue
        점수 += 1
        날별.setdefault(ref, []).append(q)
    분위묶음: dict[int, list[float]] = {}
    for d, qs in 날별.items():
        분위묶음.setdefault(pp._묶음(d), []).append(sum(qs) / len(qs))
    블록 = {k: sum(v) / len(v) for k, v in 분위묶음.items() if len(v) >= pp.EVIDENCE_BLOCK_MIN_DAYS}
    초과 = {k: sum(v) / len(v) for k, v in 초과묶음.items()}
    return {"buys": len(산), "scored": 점수, "no_control": 대조없음, "days": len(날별), **pp.block_bound(블록),
            "blocks": {str(k): round(v, 3) for k, v in sorted(블록.items())},
            "excess_blocks": {str(k): round(v, 3) for k, v in sorted(초과.items())}}  # fmt: skip


def pairs(by_variant: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """짝 차이 — 같은 28일 묶음끼리 (앞 − 뒤) 20 거래일 KOSPI 대비 초과 평균 → `pp.block_bound`.
    짝 이름 "core_h-core_g" """
    out: dict[str, dict[str, Any]] = {}
    for a, b in CORE_PAIRS:
        if a not in by_variant or b not in by_variant:
            continue
        ea, eb = by_variant[a].get("excess_blocks") or {}, by_variant[b].get("excess_blocks") or {}
        차 = {int(k): float(ea[k]) - float(eb[k]) for k in ea if k in eb}
        out[f"{a}-{b}"] = pp.block_bound(차)
    return out
