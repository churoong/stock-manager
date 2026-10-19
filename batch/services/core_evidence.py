"""핵심 H 백테스트의 결정 단위 판정 (docs/backtest.md 10장 조각 1e, docs/infra.md 25.1542·25.1544).

CAGR 하나로는 수년이어도 운과 실력을 가르기 어렵다 — 결정마다 같은 날 종목과 견주면 시장 전체·같은 날 공통
움직임이 빠진다(docs/paper.md 10.1). 잣대 둘을 함께 낸다(25.1544 반박 검증 [필수] 1 — 처음엔 "관문과 같은 잣대" 라고
적었는데 대조 모집단이 달랐다):

1. **무작위 대조 분위**(`evidence`) — 가상모의투자 가상 대조(`pp.control_picks`·`pp.control_pct`, docs/company.md 6장)와
   **같은 대조 종목**: "virtual|KR|기준일" 씨앗으로 그날 시점 유니버스에서 20, 성과는 기준일 뒤 20번째 거래일 종가
2. **관문과 같은 잣대**(`gate_evidence`) — 실전 전환 관문의 결정 단위 선정(`pp.selection_evidence`, desk GATE_SEL):
   산 종목이
   같은 날 **안 산 새 후보** 가운데 몇 분위. 하루 기록의 운영 결정 원장 모양(`DayLog.decisions`)을 그대로 넘긴다

- 기준일 = 결정의 cutoff(그 결정이 본 마지막 종가 날) — 운영 결정 원장의 기준 종가(신호 행 종가)와 같은 뜻
- 매수(1) = `DayLog.buys` 가운데 **새로 산 것**(판단 직전 보유 `DayLog.held` 가 아닌 것 — 25.1544 [권고] 4:
  상한까지 채우는
  추가 매수가 섞였다)의 에피소드 첫 매수(같은 종목 `pp.ATTRIBUTION_EPISODE_DAYS` 안 되풀이는 처음만)
- 묶음: 28일(`pp._묶음`). 핵심 H 는 매수가 드물어 운영의 "묶음 안 사흘 이상"(`pp.EVIDENCE_BLOCK_MIN_DAYS`)을 쓰면
  묶음 대부분이
  버려진다(25.1544 [필수] 3) — 무작위 대조 분위는 매수가 있는 날 하나라도 있으면 묶음으로 센다(`CORE_BLOCK_MIN_DAYS`).
  판정은 묶음이 관문과 같은 `desk.GATE_SEL_MIN_BLOCKS` 이상일 때만(`verdict`)
- 판 뒤(`after_sell`) — 매도마다 기준일 뒤 20 거래일 KOSPI 대비 초과(낮을수록 잘 판 것). core_g−core_b 짝은 매수 성과라
  매도의 몫을 재지 못한다(25.1544 [필수] 2) — 매도는 이것으로 본다
- 순수 함수 — DB·네트워크 없음. 자료는 실행이 이미 메모리에 든 수정종가·지수(읽기 0)
"""

from __future__ import annotations

import bisect
import math
from datetime import date
from typing import Any

from batch.services import desk
from batch.services import paper as pp

#: 짝 비교 — (앞 − 뒤) 매수 성과. H−G = 합의 선정의 몫(둘은 매도 규칙이 같다). G−B 는 매도 규칙만 달라 매수 성과 차이가
#: "현금이 언제 났나" 의 우연이라 짝에서 뺐다(25.1544 [필수] 2 — 매도는 `after_sell`)
CORE_PAIRS: tuple[tuple[str, str], ...] = (("core_h", "core_g"),)
#: 무작위 대조 분위 묶음에 필요한 매수 날 수 — 핵심 H 는 매수가 드물다(25.1544 [필수] 3, 운영 증거는 사흘)
CORE_BLOCK_MIN_DAYS = 1


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
    """(기준일 cutoff, 종목) — 새로 산 것(판단 직전 보유가 아닌 것)의 에피소드 첫 매수만. 하루 기록(`DayLog`)의 `buys`
    열쇠를 날 순으로"""
    마지막: dict[int, date] = {}
    out: list[tuple[str, int]] = []
    for log in sorted(logs, key=lambda x: x.day):
        d = date.fromisoformat(str(log.day)[:10])
        든 = getattr(log, "held", frozenset())
        for sid in sorted(log.buys):
            if sid in 든:
                continue  # 추가 매수 — 새 선정 결정이 아니다
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
    블록 = {k: sum(v) / len(v) for k, v in 분위묶음.items() if len(v) >= CORE_BLOCK_MIN_DAYS}
    초과 = {k: sum(v) / len(v) for k, v in 초과묶음.items()}
    return {"buys": len(산), "scored": 점수, "no_control": 대조없음, "days": len(날별), **pp.block_bound(블록),
            "blocks": {str(k): round(v, 3) for k, v in sorted(블록.items())},
            "excess_blocks": {str(k): round(v, 3) for k, v in sorted(초과.items())}}  # fmt: skip


def gate_evidence(logs: list[Any], closes: Closes) -> dict[str, Any]:
    """관문과 같은 잣대 — `pp.selection_evidence` 에 하루 기록의 운영 결정 원장 모양을 그대로 넘긴다
    (산 것 vs 같은 날 안 산
    새 후보, 묶음 안 사흘 이상, 결정일 묶음). 성과는 각 결정의 기준 종가 날(`ref_date`, 없으면 cutoff) 뒤 20 거래일"""
    n = pp.ATTRIBUTION_HORIZON
    줄: list[dict[str, Any]] = []
    for log in logs:
        for d in log.decisions:
            if d.get("kind") not in ("buy", "skip"):
                continue
            f = closes.forward(int(d["stock_id"]), str(d.get("ref_date") or log.cutoff), n)
            if f is None:
                continue
            줄.append({"decided_on": log.day, "market": "KR", "kind": d["kind"], "reason": str(d.get("reason") or ""),
                      f"ret_{n}d": f[0], f"bench_{n}d": None})  # fmt: skip
    e = pp.selection_evidence(줄)
    return {k: e.get(k) for k in ("days", "n_blocks", "mean", "se", "lower")}


def after_sell(logs: list[Any], closes: Closes, kospi: dict[str, float]) -> dict[str, Any]:
    """판 뒤 — 매도(정지로 미룬 것 빼고)마다 기준일(cutoff) 뒤 20 거래일 KOSPI 대비 초과. 낮을수록 잘 판 것.
    날 평균 → 28일 묶음 → `pp.block_bound`"""
    n = pp.ATTRIBUTION_HORIZON
    bd, bv = _bench(kospi)
    묶음: dict[int, list[float]] = {}
    k = 0
    for log in logs:
        for sid, 까닭 in log.sells.items():
            if 까닭 == ["미룬 매도"]:
                continue
            f = closes.forward(int(sid), str(log.cutoff), n)
            if f is None:
                continue
            a, b = _upto(bd, bv, str(log.cutoff)), _upto(bd, bv, f[1])
            if not a or not b:
                continue
            묶음.setdefault(pp._묶음(str(log.cutoff)), []).append(f[0] - (b / a - 1) * 100)
            k += 1
    return {"sells": k, **pp.block_bound({x: sum(v) / len(v) for x, v in 묶음.items()})}


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


def verdict(by_variant: dict[str, dict[str, Any]], pair: dict[str, Any] | None) -> str:
    """사전 등록 판정(2026-10-19 고정 — 첫 국내 기본 실행 하나로만, docs/backtest.md 10장 조각 1e).

    - core_h 무작위 대조 분위 묶음 < `desk.GATE_SEL_MIN_BLOCKS`(관문과 같은 6) 또는 H−G 짝 묶음 < 같은 수
      → 판정 불가(묶음 부족)
    - 위약 점검: core_r1·core_r2 가운데 하나라도 분위 하한 > 50 이면 판정 불가(대조 모집단 편향 — 오늘 유니버스에
      시점 필터를 건
      모집단이라 그 뒤 상장폐지·작아진 종목이 없다, 25.1544 [권고] 5)
    - core_h 분위 하한 > 50 이고 H−G 짝 하한 > 0 → "선정에 엣지 근거"
    - 그 밖 → "근거 없음". 어느 쪽이든 규칙을 바꾸지 않는다(운영 H 판 바꾸기는 따로)
    """
    h = by_variant.get("core_h") or {}
    하한 = desk.GATE_SEL_MIN_BLOCKS
    if (h.get("n_blocks") or 0) < 하한 or ((pair or {}).get("n_blocks") or 0) < 하한:
        return "판정 불가(묶음 부족)"
    for r in ("core_r1", "core_r2"):
        lo = (by_variant.get(r) or {}).get("lower")
        if lo is not None and lo > 50:
            return "판정 불가(대조 모집단 편향 — 무작위 R 이 대조를 이겼다)"
    if h.get("lower") is not None and h["lower"] > 50 and (pair or {}).get("lower") is not None and pair["lower"] > 0:
        return "선정에 엣지 근거"
    return "근거 없음"
