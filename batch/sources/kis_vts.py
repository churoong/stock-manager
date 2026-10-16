"""한국투자증권 KIS **모의투자**(VTS) — 주문·체결 조회·잔고 (docs/paper.md 15장, docs/infra.md 25.1475).

2026-10-15 사용자 제안("한국투자증권 모의투자 계좌를 연결해서 … 모의투자를 해보는건 어때?")으로 쓴다. 가상 돈이다 —
가상모의투자 H(지주사, 처음엔 B)의 주문을 그대로 베껴 내고, 실제 주문 경로(토큰·주문·거부·체결·잔고)를
돈 없이 지나 본다.

**실계좌로 새지 않게** (CLAUDE.md — 실계좌 주문은 단계 관문 뒤 사용자 결정):
- 도메인은 모의 하나만 상수로 둔다. 요청 직전에 주소가 그 도메인으로 시작하지 않으면 `KisFailed`
- tr_id 는 모의(`V…`)만 둔다. 실전 주문 tr_id 는 이 저장소에 없다(tests/test_kis_vts_1475.py 가 막는다)
- 키는 모의 전용 이름(`KIS_VTS_*`)만 읽는다 — 실전 키(`KIS_APP_KEY`)를 읽지 않는다.
  토큰 줄도 따로(`api_tokens.name='kis_vts'`)

경로·tr_id·칸 이름은 공식 예제 저장소 `koreainvestment/open-trading-api`(main, 2026-10-14 커밋 2077773)의
`examples_llm/domestic_stock/{order_cash,inquire_daily_ccld,inquire_balance}` 와 `kis_devlp.yaml` 에서 옮겼다.
모의 호출 한도는 공식 숫자가 없다 — README 는 "모의투자 계좌는 REST API 호출 제한이 낮습니다" 뿐, 공식 예제의 모의
간격은 0.5초 `[확인필요]`.
계좌번호·수량·금액은 로그에 찍지 않는다. 예외 문장에는 KIS 오류 코드와 KIS 가 준 문장만 담는다
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from batch.sources import kis
from batch.sources.kis import KisFailed

SOURCE = "kis_vts"
#: 모의투자 REST 도메인 (kis_devlp.yaml `vps`). 실전 도메인(`kis.BASE`)과 다르다 — 이 모듈은 이것만 쓴다
BASE = "https://openapivts.koreainvestment.com:29443"
TOKEN_NAME = "kis_vts"
KEY_ENV, SECRET_ENV, ACCOUNT_ENV = "KIS_VTS_APP_KEY", "KIS_VTS_APP_SECRET", "KIS_VTS_ACCOUNT"
#: 모의 tr_id (order_cash.py L110-116 · inquire_balance.py L118 · inquire_daily_ccld.py L150 — 3개월 이내)
TR_BUY, TR_SELL = "VTTC0012U", "VTTC0011U"
TR_BALANCE, TR_DAILY_CCLD = "VTTC8434R", "VTTC0081R"
ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
#: 호출 사이 최소 간격(초) — 공식 예제의 모의 간격 0.5초보다 넉넉히. 하루 수십 건이라 느려도 몇십 초다
MIN_INTERVAL = 0.6
#: 연속 조회(다음 쪽) 최대 — 잔고는 모의에서 한 번에 20건(inquire_balance.py L118 주석)
MAX_PAGES = 10
#: 체결 조회 쪽 최대 — 모의는 한 쪽에 15건쯤이라(검증 보고) 며칠 밀린 맞추기도 담게 넉넉히
CCLD_MAX_PAGES = 30
#: 일별 주문체결(VTTC0081R)은 3개월 안만 — 그보다 오래 맞추지 못한 줄은 `unknown` 으로 닫는다
CCLD_MAX_DAYS = 89
TIMEOUT = 15


def _vars(env: dict[str, str] | None) -> dict[str, str]:
    """모의 전용 세 칸만 — 실전 키(`KIS_APP_KEY`)는 읽지 않는다.

    이름을 글자로 적어 둔다(tests/test_env_example.py 가 읽는 환경변수를 글자로 훑는다)
    """
    if env is not None:
        return env
    return {
        "KIS_VTS_APP_KEY": os.environ.get("KIS_VTS_APP_KEY", ""),
        "KIS_VTS_APP_SECRET": os.environ.get("KIS_VTS_APP_SECRET", ""),
        "KIS_VTS_ACCOUNT": os.environ.get("KIS_VTS_ACCOUNT", ""),
    }


def configured(env: dict[str, str] | None = None) -> bool:
    e = _vars(env)
    return bool(e.get(KEY_ENV) and e.get(SECRET_ENV) and account(e))


def account(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """(종합계좌번호 8자리, 상품코드 2자리). `12345678-01` · `1234567801` 둘 다 받는다. 모양이 틀리면 None."""
    raw = "".join(ch for ch in str(_vars(env).get(ACCOUNT_ENV) or "") if ch.isdigit())
    return (raw[:8], raw[8:]) if len(raw) == 10 else None


class NoResponse(KisFailed):
    """보냈는지·받아들였는지 모른다(연결 끊김·시간 초과·JSON 아닌 응답·주문번호 없는 성공) — 주문이면 다시 보내지 않고
    다음 날 체결 조회로 맞춘다. KIS 가 오류 코드를 준 거부(`KisFailed`)와 가른다 (25.1475 검증 [권장] 3)"""


def _check_url(url: str, tr: str) -> str:
    """모의 도메인·모의 tr_id 가 아니면 보내지 않는다. 도메인은 **글자 그대로** 적는다 — `BASE` 와 견주면
    `BASE` 를 바꿀 때 검사도 같이 바뀐다 (25.1475 검증 [참고] 6)"""
    if not url.startswith("https://openapivts.koreainvestment.com:29443/") or not tr.startswith("V"):
        raise KisFailed("KIS모의투자 도메인·모의 tr_id 가 아니면 보내지 않습니다")
    return url


def _ok(payload: object, what: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or str(payload.get("rt_cd")) != "0":
        code = payload.get("msg_cd") if isinstance(payload, dict) else None
        msg = payload.get("msg1") if isinstance(payload, dict) else None
        raise KisFailed(f"{what} 실패 {code or ''} {str(msg or '').strip()[:80]}".strip())
    return payload


@dataclass(frozen=True)
class Accepted:
    order_no: str  # ODNO
    order_time: str | None  # ORD_TMD (HHMMSS)


def parse_order(payload: object) -> Accepted:
    body = _ok(payload, "모의 주문")
    out = body.get("output") or {}
    no = str(out.get("ODNO") or "").strip()
    if not no:
        raise NoResponse("모의 주문 응답에 주문번호가 없습니다")
    return Accepted(no, str(out.get("ORD_TMD") or "") or None)


def _int(v: object) -> int:
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _float(v: object) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


@dataclass(frozen=True)
class Execution:
    """일별 주문체결 한 줄 (inquire-daily-ccld output1)."""

    order_date: str  # ord_dt YYYYMMDD
    order_no: str  # odno
    code: str  # pdno
    side: str  # buy · sell (sll_buy_dvsn_cd 02 · 01)
    quantity: int  # ord_qty
    filled: int  # tot_ccld_qty
    avg_price: float | None  # avg_prvs
    rejected: int  # rjct_qty
    cancelled: bool  # cncl_yn


def parse_executions(payload: object) -> list[Execution]:
    body = _ok(payload, "모의 체결 조회")
    out = []
    for r in body.get("output1") or []:
        no = str(r.get("odno") or "").strip()
        if not no:
            continue
        code = str(r.get("sll_buy_dvsn_cd") or "")
        out.append(Execution(
            order_date=str(r.get("ord_dt") or ""), order_no=no, code=str(r.get("pdno") or "").strip(),
            side="sell" if code == "01" else "buy" if code == "02" else code, quantity=_int(r.get("ord_qty")),
            filled=_int(r.get("tot_ccld_qty")), avg_price=_float(r.get("avg_prvs")), rejected=_int(r.get("rjct_qty")),
            cancelled=str(r.get("cncl_yn") or "").upper() == "Y",
        ))  # fmt: skip
    return out


@dataclass(frozen=True)
class Holding:
    code: str  # pdno
    quantity: int  # hldg_qty
    sellable: int  # ord_psbl_qty
    avg_price: float | None  # pchs_avg_pric


def parse_balance(payload: object) -> tuple[list[Holding], dict[str, float | None]]:
    """(보유, {cash: 예수금총금액, total: 총평가금액}). 보유 0 주인 줄은 뺀다."""
    body = _ok(payload, "모의 잔고 조회")
    보유 = [
        Holding(str(r.get("pdno") or "").strip(), _int(r.get("hldg_qty")), _int(r.get("ord_psbl_qty")),
                _float(r.get("pchs_avg_pric")))  # fmt: skip
        for r in body.get("output1") or []
        if _int(r.get("hldg_qty")) > 0 and str(r.get("pdno") or "").strip()
    ]
    합 = body.get("output2") or [{}]
    합 = 합[0] if isinstance(합, list) and 합 else 합 if isinstance(합, dict) else {}
    return 보유, {"cash": _float(합.get("dnca_tot_amt")), "total": _float(합.get("tot_evlu_amt"))}


class Session:
    """토큰·계좌·호출 간격을 쥔 모의 세션. 만들 때 키가 없으면 `KisFailed`."""

    def __init__(self, client: Any, now: datetime | None = None, *, http: Any = None, sleep: Any = time.sleep) -> None:
        if not configured():
            raise KisFailed("KIS모의투자 키·계좌가 없습니다")
        acct = account()
        assert acct is not None
        self.cano, self.prdt = acct
        self.http = http or requests.Session()
        self.sleep = sleep
        self.calls = 0
        self._last = 0.0
        token, issued = kis.access_token(client, now or datetime.now(UTC), base=BASE, name=TOKEN_NAME,
                                         key_env=KEY_ENV, secret_env=SECRET_ENV, source=SOURCE)  # fmt: skip
        self.calls += int(issued)
        self.head = {
            "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
            "appkey": os.environ["KIS_VTS_APP_KEY"], "appsecret": os.environ["KIS_VTS_APP_SECRET"], "custtype": "P",
        }  # fmt: skip

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < MIN_INTERVAL:
            self.sleep(MIN_INTERVAL - gap)
        self._last = time.monotonic()

    def _call(self, method: str, path: str, tr: str, *, params: dict | None = None, body: dict | None = None,
              tr_cont: str = "") -> tuple[object, str]:  # fmt: skip
        self._wait()
        url = _check_url(BASE + path, tr)
        headers = self.head | {"tr_id": tr, "tr_cont": tr_cont}
        self.calls += 1
        try:
            if method == "POST":
                r = self.http.post(url, json=body, headers=headers, timeout=TIMEOUT)
            else:
                r = self.http.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            # 예외 문장에 요청 주소(조회는 `?CANO=…` 계좌번호)가 실린다 — 이름만 남기고 원래 예외를 잇지 않는다
            # (traceback 에도 안 남게, 25.1475 검증 [권장] 2)
            raise NoResponse(f"모의 응답 없음 ({type(exc).__name__})") from None
        try:
            payload = r.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise NoResponse(f"모의 HTTP {r.status_code} — JSON 아님")
        return payload, str(getattr(r, "headers", {}).get("tr_cont", "") or "")

    def order(self, side: str, code: str, quantity: int, price: float | None) -> Accepted:
        """현금 주문. price 가 없으면 시장가(ORD_DVSN 01, 단가 "0"), 있으면 지정가(00, 원 단위 정수)."""
        if side not in ("buy", "sell") or quantity <= 0 or not code:
            raise KisFailed("모의 주문 인자가 틀렸습니다")
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": code,
            "ORD_DVSN": "01" if price is None else "00", "ORD_QTY": str(int(quantity)),
            "ORD_UNPR": "0" if price is None else str(int(round(price))),
            "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side == "sell" else "", "CNDT_PRIC": "",
        }  # fmt: skip
        payload, _c = self._call("POST", ORDER_PATH, TR_SELL if side == "sell" else TR_BUY, body=body)
        return parse_order(payload)

    def executions(self, start: str, end: str) -> tuple[list[Execution], bool]:
        """(start~end(YYYYMMDD, 3개월 이내) 의 주문·체결, 다 받았나) — 연속 조회를 따라간다.

        쪽 상한에 닿으면 다 못 받은 것 — 그때 못 찾은 주문을 `unknown` 으로 닫지 않게 부르는 쪽에 알린다
        (25.1475 검증 [권장] 5)
        """
        out: list[Execution] = []
        fk = nk = ""
        cont = ""
        for _ in range(CCLD_MAX_PAGES):
            params = {
                "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "INQR_STRT_DT": start, "INQR_END_DT": end,
                "SLL_BUY_DVSN_CD": "00", "PDNO": "", "CCLD_DVSN": "00", "INQR_DVSN": "01", "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_1": "", "EXCG_ID_DVSN_CD": "KRX",
                "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk,
            }  # fmt: skip
            payload, 다음 = self._call("GET", CCLD_PATH, TR_DAILY_CCLD, params=params, tr_cont=cont)
            out += parse_executions(payload)
            if 다음 not in ("M", "F") or not isinstance(payload, dict):
                return out, True
            fk, nk, cont = str(payload.get("ctx_area_fk100") or ""), str(payload.get("ctx_area_nk100") or ""), "N"
        return out, False

    def balance(self) -> tuple[list[Holding], dict[str, float | None]]:
        보유: list[Holding] = []
        합: dict[str, float | None] = {}
        fk = nk = ""
        cont = ""
        for _ in range(MAX_PAGES):
            params = {
                "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk,
            }  # fmt: skip
            payload, 다음 = self._call("GET", BALANCE_PATH, TR_BALANCE, params=params, tr_cont=cont)
            쪽, 쪽합 = parse_balance(payload)
            보유 += 쪽
            합 = 합 or 쪽합
            if 다음 not in ("M", "F") or not isinstance(payload, dict):
                break
            fk, nk, cont = str(payload.get("ctx_area_fk100") or ""), str(payload.get("ctx_area_nk100") or ""), "N"
        return 보유, 합
