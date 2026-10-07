"""한국투자증권 KIS Open API 가 클라우드(Actions 러너)에서 불리는지 살핀다 (docs/data-sources.md 3, infra 25.983).

2026-09-16 에 KIS 를 미룬 까닭이 "클라우드 IP 에서 호출되는지 미확인" 이었다. 사용자가 2026-10-07 키를 넣었다.
접근토큰 발급 → 국내 현재가(삼성전자) → 휴장일 조회 세 가지를 한 번씩 부르고, **값이 아니라 되는지**만 찍는다.
토큰·키는 절대 찍지 않는다. 주문 API 는 부르지 않는다. DB 를 건드리지 않는다.

실행
  KIS_APP_KEY=… KIS_APP_SECRET=… python scripts/probe_kis.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))


def _show(label: str, r: requests.Response, started: float, fields: dict[str, str]) -> None:
    ms = (time.monotonic() - started) * 1000
    try:
        body = r.json()
    except ValueError:
        print(f"{label}: HTTP {r.status_code} · {ms:.0f}ms · JSON 아님 {r.text[:120]!r}")
        return
    out = body.get("output") if isinstance(body.get("output"), dict) else None
    if out is None and isinstance(body.get("output"), list) and body["output"]:
        out = body["output"][0]
    shown = " · ".join(f"{k}={(out or {}).get(f)}" for k, f in fields.items())
    print(f"{label}: HTTP {r.status_code} · {ms:.0f}ms · rt_cd={body.get('rt_cd')} msg_cd={body.get('msg_cd')} "
          f"msg={str(body.get('msg1', ''))[:60]!r} · {shown}")  # fmt: skip


def main() -> int:
    key, secret = os.environ.get("KIS_APP_KEY", ""), os.environ.get("KIS_APP_SECRET", "")
    if not key or not secret:
        print("KIS_APP_KEY·KIS_APP_SECRET 가 비어 있다 — 시크릿 이름을 확인")
        return 1
    print(f"지금 {datetime.now(KST):%Y-%m-%d %H:%M} KST · 키 길이 {len(key)} · 시크릿 길이 {len(secret)}")

    # 발급은 1분 1회(EGW00133) — 직전 실행과 겹치면 한 번 기다렸다 다시 받는다
    for wait in (0, 65):
        time.sleep(wait)
        t0 = time.monotonic()
        r = requests.post(f"{BASE}/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": key,
                          "appsecret": secret}, timeout=20)  # fmt: skip
        if r.status_code != 403:
            break
    ms = (time.monotonic() - t0) * 1000
    try:
        tok = r.json()
    except ValueError:
        tok = {}
    token = tok.get("access_token")
    print(f"1. 접근토큰: HTTP {r.status_code} · {ms:.0f}ms · 받음={'예' if token else '아니오'} · "
          f"유효 {tok.get('expires_in')}초 · 오류 {tok.get('error_code') or tok.get('msg_cd')} "
          f"{str(tok.get('error_description') or tok.get('msg1') or '')[:80]!r}")  # fmt: skip
    if not token:
        return 1

    head = {"authorization": f"Bearer {token}", "appkey": key, "appsecret": secret, "custtype": "P"}
    time.sleep(0.5)
    t0 = time.monotonic()
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
                     params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"},
                     headers=head | {"tr_id": "FHKST01010100"}, timeout=20)  # fmt: skip
    _show("2. 현재가 005930", r, t0, {"현재가": "stck_prpr", "전일대비%": "prdy_ctrt", "누적거래량": "acml_vol"})

    time.sleep(0.5)
    t0 = time.monotonic()
    r = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/chk-holiday",
                     params={"BASS_DT": datetime.now(KST).strftime("%Y%m%d"), "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
                     headers=head | {"tr_id": "CTCA0903R"}, timeout=20)  # fmt: skip
    _show("3. 휴장일 조회(오늘)", r, t0, {"기준일": "bass_dt", "개장일": "opnd_yn", "거래일": "tr_day_yn"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
