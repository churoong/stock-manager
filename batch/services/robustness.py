"""추천 흔들기 — 가중치를 조금씩 바꾼 평행 세계에서도 그 종목이 상위에 남는가 (docs/reports.md 3.2, infra 25.947).

종합 점수는 다섯 팩터의 **가중합**이다. 가중치는 설정 화면의 숫자이고, 20·20·20·20·20 은 근거가 있는 값이
아니라 기본값이다. 그러면 1부에 실린 종목 가운데 "밸류를 10%p 덜 보면 6위로 밀리는 종목" 과 "어떻게 흔들어도
1위인 종목" 은 같은 "72점 3위" 로 보여서는 안 된다 — 전자는 **설정값이 고른 종목**이고 후자는 **데이터가 고른
종목**이다.

여기서는 그 둘을 가른다. 설정 가중치를 중심으로 열여섯 세계를 만들어 각 세계에서 상위 n 을 다시 뽑고, 1부 종목마다
"몇 세계에서 남았나·어느 세계에서 빠지나" 를 센다. 점수를 바꾸지 않는다. 추천을 빼지도 않는다. **표시만** 한다 —
읽는 사람이 "이 추천은 가중치에 민감하다" 를 알고 고르게.

세계의 종류 (설정 가중치가 w, 합 100 으로 정규화한 뒤):
- 팩터 하나를 ±10%p (다섯 팩터 × 2 = 10). 나머지 넷은 **비율을 지키며** 합이 100 이 되게 줄이거나 늘린다
- 팩터 하나를 빼기 — 가중치 0 (5). `scoring.total_score` 가 남은 넷으로 재정규화한다
- 센티먼트 끄기 — 센티먼트 가중치 0 (1)
설정값이 이미 그 세계와 같으면(예: 가중치 0 인 팩터를 또 빼기) 그 세계는 세지 않는다 — 같은 세계를 두 번 세면
"16 세계 가운데 16" 이 부풀려진다.

왜 ±10%p 인가: 설정 화면에서 사람이 "조금 바꿔 본다" 고 할 때 움직이는 폭이다. 5%p 는 순위를 거의 바꾸지 않았고
20%p 는 한 팩터가 두 배가 되어 "조금" 이 아니다 (2026-10-04 실측 전, 추정 `[확인필요: 실제 리포트 몇 회 뒤 폭 재검토]`).
"그림자 후보" 도 센다 — 기본 세계의 상위 n 에는 없지만 흔들면 들어오는 종목. 1부 끝에 한 줄로 적는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from batch.services import scoring
from batch.services.accumulation import display_name

#: 한 팩터를 움직이는 폭(%p). 머리말 참고
STEP_PCT = 10.0
#: 재계산한 기본 세계 점수와 저장된 종합 점수가 이보다 벌어지면 "가중치가 점수 계산 뒤 바뀌었다" 로 본다.
#: `total_score` 는 소수 첫째 자리로 반올림하므로 0.05 안은 같은 값이다 — 여유를 둔다
BASE_TOLERANCE = 0.15

FACTOR_LABEL = {
    "value": "밸류",
    "quality": "퀄리티",
    "growth": "성장",
    "momentum": "모멘텀",
    "risk": "안정성",
}


class Scored(Protocol):
    """흔들기에 필요한 최소한 — `report_picks.SignalRow` 가 이것을 만족한다."""

    stock_id: int
    ticker: str
    name: str
    total_score: float | None
    factor_scores: dict[str, float | None]
    sentiment: float | None


@dataclass(frozen=True)
class World:
    name: str
    weights: dict[str, float]
    sentiment_weight: float


@dataclass
class Stability:
    """1부 종목 하나의 흔들기 결과. `StockPick.stability` 로 실려 `report_items.payload` 에 남는다."""

    kept: int
    total: int
    dropped_by: list[str] = field(default_factory=list)
    score_low: float | None = None
    score_high: float | None = None
    #: 설정 가중치로 다시 낸 점수가 저장된 종합 점수와 맞는가. 아니면 가중치가 점수 계산 뒤 바뀐 것이다 —
    #: 그때의 흔들기는 "지금 설정 중심" 이지 "그 점수 중심" 이 아니라고 적어야 한다
    base_matches: bool = True

    def as_payload(self) -> dict[str, Any]:
        return {
            "kept": self.kept,
            "total": self.total,
            "dropped_by": list(self.dropped_by),
            "score_low": self.score_low,
            "score_high": self.score_high,
            "base_matches": self.base_matches,
        }


@dataclass
class Shaken:
    by_stock: dict[int, Stability]
    #: 기본 세계 상위 n 밖인데 흔들면 들어오는 종목 — (이름, 들어온 세계 수). 많이 들어오는 순
    entrants: list[tuple[str, int]]
    total: int


def _normalized(weights: dict[str, float]) -> dict[str, float]:
    base = {f: max(0.0, float(weights.get(f, 0.0))) for f in scoring.FACTORS}
    s = sum(base.values())
    if s <= 0:
        return {f: 100.0 / len(scoring.FACTORS) for f in scoring.FACTORS}
    return {f: v / s * 100 for f, v in base.items()}


def _shift(base: dict[str, float], factor: str, delta: float) -> dict[str, float]:
    """`factor` 를 delta 만큼 움직이고 나머지는 비율대로 맞춰 합 100."""
    new_f = min(100.0, max(0.0, base[factor] + delta))
    rest = 100.0 - base[factor]
    out: dict[str, float] = {}
    for f, v in base.items():
        if f == factor:
            out[f] = new_f
        elif rest <= 0:
            # 그 팩터가 100 이었다 — 나머지는 전부 0 이라 비율이 없다. 균등하게 나눈다
            out[f] = (100.0 - new_f) / (len(base) - 1)
        else:
            out[f] = v * (100.0 - new_f) / rest
    return out


def worlds(weights: dict[str, float], sentiment_weight: float) -> list[World]:
    """설정값을 중심으로 한 평행 세계들. 설정값과 같은 세계는 넣지 않는다."""
    base = _normalized(weights)
    out: list[World] = []
    # **같은 세계는 한 번만** — 설정값과 같아도, 앞의 세계와 같아도 빼기. 안정성 10 이면 "안정성 −10%p" 와
    # "안정성 빼기" 가 같은 세계(안정성 0)라 둘 다 세면 그 세계가 두 표를 가진다 (2026-10-04 운영 설정 25·25·20·20·10)
    seen = [(base, sentiment_weight)]

    def 새것(w: dict[str, float], sw: float) -> bool:
        for w2, sw2 in seen:
            if abs(sw - sw2) < 1e-9 and all(abs(w[f] - w2[f]) < 1e-9 for f in scoring.FACTORS):
                return False
        seen.append((w, sw))
        return True

    for f in scoring.FACTORS:
        label = FACTOR_LABEL[f]
        for sign, delta in (("+", STEP_PCT), ("−", -STEP_PCT)):
            w = _shift(base, f, delta)
            if 새것(w, sentiment_weight):
                out.append(World(f"{label} {sign}{STEP_PCT:.0f}%p", w, sentiment_weight))
    for f in scoring.FACTORS:
        w = _shift(base, f, -base[f])
        if 새것(w, sentiment_weight):
            out.append(World(f"{FACTOR_LABEL[f]} 빼기", w, sentiment_weight))
    if sentiment_weight > 0 and 새것(base, 0.0):
        out.append(World("센티먼트 끔", dict(base), 0.0))
    return out


def _score(row: Scored, world: World) -> float | None:
    if not row.factor_scores:
        return None
    return scoring.total_score(row.factor_scores, world.weights, row.sentiment, world.sentiment_weight).total


def _top(scored: dict[int, tuple[float | None, str]], n: int) -> set[int]:
    """`report_picks.select_top` 과 같은 순서 — 점수 없음은 뒤로, 같으면 티커."""
    ranked = sorted(scored.items(), key=lambda kv: (kv[1][0] is None, -(kv[1][0] or 0), kv[1][1]))
    return {sid for sid, _ in ranked[:n]}


def shake(
    rows: list[Scored],
    weights: dict[str, float],
    sentiment_weight: float,
    n: int,
    chosen: set[int],
) -> Shaken | None:
    """`chosen`(기본 세계의 상위 n 종목)마다 흔들기 결과를 센다.

    후보 종목이 n 이하면 어떤 세계에서도 모두 남아 뜻이 없다 — None. 세계가 하나도 없어도 None.
    """
    best: dict[int, Scored] = {}
    for r in rows:
        best.setdefault(r.stock_id, r)
    if len(best) <= n or not chosen:
        return None
    ws = worlds(weights, sentiment_weight)
    if not ws:
        return None

    base_world = World("기본", _normalized(weights), sentiment_weight)
    stab = {sid: Stability(kept=0, total=len(ws)) for sid in chosen if sid in best}
    for sid, st in stab.items():
        재계산 = _score(best[sid], base_world)
        저장 = best[sid].total_score
        st.base_matches = 재계산 is not None and 저장 is not None and abs(재계산 - 저장) <= BASE_TOLERANCE

    들어옴: dict[int, int] = {}
    for w in ws:
        scored = {sid: (_score(r, w), r.ticker) for sid, r in best.items()}
        top = _top(scored, n)
        for sid, st in stab.items():
            s = scored[sid][0]
            if s is not None:
                st.score_low = s if st.score_low is None else min(st.score_low, s)
                st.score_high = s if st.score_high is None else max(st.score_high, s)
            if sid in top:
                st.kept += 1
            else:
                st.dropped_by.append(w.name)
        for sid in top - set(stab):
            들어옴[sid] = 들어옴.get(sid, 0) + 1

    # 이름은 꼬리를 뗀 짧은 이름 (25.957) — 미국 목록 이름의 " - Ordinary Shares" 가 한 줄을 다 먹었다
    entrants = sorted(((display_name(best[sid].name), c) for sid, c in 들어옴.items()), key=lambda x: (-x[1], x[0]))
    return Shaken(by_stock=stab, entrants=entrants, total=len(ws))


def stability_line(st: dict[str, Any] | Stability | None) -> str | None:
    """텔레그램 1부 한 줄. payload(dict)와 Stability 둘 다 받는다 — 웹과 글이 같은 말을 하게."""
    if st is None:
        return None
    d = st.as_payload() if isinstance(st, Stability) else st
    kept, total = d.get("kept"), d.get("total")
    if not isinstance(kept, int) or not isinstance(total, int) or total <= 0:
        return None
    line = f"흔들기 {kept}/{total} 유지"
    dropped = [str(x) for x in (d.get("dropped_by") or [])]
    if dropped:
        보임 = dropped[:3]
        더 = f" 외 {len(dropped) - 3}" if len(dropped) > 3 else ""
        line += f" — {'·'.join(보임)}{더}이면 빠짐"
    if d.get("base_matches") is False:
        line += " (가중치가 점수 계산 뒤 바뀜)"
    return line


def entrants_line(entrants: list[tuple[str, int]], total: int, limit: int = 3) -> str | None:
    if not entrants or total <= 0:
        return None
    보임 = " · ".join(f"{name} {c}/{total}" for name, c in entrants[:limit])
    더 = f" 외 {len(entrants) - limit}종목" if len(entrants) > limit else ""
    return f"가중치를 흔들면 들어오는 종목: {보임}{더}"
