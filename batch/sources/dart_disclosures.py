"""DART 공시 목록 (list.json). 종목 상세의 "최근 공시" 와 장중 공시 알림의 재료다 (docs/data-sources.md 1.2).

회사 하나씩, 접수일 구간으로 받는다. 응답에서 **확인된 것만** 쓴다: `status`, `list[].rcept_no`, `list[].report_nm`
(장중 경로 `web/app/api/cron/intraday/route.ts` 가 같은 필드를 실제로 읽고 있다). 접수일은 접수번호 앞 8자리다
(1절 "시점 스냅샷의 기준"). 그 밖의 필드(`rcept_dt` `rm` 등)는 있으면 쓰고 없어도 된다 [확인필요].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from batch import config
from batch.core.redact import 가림  # 예외 문구의 URL 에 인증키가 있다 (25.621)
from batch.sources.dart import BASE_URL, STATUS_DAILY_LIMIT, STATUS_NO_DATA, STATUS_OK, receipt_date
from batch.sources.yfinance_src import FetchResult

log = logging.getLogger(__name__)

SOURCE = "dart_opendart"
ENDPOINT = "list.json"
#: 한도 코드는 한 곳에서 정의한다 (25.605, 감사) — 사본이면 판정이 바뀔 때 한쪽만 고쳐진다
STATUS_LIMIT = STATUS_DAILY_LIMIT

#: 공시 목록 한 쪽의 제한시간(초). `dart.TIMEOUT`(120초)은 큰 재무 응답용이다. 이 호출은 **일일 배치 안에서
#: 텔레그램 발송보다 먼저** 돈다 — DART 가 응답 없이 매달리면 120초 × 여러 회사로 15분 제한에 걸려 리포트가
#: 못 나갔다 (25.605, 감사). 목록 한 쪽은 100건짜리 작은 JSON 이다 [확인필요: 평소 응답 시간 실측 아님]
LIST_TIMEOUT = 30
# 한 쪽의 건수. page_count 상한이 100 이라는 것은 기억에 의존 [확인필요]. 넘치면 쪽을 넘긴다(25.477)
PAGE_COUNT = 100
VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"


@dataclass(frozen=True)
class Disclosure:
    corp_code: str
    receipt_no: str
    title: str
    disclosed_at: str  # YYYY-MM-DD
    report_name: str | None = None
    submitter: str | None = None

    @property
    def url(self) -> str:
        return VIEW_URL.format(receipt_no=self.receipt_no)


def parse_list(payload: dict[str, Any], corp_code: str) -> list[Disclosure]:
    """응답의 list 를 공시 목록으로. 접수번호나 제목이 없는 행은 버린다 (값을 지어내지 않는다)."""
    out: list[Disclosure] = []
    for row in payload.get("list") or []:
        receipt_no = str(row.get("rcept_no") or "").strip()
        title = str(row.get("report_nm") or "").strip()
        if not receipt_no or not title:
            continue
        disclosed_at = receipt_date(receipt_no)
        if disclosed_at is None:
            rcept_dt = str(row.get("rcept_dt") or "")
            disclosed_at = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}" if len(rcept_dt) == 8 else None
        if disclosed_at is None:
            continue
        out.append(
            Disclosure(
                corp_code=str(row.get("corp_code") or corp_code),
                receipt_no=receipt_no,
                title=title,
                disclosed_at=disclosed_at,
                report_name=(str(row["rm"]).strip() or None) if row.get("rm") else None,
                submitter=(str(row["flr_nm"]).strip() or None) if row.get("flr_nm") else None,
            )
        )
    return out


#: 한 회사·한 구간에서 넘길 최대 쪽 수 (docs/infra.md 25.477). 100건 × 20쪽 = 2,000건 — 5년 창의 대형사도 덮는다.
#: 넘으면 잘렸다고 말한다(조용히 자르지 않는다)
MAX_PAGES = 20


def _page(key: str, corp_code: str, bgn_de: str, end_de: str, page_no: int) -> tuple[FetchResult | None, dict]:
    """한 쪽. 실패면 (FetchResult, {}), 성공이면 (None, payload)."""
    try:
        response = requests.get(
            f"{BASE_URL}/{ENDPOINT}",
            params={
                "crtfc_key": key, "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
                "page_no": page_no, "page_count": PAGE_COUNT,
            },
            timeout=LIST_TIMEOUT,
        )  # fmt: skip
    except requests.RequestException as exc:
        return FetchResult(ok=False, source=SOURCE, error=f"호출 실패: {가림(str(exc))}"), {}
    if response.status_code != 200:
        return FetchResult(ok=False, source=SOURCE, error=f"HTTP {response.status_code}"), {}
    try:
        payload = response.json()
    except ValueError:
        return FetchResult(ok=False, source=SOURCE, error="응답 해석 실패"), {}
    status = str(payload.get("status"))
    if status == STATUS_LIMIT:
        return FetchResult(
            ok=False, source=SOURCE, limit_state="blocked", error=f"요청 제한 초과: {payload.get('message')}"
        ), {}
    if status == STATUS_NO_DATA:
        return None, {"list": [], "total_page": 1}
    if status != STATUS_OK:
        return FetchResult(ok=False, source=SOURCE, error=f"상태 {status}: {payload.get('message')}"), {}
    return None, payload


def fetch_list(corp_code: str, bgn_de: str, end_de: str) -> FetchResult:
    """한 회사의 접수일 구간 공시. 데이터 없음(013)은 빈 목록이다. 날짜는 YYYYMMDD.

    **쪽을 넘긴다** (docs/infra.md 25.477). 예전에는 `page_no` 없이 첫 쪽 100건만 받고 `total_page` 도 보지 않아,
    `--all --days 1825` 처럼 긴 창에서는 조용히 잘렸다. `attempts` 는 실제로 부른 쪽 수 — 한도 카운터가 이 수로 센다.
    도중에 실패하면 받은 쪽까지는 버리지 않고 `data` 에 담아 실패로 돌려준다.
    """
    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, source=SOURCE, error="오프라인 모드", attempts=0)
    key = config.get("DART_API_KEY")
    if not key:
        return FetchResult(ok=False, source=SOURCE, error="DART_API_KEY 가 비어 있습니다", attempts=0)
    모은: list[Disclosure] = []
    page_no = 1
    while True:
        실패, payload = _page(key, corp_code, bgn_de, end_de, page_no)
        if 실패 is not None:
            실패.data = 모은
            실패.attempts = page_no
            return 실패
        모은 += parse_list(payload, corp_code)
        try:
            total = int(payload.get("total_page") or 1)
        except (TypeError, ValueError):
            total = 1
        if page_no >= total:
            break
        if page_no >= MAX_PAGES:
            log.warning("%s 공시가 %d쪽을 넘어 앞쪽만 받았습니다 (전체 %d쪽)", corp_code, MAX_PAGES, total)
            # 성공이지만 **다 받지 못했다** — 부르는 쪽이 "이 회사는 창을 덮었다" 로 세지 않게 표시한다 (25.487)
            return FetchResult(ok=True, source=SOURCE, data=모은, limit_state="ok", fetched_at=datetime.now(UTC),
                               attempts=page_no, error=f"잘림: {total}쪽 중 {MAX_PAGES}쪽")  # fmt: skip
        page_no += 1
    return FetchResult(ok=True, source=SOURCE, data=모은, limit_state="ok", fetched_at=datetime.now(UTC),
                       attempts=page_no)  # fmt: skip


# ---------------------------------------------------------------------------------------------------------------------
# 시장 전체 공시 (docs/disclosure_reaction.md, docs/infra.md 25.996)
# ---------------------------------------------------------------------------------------------------------------------

#: 시장 전체로 받는 공시 종류 — 주요사항보고(B)·거래소공시(I). 정기공시·지분공시까지 받으면 하루 수천 건이다
MARKET_KINDS = ("B", "I")


@dataclass(frozen=True)
class MarketDisclosure:
    corp_code: str
    stock_code: str  # 6자리 (상장사만)
    receipt_no: str
    title: str
    disclosed_at: str
    kind: str  # B · I


def parse_market(payload: dict[str, Any], kind: str) -> list[MarketDisclosure]:
    """회사를 정하지 않은 목록. 상장사(`stock_code` 6자리)만 — 비상장·펀드는 버린다."""
    out = []
    for row in payload.get("list") or []:
        receipt_no, title = str(row.get("rcept_no") or "").strip(), str(row.get("report_nm") or "").strip()
        stock_code = str(row.get("stock_code") or "").strip()
        day = receipt_date(receipt_no) if receipt_no else None
        if receipt_no and title and day and len(stock_code) == 6:
            out.append(MarketDisclosure(str(row.get("corp_code") or ""), stock_code, receipt_no, title, day, kind))
    return out


def fetch_market_day(day: str, kind: str) -> tuple[list[MarketDisclosure], int, str | None]:
    """하루치(YYYYMMDD) 시장 전체 공시 한 종류. 반환 (목록, 호출 수, 오류)."""
    key = config.DART_API_KEY
    if not key:
        return [], 0, "DART_API_KEY 없음"
    out: list[MarketDisclosure] = []
    page = 1
    while True:
        try:
            response = requests.get(
                f"{BASE_URL}/{ENDPOINT}",
                params={"crtfc_key": key, "bgn_de": day, "end_de": day, "pblntf_ty": kind, "page_no": page,
                        "page_count": PAGE_COUNT},
                timeout=LIST_TIMEOUT,
            )  # fmt: skip
            payload = response.json() if response.status_code == 200 else {}
        except (requests.RequestException, ValueError) as exc:
            return out, page, f"호출 실패: {가림(str(exc))}"
        status = str(payload.get("status"))
        if status == STATUS_NO_DATA:
            return out, page, None
        if status != STATUS_OK:
            return out, page, f"상태 {status}: {payload.get('message')}"
        out += parse_market(payload, kind)
        if page >= int(payload.get("total_page") or 1) or page >= MAX_PAGES:
            return out, page, None
        page += 1
