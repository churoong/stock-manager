"""손절 거리를 변동성 단위로 — 가상모의투자 매수 결정 행의 기록 (docs/paper.md 9장 "손절 거리 σ",
docs/factors.md 12.34 31회차 P2, docs/infra.md 25.1539).

고정 % 손절(단기 −7·중기 −15·장기 −25)이 그 종목 변동성에 비해 얼마나 가까운지를 **매수할 때** 적는다.
손절로 판 뒤 다시 오르는 "털림" 이 손절 거리가 가까운 쪽에 몰리는지를 나중에 판정한다(결정 규칙은 paper.md 9장).
기록만 — 매도 문턱은 바꾸지 않는다. 시세는 2부가 이미 메모리에 든 상관용 계열
(`daily.load_price_series` — 달력 206일, 약 140 거래일 수정종가)이라 읽기 0.
"""

from __future__ import annotations

import math
from typing import Any

from batch.core import markets

#: σ 를 내는 일간 수익 수 하한 — 2부 상관 계열(약 140 거래일)의 절반쯤. 그보다 적으면 모름
STOP_VOL_MIN_RETURNS = 60
#: 국내 하루 등락 제한(±30%)을 넘는 날은 수정 안 된 분할·병합으로 보고 뺀다(16회차 규칙) — 미국은 제한이 없다
STOP_VOL_KR_JUMP = markets.KR_DAILY_LIMIT  # 정의처 core/markets(25.1756 — 다섯째 사본이었다)
#: 손절 거리를 견주는 기간 — 성과 분해의 20 거래일과 같은 한 달
STOP_VOL_HORIZON_DAYS = 20


def stop_vol(series: dict[str, float] | None, stop_pct: float | None, country: str) -> dict[str, Any]:
    """{sigma_d, n, stop_pct, stop_z, from, to} 또는 {unknown: 까닭}.

    σ_d = 일간 로그수익 표준편차(표본, n−1), stop_z = |stop_pct|/100 ÷ (σ_d·√20) — 손절 거리가 한 달치 σ 의 몇 배인가
    """
    if stop_pct is None:
        return {"unknown": "손절폭 없음"}
    if not series:
        return {"unknown": "시세 계열 없음"}
    날 = sorted(d for d, c in series.items() if c and c > 0)
    수익: list[float] = []
    뺀 = 0
    for a, b in zip(날, 날[1:], strict=False):
        r = series[b] / series[a] - 1
        if country == "KR" and abs(r) > STOP_VOL_KR_JUMP:
            뺀 += 1
            continue
        수익.append(math.log(1 + r))
    if len(수익) < STOP_VOL_MIN_RETURNS:
        return {"unknown": f"수익 {len(수익)}개 < {STOP_VOL_MIN_RETURNS}"}
    m = sum(수익) / len(수익)
    sd = math.sqrt(sum((x - m) ** 2 for x in 수익) / (len(수익) - 1))
    if sd <= 0:
        return {"unknown": "변동 없음"}
    out: dict[str, Any] = {
        "sigma_d": round(sd, 6), "n": len(수익), "stop_pct": stop_pct,
        "stop_z": round(abs(stop_pct) / 100 / (sd * math.sqrt(STOP_VOL_HORIZON_DAYS)), 4), "from": 날[0], "to": 날[-1],
    }  # fmt: skip
    if 뺀:
        out["jumps"] = 뺀
    return out
