"""한국거래소 정보데이터시스템 Open API 어댑터.

호출 규격은 2026-09-16 에 실제 서버 응답으로 검증했다(docs/data-sources.md 2번).
  - 데이터 호출 도메인은 data-dbg.krx.co.kr 다. openapi.krx.co.kr 은 포털이다
  - 인증키는 요청 헤더 AUTH_KEY 로 보낸다
  - 조회 기준일은 basDd=YYYYMMDD 하나뿐이다. 종목별·기간별 조회는 없다
  - 응답은 {"OutBlock_1": [...]} 이고 모든 값이 문자열이다. 결측은 빈 문자열
  - 휴장일이나 아직 집계되지 않은 날은 오류가 아니라 빈 배열이 온다

무료 한도는 인증키당 하루 10,000회다. 하루치가 전종목 1회 호출이라
1년 백필이 시장당 250회 정도로 한도 안에 든다.

이용 조건: 비상업 전용, 제3자 제공 금지. 인증 뒤에서만 쓴다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import requests

from batch import config
from batch.sources.yfinance_src import FetchResult

log = logging.getLogger(__name__)

SOURCE = "krx_openapi"
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
DAILY_LIMIT = 10_000
TIMEOUT = 60
MAX_RETRY = 3

# 검증된 API 식별자. 여기 없는 것은 쓰지 않는다.
API = {
    "kospi_daily": ("sto", "stk_bydd_trd"),  # 유가증권 일별매매정보
    "kosdaq_daily": ("sto", "ksq_bydd_trd"),  # 코스닥 일별매매정보
    "kospi_master": ("sto", "stk_isu_base_info"),  # 유가증권 종목기본정보
    "kosdaq_master": ("sto", "ksq_isu_base_info"),  # 코스닥 종목기본정보
    "kospi_index": ("idx", "kospi_dd_trd"),  # KOSPI 시리즈 일별시세
    "kosdaq_index": ("idx", "kosdaq_dd_trd"),  # KOSDAQ 시리즈 일별시세
    # 2026-09-17 이용신청 승인. 실제 응답 구조는 docs/data-sources.md 13.1-1
    "etf_daily": ("etp", "etf_bydd_trd"),  # ETF 일별매매정보
}


class KrxError(RuntimeError):
    pass


class KrxUnauthorized(KrxError):
    """키가 무효하거나, 해당 API 의 이용신청이 승인되지 않았다."""


def _to_float(raw: str | None) -> float | None:
    """문자열 숫자를 변환한다. 빈 문자열과 '-' 는 결측이다."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(raw: str | None) -> int | None:
    value = _to_float(raw)
    return int(value) if value is not None else None


def fetch(api_name: str, bas_dd: str) -> FetchResult:
    """검증된 API 하나를 기준일 하나로 호출한다.

    Args:
        api_name: API 딕셔너리의 키
        bas_dd: 기준일 YYYYMMDD
    """
    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, source=SOURCE, error="오프라인 모드", attempts=0)

    if api_name not in API:
        return FetchResult(ok=False, source=SOURCE, error=f"알 수 없는 API: {api_name}", attempts=0)
    category, api_id = API[api_name]

    key = config.get("KRX_API_KEY")
    if not key:
        return FetchResult(ok=False, source=SOURCE, error="KRX_API_KEY 가 비어 있습니다", attempts=0)

    url = f"{BASE_URL}/{category}/{api_id}.json"
    headers = {"AUTH_KEY": key}
    params = {"basDd": bas_dd}

    last_error = ""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt == MAX_RETRY:
                break
            time.sleep(2 * attempt)
            continue

        body: dict[str, Any]
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}

        # 인증 실패는 재시도해도 소용없다. 원인을 그대로 알린다.
        if resp.status_code == 401 or str(body.get("respCode")) == "401":
            msg = body.get("respMsg", "Unauthorized")
            if "API Call" in msg:
                hint = (
                    f"{api_id} 이용신청이 승인되지 않았습니다. "
                    "openapi.krx.co.kr 마이페이지에서 이 API 를 신청하세요"
                )
            else:
                hint = "인증키가 무효합니다"
            return FetchResult(
                ok=False, source=SOURCE, limit_state="blocked", error=f"{msg}: {hint}", attempts=attempt
            )

        if resp.status_code >= 500:
            last_error = f"서버 오류 {resp.status_code}"
            if attempt == MAX_RETRY:
                break
            time.sleep(2 * attempt)
            continue

        if resp.status_code != 200:
            return FetchResult(
                ok=False, source=SOURCE, error=f"HTTP {resp.status_code}: {resp.text[:200]}", attempts=attempt
            )

        rows = body.get("OutBlock_1")
        if rows is None:
            return FetchResult(
                ok=False, source=SOURCE, error=f"예상 밖 응답 구조: {str(body)[:200]}", attempts=attempt
            )
        return FetchResult(
            ok=True,
            source=SOURCE,
            data=rows,
            limit_state="ok",
            fetched_at=datetime.now(UTC),
            attempts=attempt,
        )

    return FetchResult(ok=False, source=SOURCE, error=last_error or "알 수 없는 실패", attempts=MAX_RETRY)


@dataclass
class KrxDailyRow:
    """일별매매정보 한 행. 필드명은 실제 응답 키를 그대로 옮겼다."""

    bas_dd: str
    isu_cd: str  # 단축코드 6자리
    isu_nm: str
    mkt_nm: str
    close: float | None
    change: float | None
    change_pct: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: int | None
    value: int | None  # 거래대금
    market_cap: int | None
    listed_shares: int | None

    @classmethod
    def from_raw(cls, raw: dict[str, str]) -> KrxDailyRow:
        return cls(
            bas_dd=raw.get("BAS_DD", ""),
            isu_cd=raw.get("ISU_CD", ""),
            isu_nm=raw.get("ISU_NM", ""),
            mkt_nm=raw.get("MKT_NM", ""),
            close=_to_float(raw.get("TDD_CLSPRC")),
            change=_to_float(raw.get("CMPPREVDD_PRC")),
            change_pct=_to_float(raw.get("FLUC_RT")),
            open=_to_float(raw.get("TDD_OPNPRC")),
            high=_to_float(raw.get("TDD_HGPRC")),
            low=_to_float(raw.get("TDD_LWPRC")),
            volume=_to_int(raw.get("ACC_TRDVOL")),
            value=_to_int(raw.get("ACC_TRDVAL")),
            market_cap=_to_int(raw.get("MKTCAP")),
            listed_shares=_to_int(raw.get("LIST_SHRS")),
        )


def fetch_daily(market: str, bas_dd: str) -> FetchResult:
    """한 시장의 하루치 전종목 일별매매정보를 받아 KrxDailyRow 목록으로 돌려준다.

    Args:
        market: "KOSPI" 또는 "KOSDAQ"
        bas_dd: 기준일 YYYYMMDD
    """
    name = {"KOSPI": "kospi_daily", "KOSDAQ": "kosdaq_daily"}.get(market.upper())
    if name is None:
        return FetchResult(ok=False, source=SOURCE, error=f"지원하지 않는 시장: {market}")

    result = fetch(name, bas_dd)
    if not result.ok:
        return result
    result.data = [KrxDailyRow.from_raw(row) for row in result.data]
    return result


@dataclass
class KrxEtfRow:
    """ETF 일별매매정보 한 행. 2026-09-17 실제 응답에서 확인한 필드만 옮겼다.

    총보수·상장일·복제 방법·구성종목은 이 API 에 없다.
    """

    bas_dd: str
    isu_cd: str
    isu_nm: str
    close: float | None
    nav: float | None
    value: int | None  # 거래대금. 야후와 달리 실제 값이다
    net_assets: int | None  # 순자산총액 INVSTASST_NETASST_TOTAMT
    index_name: str  # 기초지수 IDX_IND_NM
    index_close: float | None
    # 일별 시세로 저장할 때 쓴다 (docs/infra.md 25.896). 주식 일별(`KrxDailyRow`)과 같은 필드 이름이다
    # `[확인필요: ETF 응답에도 있는지 — 없으면 None 으로 두고 종가만 저장한다]`
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    change_pct: float | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, str]) -> KrxEtfRow:
        return cls(
            bas_dd=raw.get("BAS_DD", ""),
            isu_cd=raw.get("ISU_CD", ""),
            isu_nm=raw.get("ISU_NM", ""),
            close=_to_float(raw.get("TDD_CLSPRC")),
            nav=_to_float(raw.get("NAV")),
            value=_to_int(raw.get("ACC_TRDVAL")),
            net_assets=_to_int(raw.get("INVSTASST_NETASST_TOTAMT")),
            index_name=(raw.get("IDX_IND_NM") or "").strip(),
            index_close=_to_float(raw.get("OBJ_STKPRC_IDX")),
            open=_to_float(raw.get("TDD_OPNPRC")),
            high=_to_float(raw.get("TDD_HGPRC")),
            low=_to_float(raw.get("TDD_LWPRC")),
            volume=_to_int(raw.get("ACC_TRDVOL")),
            change_pct=_to_float(raw.get("FLUC_RT")),
        )

    @property
    def premium(self) -> float | None:
        """괴리율 (종가 − NAV) / NAV. 양수면 순자산가치보다 비싸게 거래됐다."""
        # 종가 0 은 값 없는 날이다(거래정지·무거래, docs/infra.md 25.203) — 괴리율 −100% 로 잡혀 평균을 끌어내리고
        # 같은 지수
        # ETF 사이 순위가 뒤집혔다 (25.713, 감사 재현)
        if not self.close or self.close <= 0 or not self.nav or self.nav <= 0:
            return None
        return (self.close - self.nav) / self.nav


def fetch_etf_daily(bas_dd: str) -> FetchResult:
    """하루치 전체 ETF. 1,171개가 한 번에 온다(2026-09-16 기준)."""
    result = fetch("etf_daily", bas_dd)
    if not result.ok:
        return result
    result.data = [KrxEtfRow.from_raw(row) for row in result.data]
    return result


def previous_session_yyyymmdd(today: date | None = None) -> str:
    """오늘 이전의 마지막 거래일을 YYYYMMDD 로 돌려준다.

    장중에 호출하면 오늘 데이터가 아직 없으므로, 확실히 집계된 직전 거래일을 쓴다.

    **달력 규칙은 `core.calendar.previous_session` 한 곳이다** (docs/infra.md 25.321). 예전 사본은
    - 오늘을 **UTC 날짜**로 잡아 한국 00~09시에 하루 전 기준이 됐고
    - 거래소 달력의 `previous_session` 에 **휴장일을 그대로** 넣어 주말·공휴일에 예외로 죽었다
    """
    from batch.core import calendar as cal_core

    return cal_core.previous_session("KR", today).strftime("%Y%m%d")


@dataclass
class KrxMasterRow:
    """종목기본정보 한 행.

    주의: ISU_CD 가 일별매매정보에서는 단축코드 6자리인데
    여기서는 표준코드 12자리다. 같은 이름이 다른 값을 담는다.
    두 API 를 이어 붙일 때는 아래 ticker 와 일별매매정보의 isu_cd 를 맞춘다.
    """

    ticker: str  # ISU_SRT_CD. 단축코드 6자리
    isin: str  # ISU_CD. 표준코드 12자리
    name: str  # ISU_ABBRV. 한글 약명
    name_full: str  # ISU_NM. 한글 정식명
    name_en: str
    listed_date: str  # LIST_DD. YYYYMMDD
    market: str  # MKT_TP_NM. KOSPI KOSDAQ KONEX
    security_group: str  # SECUGRP_NM. 주권, 부동산투자회사 등
    section_type: str  # SECT_TP_NM. 소속부. 관리종목이 여기 나타난다
    share_kind: str  # KIND_STKCERT_TP_NM. 보통주 우선주
    par_value: str  # PARVAL. "무액면" 같은 문자열이 올 수 있어 그대로 둔다
    listed_shares: int | None

    @classmethod
    def from_raw(cls, raw: dict[str, str]) -> KrxMasterRow:
        return cls(
            ticker=raw.get("ISU_SRT_CD", "").strip(),
            isin=raw.get("ISU_CD", "").strip(),
            name=raw.get("ISU_ABBRV", "").strip(),
            name_full=raw.get("ISU_NM", "").strip(),
            name_en=raw.get("ISU_ENG_NM", "").strip(),
            listed_date=raw.get("LIST_DD", "").strip(),
            market=raw.get("MKT_TP_NM", "").strip(),
            security_group=raw.get("SECUGRP_NM", "").strip(),
            section_type=raw.get("SECT_TP_NM", "").strip(),
            share_kind=raw.get("KIND_STKCERT_TP_NM", "").strip(),
            par_value=raw.get("PARVAL", "").strip(),
            listed_shares=_to_int(raw.get("LIST_SHRS")),
        )

    @property
    def listed_date_iso(self) -> str | None:
        """YYYYMMDD 를 YYYY-MM-DD 로. 값이 이상하면 None."""
        raw = self.listed_date
        if len(raw) != 8 or not raw.isdigit():
            return None
        글 = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        # 날짜로 유효한지도 본다 (25.713, 감사) — "20231399" 가 그대로 저장돼 `COALESCE` 가 멀쩡한 상장일을 덮고
        # "데이터없음" 으로 제외됐다
        try:
            date.fromisoformat(글)
        except ValueError:
            return None
        return 글


def fetch_master(market: str, bas_dd: str) -> FetchResult:
    """한 시장의 종목기본정보 전체를 받는다.

    Args:
        market: "KOSPI" 또는 "KOSDAQ"
        bas_dd: 기준일 YYYYMMDD. 그 시점의 상장 종목이 나온다
    """
    name = {"KOSPI": "kospi_master", "KOSDAQ": "kosdaq_master"}.get(market.upper())
    if name is None:
        return FetchResult(ok=False, source=SOURCE, error=f"지원하지 않는 시장: {market}")

    result = fetch(name, bas_dd)
    if not result.ok:
        return result
    result.data = [KrxMasterRow.from_raw(row) for row in result.data]
    return result
