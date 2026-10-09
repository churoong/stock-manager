"""15회차 후보 기법 — 2026-10-09 사용자 지시분 (docs/factors.md 12.17, docs/infra.md 25.1069).

사용자가 "점수에 더할 기법은 다 반영해줘" 라고 지시한 여덟 가운데 백테스트로 잴 수 있는 여섯의 계산.
**정의·창·방향은 12.17 에 결과를 보기 전에 적었다 — 바꾸지 않는다.**
실제 추천(종합 점수·신호)에는 쓰지 않는다(가중치 0).

- S1 `ic_weights`     IC 가중 결합 — 끝난 기간의 팩터 IC 확장 평균으로 가중, 현행 가중으로 수축
- S2 `shrink_missing` 정보가 적은 종목 수축 — 팩터 점수를 살아 있는 지표 몫만큼 50 쪽으로
- S3 `lt_reversal`    장기 반전 — −(P[t−252] ÷ P[t−756] − 1)
- S4 `tp_dispersion`  목표가 분산도 — −(모표준편차 ÷ 중앙값), 기록 전용
- S7 `credit_balance` 신용잔고율 — −(기준일 2거래일 전 이하 마지막 값)
- S8 `retail_flow`    개인 순매수 쏠림 — −(20거래일 개인 순매수 ÷ 거래대금), 기록 전용
S5(업종 선도-지연)·S6(커버리지 개시)는 두 검증 모두 탈락이라 IC 로 넣지 않고 종목 분석의 사실 줄로 넣었다.
계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from datetime import date

from batch.services import factor_ic as fic
from batch.services.scoring import MARKET_CAP_STALE_DAYS

# ----------------------------------------------------------------------
# S3. 장기 반전 (De Bondt·Thaler 1985)
# ----------------------------------------------------------------------

#: 장기 반전의 가까운 끝·먼 끝(거래일). 최근 1년(12-1 모멘텀의 창)을 건너뛰고 그 앞 2년을 본다
LT_NEAR = 252
LT_FAR = 756
#: 국내 가격제한(±30%) 밖의 하루 움직임 — 수정 안 된 분할·병합 흔적이라 그 창은 NULL (docs/adjust.md)
LT_KR_LIMIT = 0.30


def lt_reversal(closes: list[float], kr: bool) -> float | None:
    """`closes` 는 수정종가, 날짜 오름차순(마지막이 기준일). 3년 전→1년 전 수익의 반대 부호(+: 덜 오른 쪽이 크다)."""
    if len(closes) < LT_FAR + 1:
        return None
    far, near = closes[-1 - LT_FAR], closes[-1 - LT_NEAR]
    if not far or not near or far <= 0 or near <= 0:
        return None
    if kr:
        창 = closes[-1 - LT_FAR : len(closes) - LT_NEAR]
        for a, b in zip(창, 창[1:], strict=False):
            if a and b and a > 0 and abs(b / a - 1) > LT_KR_LIMIT:
                return None
    return -(near / far - 1)


# ----------------------------------------------------------------------
# S7. 신용잔고율 · S8. 개인 순매수 쏠림 (국내 `kr_flows`)
# ----------------------------------------------------------------------

#: 신용잔고율을 기준일보다 이만큼 앞선 거래일까지만 본다 — 공표 시차 `[확인필요]` 를 넉넉히 덮는다
CREDIT_LAG_DAYS = 2
#: 개인 순매수를 합하는 거래일
RETAIL_DAYS = 20


def credit_balance(rows: list[tuple[str, float]], trade_dates: list[str]) -> float | None:
    """rows = (매매일, 신용잔고율 %) 오름차순 아니어도 된다. trade_dates = 그 종목의 기준일 이하 거래일(오름차순).
    기준일 `CREDIT_LAG_DAYS` 거래일 전 이하의 마지막 값, 그날보다 `MARKET_CAP_STALE_DAYS` 일 넘게 묵었으면 None.
    점수 = −값."""
    if len(trade_dates) <= CREDIT_LAG_DAYS:
        return None
    끝 = trade_dates[-1 - CREDIT_LAG_DAYS]
    아는 = [(d, v) for d, v in rows if d <= 끝 and v is not None]
    if not 아는:
        return None
    d, v = max(아는)
    if (date.fromisoformat(끝[:10]) - date.fromisoformat(d[:10])).days > MARKET_CAP_STALE_DAYS:
        return None
    return -float(v)


def retail_flow(net_by_date: dict[str, float | None], trade_dates: list[str],
                values: list[float | None]) -> float | None:  # fmt: skip
    """최근 `RETAIL_DAYS` 거래일 Σ개인 순매수 대금(백만원 → 원으로 ×10⁶) ÷ Σ거래대금(원).
    하루라도 비면 None. 점수 = −값."""
    if len(trade_dates) < RETAIL_DAYS or len(values) != len(trade_dates):
        return None
    순, 대금 = 0.0, 0.0
    for d, v in zip(trade_dates[-RETAIL_DAYS:], values[-RETAIL_DAYS:], strict=True):
        n = net_by_date.get(d)
        if n is None or v is None or v <= 0:
            return None
        순 += float(n) * 1e6
        대금 += float(v)
    return -(순 / 대금)


# ----------------------------------------------------------------------
# S4. 목표가 분산도 (Diether·Malloy·Scherbina 2002)
# ----------------------------------------------------------------------

#: 목표가를 모으는 달력일 — 일일 의견의 증권사 목표가(9.3)와 같은 창
TP_WINDOW_DAYS = 90
#: 분산을 말할 최소 증권사 수
TP_MIN_BROKERS = 3


def tp_dispersion(opinions: list[tuple[str, str, float]], cutoff: str,
                  adj_factor: Callable[[str], float | None]) -> float | None:  # fmt: skip
    """opinions = (발표일, 증권사, 목표가 원값). (cutoff − 90일, cutoff] 증권사마다 마지막 목표가를 **그날 수정 계수**
    (수정종가 ÷ 원 종가)로 수정주가 기준에 옮긴 뒤 −(모표준편차 ÷ 중앙값).
    증권사가 3곳 미만이거나 계수를 모르면 None."""
    시작 = date.fromordinal(date.fromisoformat(cutoff[:10]).toordinal() - TP_WINDOW_DAYS).isoformat()
    마지막: dict[str, tuple[str, float]] = {}
    for d, b, x in sorted(opinions):
        if 시작 < d <= cutoff and x and x > 0:
            마지막[b] = (d, x)
    if len(마지막) < TP_MIN_BROKERS:
        return None
    값 = []
    for d, x in 마지막.values():
        k = adj_factor(d)
        if k is None or k <= 0:
            return None
        값.append(x * k)
    med = statistics.median(값)
    return -(statistics.pstdev(값) / med) if med > 0 else None


# ----------------------------------------------------------------------
# S1. IC 가중 결합 · S2. 정보가 적은 종목 수축
# ----------------------------------------------------------------------

#: IC 가중의 수축 — λ = n ÷ (n + 이 값). 켜는 기준의 IC 최소 달 수(36)를 그대로 쓴다: 36기간 쌓이면 반반
IC_SHRINK_PRIOR = fic.MIN_MONTHS


def ic_weights(fixed: dict[str, float], ic_history: dict[str, list[float | None]]) -> dict[str, float]:
    """끝난 기간들의 팩터별 IC(시장 안) 확장 평균 ĪC 로 w = (1−λ)·현행 + λ·합·max(ĪC,0)/Σmax.
    n = 값이 있는 기간 수의 팩터 최소값(가장 덜 쌓인 팩터에 맞춘다). Σmax = 0 이거나 n = 0 이면 현행 그대로."""
    평균: dict[str, float] = {}
    n = None
    for f in fixed:
        xs = [x for x in ic_history.get(f, []) if x is not None]
        n = len(xs) if n is None else min(n, len(xs))
        평균[f] = sum(xs) / len(xs) if xs else 0.0
    합 = sum(fixed.values())
    양 = {f: max(0.0, v) for f, v in 평균.items()}
    if not n or sum(양.values()) <= 0:
        return dict(fixed)
    lam = n / (n + IC_SHRINK_PRIOR)
    return {f: (1 - lam) * fixed[f] + lam * 합 * 양[f] / sum(양.values()) for f in fixed}


def shrink_missing(score: float | None, alive: int, registered: int) -> float | None:
    """팩터 점수 f → 50 + (f − 50)·(살아 있는 지표 수 ÷ 등록 지표 수). 점수가 없거나 등록 지표가 0 이면 그대로."""
    if score is None or registered <= 0:
        return score
    return 50 + (score - 50) * max(0, alive) / registered
