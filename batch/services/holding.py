"""가상 H — 지주사(성적 가중 합의) 순수 함수 (docs/company.md 10장, docs/infra.md 25.1482).

회사들(A·C·D·E 위원회 + B 점수 상위)의 추천에 **회사 성적으로 정한 가중치**를 곱해 종목 점수를 내고,
두 곳 이상이 추천한 종목만 후보로 둔다. 가중치는 H 자신의 결정 원장(추천받은 종목 전부와 그 뒤 20 거래일 성과)에서만
낸다 — 그날까지 성과가 찬 행뿐이라 미래 정보가 없다.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

#: 합의 조건 — 서로 다른 회사 이만큼 이상이 추천한 종목만 후보 (사용자 제안의 "합의", 25.1482)
HOLDING_MIN_VOTERS = 2
#: 위원회 추천의 세기 = min(표 ÷ 문턱, 이 값) — 문턱을 겨우 넘은 가결과 크게 넘은 가결을 가른다. B 의 추천은 1
HOLDING_STRENGTH_MAX = 2.0
#: 성적을 가중치로 옮기는 축소 — k = n ÷ (n + 이 값). 에피소드 30 개면 성적 차이의 절반만 반영 `[확인필요]`
HOLDING_SHRINK_N = 30
#: 회사 성적이 평균보다 이만큼(%p, 20 거래일 지수 대비 초과) 높으면 가중치 +1 (축소 전) `[확인필요]`
HOLDING_SCALE_PCT = 5.0
#: 가중치 하한·상한 — 한 회사가 다 먹거나 한 회사가 아예 빠지지 않게 `[확인필요]`
HOLDING_W_MIN = 0.25
HOLDING_W_MAX = 2.0
#: 성적을 보는 기간(달력일)
HOLDING_WINDOW_DAYS = 365
#: 같은 회사·같은 종목의 추천이 이 달력일 안에 되풀이되면 한 번 — 성과 분해의 에피소드와 같은 길이(20 거래일 ≈ 28
#: 달력일)
HOLDING_EPISODE_DAYS = 28


def weights(records: list[dict[str, Any]], voters: list[str]) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """(회사별 가중치, 회사별 성적 {n, avg}).

    `records` 는 H 결정 원장 행(decided_on·stock_id·ret_20d·bench_20d·detail_json). detail.holding.votes = {회사: 세기}
    인 행에서, 회사마다 그 회사가 추천한 종목의 20 거래일 지수 대비 초과(%p)를 에피소드로 평균.
    w = 1 + k × (성적 − 회사들 평균) ÷ `HOLDING_SCALE_PCT`, k = n ÷ (n + `HOLDING_SHRINK_N`), [하한, 상한] 으로 자른다.
    표본이 없는 회사는 성적 None·가중치 1. 성적이 있는 회사가 하나뿐이면 견줄 평균이 없어 모두 1
    """
    표: dict[str, list[tuple[str, int, float]]] = {v: [] for v in voters}
    for r in records:
        if r.get("ret_20d") is None or r.get("bench_20d") is None:
            continue
        try:
            votes = ((json.loads(r.get("detail_json") or "{}") or {}).get("holding") or {}).get("votes") or {}
        except ValueError:
            continue
        초과 = float(r["ret_20d"]) - float(r["bench_20d"])
        for v in votes:
            if v in 표:
                표[v].append((str(r["decided_on"])[:10], int(r["stock_id"]), 초과))
    성적: dict[str, dict[str, Any]] = {}
    for v, xs in 표.items():
        마지막: dict[int, date] = {}
        남김: list[float] = []
        for 날, sid, x in sorted(xs):
            d = date.fromisoformat(날)
            if sid in 마지막 and (d - 마지막[sid]).days < HOLDING_EPISODE_DAYS:
                continue
            마지막[sid] = d
            남김.append(x)
        성적[v] = {"n": len(남김), "avg": sum(남김) / len(남김) if 남김 else None}
    있는 = [s["avg"] for s in 성적.values() if s["avg"] is not None]
    평균 = sum(있는) / len(있는) if len(있는) >= 2 else None
    out: dict[str, float] = {}
    for v in voters:
        s = 성적[v]
        if 평균 is None or s["avg"] is None:
            out[v] = 1.0
            continue
        k = s["n"] / (s["n"] + HOLDING_SHRINK_N)
        out[v] = round(min(HOLDING_W_MAX, max(HOLDING_W_MIN, 1 + k * (s["avg"] - 평균) / HOLDING_SCALE_PCT)), 4)
    return out, 성적


def strength(vote: float, threshold: float) -> float:
    """위원회 가결의 세기 — min(표 ÷ 문턱, 상한), 1 아래로는 내리지 않는다(가결은 문턱 이상)."""
    if threshold <= 0:
        return 1.0
    return round(min(HOLDING_STRENGTH_MAX, max(1.0, vote / threshold)), 4)


def rank(
    votes: dict[str, dict[int, float]], w: dict[str, float], tiebreak: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """종목 점수 = Σ 회사(가중치 × 세기). [{stock_id, score, voters, eligible}] 점수 순
    (같으면 `tiebreak` 큰 순, 그다음 종목 id).

    `eligible` = 서로 다른 회사 `HOLDING_MIN_VOTERS` 곳 이상이 추천
    """
    합: dict[int, float] = {}
    누가: dict[int, list[str]] = {}
    for v, 표 in votes.items():
        for sid, s in 표.items():
            합[sid] = 합.get(sid, 0.0) + w.get(v, 1.0) * s
            누가.setdefault(sid, []).append(v)
    out = [{"stock_id": sid, "score": round(합[sid], 4), "voters": sorted(누가[sid]),
            "eligible": len(set(누가[sid])) >= HOLDING_MIN_VOTERS} for sid in 합]  # fmt: skip
    return sorted(out, key=lambda x: (-x["score"], -(tiebreak or {}).get(x["stock_id"], 0.0), x["stock_id"]))
