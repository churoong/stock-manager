"""금융감독원 전자공시 DART OpenAPI 어댑터.

호출 규격은 2026-09-16 에 실제 응답으로 검증했다.

핵심은 **접수번호 앞 8자리가 접수일자**라는 점이다. 이것이 "언제 알 수 있게
됐는가" 이고 시점 스냅샷의 기준이 된다. 2025 사업보고서는 2026-03-10 에
접수됐으므로 2026-03-09 에는 그 숫자를 알 수 없었다. 백테스트가 이걸 무시하면
미래를 알고 투자한 결과가 나온다.

쓰는 API 는 셋이다.
  corpCode.xml        고유번호 목록. zip 으로 온다. 상장사 약 3,990개
  fnlttMultiAcnt      다중회사 주요계정. **한 번에 100개까지**
  fnlttSinglAcntAll   단일회사 전체 재무제표. 필요할 때만

**함정 세 가지** (실제 응답에서 확인)
  1. 다중회사 API 의 금액에는 쉼표가 들어간다. 단일회사 API 에는 없다
  2. 같은 계정이 여러 재무제표(IS, CIS, CF, SCE)에 중복으로 나온다
  3. 회사마다 매출 계정 이름이 다르다. 매출액, 수익(매출액), 영업수익 등

무료 한도는 일 20,000건이다. 885종목을 100개씩 묶으면 기간당 9회,
5년 20개 기간이면 180회다. 한도에 한참 못 미친다.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

from batch import config
from batch.core.redact import 가림  # 예외 문구의 URL 에 인증키가 있다 (25.621)
from batch.sources.yfinance_src import FetchResult

log = logging.getLogger(__name__)

SOURCE = "dart_opendart"
BASE_URL = "https://opendart.fss.or.kr/api"
DAILY_LIMIT = 20_000
TIMEOUT = 120

# 한 번에 보낼 수 있는 고유번호 개수. 실제 호출로 100 까지 확인했다.
MAX_CORPS_PER_CALL = 100

#: **연간(사업보고서) 코드의 단일 정의처** (docs/infra.md 25.143).
#:
#: 2026-09-23 까지 이 다섯 글자가 **아홉 군데**에 따로 적혀 있었다 — 일곱은 각자의
#: `ANNUAL_REPORT_CODE`·`ANNUAL` 상수로, 둘(`jobs/portfolio`·`jobs/sell_flags`)은
#: SQL 안의 **날글자**로. 날글자 쪽은 상수 이름으로 찾을 수조차 없었다.
#: 25.0 「한 규칙이 두 곳에 있다」의 가장 많은 사본이다.
#:
#: **미국 재무도 이 코드로 적힌다.** SEC 10-K 를 넣는 `jobs/us_financials` 가 일부러
#: 같은 값을 쓴다 — 그래야 `financials` 를 읽는 한 벌의 질의가 두 나라를 함께 덮는다.
#: DART 의 코드를 미국 행이 달고 있는 것은 이상해 보이지만, 갈라 두면 읽는 쪽이 두 벌이 된다.
ANNUAL_REPORT_CODE = "11011"

# 보고서 코드
REPORT_CODES = {
    ANNUAL_REPORT_CODE: ("사업보고서", "A"),
    "11012": ("반기보고서", "Q"),
    "11013": ("1분기보고서", "Q"),
    "11014": ("3분기보고서", "Q"),
}

# 응답 상태
STATUS_OK = "000"
STATUS_NO_DATA = "013"
#: **일일 한도는 020 하나다** (docs/infra.md 25.389). 021 은 "조회 가능 회사 개수 초과(최대 100건)" 로 묶음 크기
#: 문제지 한도가 아니다(docs/data-sources.md 1장). 예전에는 둘을 함께 `blocked` 로 봐, 021 이 오면 재무 수집이
#: 모든 기간을 "DART 일일 한도에 도달" 이라는 틀린 원인으로 멈췄다
STATUS_DAILY_LIMIT = "020"

#: 한도가 아닌 DART 실패가 이만큼 잇달면 그 잡은 멈춘다 (docs/infra.md 25.604·25.605). 키 사용 불가(011)·점검(800)·
#: 5xx 는 매 호출이 똑같이 실패한다 — 끝까지 부르면 한도와 시간만 쓴다. 5 는 일시 오류 몇 번은 넘기는 선
#: [확인필요: 실측 아님]. 재무·공시·내부자·업종이 같은 값을 쓴다. 배당은 한 해 며칠만 돌아 20 (25.609)
MAX_CONSECUTIVE_FAILURES = 5


class DartError(RuntimeError):
    pass


def _to_int(raw: str | None) -> int | None:
    """금액 문자열을 정수로. 쉼표와 결측 표기를 처리한다.

    다중회사 API 는 쉼표를 넣어 주고 단일회사 API 는 넣지 않는다.
    둘 다 받아야 한다.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if text in ("", "-", "."):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = int(float(text))
    except ValueError:
        return None
    return -value if negative else value


def receipt_date(receipt_no: str) -> str | None:
    """접수번호에서 접수일자를 뽑는다. 앞 8자리가 YYYYMMDD 다.

    이 값이 시점 스냅샷의 기준이다. 틀리면 백테스트가 미래를 본다.
    """
    text = str(receipt_no or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    year, month, day = text[:4], text[4:6], text[6:8]
    if not ("1900" <= year <= "2200" and "01" <= month <= "12" and "01" <= day <= "31"):
        return None
    return f"{year}-{month}-{day}"


# ----------------------------------------------------------------------
# 고유번호 목록
# ----------------------------------------------------------------------
@dataclass
class CorpCode:
    corp_code: str
    corp_name: str
    stock_code: str


_LIST_RE = re.compile(r"<list>(.*?)</list>", re.DOTALL)


def _tag(block: str, name: str) -> str:
    match = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return match.group(1).strip() if match else ""


def parse_corp_codes(xml_text: str) -> list[CorpCode]:
    """고유번호 XML 에서 상장사만 뽑는다.

    비상장사는 종목코드 자리가 공백이다. 전체 11만 건 중 상장사는 4천 건쯤이다.
    """
    out: list[CorpCode] = []
    for match in _LIST_RE.finditer(xml_text):
        block = match.group(1)
        stock_code = _tag(block, "stock_code")
        if len(stock_code) != 6:
            continue
        out.append(
            CorpCode(
                corp_code=_tag(block, "corp_code"),
                corp_name=_tag(block, "corp_name"),
                stock_code=stock_code,
            )
        )
    return out


def fetch_corp_codes() -> FetchResult:
    """고유번호 목록을 받는다. zip 안에 XML 하나가 들어 있다."""
    if config.SETTINGS.offline_mode:
        return _offline_corp_codes()

    key = config.get("DART_API_KEY")
    if not key:
        return FetchResult(ok=False, source=SOURCE, error="DART_API_KEY 가 비어 있습니다")

    try:
        response = requests.get(
            f"{BASE_URL}/corpCode.xml", params={"crtfc_key": key}, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        return FetchResult(ok=False, source=SOURCE, error=f"고유번호 받기 실패: {가림(str(exc))}")

    if response.status_code != 200:
        return FetchResult(ok=False, source=SOURCE, error=f"HTTP {response.status_code}")

    # 오류일 때는 zip 이 아니라 XML 로 상태를 돌려준다
    if not response.content.startswith(b"PK"):
        text = response.text[:300]
        return FetchResult(ok=False, source=SOURCE, error=f"zip 이 아닙니다: {text}")

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".xml")]
            if not names:
                return FetchResult(ok=False, source=SOURCE, error="zip 안에 XML 이 없습니다")
            xml_text = archive.read(names[0]).decode("utf-8")
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        return FetchResult(ok=False, source=SOURCE, error=f"zip 처리 실패: {exc}")

    codes = parse_corp_codes(xml_text)
    if not codes:
        return FetchResult(ok=False, source=SOURCE, error="상장사를 한 건도 찾지 못했습니다")

    return FetchResult(
        ok=True, source=SOURCE, data=codes, limit_state="ok", fetched_at=datetime.now(UTC)
    )


def _offline_corp_codes() -> FetchResult:
    import json
    from pathlib import Path

    fixture = Path(config.ROOT) / "tests" / "fixtures" / "dart_corp_codes.json"
    if not fixture.exists():
        return FetchResult(ok=False, source=SOURCE, error=f"픽스처가 없습니다: {fixture}")
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    return FetchResult(
        ok=True,
        source=SOURCE,
        data=[CorpCode(**row) for row in raw.get("corps", [])],
        from_cache=True,
        limit_state="ok",
    )


# ----------------------------------------------------------------------
# 주요계정
# ----------------------------------------------------------------------

# 회사마다 계정 이름이 조금씩 다르다. 하나의 뜻에 여러 표기를 잇는다.
# 순서가 중요하다. 앞에 오는 표기를 먼저 찾는다.
ACCOUNT_MAP: dict[str, tuple[str, ...]] = {
    "current_assets": ("유동자산",),
    "noncurrent_assets": ("비유동자산",),
    "total_assets": ("자산총계",),
    "current_liabilities": ("유동부채",),
    "noncurrent_liabilities": ("비유동부채",),
    "total_liabilities": ("부채총계",),
    "capital_stock": ("자본금",),
    "retained_earnings": ("이익잉여금", "이익잉여금(결손금)"),
    "total_equity": ("자본총계",),
    "revenue": ("매출액", "수익(매출액)", "영업수익"),
    "operating_income": ("영업이익", "영업이익(손실)"),
    "pretax_income": ("법인세차감전 순이익", "법인세비용차감전순이익"),
    "net_income": ("당기순이익(손실)", "당기순이익", "당기순이익(당기순손실)"),
    "comprehensive_income": ("총포괄손익", "총포괄이익"),
}

# 같은 계정이 여러 재무제표에 중복으로 나온다.
# 손익 항목은 손익계산서(IS)에서 읽는다. 포괄손익계산서나 현금흐름표에서
# 같은 이름을 다시 읽으면 값이 덮어써지거나 뜻이 달라진다. IS 에서 그 항목을 못 찾을 때만 CIS 로 물러난다(25.659·25.661)
STATEMENT_FOR = {
    "current_assets": "BS",
    "noncurrent_assets": "BS",
    "total_assets": "BS",
    "current_liabilities": "BS",
    "noncurrent_liabilities": "BS",
    "total_liabilities": "BS",
    "capital_stock": "BS",
    "retained_earnings": "BS",
    "total_equity": "BS",
    "revenue": "IS",
    "operating_income": "IS",
    "pretax_income": "IS",
    "net_income": "IS",
    "comprehensive_income": "IS",
}


@dataclass
class CompanyFinancials:
    corp_code: str
    stock_code: str
    fiscal_year: int
    report_code: str
    consolidated: bool
    receipt_no: str
    report_date: str
    currency: str
    values: dict[str, int | None] = field(default_factory=dict)

    @property
    def period_type(self) -> str:
        return REPORT_CODES.get(self.report_code, ("", "Q"))[1]


def _pick(rows: list[dict[str, Any]], field_name: str) -> int | None:
    """한 회사의 행들에서 원하는 계정 값을 찾는다.

    재무제표 구분을 먼저 맞추고, 그다음 이름 표기를 순서대로 본다.
    """
    wanted_sj = STATEMENT_FOR.get(field_name)
    names = ACCOUNT_MAP[field_name]
    # 손익을 **포괄손익계산서 한 장**으로 내는 회사는 IS 행 없이 CIS 행만 온다 [확인필요: 다중회사 API 실측].
    # IS 에서 못 찾을 때만 CIS 를 본다 — 둘 다 내는 회사는 예전처럼 IS 값이다 (docs/infra.md 25.659, 감사)
    구분들 = [wanted_sj, "CIS"] if wanted_sj == "IS" else [wanted_sj]

    for 구분 in 구분들:
        for name in names:
            for row in rows:
                if 구분 and row.get("sj_div") != 구분:
                    continue
                if (row.get("account_nm") or "").strip() == name:
                    # 분기·반기 보고서에서 손익의 thstrm_amount 는 그 분기 **3개월** 값이다(누적은 thstrm_add_amount).
                    # 반기 행의 thstrm_dt 는 누적 기간을 적어 헷갈린다 (docs/data-sources.md 1.3, 2026-09-18 확인)
                    return _to_int(row.get("thstrm_amount"))
    return None


def parse_multi_response(
    body: dict[str, Any], fiscal_year: int, report_code: str
) -> list[CompanyFinancials]:
    """다중회사 응답을 회사별로 묶는다.

    한 회사가 연결(CFS)과 별도(OFS) 두 벌로 나온다. 둘 다 남긴다.
    """
    rows = body.get("list") or []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in rows:
        key = (str(row.get("corp_code", "")), str(row.get("fs_div", "")))
        grouped.setdefault(key, []).append(row)

    out: list[CompanyFinancials] = []
    for (corp_code, fs_div), group in grouped.items():
        if not corp_code or fs_div not in ("CFS", "OFS"):
            continue
        first = group[0]
        as_of = receipt_date(first.get("rcept_no", ""))
        if not as_of:
            # 접수일을 모르면 시점 스냅샷을 만들 수 없다. 버린다.
            log.warning("%s 접수번호가 이상해 건너뜁니다: %s", corp_code, first.get("rcept_no"))
            continue

        out.append(
            CompanyFinancials(
                corp_code=corp_code,
                stock_code=str(first.get("stock_code", "")).strip(),
                fiscal_year=fiscal_year,
                report_code=report_code,
                consolidated=fs_div == "CFS",
                receipt_no=str(first.get("rcept_no", "")),
                report_date=as_of,
                currency=str(first.get("currency", "KRW")).strip() or "KRW",
                values={name: _pick(group, name) for name in ACCOUNT_MAP},
            )
        )
    return out


def fetch_multi_financials(
    corp_codes: list[str], fiscal_year: int, report_code: str
) -> FetchResult:
    """여러 회사의 주요계정을 한 번에 받는다.

    100개를 넘겨도 오류가 나지 않지만, 넘기지 않는다.
    데이터가 하나도 없으면 상태 013 이 온다. 오류가 아니다.
    """
    # 인자 검증이 먼저다. 잘못된 인자는 오프라인이든 아니든 잘못된 인자다.
    # 모드에 따라 검증이 건너뛰어지면 호출부의 버그가 조용히 넘어간다.
    if len(corp_codes) > MAX_CORPS_PER_CALL:
        return FetchResult(
            ok=False,
            source=SOURCE,
            error=f"한 번에 {MAX_CORPS_PER_CALL}개까지입니다. {len(corp_codes)}개를 받았습니다",
        )
    if not corp_codes:
        return FetchResult(ok=False, source=SOURCE, error="고유번호가 비어 있습니다")

    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, source=SOURCE, error="오프라인 모드")

    key = config.get("DART_API_KEY")
    if not key:
        return FetchResult(ok=False, source=SOURCE, error="DART_API_KEY 가 비어 있습니다")

    try:
        response = requests.get(
            f"{BASE_URL}/fnlttMultiAcnt.json",
            params={
                "crtfc_key": key,
                "corp_code": ",".join(corp_codes),
                "bsns_year": str(fiscal_year),
                "reprt_code": report_code,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        return FetchResult(ok=False, source=SOURCE, error=f"호출 실패: {가림(str(exc))}")

    if response.status_code != 200:
        return FetchResult(ok=False, source=SOURCE, error=f"HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError:
        return FetchResult(ok=False, source=SOURCE, error="응답을 해석하지 못했습니다")

    status = str(body.get("status", ""))
    if status == STATUS_NO_DATA:
        # 이 묶음에 데이터가 없다는 뜻이지 실패가 아니다
        return FetchResult(ok=True, source=SOURCE, data=[], limit_state="ok")
    if status != STATUS_OK:
        return FetchResult(
            ok=False,
            source=SOURCE,
            error=f"DART 오류 {status}: {body.get('message', '')}",
            limit_state="blocked" if status == STATUS_DAILY_LIMIT else "unknown",
        )

    return FetchResult(
        ok=True,
        source=SOURCE,
        data=parse_multi_response(body, fiscal_year, report_code),
        limit_state="ok",
        fetched_at=datetime.now(UTC),
    )


def fetch_company_industry(corp_code: str) -> FetchResult:
    """기업개황(company.json)의 업종코드 `induty_code` (한국표준산업분류). data 는 코드 문자열 또는 None.

    2026-09-17 실측: 삼성전자(00126380) → induty_code "264", acc_mt "12". 회사당 1회.
    """
    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, source=SOURCE, error="오프라인 모드", attempts=0)
    key = config.get("DART_API_KEY")
    if not key:
        # 안 불렀으면 세지 않는다 (25.607)
        return FetchResult(ok=False, source=SOURCE, error="DART_API_KEY 가 비어 있습니다", attempts=0)
    try:
        response = requests.get(
            f"{BASE_URL}/company.json", params={"crtfc_key": key, "corp_code": corp_code}, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        return FetchResult(ok=False, source=SOURCE, error=f"호출 실패: {가림(str(exc))}")
    if response.status_code != 200:
        return FetchResult(ok=False, source=SOURCE, error=f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        return FetchResult(ok=False, source=SOURCE, error="응답을 해석하지 못했습니다")
    status = str(body.get("status", ""))
    if status == STATUS_NO_DATA:
        # 개황이 없는 회사는 "업종코드 없음" 이지 실패가 아니다 (25.607) — 실패로 세면 잇단 실패 멈춤이 헛걸린다.
        # 다른 DART 함수들도 013 을 빈 성공으로 본다 [확인필요: company.json 이 013 을 실제로 주는지]
        return FetchResult(ok=True, source=SOURCE, data=None, limit_state="ok")
    if status != STATUS_OK:
        return FetchResult(
            ok=False, source=SOURCE, error=f"DART 오류 {status}: {body.get('message', '')}",
            limit_state="blocked" if status == STATUS_DAILY_LIMIT else "unknown",
        )
    return FetchResult(ok=True, source=SOURCE, data=(body.get("induty_code") or None), limit_state="ok")
