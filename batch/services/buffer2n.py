"""38회차 P1′ `buffer_2n` — 순위 버퍼로 회전 줄이기 (docs/factors.md 12.41, docs/infra.md 25.2179).

사전 등록(2026-10-23, 결과 보기 전 — 코드보다 먼저 커밋됨): 매월 리밸런스에 t−1 까지의 나라 전체 종합
순위(`composite` 와 같은 순위)로, **이미 든 종목은 순위 2N 안이면 남기고** 빈자리를 순위대로 채워 N 종목을
동일가중으로 든다(N = 기본 20, 2N = 40). 비중은 매달 1/N 으로 다시 맞춘다. 버퍼 폭 변형(30·50·백분위)은 만들지
않는다 — 자유 숫자 0.

근거: Novy-Marx·Velikov 2016(이상 현상의 거래 비용 — 버퍼가 회전을 줄여 비용 뒤 수익을 지킨다). 효과는 주로
비용 절감이지 알파가 아니다. 판정(`judge`)은 같은 판정 실행의 `composite` 와 견주고 **기록만** 한다 — 이 값이
무엇을 켜지 않는다.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any

from batch.services import backtest as bt
from batch.services import luck

#: 버퍼 폭 — 순위 N 의 몇 배까지 든 종목을 남기나(사전 등록 2N, 변형 없음)
BUFFER_MULT = 2
#: 판정 표본 하한 — 리밸런스 수(사전 등록 36)
JUDGE_MIN_REBALANCES = 36
#: MDD 악화 허용 — `composite` 보다 이만큼(비율) 넘게 나쁘면 탈락(사전 등록 2%p)
JUDGE_MDD_SLACK = 0.02
#: 판정 비교 대상
BASE = "composite"
NAME = "buffer_2n"


def ranked(scores: dict[int, float | None]) -> list[int]:
    """종합 점수 순위(높은 순, 같으면 종목 번호 순) — `bt.top_n_equal_weight` 와 같은 줄 세우기.
    점수 없음·NaN 은 뺀다"""
    return [sid for sid, _s in sorted(((sid, s) for sid, s in scores.items() if s is not None and math.isfinite(s)),
                                      key=lambda pair: (-pair[1], pair[0]))]  # fmt: skip


def pick(scores: dict[int, float | None], held: Iterable[int], n: int = bt.DEFAULT_TOP_N) -> dict[int, float]:
    """이번 리밸런스의 동일가중 비중 — 든 종목(`held`) 가운데 순위 `BUFFER_MULT × n` 안인 것을 남기고, 빈자리를
    순위대로 채워 n 종목. 점수가 없어진 든 종목은 순위 밖으로 보고 판다. 남긴 것이 n 을 넘을 일은 없지만
    (든 수 ≤ n) 넘으면 순위대로 n 개"""
    if n <= 0:
        raise ValueError("n 은 1 이상이어야 합니다")
    순위 = ranked(scores)
    자리 = {sid: i + 1 for i, sid in enumerate(순위)}
    안 = [sid for sid in set(held) if 자리.get(sid, math.inf) <= BUFFER_MULT * n]
    남김 = sorted(안, key=lambda s: 자리[s])[:n]
    고름 = list(남김)
    for sid in 순위:
        if len(고름) >= n:
            break
        if sid not in 고름:
            고름.append(sid)
    return {sid: 1.0 / len(고름) for sid in 고름} if 고름 else {}


def judge(
    results: dict[str, bt.BacktestResult], risk_free_annual: float | None,
    summarize: Callable[..., bt.Summary] = bt.summarize,
) -> dict[str, Any]:
    """12.41 P1′ 판정 — 같은 실행의 `composite` 와 전 구간으로 견준다(두 전략은 같은 후보·같은 리밸런스 날).

    통과 = 비용 반영 CAGR·샤프 둘 다 높고, MDD 악화 ≤ `JUDGE_MDD_SLACK`, (buffer_2n − composite) 월 초과의
    앞·뒤 반 평균이 모두 양. **회전율이 `composite` 보다 낮지 않으면 판정 불가**(버퍼가 회전을 줄이지 못했다 =
    구현 의심). 리밸런스가 `JUDGE_MIN_REBALANCES` 미만이거나 지표가 비면 판정 불가. 앞 반 = 앞쪽 ⌊n/2⌋ 달,
    뒤 반 = 나머지(`high52.judge` 와 같다)"""
    out: dict[str, Any] = {"verdict": "판정 불가"}
    if NAME not in results or BASE not in results:
        out["why"] = "견줄 전략 없음"
        return out
    b, c = results[NAME], results[BASE]
    리밸 = len(b.rebalances)
    out["rebalances"] = 리밸
    지표: dict[str, dict[str, float | None]] = {}
    for 이름, r in ((NAME, b), (BASE, c)):
        mt = summarize(r, None, risk_free_annual=risk_free_annual).metrics
        지표[이름] = {"cagr": mt.cagr, "sharpe": mt.sharpe, "mdd": mt.mdd, "turnover": r.turnover_avg}
    out["metrics"] = 지표
    초과 = luck.excess_series(luck.monthly_returns(b.curve), luck.monthly_returns(c.curve))
    반 = len(초과) // 2
    앞, 뒤 = 초과[:반], 초과[반:]
    out["halves"] = [sum(앞) / len(앞) if 앞 else None, sum(뒤) / len(뒤) if 뒤 else None]
    if 리밸 < JUDGE_MIN_REBALANCES:
        out["why"] = f"리밸런스 {리밸} < {JUDGE_MIN_REBALANCES}"
        return out
    빈칸 = [f"{이름}.{k}" for 이름, x in 지표.items() for k, v in x.items() if v is None]
    if 빈칸 or None in out["halves"]:
        out["why"] = "지표 빈칸: " + ", ".join(빈칸 or ["월 초과 반"])
        if any(k.endswith(".sharpe") for k in 빈칸) and risk_free_annual is None:
            out["why"] += " — 무위험수익률 설정 없음"
        return out
    h, o = 지표[NAME], 지표[BASE]
    if not h["turnover"] < o["turnover"]:  # type: ignore[operator]
        out["why"] = f"회전율이 {BASE} 보다 낮지 않음 — 구현 의심"
        return out
    실패: list[str] = []
    if not h["cagr"] > o["cagr"]:  # type: ignore[operator]
        실패.append(f"CAGR ≤ {BASE}")
    if not h["sharpe"] > o["sharpe"]:  # type: ignore[operator]
        실패.append(f"샤프 ≤ {BASE}")
    if h["mdd"] < o["mdd"] - JUDGE_MDD_SLACK:  # type: ignore[operator]
        실패.append(f"MDD 가 {BASE} 보다 2%p 넘게 나쁨")
    if not (out["halves"][0] > 0 and out["halves"][1] > 0):
        실패.append("앞·뒤 반 월 초과가 모두 양이 아님")
    out["verdict"] = "탈락" if 실패 else "통과"
    if 실패:
        out["why"] = 실패
    return out
