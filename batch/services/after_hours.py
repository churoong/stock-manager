"""보유 종목 시간외 단일가 알림 (docs/intraday.md 1.2, docs/infra.md 25.992).

장 마감 뒤 공시·뉴스는 다음 날 아침에야 가격에 드러난다. 시간외 단일가(16:00~18:00)가 이미 크게 움직였으면 그날 저녁에
알면 다음 날 장 시작 전에 판단할 수 있다. 문턱은 장중 급등락과 같은 설정값(`alert_thresholds.spike_pct`, 기본 5%).
알림은 장중 알림과 같은 표(`alerts`, 트리거 `after_hours`)에 남겨 하루 한 번을 DB 가 지킨다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from batch.core import calendar as cal

KST = ZoneInfo("Asia/Seoul")

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


#: 시간외 단일가가 끝나는 시각(KST). 이보다 이르면 그날 시간외는 아직 진행 중이다
CLOSE_KST = time(18, 0)
#: 시간외 단일가가 시작하는 시각(KST)
OPEN_KST = time(16, 0)
#: 조용시간 기본값 — web/lib/settings.ts DEFAULT_SETTINGS.quiet_hours 와 같다
DEFAULT_QUIET = {"enabled": True, "start": "00:00", "end": "07:00", "deliver_on_release": True}
QUIET_SKIPPED = "quiet-skipped"  # 웹 장중 경로의 같은 이름과 같다


def session_day(now_utc: datetime) -> date | None:
    """이 시각에 **마감된** 가장 최근 시간외 단일가의 거래일 (25.1012). 진행 중(16:00~18:00 KST)이면 None.

    GitHub 예약이 7시간 늦게(01:25 KST) 돌아 `user_today()` 로 다음 날 날짜를 붙였다 — 시세는 전 거래일 것이다."""
    kst = now_utc.astimezone(KST)
    d = kst.date()
    if cal.is_session("KR", d):
        if OPEN_KST <= kst.time() < CLOSE_KST:
            return None
        if kst.time() >= CLOSE_KST:
            return d
    return cal.previous_session("KR", d)


def quiet_state(raw: str | None, now_utc: datetime) -> tuple[bool, bool]:
    """(지금 조용시간인가, 해제 뒤 보내나). 웹 `inQuietHours` 와 같은 판정 — 시작=끝이면 조용시간 없음."""
    try:
        q = {**DEFAULT_QUIET, **(json.loads(raw or "{}") or {})}
    except (TypeError, ValueError):
        q = dict(DEFAULT_QUIET)
    if not q.get("enabled"):
        return False, bool(q.get("deliver_on_release", True))
    hm = now_utc.astimezone(KST).strftime("%H:%M")
    start, end = str(q.get("start")), str(q.get("end"))
    if start == end:
        return False, True
    quiet = start <= hm < end if start < end else (hm >= start or hm < end)
    return quiet, bool(q.get("deliver_on_release", True))
