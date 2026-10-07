"""한국투자증권 KIS Open API — 국내 휴장일 조회 (docs/data-sources.md 3, docs/infra.md 25.985).

`exchange_calendars` 는 사용자 기여로 유지돼 임시공휴일 반영이 늦을 수 있다(CLAUDE.md). 예전에는 대조할 상대가 없어
"판정을 남겨 두고 연 1회 거래소 공지와 대조" 만 했다(`core/calendar.record_decision`). 2026-10-07 사용자가 KIS 키를 넣어
공식 휴장일 조회(`chk-holiday`, tr `CTCA0903R`)를 쓸 수 있게 됐다 — 같은 날 Actions 러너에서 불리는 것을 확인했다
(`scripts/probe_kis.py`, 실행 37577384577: 2026-10-07 개장일 Y).

- **하루 한 번** 국내 일일 배치가 부른다. KIS 는 이 조회를 원장 서비스와 이어져 있다며 1일 1회 호출을 권한다
  `[확인필요: 원문 문구]`
- 접근토큰은 웹 장중 감시와 **같은 줄**(`api_tokens` name='kis')을 함께 쓴다 — 발급은 1분 1회 제한, 유효 24시간
- 시세는 받지 않는다. 주문 API·계좌번호는 쓰지 않는다
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import requests

SOURCE = "kis_openapi"
BASE = "https://openapi.koreainvestment.com:9443"
HOLIDAY_TR = "CTCA0903R"
TOKEN_NAME = "kis"
#: 토큰이 이만큼 남았으면 새로 받는다 — 웹(`web/lib/kis.KIS_TOKEN_REFRESH_MS`)과 같은 값
TOKEN_REFRESH = timedelta(hours=1)
TIMEOUT = 15


class KisFailed(RuntimeError):
    """키 없음·HTTP 오류·rt_cd≠0 — 값은 담지 않는다."""


@dataclass(frozen=True)
class DayStatus:
    day: date
    is_open: bool  # opnd_yn (개장일)


def configured(env: dict[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    return bool(e.get("KIS_APP_KEY") and e.get("KIS_APP_SECRET"))


def parse_holidays(payload: object) -> list[DayStatus]:
    """`chk-holiday` 응답 → 날짜별 개장 여부. rt_cd 가 0 이 아니면 못 받음."""
    if not isinstance(payload, dict) or str(payload.get("rt_cd")) != "0":
        code = payload.get("msg_cd") if isinstance(payload, dict) else None
        raise KisFailed(f"KIS 휴장일 조회 실패 {code or ''}".strip())
    out = []
    for row in payload.get("output") or []:
        ymd, yn = str(row.get("bass_dt") or ""), str(row.get("opnd_yn") or "")
        if len(ymd) == 8 and yn in ("Y", "N"):
            out.append(DayStatus(date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])), yn == "Y"))
    return out


def access_token(client, now: datetime | None = None) -> tuple[str, bool]:
    """(토큰, 새로 받았나). DB 에 둔 것이 충분히 남았으면 그것을 쓴다."""
    now = now or datetime.now(UTC)
    row = client.execute("SELECT token, expires_at FROM api_tokens WHERE name = ?", [TOKEN_NAME]).dicts()
    if row:
        expires = datetime.fromisoformat(str(row[0]["expires_at"]).replace("Z", "+00:00"))
        if expires - now > TOKEN_REFRESH:
            return str(row[0]["token"]), False
    response = requests.post(
        f"{BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": os.environ["KIS_APP_KEY"],
              "appsecret": os.environ["KIS_APP_SECRET"]},
        timeout=TIMEOUT,
    )  # fmt: skip
    try:
        body = response.json()
    except ValueError:
        body = {}
    token = body.get("access_token") if isinstance(body, dict) else None
    if response.status_code != 200 or not token:
        code = body.get("error_code", "") if isinstance(body, dict) else ""
        raise KisFailed(f"KIS 토큰 HTTP {response.status_code} {code}".strip())
    expires_at = (now + timedelta(seconds=int(body.get("expires_in") or 86_400))).isoformat()
    client.execute(
        "INSERT INTO api_tokens (name, token, expires_at, source, fetched_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (name) DO UPDATE SET token = excluded.token, expires_at = excluded.expires_at,"
        " fetched_at = excluded.fetched_at",
        [TOKEN_NAME, token, expires_at, SOURCE, now.isoformat()],
    )
    return str(token), True


def holidays(client, base: date) -> tuple[list[DayStatus], int]:
    """기준일부터의 개장 여부(한 쪽, 대개 몇 주치)와 나간 호출 수."""
    token, issued = access_token(client)
    response = requests.get(
        f"{BASE}/uapi/domestic-stock/v1/quotations/chk-holiday",
        params={"BASS_DT": base.strftime("%Y%m%d"), "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
        headers={"authorization": f"Bearer {token}", "appkey": os.environ["KIS_APP_KEY"],
                 "appsecret": os.environ["KIS_APP_SECRET"], "tr_id": HOLIDAY_TR, "custtype": "P"},
        timeout=TIMEOUT,
    )  # fmt: skip
    if response.status_code != 200:
        raise KisFailed(f"KIS 휴장일 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise KisFailed("KIS 휴장일 응답이 JSON 이 아닙니다") from exc
    return parse_holidays(payload), 1 + int(issued)
