"""보유 종목 시간외 단일가 알림 (docs/intraday.md 1.2, docs/infra.md 25.992).

장 마감 뒤 공시·뉴스는 다음 날 아침에야 가격에 드러난다. 시간외 단일가(16:00~18:00)가 이미 크게 움직였으면 그날 저녁에
알면 다음 날 장 시작 전에 판단할 수 있다. 문턱은 장중 급등락과 같은 설정값(`alert_thresholds.spike_pct`, 기본 5%).
알림은 장중 알림과 같은 표(`alerts`, 트리거 `after_hours`)에 남겨 하루 한 번을 DB 가 지킨다.
"""

from __future__ import annotations

import json

#: 장중 급등락 기본 문턱과 같다 (web/lib/settings.ts DEFAULT_SETTINGS.alert_thresholds.spike_pct)
DEFAULT_SPIKE_PCT = 5.0
TRIGGER = "after_hours"


def spike_pct(raw: str | None) -> float:
    """설정 `alert_thresholds` JSON 의 spike_pct. 없거나 깨졌거나 범위(0.1~50) 밖이면 기본값."""
    try:
        v = float((json.loads(raw or "{}") or {}).get("spike_pct"))
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_SPIKE_PCT
    return v if 0.1 <= v <= 50 else DEFAULT_SPIKE_PCT


def message(name: str, code: str, q: dict) -> str:
    """알림 한 줄."""
    return f"{name}({code}) 시간외 단일가 {q['change_pct']:+.1f}% · {q['price']:,.0f}원" + (
        f" · 거래량 {q['volume']:,}주" if q.get("volume") else ""
    )


def hits(holdings: list[tuple[int, str, str]], quotes: dict[str, dict | None], threshold: float) -> list[dict]:
    """(stock_id, 코드, 이름) 보유 × 시간외 시세 → 알림. 문턱은 "이상"(장중과 같다, 25.726)."""
    out = []
    for sid, code, name in holdings:
        q = quotes.get(code)
        if q and abs(q["change_pct"]) >= threshold - 1e-9:
            out.append({"stock_id": sid, "code": code, "message": message(name, code, q),
                        "data": {**q, "threshold_pct": threshold}})  # fmt: skip
    return out
