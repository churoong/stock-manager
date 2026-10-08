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


# ---------------------------------------------------------------------------------------------------------------------
# 수급 일별 (docs/infra.md 25.987) — 투자자별 · 공매도 · 신용.
# 응답 모양은 2026-10-07 실측(`scripts/probe_kis_catalog.py`)
# ---------------------------------------------------------------------------------------------------------------------

Q = "/uapi/domestic-stock/v1/quotations/"
#: 실전 REST 초당 20건(공식). 2026-10-07 첫 수집(초당 15건, 스레드 8)에서 3,504회 중 80회가
#: EGW00201(초당 거래건수 초과)이었다
#: — KIS 쪽 집계 창이 우리 1초 창과 어긋나는 것으로 본다. 10건으로 낮추고 걸리면 쉬었다 다시 묻는다 (25.993)
PER_SECOND = 10
#: 초당 한도 오류 코드와 다시 묻는 횟수
RATE_LIMIT_CODE = "EGW00201"
RATE_RETRY = 2


def _int(v: object) -> int | None:
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _float(v: object) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _iso(ymd: object) -> str | None:
    s = str(ymd or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def _list(payload: object, key: str) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("rt_cd")) != "0":
        code = payload.get("msg_cd") if isinstance(payload, dict) else None
        raise KisFailed(f"KIS 응답 실패 {code or ''}".strip())
    v = payload.get(key)
    return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []


def parse_investor(payload: object) -> dict[str, dict]:
    """투자자별 (`FHKST01010900`, output 30행) → 날짜 → 칸. 대금은 백만원."""
    out = {}
    for r in _list(payload, "output"):
        day = _iso(r.get("stck_bsop_date"))
        if day and r.get("frgn_ntby_qty") not in (None, ""):
            out[day] = {
                "frgn_net_qty": _int(r.get("frgn_ntby_qty")), "orgn_net_qty": _int(r.get("orgn_ntby_qty")),
                "prsn_net_qty": _int(r.get("prsn_ntby_qty")), "frgn_net_amt": _int(r.get("frgn_ntby_tr_pbmn")),
                "orgn_net_amt": _int(r.get("orgn_ntby_tr_pbmn")), "prsn_net_amt": _int(r.get("prsn_ntby_tr_pbmn")),
            }  # fmt: skip
    return out


def parse_short(payload: object) -> dict[str, dict]:
    """공매도 일별 (`FHPST04830000`, output2 최대 100행)."""
    out = {}
    for r in _list(payload, "output2"):
        day = _iso(r.get("stck_bsop_date"))
        if day:
            out[day] = {"short_qty": _int(r.get("ssts_cntg_qty")), "short_vol_pct": _float(r.get("ssts_vol_rlim"))}
    return out


def parse_credit(payload: object) -> dict[str, dict]:
    """신용잔고 일별 (`FHPST04760000`, output 30행). 날짜는 매매일(`deal_date`) — 결제일이 아니다."""
    out = {}
    for r in _list(payload, "output"):
        day = _iso(r.get("deal_date"))
        if day:
            out[day] = {"credit_rmnd_qty": _int(r.get("whol_loan_rmnd_stcn")),
                        "credit_rmnd_pct": _float(r.get("whol_loan_rmnd_rate"))}  # fmt: skip
    return out



# ---------------------------------------------------------------------------------------------------------------------
# 투자의견 · 기업행위 일정 (docs/infra.md 25.988)
# ---------------------------------------------------------------------------------------------------------------------

KSD = "/uapi/domestic-stock/v1/ksdinfo/"
#: 기업행위 종류 → (경로, tr_id). 실측 2026-10-07 — 액면교체는 `CTS` 인자가 없으면 OPSQ2001 로 거절한다
EVENT_APIS = {
    "dividend": ("dividend", "HHKDB669102C0"),
    "bonus": ("bonus-issue", "HHKDB669101C0"),
    "rights": ("paidin-capin", "HHKDB669100C0"),
    "split": ("rev-split", "HHKDB669104C0"),
}


def parse_opinions(payload: object) -> list[dict]:
    out = []
    for r in _list(payload, "output"):
        day, broker = _iso(r.get("stck_bsop_date")), str(r.get("mbcr_name") or "").strip()
        if day and broker:
            target = _float(r.get("hts_goal_prc"))
            out.append({"date": day, "broker": broker, "opinion": str(r.get("invt_opnn") or "").strip() or None,
                        "opinion_code": _int(r.get("invt_opnn_cls_code")),
                        "prev_opinion_code": _int(r.get("rgbf_invt_opnn_cls_code")),
                        "target_price": target if target and target > 0 else None})  # fmt: skip
    return out


def parse_events(kind: str, payload: object) -> list[dict]:
    """기업행위 일정 한 종류. **네 종류 모두 output1** 에 온다
    (25.994 — 처음에는 배당·무상·유상을 output 으로 읽어 0건이었다).
    한 번에 100행까지 온다(배당 지난30~앞90일 실측 100행에서 잘림) — 부른 쪽이 기간을 좁힌다."""
    rows = _list(payload, "output1")
    out = []
    for r in rows:
        day, code = _iso(r.get("record_date")), str(r.get("sht_cd") or "").strip()
        if day and len(code) == 6:
            name = r.get("isin_name") or r.get("opp_cust_nm") or r.get("cust_nm")
            out.append({"code": code, "kind": kind, "record_date": day, "name": str(name or "").strip() or None,
                        "detail": r})  # fmt: skip
    return out


class QuoteClient:
    """시세 조회 묶음. 초당 `PER_SECOND` 건을 넘지 않게 여러 스레드가 한 문을 지난다."""

    def __init__(self, token: str) -> None:
        import threading

        self.head = {"authorization": f"Bearer {token}", "appkey": os.environ["KIS_APP_KEY"],
                     "appsecret": os.environ["KIS_APP_SECRET"], "custtype": "P"}  # fmt: skip
        self.session = requests.Session()
        self.calls = 0
        self._lock = threading.Lock()
        self._stamps: list[float] = []

    def _gate(self) -> None:
        import time

        while True:
            with self._lock:
                now = time.monotonic()
                self._stamps = [t for t in self._stamps if now - t < 1.0]
                if len(self._stamps) < PER_SECOND:
                    self._stamps.append(now)
                    self.calls += 1
                    return
            time.sleep(0.05)

    def get(self, path: str, tr: str, params: dict[str, str]) -> object:
        import time

        for attempt in range(RATE_RETRY + 1):
            self._gate()
            r = self.session.get(BASE + path, params=params, headers=self.head | {"tr_id": tr}, timeout=TIMEOUT)
            try:
                body = r.json()
            except ValueError as exc:
                raise KisFailed(f"KIS HTTP {r.status_code} JSON 아님") from exc
            if r.status_code != 200 and not isinstance(body, dict):
                raise KisFailed(f"KIS HTTP {r.status_code}")
            # 초당 한도(EGW00201)면 잠깐 쉬고 다시 — 다른 오류는 그대로 돌려 부른 쪽이 판단한다 (25.993)
            if isinstance(body, dict) and body.get("msg_cd") == RATE_LIMIT_CODE and attempt < RATE_RETRY:
                time.sleep(1.0 + attempt)
                continue
            return body
        return body

    def investor(self, code: str) -> dict[str, dict]:
        return parse_investor(self.get(Q + "inquire-investor", "FHKST01010900",
                                       {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}))  # fmt: skip

    def short(self, code: str, since: date, until: date) -> dict[str, dict]:
        return parse_short(self.get(Q + "daily-short-sale", "FHPST04830000", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": since.strftime("%Y%m%d"), "FID_INPUT_DATE_2": until.strftime("%Y%m%d")}))  # fmt: skip

    def credit(self, code: str, until: date) -> dict[str, dict]:
        return parse_credit(self.get(Q + "daily-credit-balance", "FHPST04760000", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20476", "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": until.strftime("%Y%m%d")}))  # fmt: skip

    def opinions(self, code: str, since: date, until: date) -> list[dict]:
        return parse_opinions(self.get(Q + "invest-opinion", "FHKST663300C0", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633", "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": since.strftime("%Y%m%d"), "FID_INPUT_DATE_2": until.strftime("%Y%m%d")}))  # fmt: skip


    def events(self, kind: str, since: date, until: date, code: str = "") -> list[dict]:
        """`code` 를 주면 그 종목만(`SHT_CD`) — 시장 전체가 100행에서 잘리는 날에도 보유 종목이 빠지지 않게
        (25.1008)."""
        path, tr = EVENT_APIS[kind]
        f, t = since.strftime("%Y%m%d"), until.strftime("%Y%m%d")
        params = {
            "dividend": {"CTS": "", "GB1": "0", "F_DT": f, "T_DT": t, "SHT_CD": code, "HIGH_GB": ""},
            "bonus": {"CTS": "", "F_DT": f, "T_DT": t, "SHT_CD": code},
            "rights": {"CTS": "", "GB1": "1", "F_DT": f, "T_DT": t, "SHT_CD": code},
            "split": {"CTS": "", "SHT_CD": code, "MARKET_GB": "0", "F_DT": f, "T_DT": t},
        }[kind]
        return parse_events(kind, self.get(KSD + path, tr, params))

    def etf_components(self, code: str) -> list[tuple[str, float]]:
        """국내 ETF 구성종목 **상위 30**(`FHKST121600C0`, output2) → [(단축코드, 비중 %)] (25.990).

        2026-10-07 실측: `etf_cnfg_issu_rlim` 이 비중 %(KODEX 200 삼성전자 34.63 — KODEX 공시 34.46).
        30종목 합은 코스피200 ETF 에서 84.94% 다. 해외지수 ETF 는 목록이 비어 온다."""
        payload = self.get(
            "/uapi/etfetn/v1/quotations/inquire-component-stock-price", "FHKST121600C0",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_COND_SCR_DIV_CODE": "11216"},
        )  # fmt: skip
        out = []
        for r in _list(payload, "output2"):
            c, w = str(r.get("stck_shrn_iscd") or "").strip(), _float(r.get("etf_cnfg_issu_rlim"))
            if len(c) == 6 and w and w > 0:
                out.append((c, w))
        return out

    def after_hours(self, code: str) -> dict | None:
        """시간외 단일가 (`FHPST02300000`, output) → {가격, 전일 대비 %, 거래량}.

        시간외 체결이 없으면(가격 0) None (25.992)."""
        payload = self.get(Q + "inquire-overtime-price", "FHPST02300000",
                           {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})  # fmt: skip
        o = payload.get("output") if isinstance(payload, dict) else None
        if str(payload.get("rt_cd") if isinstance(payload, dict) else "") != "0" or not isinstance(o, dict):
            raise KisFailed(f"KIS 시간외 실패 {payload.get('msg_cd') if isinstance(payload, dict) else ''}".strip())
        price, pct = _float(o.get("ovtm_untp_prpr")), _float(o.get("ovtm_untp_prdy_ctrt"))
        vol = _int(o.get("ovtm_untp_vol"))
        if not price or price <= 0 or pct is None:
            return None
        return {"price": price, "change_pct": pct, "volume": vol}
