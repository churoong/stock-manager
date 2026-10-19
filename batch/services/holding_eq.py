"""H 회사 가중치 대 동일가중 대조 — 핵심 H 백테스트 실행 기록 진단 (docs/backtest.md 10장 "가중 대조",
docs/factors.md 12.34 31회차 P1, docs/infra.md 25.1538).

H 는 회사 성적으로 가중치를 [0.25, 2] 안에서 스스로 기울인다(`holding.weights`, 성적 표본 n 으로 1 쪽으로 줄임).
그 **줄인 가중**이 동일가중(모든 회사 1)보다 나은지를 같은 날·같은 후보 안에서 견준다. 예측 결합 문헌은 짧은 표본으로
추정한 가중이 동일가중을 자주 못 이긴다고 본다(Clemen 1989, Stock·Watson 2004, Smith·Wallis 2009).

- 새 매매 변형이 아니다 — 변형 다섯이 나눠 읽은 하루 재료(`DayView`)와 메모리 원장으로 **끝난 뒤** 센다(읽기 0).
  31회차 검증 B: 이름 붙은 변형(`core_h1`)은 동결 대상이고, 선정 단위 비교라 변형이 필요 없다
- 이 파일은 `holding.py` 밖이다 — 부서·H 판단식 지문(tests/test_dept_logic_frozen_1488.py)을 건드리지 않는다
- 결정 규칙은 결과를 보기 전에 고정한다(아래 `decide`). 판정은 **핵심 H 첫 국내 기본 실행 하나**로만 한다
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from batch.services import holding as hold
from batch.services import paper as pp
from batch.services import report_picks

#: 같은 (W에만, E에만) 종목 짝이 이 달력일 안에 되풀이되면 첫 날만 센다 — 성과 분해 에피소드와 같은 길이
EQ_EPISODE_DAYS = hold.HOLDING_EPISODE_DAYS
#: 판정 가능 — d 가 있는 28일 묶음 수 하한(31회차 검증 B 문구)
EQ_MIN_BLOCKS = 12
#: 판정 가능 — 서로 다른 짝 수 하한(31회차 검증 B 문구)
EQ_MIN_PAIRS = 30
#: 규칙을 바꾸는 갈래(`equal`)의 한쪽 유의수준 — 본페로니. 같은 첫 실행에서 규칙을 바꿀 수 있는 갈래가 둘
#: (이것과 `exit_pair` 의 `target`)이라 0.05 ÷ 2 (32회차 검증 B, 25.1552 — 첫 실행 결과 전에 고침, 더 보수적으로만)
EQ_CHANGE_ALPHA = 0.025


def picks(votes: dict[str, dict[int, float]], w: dict[str, float], tiebreak: dict[int, float], top_n: int) -> list[int]:
    """운영 H 와 같은 고르기 — 두 곳 이상 추천(`eligible`)·신호 행 있음(`tiebreak` 열쇠), 점수 순 상위 top_n"""
    순위 = hold.rank(votes, w, tiebreak)
    return [x["stock_id"] for x in 순위 if x["eligible"] and x["stock_id"] in tiebreak][:top_n]


def day_sets(dv: Any, top_n: int) -> tuple[list[int], list[int]]:
    """하루 재료 → (W = 그날 가중으로 고른 것, E = 동일가중으로 고른 것).

    W 는 `dv.picks` 를 다시 내지 않고 같은 식으로 다시 낸다 — 다르면 이 진단이 운영 고르기와 어긋난 것이라 크게 멈춘다.
    동점 깨기 점수는 신호 행의 종합점수(첫 행) — `SharedDays` 가 비우는 `total_scores` 가 아니다(31회차 검증 B ②)
    """
    점수: dict[int, float] = {}
    for r in report_picks.with_criteria(dv.rows):  # 운영·`CoreMarket.at` 과 같이 기준을 단 행만(그 첫 행의 점수)
        점수.setdefault(int(r.stock_id), float(r.total_score or 0))
    w = picks(dv.votes, dv.company_weights, 점수, top_n)
    if w != list(dv.picks):
        raise ValueError(f"{dv.day} 가중 고르기를 다시 낸 값이 하루 재료와 다르다 — {w} ≠ {list(dv.picks)}")
    return w, picks(dv.votes, {}, 점수, top_n)


def summarize(days: list[tuple[str, list[int], list[int]]], outcomes: dict[tuple[str, int], float]) -> dict[str, Any]:
    """(결정일, W, E) 들과 원장 성과 {(결정일, 종목): 20 거래일 지수 대비 %p} → 실행 기록 `holding_eq`.

    W≠E 인 날만 d = 평균 초과(W∖E) − 평균 초과(E∖W). 그날 한쪽이라도 성과가 없으면(끝 28일·빈 원장 줄)
    그 날은 빼고 센다.
    같은 짝이 `EQ_EPISODE_DAYS` 안에 되풀이되면 첫 날만. 28일 묶음 평균 → `pp.block_bound`(뉴이–웨스트)
    """
    다른날 = 빈날 = 되풀이 = 0
    마지막: dict[tuple[tuple[int, ...], tuple[int, ...]], date] = {}
    값: list[tuple[str, float]] = []
    for day, w, e in sorted(days):
        if set(w) == set(e):
            continue
        다른날 += 1
        wo, eo = tuple(sorted(set(w) - set(e))), tuple(sorted(set(e) - set(w)))
        xs_w = [outcomes.get((day, s)) for s in wo]
        xs_e = [outcomes.get((day, s)) for s in eo]
        if not wo or not eo or any(x is None for x in xs_w + xs_e):
            빈날 += 1
            continue
        d = date.fromisoformat(day[:10])
        if (wo, eo) in 마지막 and (d - 마지막[(wo, eo)]).days < EQ_EPISODE_DAYS:
            되풀이 += 1
            continue
        마지막[(wo, eo)] = d
        값.append((day, sum(xs_w) / len(xs_w) - sum(xs_e) / len(xs_e)))  # type: ignore[arg-type]

    def 묶음(vs: list[tuple[str, float]]) -> dict[int, float]:
        b: dict[int, list[float]] = {}
        for day, x in vs:
            b.setdefault(pp._묶음(day), []).append(x)
        return {k: sum(v) / len(v) for k, v in b.items()}

    블록 = 묶음(값)
    bb = pp.block_bound(블록)
    n = bb["n_blocks"]
    upper = (bb["mean"] + pp.t_quantile(1 - EQ_CHANGE_ALPHA, n - 1) * bb["se"]
             if n >= pp.EVIDENCE_MIN_BLOCKS and bb["se"] is not None else None)  # fmt: skip
    반 = len(값) // 2
    앞 = [x for _d, x in 값[:반]]
    뒤 = [x for _d, x in 값[반:]]
    out = {
        "days": len(days), "differ_days": 다른날, "differ_pct": round(100 * 다른날 / len(days), 1) if days else None,
        "missing_days": 빈날, "repeat_days": 되풀이, "pairs": len(마지막), "n": len(값),
        "n_blocks": n, "mean": _r(bb["mean"]), "lower": _r(bb["lower"]), "upper": _r(upper),
        "first_half": _r(sum(앞) / len(앞)) if 앞 else None, "second_half": _r(sum(뒤) / len(뒤)) if 뒤 else None,
    }  # fmt: skip
    out["verdict"] = decide(out)
    return out


def decide(s: dict[str, Any]) -> str:
    """사전 등록 결정 규칙(2026-10-19 고정 — 첫 실행 결과 전, 31회차 검증 B 문구).

    - 판정 불가: 묶음 < `EQ_MIN_BLOCKS` 또는 서로 다른 짝 < `EQ_MIN_PAIRS` → 지금대로(가중 유지)
    - keep(가): 하한 > 0 이고 앞·뒤 반 평균 모두 > 0 → 기울기 그대로
    - equal(나): 상한 < 0 이고 앞·뒤 반 평균 모두 < 0 → 판을 올려 w≡1 로 바꾸는 후보
      (PREREG 에 가설로 적고 판 올림 절차).
      상한은 한쪽 97.5%(`EQ_CHANGE_ALPHA`, 25.1552 본페로니) — 하한(`keep`, 규칙 그대로)은 95% 그대로
    - none(다): 그 밖 → 지금대로. "W≠E 결정일 비율 x%" 를 infra 에 적고 다시 보지 않는다
    """
    if (s.get("n_blocks") or 0) < EQ_MIN_BLOCKS or (s.get("pairs") or 0) < EQ_MIN_PAIRS:
        return "undecidable"
    앞, 뒤 = s.get("first_half"), s.get("second_half")
    if s.get("lower") is not None and s["lower"] > 0 and 앞 is not None and 뒤 is not None and 앞 > 0 and 뒤 > 0:
        return "keep"
    if s.get("upper") is not None and s["upper"] < 0 and 앞 is not None and 뒤 is not None and 앞 < 0 and 뒤 < 0:
        return "equal"
    return "none"


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None and math.isfinite(x) else None
