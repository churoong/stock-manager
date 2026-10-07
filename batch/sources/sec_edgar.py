"""SEC EDGAR 어댑터 — 티커 매핑과 발행주식수 (docs/data-sources.md 14절).

2026-09-17 실측
  company_tickers.json   219KB(gzip), 10,422행. 클래스주는 대시 표기(BRK-B) → 앱 yahoo_symbol 과 같다
  companyconcept          개념 하나만 준다. 약 2KB. 주식수만 필요할 때 회사 전체 JSON(평균 1.5MB) 대신 쓴다
    AAPL dei:EntityCommonStockSharesOutstanding  14,594,180,000 (10-Q, filed 2026-07-31)
    GOOGL dei 404 → us-gaap:CommonStockSharesOutstanding 12,230,000,000 (10-Q, filed 2026-07-23)
    ABNB·META 둘 다 404 (클래스별 차원으로만 공시) → 가중평균 기본주식수로 대신한다
      ABNB 592,000,000 (2026 Q2), META 2,543,000,000 (2026 Q2). 실제 발행주식수와 몇 % 차이
    BRK  dei 는 2011-04, 가중평균은 2015 에서 끊김 → 오래된 값이라 버린다(MAX_AGE_DAYS)

**이용 조건**: 키는 없지만 연락처가 든 User-Agent 가 필수다(없으면 403). 초당 10회 이하.
UA 값은 GitHub 시크릿 SEC_USER_AGENT 에만 둔다(사용자 결정 2026-09-17).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import requests

from batch import config
from batch.sources.yfinance_src import FetchResult

log = logging.getLogger(__name__)

SOURCE = "sec_edgar"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
TIMEOUT = 30

# 공식 한도 초당 10회. 여유를 두고 초당 약 7회.
MIN_INTERVAL = 0.14

# 주식수 개념 우선순위 (docs/data-sources.md 14.2)
# 셋째는 기간 평균이라 시점 값이 아니다. 복수 클래스 회사는 앞의 둘이 없어서 이것 말고는 시총을 못 구한다.
SHARE_CONCEPTS = (
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
)

# 공시가 이보다 오래되면 버린다. 연간 보고만 하는 회사(10-K 1년 + 제출 지연)를 살리는 선.
# BRK 처럼 공시 구조가 바뀌어 끊긴 개념의 옛 값으로 시총을 만들지 않기 위함.
MAX_AGE_DAYS = 550

# 위임장(DEF 14A) 같은 공시의 fact 가 섞여 온다. 정기 보고서만 쓴다(14.1).
PERIODIC_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})


class SecFailed(RuntimeError):
    """SEC 가 5xx 를 주거나 본문이 깨졌다 — "없음" 이 아니라 **못 받음**이다 (docs/infra.md 25.601, 감사).

    예전에는 None·[] 로 삼켜 호출한 작업이 "fact 없음"·"공시 없음" 으로 세고 success 로 닫았고,
    주식수는 dei 가 503 이면 다음 개념(가중평균)으로 조용히 내려가 그 값이 시총을 덮었다.
    호출부(`us_financials`·`disclosures_us`·`fetch_shares`)는 한 회사 실패로 센다
    (25.603 — `sic()` 은 아직 None 으로 삼킨다)
    """


class SecBlocked(RuntimeError):
    """403·429. 계속 부르면 더 오래 막힌다."""


@dataclass
class Shares:
    value: int
    as_of: str  # 주식수 기준일(end)
    filed: str
    form: str
    concept: str


def parse_ticker_map(payload: dict) -> dict[str, str]:
    """{"0": {"cik_str": 320193, "ticker": "AAPL", ...}} → {"AAPL": "0000320193"}. 먼저 온 것을 남긴다."""
    out: dict[str, str] = {}
    for row in payload.values():
        ticker = str(row.get("ticker") or "").upper()
        cik = row.get("cik_str")
        if ticker and cik is not None and ticker not in out:
            out[ticker] = f"{int(cik):010d}"
    return out


def latest_shares(payload: dict, concept: str, today: date | None = None) -> Shares | None:
    """개념 응답에서 정기 보고서의 가장 최근 공시값. 같은 날 공시면 기준일이 늦은 것, 그다음 짧은 기간.

    today 를 주면 MAX_AGE_DAYS 보다 오래된 공시는 없는 것으로 본다.
    """
    candidates: list[dict[str, Any]] = []
    for facts in (payload.get("units") or {}).values():
        candidates.extend(f for f in facts if f.get("form") in PERIODIC_FORMS and f.get("val") is not None)
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda f: (str(f.get("filed") or ""), str(f.get("end") or ""), str(f.get("start") or "")),
    )
    if today is not None and str(best.get("filed") or "") < (today - timedelta(days=MAX_AGE_DAYS)).isoformat():
        return None
    return Shares(
        value=int(best["val"]),
        as_of=str(best.get("end") or ""),
        filed=str(best.get("filed") or ""),
        form=str(best.get("form") or ""),
        concept=concept,
    )


# 최근 공시로 셀 폼. 전부 보면 목록이 8-K·4 로 덮여 "무슨 일이 있었나" 가 묻힌다.
# 국내 disclosures 와 나란히 읽히도록 정기보고서와 주요사항 위주로 둔다 [확인필요: 더할 폼]
FILING_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "40-F", "6-K")
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/"


@dataclass(frozen=True)
class Filing:
    """SEC 공시 한 건. 국내 disclosures 표와 같은 열로 옮길 수 있게 맞춰 둔다."""

    cik: str
    accession: str  # 대시 있는 원형 (0000320193-26-000001). 국내 receipt_no 자리에 넣는다
    form: str
    filed: str  # YYYY-MM-DD
    title: str
    url: str


def parse_filings(payload: dict, cik: str, forms: tuple[str, ...] = FILING_FORMS, limit: int = 20) -> list[Filing]:
    """submissions 응답의 filings.recent 에서 최근 공시.

    recent 는 **열 단위 배열**이다: accessionNumber[i] 와 form[i] 가 같은 건이다 `[확인필요: 실측]`.
    필요한 값(접수번호·폼·접수일)이 없는 건은 버린다 — 값을 지어내지 않는다.
    """
    recent = ((payload.get("filings") or {}).get("recent") or {}) if isinstance(payload.get("filings"), dict) else {}
    accns = recent.get("accessionNumber") or []
    if not isinstance(accns, list):
        return []
    forms_col = recent.get("form") or []
    filed_col = recent.get("filingDate") or []
    doc_col = recent.get("primaryDocument") or []
    desc_col = recent.get("primaryDocDescription") or []
    report_col = recent.get("reportDate") or []

    def at(column: list, index: int) -> str:
        value = column[index] if index < len(column) else None
        return str(value).strip() if value else ""

    out: list[Filing] = []
    for i, raw in enumerate(accns):
        accession = str(raw).strip()
        form = at(forms_col, i)
        filed = at(filed_col, i)
        if not accession or not form or len(filed) != 10:
            continue
        if forms and form not in forms:
            continue
        bare = accession.replace("-", "")
        doc = at(doc_col, i)
        url = (
            FILING_URL.format(cik=cik.lstrip("0") or cik, accn=bare, doc=doc)
            if doc
            else FILING_INDEX_URL.format(cik=cik.lstrip("0") or cik, accn=bare)
        )
        period = at(report_col, i)
        description = at(desc_col, i)
        title = f"{form}{f' ({period})' if period else ''}{f' — {description}' if description else ''}"
        out.append(Filing(cik=cik, accession=accession, form=form, filed=filed, title=title, url=url))
        if len(out) >= limit:
            break
    return out


class SecClient:
    def __init__(self, today: date | None = None) -> None:
        self.today = today or date.today()
        agent = config.get("SEC_USER_AGENT")
        if not agent or "@" not in agent:
            raise RuntimeError("SEC_USER_AGENT 에 연락처(이메일)가 없습니다. SEC 는 연락처 없는 요청을 403 으로 막는다")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": agent, "Accept-Encoding": "gzip, deflate"})
        self._last = 0.0
        self.calls = 0

    def _get(self, url: str) -> requests.Response:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        response = self.session.get(url, timeout=TIMEOUT)
        if response.status_code in (403, 429):
            raise SecBlocked(f"HTTP {response.status_code}")
        return response

    def ticker_map(self) -> dict[str, str]:
        response = self._get(TICKERS_URL)
        response.raise_for_status()
        return parse_ticker_map(response.json())

    def company_facts(self, cik: str) -> dict | None:
        """회사 XBRL fact 전체(docs/data-sources.md 14절). 없는 CIK 는 404 이고 본문이 JSON 이 아니다."""
        response = self._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise SecFailed(f"companyfacts {cik} HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SecFailed(f"companyfacts {cik} 본문이 JSON 이 아닙니다") from exc

    def sic(self, cik: str) -> str | None:
        """회사 정보(submissions)의 SIC 코드. 응답이 크다(평균 160KB, JPM 4.6MB). 공시 목록이 함께 와서다."""
        response = self._get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if response.status_code != 200:
            return None
        try:
            return parse_sic(response.json())
        except ValueError:
            return None

    def filings(self, cik: str, forms: tuple[str, ...] = FILING_FORMS, limit: int = 20) -> list[Filing]:
        """최근 공시 목록. sic() 과 같은 응답을 쓰지만 호출은 따로다(한 번 더 부른다)."""
        response = self._get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise SecFailed(f"submissions {cik} HTTP {response.status_code}")
        try:
            return parse_filings(response.json(), cik, forms, limit)
        except ValueError as exc:
            raise SecFailed(f"submissions {cik} 본문이 JSON 이 아닙니다") from exc

    def shares(self, cik: str) -> Shares | None:
        """우선순위대로 불러 처음 나온 값. 404 는 그 개념이 없다는 뜻(본문이 JSON 이 아니다)."""
        for taxonomy, concept in SHARE_CONCEPTS:
            response = self._get(CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, concept=concept))
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                # 다음 개념으로 내려가지 않는다 — 우선 개념(dei)이 5xx 인데 가중평균으로 시총을 덮었다 (25.601)
                raise SecFailed(f"{cik} {taxonomy}/{concept} HTTP {response.status_code}")
            try:
                found = latest_shares(response.json(), f"{taxonomy}:{concept}", self.today)
            except ValueError as exc:
                raise SecFailed(f"{cik} {taxonomy}/{concept} 본문이 JSON 이 아닙니다") from exc
            if found is not None:
                return found
        return None


def fetch_shares(client: SecClient, ciks: list[str]) -> FetchResult:
    """여러 회사의 주식수. data 는 {cik: Shares}. 막히면 받은 것까지만 돌려준다."""
    if config.SETTINGS.offline_mode:
        return FetchResult(ok=False, source=SOURCE, error="오프라인 모드")
    out: dict[str, Shares] = {}
    blocked = False
    error = ""
    실패 = 0
    for index, cik in enumerate(ciks):
        try:
            found = client.shares(cik)
        except SecBlocked as exc:
            blocked, error = True, f"{index}/{len(ciks)} 에서 차단: {exc}"
            break
        except (requests.RequestException, SecFailed) as exc:
            log.warning("SEC %s 호출 실패: %s", cik, exc)
            실패 += 1  # 못 받은 회사를 센다 — 로그만 남아 5xx 가 몇 건이든 success 였다 (25.603, 교차검증)
            continue
        if found is not None:
            out[cik] = found
        if (index + 1) % 250 == 0:
            log.info("SEC 주식수 %d/%d", index + 1, len(ciks))
    return FetchResult(
        ok=bool(out),
        source=SOURCE,
        data=out,
        limit_state="blocked" if blocked else ("warn" if 실패 else "ok"),
        error=error
        or ("" if out else "주식수를 하나도 받지 못했습니다")
        or (f"{실패}사 호출 실패" if 실패 else ""),
    )


def parse_sic(payload: dict) -> str | None:
    """submissions 응답의 SIC 4자리. 없거나 0000 이면 None."""
    sic = str(payload.get("sic") or "").strip()
    return sic if sic and sic.strip("0") else None
