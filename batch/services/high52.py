"""37회차 후보 A — 52주 고점 근접 전략 `high52` (docs/factors.md 12.40, docs/infra.md 25.1646).

George·Hwang(2004): 52주 고점에 가까운 종목이 이어서 오른다. 근접도 자체는 모멘텀 팩터 여섯 지표 가운데 하나로 이미 있다
(`scoring.high_52w_proximity`) — 이 모듈은 **근접도 하나로 고르는 매매 전략**과 그 판정·중복 진단만 낸다.
**정의·문턱·판정 기준은 12.40 에 결과를 보기 전에 적었다 — 바꾸지 않는다.** 실제 추천(종합 점수·신호)에는 쓰지 않는다.
계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from batch.services import backtest as bt
from batch.services import factor_ic as fic
from batch.services import luck
from batch.services.scoring import HIGH_52W_DAYS, MIN_PEER_SIZE, high_52w_proximity
from batch.services.signals import KR_DAILY_LIMIT

#: 동점(근접도 1.0 — 신고가 날)을 깨는 고정 씨앗. 엔진 기본(종목 번호 순)은 근거 없이 기운다 — R 계좌와 같은 랑데부 해싱
TIE_SEED = "high52"
#: 작은 시장을 한데 모은 무리 이름 — 종목이 `MIN_PEER_SIZE`(점수 정규화의 집단 하한) 미만인 시장은 백분위가 거칠어
#: (4종목 시장의 1등이 NASDAQ 1등과 같은 1.0) 한데 모아 순위를 낸다
SMALL_GROUP = "기타"
#: 판정 — 같은 판정 실행·공통 구간에서 `momentum`·`benchmark` 를 둘 다 이겨야 한다(12.40 A)
JUDGE_MDD_SLACK = 0.02  # MDD 가 이만큼(2%p) 넘게 나빠지지 않는다
JUDGE_MIN_REBALANCES = 36  # 공통 구간 리밸런스 하한(백테스트 IC 36개월과 같은 수) — 모자라면 판정 불가
#: 중복 진단 — 시장 안 스피어만 평균이 이 이상이면 계좌를 열지 않는다(6회차 중복 문턱 재사용, 12.40)
OVERLAP_MAX = 0.9


def proximity(closes: list[float], kr: bool) -> float | None:
    """`scoring.high_52w_proximity` 그대로. 국내는 그 252거래일 창 안에 하루 |수익| > `KR_DAILY_LIMIT` 이 있으면 None —
    조정 안 된 병합·감자가 가격을 ×N 으로 올려 근접도가 거짓 1.0 이 된다(15회차 S3 `lt_reversal` 과 같은 꼴)."""
    값 = high_52w_proximity(closes)
    if 값 is None or not kr:
        return 값
    창 = closes[-HIGH_52W_DAYS:]
    for a, b in zip(창, 창[1:], strict=False):
        if a and b and a > 0 and abs(b / a - 1) > KR_DAILY_LIMIT + 1e-9:
            return None
    return 값


def market_percentiles(values: dict[int, float | None], market_of: dict[int, str]) -> dict[int, float]:
    """시장 안 백분위 = 그 시장 안 값이 자기 이하인 종목 수 ÷ 그 시장 종목 수(동점은 같은 값, 0 < p ≤ 1).
    값이 있는 종목이 `MIN_PEER_SIZE` 미만인 시장은 `SMALL_GROUP` 하나로 모은다. **모은 무리도 그보다 작으면**
    그 종목들은 값이 있는 전 종목(그 실행의 나라 전체) 안에서 잰다 — 작은 무리의 1등이 큰 시장 1등과 같은 1.0 이
    되지 않게(25.1646 교차검증 [권고]). 값 없는 종목은 빠진다."""
    무리: dict[str, list[int]] = {}
    for sid, v in values.items():
        if v is not None:
            무리.setdefault(market_of.get(sid) or "?", []).append(sid)
    합친: dict[str, list[int]] = {}
    for 시장, ids in 무리.items():
        합친.setdefault(시장 if len(ids) >= MIN_PEER_SIZE else SMALL_GROUP, []).extend(ids)
    out: dict[int, float] = {}
    전체 = [s for ids in 무리.values() for s in ids]
    for 이름, ids in 합친.items():
        잣대 = 전체 if 이름 == SMALL_GROUP and len(ids) < MIN_PEER_SIZE else ids
        정렬 = sorted(float(values[s]) for s in 잣대)  # type: ignore[arg-type]
        n = len(정렬)
        for s in ids:
            v = float(values[s])  # type: ignore[arg-type]
            # 자기 이하인 수 = v 보다 큰 첫 자리
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi) // 2
                if 정렬[mid] <= v:
                    lo = mid + 1
                else:
                    hi = mid
            out[s] = lo / n
    return out


def _열쇠(sid: int, seed: str) -> bytes:
    return hashlib.sha256(f"{seed}|{sid}".encode()).digest()


def pick(
    values: dict[int, float | None], market_of: dict[int, str], n: int, seed: str = TIE_SEED,
) -> tuple[dict[int, float], int]:
    """시장 안 백분위 상위 n 을 동일가중으로. 같은 백분위는 `sha256(씨앗|종목)` 작은 순.
    돌려주는 것: (비중, n 번째 자리 경계에서 갈린 동점 수 — 경계 백분위와 같은 종목 가운데 못 든 수)."""
    if n <= 0:
        raise ValueError("n 은 1 이상이어야 합니다")
    p = market_percentiles(values, market_of)
    순서 = sorted(p, key=lambda s: (-p[s], _열쇠(s, seed)))
    고름 = 순서[:n]
    if not 고름:
        return {}, 0
    경계 = p[고름[-1]]
    갈린 = sum(1 for s in 순서[n:] if p[s] == 경계)
    return {s: 1.0 / len(고름) for s in 고름}, 갈린


def overlap(
    h52: dict[int, float | None], other: dict[int, float | None], market_of: dict[int, str],
) -> float | None:
    """한 리밸런스의 시장 안 스피어만(종목 수 가중) — IC 도구 그대로(`period_ic_by_market`,
    표본 `MIN_STOCKS` 미만 시장 뺌)."""
    return fic.period_ic_by_market(h52, other, market_of)[0]


def _자른(curve: list[tuple[str, float]], start: str) -> list[tuple[str, float]]:
    return [(d, v) for d, v in curve if d >= start]


def judge(
    results: dict[str, bt.BacktestResult], start: str | None, risk_free_annual: float | None,
    summarize: Callable[..., bt.Summary] = bt.summarize,
) -> dict[str, Any]:
    """12.40 A 판정 — `high52` 가 처음 상위 N 을 다 채운 리밸런스(`start`)부터 같은 구간에서
    `momentum`·`benchmark` 와 견준다.

    통과 = CAGR·샤프 둘 다 높고, MDD 가 `JUDGE_MDD_SLACK` 넘게 나빠지지 않고(둘 다에 대해),
    (high52 − momentum) 월 초과의
    앞·뒤 반 평균이 모두 양. 공통 구간 리밸런스가 `JUDGE_MIN_REBALANCES` 미만이거나 지표가 비면 판정 불가.
    월 초과의 앞 반 = 앞쪽 ⌊n/2⌋ 달, 뒤 반 = 나머지. 기록 전용 — 이 값이 무엇을 켜지 않는다
    """
    out: dict[str, Any] = {"start": start, "verdict": "판정 불가"}
    if start is None or not all(k in results for k in ("high52", "momentum", "benchmark")):
        out["why"] = "상위 N 을 다 채운 리밸런스 없음" if start is None else "견줄 전략 없음"
        return out
    리밸 = sum(1 for r in results["high52"].rebalances if r.date >= start)
    out["rebalances"] = 리밸
    지표: dict[str, dict[str, float | None]] = {}
    for 이름 in ("high52", "momentum", "benchmark"):
        r = results[이름]
        조각 = bt.BacktestResult(curve=_자른(r.curve, start), rebalances=[x for x in r.rebalances if x.date >= start],
                               costs=r.costs)  # fmt: skip
        mt = summarize(조각, None, risk_free_annual=risk_free_annual).metrics
        지표[이름] = {"cagr": mt.cagr, "sharpe": mt.sharpe, "mdd": mt.mdd}
    out["metrics"] = 지표
    a, b = luck.monthly_returns(_자른(results["high52"].curve, start)), luck.monthly_returns(
        _자른(results["momentum"].curve, start))  # fmt: skip
    초과 = luck.excess_series(a, b)
    반 = len(초과) // 2
    앞, 뒤 = 초과[:반], 초과[반:]
    out["halves"] = [sum(앞) / len(앞) if 앞 else None, sum(뒤) / len(뒤) if 뒤 else None]
    if 리밸 < JUDGE_MIN_REBALANCES:
        out["why"] = f"공통 구간 리밸런스 {리밸} < {JUDGE_MIN_REBALANCES}"
        return out
    h = 지표["high52"]
    빈칸 = [f"{이름}.{k}" for 이름, x in 지표.items() for k, v in x.items() if v is None]
    if 빈칸 or None in out["halves"]:
        # 어느 칸인지 적는다 — 무위험수익률 설정이 비면 샤프가 늘 비어 다음 달에도 같은 까닭으로 불가다
        # (25.1646 교차검증)
        out["why"] = "지표 빈칸: " + ", ".join(빈칸 or ["월 초과 반"])
        if any(k.endswith(".sharpe") for k in 빈칸) and risk_free_annual is None:
            out["why"] += " — 무위험수익률 설정 없음"
        return out
    실패: list[str] = []
    for 대조 in ("momentum", "benchmark"):
        o = 지표[대조]
        if not h["cagr"] > o["cagr"]:  # type: ignore[operator]
            실패.append(f"CAGR ≤ {대조}")
        if not h["sharpe"] > o["sharpe"]:  # type: ignore[operator]
            실패.append(f"샤프 ≤ {대조}")
        if h["mdd"] < o["mdd"] - JUDGE_MDD_SLACK:  # type: ignore[operator]
            실패.append(f"MDD 가 {대조} 보다 2%p 넘게 나쁨")
    if not (out["halves"][0] > 0 and out["halves"][1] > 0):
        실패.append("앞·뒤 반 월 초과가 모두 양이 아님")
    out["verdict"] = "탈락" if 실패 else "통과"
    if 실패:
        out["why"] = 실패
    return out


def overlap_summary(vs_score: list[float | None], vs_12_1: list[float | None]) -> dict[str, Any]:
    """리밸런스마다 잰 스피어만의 평균 — 하나라도 `OVERLAP_MAX` 이상이면 계좌를 열지 않는다(전략 판정은 그대로)."""

    def 평균(xs: list[float | None]) -> float | None:
        ys = [x for x in xs if x is not None]
        return sum(ys) / len(ys) if ys else None

    # 문턱은 반올림 **전** 값으로 가른다 — 0.89996 이 0.9 로 올라 계좌를 닫던 것(25.1646 교차검증 [권고])
    a, b = 평균(vs_score), 평균(vs_12_1)
    return {"vs_momentum_score": None if a is None else round(a, 4),
            "vs_momentum_12_1": None if b is None else round(b, 4), "n": sum(1 for x in vs_score if x is not None),
            "open_account": None if a is None or b is None else (a < OVERLAP_MAX and b < OVERLAP_MAX)}  # fmt: skip
