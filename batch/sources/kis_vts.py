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


@dataclass(frozen=True)
class Keys:
    """모의 계좌 하나의 키 이름·토큰 줄 이름. 키 값은 담지 않는다 — 이름만 (25.1515)"""

    source: str
    token: str
    key_env: str
    secret_env: str
    account_env: str


#: 첫째 모의 계좌 — 챔피언(H)을 베낀다
MAIN = Keys(SOURCE, TOKEN_NAME, KEY_ENV, SECRET_ENV, ACCOUNT_ENV)
#: 둘째 모의 계좌 — 도전자(설정 `kis_challenger`)를 베낀다 (docs/paper.md 15.10, 25.1515). 키가 없으면 꺼짐.
#: 토큰 줄·출처도 따로 — 두 앱키의 토큰이 서로 덮지 않게
CHALLENGER = Keys("kis_vts2", "kis_vts2", "KIS_VTS2_APP_KEY", "KIS_VTS2_APP_SECRET", "KIS_VTS2_ACCOUNT")
#: 모의 tr_id (order_cash.py L110-116 · inquire_balance.py L118 · inquire_daily_ccld.py L150 — 3개월 이내)
TR_BUY, TR_SELL = "VTTC0012U", "VTTC0011U"
TR_BALANCE, TR_DAILY_CCLD = "VTTC8434R", "VTTC0081R"
#: 미국 — **예약주문**(정규장 밖에 접수, 개장 때 정규장 지정가로 전송). 우리 미국 배치는 개장 약 1시간 전이라 정규 주문
#: 을 못 낸다. 모의 tr_id 가 따로 있다(order_resv.py L149-151: 매수 VTTT3014U · 매도 VTTT3016U). 접수 10:00~23:20 KST
#: (서머타임 22:20), 16:30~16:45 정산 시간 제외(order_resv.py 주석). 미국 모의 정규 주문은 지정가만 되고, 매도 tr_id 는
#: 주석(VTTT1001U)과 코드(VTTT1006U)가 엇갈려 쓰지 않는다
TR_US_RESV_BUY, TR_US_RESV_SELL = "VTTT3014U", "VTTT3016U"
#: 미국 체결·잔고 (inquire_ccnl.py L131 · inquire_balance.py L98). 모의 체결 조회는 매도매수·체결·거래소 칸이
#: "전체" 만 된다
TR_US_CCNL, TR_US_BALANCE = "VTTS3035R", "VTTS3012R"
US_RESV_PATH = "/uapi/overseas-stock/v1/trading/order-resv"
US_CCNL_PATH = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
US_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"
#: 미국 매수가능금액 (inquire_psamount.py L26·L96) — 시험 주문(`scripts/probe_kis_vts.py`, 25.1491)이 원화만 있는
#: 모의계좌로 미국을 살 수 있는지(환전 뒤 주문가능) 보려고 읽기만 한다
TR_US_PSAMOUNT = "VTTS3007R"
US_PSAMOUNT_PATH = "/uapi/overseas-stock/v1/trading/inquire-psamount"
#: 미국 체결기준현재잔고(inquire_present_balance.py L26·L108) — `output3` 의 해외 총자산·해외 주식 평가·해외 현금(원화
#: 환산)을 읽어 계좌 전체 원화 총액에 더하고(25.1527 — 모의는 보유 줄 수량·평가가 0), `output2` 의 KIS 고시환율로
#: H 견주기의 쉬는 해외 현금 환율 몫을 뺀다(25.1543). 미국 보유는 거래소별 잔고(`VTTS3012R`)에서 읽는다(25.1527)
TR_US_PRESENT = "VTRP6504R"
US_PRESENT_PATH = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
#: `stocks.market`(nasdaq_symbols.EXCHANGE_CODES) → KIS 해외거래소 코드. 모르는 시장은 주문하지 않는다
US_EXCHANGE = {"NASDAQ": "NASD", "NYSE": "NYSE", "NYSE American": "AMEX", "NYSE Arca": "AMEX"}
ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
#: 호출 사이 최소 간격(초) — 공식 예제의 모의 간격 0.5초보다 넉넉히. 하루 수십 건이라 느려도 몇십 초다.
#: 0.6 이던 것을 2026-10-16 시험에서 첫 호출부터 `EGW00201`(초당 거래건수 초과)을 받아 1.0 으로 (25.1494)
MIN_INTERVAL = 1.0
#: 조회(GET)가 `EGW00201` 을 받으면 이만큼 기다렸다 다시 — 공식 예제 backtester 는 61초("한도 초기화 대기") 뒤 다시
#: (`kis_backtest/providers/kis/data.py` L12-14). 주문(POST)은 다시 보내지 않는다 — 두 번 주문 위험보다 거부 기록이 낫다
RATE_LIMIT_CODE = "EGW00201"
RATE_WAITS = (1.5, 61.0)
#: 연속 조회(다음 쪽) 최대 — 잔고는 모의에서 한 번에 20건(inquire_balance.py L118 주석)
MAX_PAGES = 10
#: 체결 조회 쪽 최대 — 모의는 한 쪽에 15건쯤이라(검증 보고) 며칠 밀린 맞추기도 담게 넉넉히
CCLD_MAX_PAGES = 30
#: 일별 주문체결(VTTC0081R)은 3개월 안만 — 그보다 오래 맞추지 못한 줄은 `unknown` 으로 닫는다
CCLD_MAX_DAYS = 89
TIMEOUT = 15


def _vars(env: dict[str, str] | None) -> dict[str, str]:
    """모의 전용 칸만(첫째·둘째 계좌) — 실전 키(`KIS_APP_KEY`)는 읽지 않는다.

    이름을 글자로 적어 둔다(tests/test_env_example.py 가 읽는 환경변수를 글자로 훑는다)
    """
    if env is not None:
        return env
    return {
        "KIS_VTS_APP_KEY": os.environ.get("KIS_VTS_APP_KEY", ""),
        "KIS_VTS_APP_SECRET": os.environ.get("KIS_VTS_APP_SECRET", ""),
        "KIS_VTS_ACCOUNT": os.environ.get("KIS_VTS_ACCOUNT", ""),
        "KIS_VTS2_APP_KEY": os.environ.get("KIS_VTS2_APP_KEY", ""),
        "KIS_VTS2_APP_SECRET": os.environ.get("KIS_VTS2_APP_SECRET", ""),
        "KIS_VTS2_ACCOUNT": os.environ.get("KIS_VTS2_ACCOUNT", ""),
    }


def configured(env: dict[str, str] | None = None, keys: Keys = MAIN) -> bool:
    return not missing(env, keys)


def missing(env: dict[str, str] | None = None, keys: Keys = MAIN) -> list[str]:
    """비었거나 모양이 틀린 칸 이름(값은 담지 않는다). 셋 다 비면 연결하지 않은 것, 일부만이면 설정 실수다 (25.1492)."""
    e = _vars(env)
    out = [k for k in (keys.key_env, keys.secret_env) if not e.get(k)]
    if not e.get(keys.account_env):
        out.append(keys.account_env)
    elif account(e, keys) is None:
        out.append(f"{keys.account_env}(숫자 8·10자리 아님)")
    return out


#: 계좌번호만 8자리로 넣었을 때의 상품코드 — 종합계좌 (공식 예제 `kis_devlp.yaml` `my_prod: "01" # 종합계좌`).
#: 모의투자 화면은 8자리만 보여 준다(2026-10-16 사용자 확인 "계좌번호는 … 8자리던데")
DEFAULT_PRODUCT = "01"


def account(env: dict[str, str] | None = None, keys: Keys = MAIN) -> tuple[str, str] | None:
    """(종합계좌번호 8자리, 상품코드 2자리). `12345678` · `12345678-01` · `1234567801` 모두 받는다. 모양이 틀리면 None.

    8자리면 상품코드는 종합계좌 `01`(`DEFAULT_PRODUCT`)
    """
    raw = "".join(ch for ch in str(_vars(env).get(keys.account_env) or "") if ch.isdigit())
    if len(raw) == 8:
        return raw, DEFAULT_PRODUCT
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


def _num(v: object) -> float | None:
    """부호 있는 수(손익) — 0·음수도 그대로, 못 읽으면 None."""
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


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
    #: 주문 단가(미국 ft_ord_unpr3, 국내 ord_unpr) — 번호 없이 맞추는 줄에서 수량이 같은 분할 회차를 가른다
    #: (미국 25.1490, 국내 25.1566). 0 이하(시장가)·빈칸은 None
    price: float | None = None


def _단가(v: object) -> float | None:
    """주문 단가 — 0 이하(시장가)·빈칸은 모름(None). `ord_unpr`(주문단가)는 공식 예제의 칸 표에 있다
    (open-trading-api `examples_llm/domestic_stock/inquire_daily_ccld/chk_inquire_daily_ccld.py`, 2026-10-20 확인).
    모의 실측 응답으로는 아직 못 봤다 `[확인필요]` — 안 오면 None 이라 예전처럼 단가를 보지 않는다(25.1566)"""
    x = _float(v)
    return x if x is not None and x > 0 else None


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
            cancelled=str(r.get("cncl_yn") or "").upper() == "Y", price=_단가(r.get("ord_unpr")),
        ))  # fmt: skip
    return out


def parse_us_reservation(payload: object) -> Accepted:
    """미국 예약주문 응답 — 성공 코드(`rt_cd` 0)면 받아들여진 것이다.

    번호는 공식 예제의 `OVRS_RSVN_ODNO`(해외예약주문번호, 대소문자 무관), 없으면 `ODNO`.
    2026-10-16 첫 시험 주문(25.1491)에서 모의가 성공 코드를 주면서 `OVRS_RSVN_ODNO` 를 주지 않았다 — 번호가 없다고
    "모름" 으로 두면 받아들여진 주문을 응답 없는 주문으로 세었다. 번호가 없으면 응답의 **칸 이름만**(값 없이) 남겨
    무엇이 오는지 보게 한다. 정규 주문번호는 개장 때 생겨 체결 조회와는 날·종목·방향·수량·단가로 맞추므로 이 번호는
    기록일 뿐이다
    """
    body = _ok(payload, "모의 미국 예약주문")
    out = body.get("output") or {}
    if isinstance(out, list):
        out = out[0] if out and isinstance(out[0], dict) else {}
    칸 = {str(k).upper(): v for k, v in out.items()} if isinstance(out, dict) else {}
    no = str(칸.get("OVRS_RSVN_ODNO") or 칸.get("ODNO") or "").strip()
    if not no:
        no = "번호 칸 없음(" + ("·".join(sorted(칸)) or "output 비어 있음") + ")"
    return Accepted(no, None)


def parse_us_executions(payload: object) -> list[Execution]:
    """미국 주문체결 내역(inquire-ccnl output) → `Execution`. 거부 사유가 있으면 거부 수량 = 주문 − 체결.

    거부 사유 칸이 정상 주문에도 "0" 같은 값을 줄 수 있어 빈칸·0 은 사유 없음으로 본다 (25.1485 검증 [참고] 1)
    """
    body = _ok(payload, "모의 미국 체결 조회")
    out = []
    for r in body.get("output") or []:
        no = str(r.get("odno") or "").strip()
        if not no:
            continue
        code = str(r.get("sll_buy_dvsn_cd") or "")
        주문, 체결 = _int(r.get("ft_ord_qty")), _int(r.get("ft_ccld_qty"))
        거부 = str(r.get("rjct_rson") or "").strip().strip("0") != ""
        out.append(Execution(
            order_date=str(r.get("ord_dt") or ""), order_no=no, code=str(r.get("pdno") or "").strip(),
            side="sell" if code == "01" else "buy" if code == "02" else code, quantity=주문, filled=체결,
            avg_price=_float(r.get("ft_ccld_unpr3")), rejected=max(주문 - 체결, 0) if 거부 else 0,
            cancelled=str(r.get("rvse_cncl_dvsn") or "") == "02", price=_float(r.get("ft_ord_unpr3")),
        ))  # fmt: skip
    return out


def parse_us_balance(payload: object) -> list[Holding]:
    body = _ok(payload, "모의 미국 잔고 조회")
    return [
        Holding(str(r.get("ovrs_pdno") or "").strip(), _int(r.get("ovrs_cblc_qty")), _int(r.get("ord_psbl_qty")),
                _float(r.get("pchs_avg_pric")), _float(r.get("now_pric2")), _num(r.get("ovrs_stck_evlu_amt")),
                _num(r.get("frcr_evlu_pfls_amt")))  # fmt: skip
        for r in body.get("output1") or []
        if _int(r.get("ovrs_cblc_qty")) > 0 and str(r.get("ovrs_pdno") or "").strip()
    ]


def us_totals(present: dict[str, Any]) -> dict[str, float | None]:
    """체결기준현재잔고(외화 기준) → {value_usd: 보유 외화평가 합, cash_usd: 달러 예수금, usd_krw: KIS 최초고시환율}.

    보유 줄 하나라도 평가가 없으면 value_usd 는 None. 25.1497 은 "미국 매수 돈이 원화 예수금에서 자동 환전으로
    나간다" 고 읽었는데 틀렸다 — 2026-10-18 탐침으로 모의 계좌의 미국 돈은 원화 예수금과 **따로 있는 해외 자산**이고
    (미국 매수 뒤 국내 D+2 그대로), 계좌 총액은 국내 총평가 + `output3.tot_asst_amt` 다(25.1527)
    """
    보유 = [r for r in present.get("output1") or [] if isinstance(r, dict) and _int(r.get("cblc_qty13")) > 0]
    값 = [_num(r.get("frcr_evlu_amt2")) for r in 보유]
    usd = next((r for r in present.get("output2") or [] if isinstance(r, dict)
                and str(r.get("crcy_cd") or "").upper() == "USD"), {})  # fmt: skip
    셋 = present.get("output3") or {}
    셋 = 셋 if isinstance(셋, dict) else {}
    # 미결제 매수·매도 합·외화매수증거금은 기록만 — 체결 기준 보유와 결제 기준 원화 차감이 하루 엇갈리는지
    # 첫 미국 체결 뒤 계열로 보려고(25.1499, 검토 [필수] 1 `[확인필요]`)
    return {"value_usd": None if any(v is None for v in 값) else float(sum(v for v in 값 if v is not None)),
            "cash_usd": _num(usd.get("frcr_dncl_amt_2")), "usd_krw": _float(usd.get("frst_bltn_exrt")),
            # 해외 총자산·해외 주식 평가·해외 현금(원화 환산) — 계좌 총액은 이것을 쓴다(25.1527 — 모의는 output1 보유 줄
            # 수량·평가가 0 이고 미국 돈은 원화 예수금과 따로 있다).
            # 2026-10-18 탐침: tot_asst_amt = evlu_amt_smtl + frcr_evlu_tota
            "assets_krw": _num(셋.get("tot_asst_amt")), "stocks_krw": _num(셋.get("evlu_amt_smtl")),
            "cash_krw": _num(셋.get("frcr_evlu_tota")),
            "unsettled_buy": _num(셋.get("ustl_buy_amt_smtl")), "unsettled_sell": _num(셋.get("ustl_sll_amt_smtl")),
            "buy_margin_usd": _num(usd.get("frcr_buy_mgn_amt"))}  # fmt: skip


@dataclass(frozen=True)
class Holding:
    code: str  # pdno
    quantity: int  # hldg_qty
    sellable: int  # ord_psbl_qty
    avg_price: float | None  # pchs_avg_pric
    #: KIS 가 준 현재가·평가금액·평가손익(그 시장 통화) — 웹 보유내역이 그대로 보인다(docs/paper.md 15.7, 25.1496)
    price: float | None = None  # prpr · now_pric2
    value: float | None = None  # evlu_amt · ovrs_stck_evlu_amt
    pnl: float | None = None  # evlu_pfls_amt · frcr_evlu_pfls_amt


def parse_balance(payload: object) -> tuple[list[Holding], dict[str, float | None]]:
    """(보유, {cash: 예수금총금액, total: 총평가금액}). 보유 0 주인 줄은 뺀다."""
    body = _ok(payload, "모의 잔고 조회")
    보유 = [
        Holding(str(r.get("pdno") or "").strip(), _int(r.get("hldg_qty")), _int(r.get("ord_psbl_qty")),
                _float(r.get("pchs_avg_pric")), _float(r.get("prpr")), _num(r.get("evlu_amt")),
                _num(r.get("evlu_pfls_amt")))  # fmt: skip
        for r in body.get("output1") or []
        if _int(r.get("hldg_qty")) > 0 and str(r.get("pdno") or "").strip()
    ]
    합 = body.get("output2") or [{}]
    합 = 합[0] if isinstance(합, list) and 합 else 합 if isinstance(합, dict) else {}
    # 총평가(`tot_evlu_amt`)는 D+2 예수금 + 유가평가 — 현금도 D+2(`prvs_rcdl_excc_amt`)로 봐야 기준이 맞는다
    # (2026-10-16 계좌 칸 탐침: 1주 사고판 뒤 D+0 500,000,000 · D+2 499,999,261 = 총평가, 25.1497).
    # `cash` 는 예전처럼 D+0(`cash_krw` 칸)
    return 보유, {"cash": _float(합.get("dnca_tot_amt")), "total": _float(합.get("tot_evlu_amt")),
                "holdings_value": _num(합.get("scts_evlu_amt")),
                "cash_d2": _num(합.get("prvs_rcdl_excc_amt"))}  # fmt: skip


class Session:
    """토큰·계좌·호출 간격을 쥔 모의 세션. 만들 때 키가 없으면 `KisFailed`."""

    def __init__(self, client: Any, now: datetime | None = None, *, http: Any = None, sleep: Any = time.sleep,
                 keys: Keys = MAIN) -> None:  # fmt: skip
        if not configured(keys=keys):
            raise KisFailed("KIS모의투자 키·계좌가 없습니다")
        acct = account(keys=keys)
        assert acct is not None
        self.cano, self.prdt = acct
        self.http = http or requests.Session()
        self.sleep = sleep
        self.calls = 0
        self.rate_retries = 0
        self._last = 0.0
        token, issued = kis.access_token(
            client, now or datetime.now(UTC), base=BASE, name=keys.token, key_env=keys.key_env,
            secret_env=keys.secret_env, source=keys.source,
        )  # fmt: skip
        self.calls += int(issued)
        self.head = {
            "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
            "appkey": os.environ[keys.key_env], "appsecret": os.environ[keys.secret_env], "custtype": "P",
        }  # fmt: skip

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < MIN_INTERVAL:
            self.sleep(MIN_INTERVAL - gap)
        self._last = time.monotonic()

    def _call(self, method: str, path: str, tr: str, *, params: dict | None = None, body: dict | None = None,
              tr_cont: str = "", _retry: int = 0) -> tuple[object, str]:  # fmt: skip
        self._wait()
        url = _check_url(BASE + path, tr)
        headers = self.head | {"tr_id": tr, "tr_cont": tr_cont}
        self.calls += 1
        try:
            if method == "POST":
                r = self.http.post(url, json=body, headers=headers, timeout=TIMEOUT, allow_redirects=False)
            else:
                # 리디렉트를 따라가지 않는다 — 다른 호스트로 넘어가면 앱키·시크릿 머리글이 그대로 따라간다
                # (25.1499, 검토 [참고] 6)
                r = self.http.get(url, params=params, headers=headers, timeout=TIMEOUT, allow_redirects=False)
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
        if method == "GET" and str(payload.get("msg_cd") or "") == RATE_LIMIT_CODE and _retry < len(RATE_WAITS):
            self.sleep(RATE_WAITS[_retry])
            self.rate_retries += 1
            return self._call(method, path, tr, params=params, body=body, tr_cont=tr_cont, _retry=_retry + 1)
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

    # ── 미국 (예약주문) ──────────────────────────────────────────────────────

    def buyable_us(self, code: str, exchange: str, price: float) -> dict[str, Any]:
        """미국 매수가능금액 조회(읽기만) — output 칸 그대로(주문가능외화금액·환전이후주문가능수량 등)."""
        params = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "OVRS_EXCG_CD": exchange,
                  "OVRS_ORD_UNPR": f"{price:.2f}", "ITEM_CD": code}  # fmt: skip
        payload, _c = self._call("GET", US_PSAMOUNT_PATH, TR_US_PSAMOUNT, params=params)
        out = _ok(payload, "모의 미국 매수가능금액 조회").get("output") or {}
        return out if isinstance(out, dict) else (out[0] if out else {})

    def order_us(self, side: str, code: str, exchange: str, quantity: int, price: float) -> Accepted:
        """미국 예약주문 — 지정가(달러, 소수 둘째 자리). 시장가가 없어 매도는 부르는 쪽이 낮춘 지정가를 넘긴다."""
        if (side not in ("buy", "sell") or quantity <= 0 or not code or price <= 0
                or exchange not in US_EXCHANGE.values()):
            raise KisFailed("모의 미국 주문 인자가 틀렸습니다")
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": code, "OVRS_EXCG_CD": exchange,
            "FT_ORD_QTY": str(int(quantity)), "FT_ORD_UNPR3": f"{price:.2f}",
        }  # fmt: skip
        tr = TR_US_RESV_SELL if side == "sell" else TR_US_RESV_BUY
        payload, _c = self._call("POST", US_RESV_PATH, tr, body=body)
        return parse_us_reservation(payload)

    def executions_us(self, start: str, end: str) -> tuple[list[Execution], bool]:
        """미국 주문체결 내역 (start~end YYYYMMDD) — 국내 `executions` 와 같은 꼴. 모의는 칸 거르기가 "전체" 만 된다."""
        out: list[Execution] = []
        fk = nk = ""
        cont = ""
        for _ in range(CCLD_MAX_PAGES):
            params = {
                "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": "", "ORD_STRT_DT": start, "ORD_END_DT": end,
                "SLL_BUY_DVSN": "00", "CCLD_NCCS_DVSN": "00", "OVRS_EXCG_CD": "", "SORT_SQN": "DS", "ORD_DT": "",
                "ORD_GNO_BRNO": "", "ODNO": "", "CTX_AREA_NK200": nk, "CTX_AREA_FK200": fk,
            }  # fmt: skip
            payload, 다음 = self._call("GET", US_CCNL_PATH, TR_US_CCNL, params=params, tr_cont=cont)
            out += parse_us_executions(payload)
            if 다음 not in ("M", "F") or not isinstance(payload, dict):
                return out, True
            fk, nk, cont = str(payload.get("ctx_area_fk200") or ""), str(payload.get("ctx_area_nk200") or ""), "N"
        return out, False

    def present_us(self, won: bool = False) -> dict[str, Any]:
        """미국 체결기준현재잔고(읽기만) — {output1: [...], output2: [...], output3: {...}} 그대로.

        `won` 이면 원화 기준(01), 아니면 외화(02)
        """
        params = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "WCRC_FRCR_DVSN_CD": "01" if won else "02",
                  "NATN_CD": "840", "TR_MKET_CD": "00", "INQR_DVSN_CD": "00"}  # fmt: skip
        payload, _c = self._call("GET", US_PRESENT_PATH, TR_US_PRESENT, params=params)
        body = _ok(payload, "모의 미국 체결기준현재잔고")
        목록 = {k: body.get(k) or [] for k in ("output1", "output2")}
        목록 = {k: [v] if isinstance(v, dict) else v for k, v in 목록.items()}
        셋 = body.get("output3") or {}
        return 목록 | {"output3": 셋[0] if isinstance(셋, list) and 셋 else 셋 if isinstance(셋, dict) else {}}

    def balance_us(self) -> tuple[list[Holding], dict[str, float | None]]:
        """미국 보유 — 거래소마다 묻고 종목 코드로 겹침을 뺀다.

        공식 예제 주석은 실전 `NASD` = 미국 전체, 모의는 NASD·NYSE·AMEX 를 따로 받는다고 적었다 `[확인필요]` — 모의가
        NASD 로 전체를 주더라도 겹침을 빼므로 두 번 세지 않는다. 예수금·평가는 국내 원화 잔고와 단위가 달라 적지 않는다
        """
        보유: dict[str, Holding] = {}
        for 거래소 in sorted(set(US_EXCHANGE.values())):
            fk = nk = ""
            cont = ""
            for _ in range(MAX_PAGES):
                params = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "OVRS_EXCG_CD": 거래소, "TR_CRCY_CD": "USD",
                          "CTX_AREA_FK200": fk, "CTX_AREA_NK200": nk}  # fmt: skip
                payload, 다음 = self._call("GET", US_BALANCE_PATH, TR_US_BALANCE, params=params, tr_cont=cont)
                for h in parse_us_balance(payload):
                    보유.setdefault(h.code, h)
                if 다음 not in ("M", "F") or not isinstance(payload, dict):
                    break
                fk, nk, cont = str(payload.get("ctx_area_fk200") or ""), str(payload.get("ctx_area_nk200") or ""), "N"
        목록 = list(보유.values())
        # 계좌 총액(달러) = 외화예수금 + 보유 평가 합. 예수금 조회가 안 되면(모의 미지원 등) 총액은 모름 —
        # 보유는 그대로 돌려준다. 해외 현금·해외 총자산(원화)도 같은 조회에서 붙인다
        # (25.1527 — 모의는 달러 예수금이 늘 0)
        try:
            셋 = us_totals(self.present_us())
        except KisFailed:
            셋 = {}
        현금 = 셋.get("cash_usd")
        평가 = sum(h.value for h in 목록 if h.value is not None) if all(h.value is not None for h in 목록) else None
        총 = None if 현금 is None or 평가 is None else 현금 + 평가
        return 목록, {"cash": 현금, "total": 총, "holdings_value": 평가, "cash_krw": 셋.get("cash_krw"),
                    "assets_krw": 셋.get("assets_krw")}
