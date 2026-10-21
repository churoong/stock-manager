"""하루 손실 관문의 빈도 — 핵심 H 실행 기록 진단, 보고만 (docs/paper.md 11장 "하루 손실 한도",
docs/factors.md 12.35 32회차 P4, docs/infra.md 25.1552).

`risk_gate.DAILY_LOSS_HALT_PCT` 는 "운영 1년 뒤 걸린 날 수로 다시 본다" 고 미뤄 둔 문턱이다.
핵심 H 는 이미 날마다 이 관문을 돌리므로 5년치 빈도를 바로 낼 수 있다. **결정 규칙은 없다**(32회차 검증 B —
관문은 시세 오류·분할 미반영을 막는 장치인데 핵심 H 는 수정주가라 막아 주는 이득이 구조상 0 이다.
비용만 잴 수 있는 곳에서 재면 늘 "풀자" 쪽으로 기운다).

**운영 빈도의 하한으로만 읽는다** — 하루 수익은 직전 cutoff 비중 × 종가 변화로 근사한 국내 다섯 종목 값이고
(운영은 계좌 전체·국내+미국·환율 포함 시간가중), 결함 없는 환경(수정주가)이다(32회차 검증 A). 읽기 0.
"""

from __future__ import annotations

import bisect
from typing import Any

from batch.services import risk_gate as rg

#: 막힌 날 가운데 "시장 전체가 빠진 날" 로 세는 KOSPI 하루 수익(%) 문턱 — 사실로만 쓰는 구분(32회차 제안 문구)
LOSS_GATE_KOSPI_DOWN_PCT = -2.0


def frequency(logs: list[Any], kospi: dict[str, float]) -> dict[str, Any]:
    """하루 기록(`DayLog`)·KOSPI → 실행 기록 `loss_gate`.

    막힘은 사유 글이 아니라 코드 기준(`day_return_pct < DAILY_LOSS_HALT_PCT` — 관문과 같은 "넘게", 25.1632).
    KOSPI 하루 수익은 하루 수익과 같은 창
    (직전 cutoff → 이번 cutoff). 시장 조정 = 하루 수익 − 투자 비중 × KOSPI 하루 수익 —
    그 한도였다면 걸린 날 수를 사실로만
    """
    날 = sorted(kospi)
    값 = [float(kospi[d]) for d in 날]

    def upto(d: str) -> float | None:
        k = bisect.bisect_right(날, d)
        return 값[k - 1] if k else None

    한도 = rg.DAILY_LOSS_HALT_PCT
    n = 막힘 = 지수도 = 후보 = 조정 = 원래만 = 조정만 = 0
    앞cut: str | None = None
    for log in logs:
        r = log.day_return_pct
        if r is not None and 앞cut is not None:
            n += 1
            a, b = upto(앞cut), upto(str(log.cutoff))
            ir = (b / a - 1) * 100 if a and b else None
            걸림 = r < 한도
            adj = ir is not None and log.invested is not None and r - log.invested * ir < 한도
            if 걸림:
                막힘 += 1
                지수도 += ir is not None and ir <= LOSS_GATE_KOSPI_DOWN_PCT
                후보 += any(d.get("kind") == "blocked" for d in log.decisions)
                원래만 += ir is not None and not adj  # 지수를 모르면 조정 한도로 가를 수 없다(25.1556)
            조정 += adj
            조정만 += adj and not 걸림
        앞cut = str(log.cutoff)
    return {
        "days": n, "blocked_days": 막힘, "blocked_pct": round(100 * 막힘 / n, 2) if n else None,
        "kospi_down_days": 지수도, "with_candidates": 후보, "adjusted_days": 조정, "only_raw": 원래만,
        "only_adjusted": 조정만,
        "threshold_pct": 한도, "kospi_down_pct": LOSS_GATE_KOSPI_DOWN_PCT,
    }  # fmt: skip
