"""DART 배당에 관한 사항 (alotMatter) 어댑터.

2026-09-17 실제 응답으로 구조를 확인했다(삼성전자 2025·2018, SK하이닉스 2022 사업보고서).

  GET https://opendart.fss.or.kr/api/alotMatter.json
      ?crtfc_key=<키>&corp_code=<고유번호 하나>&bsns_year=<연도>&reprt_code=11011

응답 한 번에 **당기·전기·전전기 3년치**가 온다(thstrm·frmtrm·lwfr). 회사당 두 번(예: 2025·2022)
부르면 6개 사업연도를 덮는다. 유니버스 879사 × 2 = 1,758회로 일 한도 20,000 안이다.

**함정 셋** (실제 응답)
  1. 액면분할이 주당배당금을 바꾼다. 삼성전자 2018 보고서: 전기 42,500원 → 당기 1,416원(50:1 분할).
     주당 기준으로 "배당이 줄었는가" 를 보면 분할을 감소로 읽는다. 연속성·감소는 **총액**으로 본다
  2. 금액 단위가 줄마다 다르다. 총액·순이익은 백만원, 주당값은 원, 성향·수익률은 %
  3. 값이 없으면 "-" 다. 무배당인지 해당 없음인지 구분되지 않는다 [확인필요]. 판정에서는 "배당 없음" 으로 본다

시점: 접수번호 앞 8자리가 접수일이다(dart.receipt_date). 같은 사업연도 값을 나중 보고서가 전기로 다시
적으면 두 값을 모두 보관한다(report_year 로 구분). 과거 시점 판정은 그때 접수된 값만 쓴다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

from batch import config
from batch.core.redact import 가림  # 예외 문구의 URL 에 인증키가 있다 (25.621)
from batch.sources import dart
from batch.sources.dart import BASE_URL, SOURCE, STATUS_DAILY_LIMIT, STATUS_NO_DATA, STATUS_OK, TIMEOUT, receipt_date
from batch.sources.yfinance_src import FetchResult

log = logging.getLogger(__name__)

ENDPOINT = "alotMatter.json"
#: 정의처는 `batch/sources/dart.ANNUAL_REPORT_CODE` 하나다 (docs/infra.md 25.143)
ANNUAL = dart.ANNUAL_REPORT_CODE
STATUS_LIMIT = STATUS_DAILY_LIMIT  # 요청 제한 초과. 더 부르지 않는다. 사본을 두지 않는다 (25.606)

MILLION = 1_000_000

# (se 라벨, stock_knd) → (필드, 배수). stock_knd 가 None 이면 종류 구분 없는 줄이다.
FIELD_MAP: dict[tuple[str, str | None], tuple[str, int | None]] = {
    ("주당액면가액(원)", None): ("face_value", 1),
    ("(연결)당기순이익(백만원)", None): ("net_income_consolidated", MILLION),
    ("(연결)주당순이익(원)", None): ("eps_consolidated", 1),
    ("현금배당금총액(백만원)", None): ("cash_dividend_total", MILLION),
    ("(연결)현금배당성향(%)", None): ("payout_ratio", None),
    ("현금배당수익률(%)", "보통주"): ("yield_common", None),
    ("주당 현금배당금(원)", "보통주"): ("dps_common", 1),
    ("주당 현금배당금(원)", "우선주"): ("dps_preferred", 1),
}

# 연결이 없는 회사는 별도 성향만 준다고 본다 [확인필요: 실제 사례 미확인]. 그때만 쓴다.
FALLBACK_PAYOUT = ("(별도)현금배당성향(%)", None)

PERIODS = (("thstrm", 0), ("frmtrm", 1), ("lwfr", 2))


@dataclass
class DividendYear:
    """한 사업연도의 배당. stock_dividends 한 행과 맞춘다."""

    fiscal_year: int
    report_year: int  # 이 값을 실어 온 보고서의 사업연도
    receipt_no: str
    as_of_date: str | None  # 접수일
    face_value: int | None = None
    net_income_consolidated: int | None = None
    eps_consolidated: int | None = None
    cash_dividend_total: int | None = None
    payout_ratio: float | None = None
    payout_basis: str | None = None  # 연결 / 별도
    yield_common: float | None = None
    dps_common: int | None = None
    dps_preferred: int | None = None


def _number(raw: Any) -> float | None:
    """쉼표·괄호 음수·'-' 를 처리한다. 성향·수익률은 소수라 float 로 받는다."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if text in ("", "-", "."):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def parse_alot_matter(payload: dict, report_year: int) -> list[DividendYear]:
    """응답을 사업연도별 행으로. 결과가 없거나 상태가 정상이 아니면 빈 목록."""
    if str(payload.get("status")) != STATUS_OK:
        return []
    rows = payload.get("list") or []
    if not rows:
        return []

    receipt_no = str(rows[0].get("rcept_no") or "")
    # **연도 열쇠는 요청한 사업연도(bsns_year)다 — 결산일(stlm_dt)의 해가 아니다** (docs/infra.md 25.732, 재무 감사
    # 2번).
    # 재무(`financials.fiscal_year`)는 bsns_year 로 적재한다. 결산월이 12월이 아닌 회사는 두 해가 다를 수 있어
    # [확인필요: 3월 결산의 bsns_year 가 시작 해인지 끝 해인지] 배당이 재무와 한 해 어긋나 짝지어졌다(적립 판정의
    # 배당성향·연속 배당).
    # 12월 결산은 두 해가 같아 결과가 그대로다
    base_year = report_year

    years: dict[int, DividendYear] = {
        base_year - back: DividendYear(
            fiscal_year=base_year - back, report_year=report_year,
            receipt_no=receipt_no, as_of_date=receipt_date(receipt_no),
        )
        for _, back in PERIODS
    }

    fallback: dict[int, float] = {}
    for row in rows:
        label = str(row.get("se") or "").strip()
        kind_raw = str(row.get("stock_knd") or "").strip()
        kind = kind_raw if kind_raw in ("보통주", "우선주") else None
        for column, back in PERIODS:
            value = _number(row.get(column))
            if value is None:
                continue
            year = years[base_year - back]
            if (label, kind) == FALLBACK_PAYOUT:
                fallback[year.fiscal_year] = value
                continue
            target = FIELD_MAP.get((label, kind))
            if target is None:
                continue
            field_name, multiplier = target
            setattr(year, field_name, int(round(value * multiplier)) if multiplier else value)

    for year in years.values():
        if year.payout_ratio is not None:
            year.payout_basis = "연결"
        elif year.fiscal_year in fallback:
            year.payout_ratio = fallback[year.fiscal_year]
            year.payout_basis = "별도"

    # **값이 하나도 없는 사업연도 행은 만들지 않는다** (docs/infra.md 25.830, 감사). 신규상장·인적분할 뒤 보고서의
    # 전기·전전기 칸은
    # 전부 "-" 일 수 있다. 그 빈 행이 저장되면 읽는 쪽(점수·적립·종목 상세)이 같은 사업연도 중 **가장 나중 보고서**를
    # 골라
    # 예전 보고서의 실제 배당을 가렸다 — 점수는 빈 값을 0 으로 읽어 배당수익률 0·연속 배당 끊김이 됐다. 무배당은 "-"
    # 여도
    # 액면가·순이익·EPS 칸이 차 있어 빈 행이 아니다
    값_칸 = ("face_value", "net_income_consolidated", "eps_consolidated", "cash_dividend_total", "payout_ratio",
            "yield_common", "dps_common", "dps_preferred")  # fmt: skip
    kept = [y for y in years.values() if any(getattr(y, f) is not None for f in 값_칸)]
    return sorted(kept, key=lambda y: y.fiscal_year)


def fetch_alot_matter(corp_code: str, bsns_year: int) -> FetchResult:
    """한 회사 한 사업보고서. 데이터 없음(013)은 실패가 아니라 빈 목록이다."""
    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, source=SOURCE, error="오프라인 모드", attempts=0)
    key = config.get("DART_API_KEY")
    if not key:
        # 안 불렀으면 세지 않는다 (25.607)
        return FetchResult(ok=False, source=SOURCE, error="DART_API_KEY 가 비어 있습니다", attempts=0)

    try:
        response = requests.get(
            f"{BASE_URL}/{ENDPOINT}",
            params={"crtfc_key": key, "corp_code": corp_code, "bsns_year": str(bsns_year), "reprt_code": ANNUAL},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        return FetchResult(ok=False, source=SOURCE, error=f"호출 실패: {가림(str(exc))}")
    if response.status_code != 200:
        return FetchResult(ok=False, source=SOURCE, error=f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        return FetchResult(ok=False, source=SOURCE, error="응답 해석 실패")

    status = str(payload.get("status"))
    if status == STATUS_LIMIT:
        return FetchResult(
            ok=False, source=SOURCE, limit_state="blocked", error=f"요청 제한 초과: {payload.get('message')}"
        )
    if status == STATUS_NO_DATA:
        return FetchResult(ok=True, source=SOURCE, data=[], limit_state="ok")
    if status != STATUS_OK:
        return FetchResult(ok=False, source=SOURCE, error=f"상태 {status}: {payload.get('message')}")
    return FetchResult(ok=True, source=SOURCE, data=parse_alot_matter(payload, bsns_year), limit_state="ok")
