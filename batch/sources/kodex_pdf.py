"""삼성자산운용 KODEX 구성종목(PDF) 어댑터 — 국내 지수 ETF 의 전 종목 보유 (docs/data-sources.md 13.6, docs/etf.md 11.6,
docs/infra.md 25.974).

2026-10-06 실측 (scripts/probe_kr_etf_pdf.py, Actions 러너)
  상품 목록   GET /api/v1/kodex/product.do?pageNo=n   — 20개씩, totalCnt 242. 열 fId(2ETF01)·stkTicker(069500)·fNm
  구성종목    GET /api/v1/kodex/product-pdf/{fId}.do?gijunYMD=YYYYMMDD
              → {"pdf": {"gijunYMD", "totalCnt", "list": [{itmNo, secNm, evalA, applyQ, ratio, …}]}}
              KODEX 200 10-02: 202행. itmNo 는 국내 주식이면 6자리 단축코드, 현금은 KRD… 같은 다른 코드

**이용 조건**: 홈페이지 하단에 "본 웹사이트의 자료를 사전 허가없이 무단으로 사용하거나 데이터베이스화 할 경우,
민형사상 법적 책임을 질 수 있습니다." 가 있다. **사용자가 알고 쓰기로 정했다**(2026-10-06 "코덱스는 그냥해줘").
그래서 호출을 줄인다 — 월 1회 ETF 판정 때만, 필요한 상품만, 호출 사이 `INTERVAL` 초, 429 면 물러난다(25.984).
받은 구성종목은 판정 행의 근거(상위 기여 종목)로만 남기고 전 종목 표를 따로 쌓지 않는다. 앱은 본인만 보는 개인용이다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

import requests

SOURCE = "samsungfund_kodex"
BASE = "https://www.samsungfund.com"
LIST_URL = BASE + "/api/v1/kodex/product.do?pageNo={page}"
PDF_URL = BASE + "/api/v1/kodex/product-pdf/{fid}.do?gijunYMD={ymd}"
#: 호출 사이 쉬는 초 — 사용자가 이용 조건을 알고 쓰기로 했으므로 사이트에 짐을 주지 않게 느리게.
#: 2026-10-07 첫 운영(1.0초): 목록 13쪽 + 구성종목 20개쯤 뒤부터 나머지 117개가 전부 HTTP 429 였다
#: (docs/infra.md 25.984).
#: 한도 수치는 공개돼 있지 않다 `[확인필요]` — 셋 배로 늦춘다(호출 131회 ≈ 6.5분, 워크플로 상한 90분)
INTERVAL = 3.0
#: 429 를 받으면 쉬었다 다시 묻는 횟수와 쉬는 초(`Retry-After` 가 있으면 그 값, 상한 `WAIT_429_MAX`)
RETRY_429 = 2
WAIT_429 = 60
WAIT_429_MAX = 300
#: 상품 목록 쪽수 상한 (242개 ÷ 20 = 13쪽, 2026-10-06). 목록이 끝없이 이어지는 고장을 막는다
MAX_PAGES = 30
#: 기준일에 구성종목이 비어 있으면(휴장·아직 안 올림) 거슬러 보는 날 수
BACK_DAYS = 5
TIMEOUT = 30


class KodexFailed(RuntimeError):
    """응답이 깨졌거나 5xx·4xx — "없음" 이 아니라 **못 받음**이다."""


class KodexBlocked(KodexFailed):
    """쉬었다 다시 물어도 429 — 이번 실행에서는 더 부르지 않는다(계속 부르면 더 오래 막힌다)."""


@dataclass(frozen=True)
class Product:
    fid: str
    ticker: str
    name: str


@dataclass(frozen=True)
class Holding:
    code: str  # 국내 주식이면 6자리 단축코드
    name: str
    pct: float  # 평가금액 합 대비 %


@dataclass(frozen=True)
class PdfDoc:
    fid: str
    ticker: str
    base_date: str  # YYYY-MM-DD
    holdings: list[Holding]


def parse_products(payload: object) -> list[Product]:
    if not isinstance(payload, list):
        raise KodexFailed("상품 목록이 배열이 아닙니다")
    out = []
    for row in payload:
        fid, ticker = str(row.get("fId") or ""), str(row.get("stkTicker") or "")
        if fid and ticker:
            out.append(Product(fid=fid, ticker=ticker, name=str(row.get("fNm") or "")))
    return out


def parse_pdf(payload: object, fid: str, ticker: str) -> PdfDoc | None:
    """구성종목 → 평가금액 비중(%). 목록이 비면 None(그날 공시 없음)."""
    pdf = payload.get("pdf") if isinstance(payload, dict) else None
    if not isinstance(pdf, dict):
        raise KodexFailed(f"KODEX {ticker} 구성종목 응답에 pdf 가 없습니다")
    rows = pdf.get("list") or []
    values = []
    for r in rows:
        try:
            v = float(r.get("evalA") or 0)
        except (TypeError, ValueError):
            v = 0.0
        values.append((str(r.get("itmNo") or ""), str(r.get("secNm") or ""), v))
    total = sum(v for *_, v in values if v > 0)
    if not values or total <= 0:
        return None
    ymd = str(pdf.get("gijunYMD") or "")
    base = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
    return PdfDoc(fid=fid, ticker=ticker, base_date=base,
                  holdings=[Holding(c, n, v / total * 100) for c, n, v in values if v > 0])  # fmt: skip


def _retry_after(value: str | None) -> float:
    """`Retry-After` 초. 없거나 날짜 꼴이면 `WAIT_429`. 너무 길면 `WAIT_429_MAX`."""
    try:
        return min(max(float(value), 1.0), WAIT_429_MAX) if value else WAIT_429
    except ValueError:
        return WAIT_429


class KodexClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Referer": BASE + "/etf/main.do"})
        self.calls = 0
        self._last = 0.0

    def _get(self, url: str) -> object:
        wait = INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        response = self.session.get(url, timeout=TIMEOUT)
        for _ in range(RETRY_429):
            if response.status_code != 429:
                break
            time.sleep(_retry_after(response.headers.get("Retry-After")))
            self._last = time.monotonic()
            self.calls += 1
            response = self.session.get(url, timeout=TIMEOUT)
        if response.status_code == 429:
            raise KodexBlocked(f"HTTP 429 {url.split('?')[0]} — {RETRY_429}번 쉬었다 물어도 막힘")
        if response.status_code != 200:
            raise KodexFailed(f"HTTP {response.status_code} {url.split('?')[0]}")
        try:
            return response.json()
        except ValueError as exc:
            raise KodexFailed(f"JSON 이 아닙니다 {url.split('?')[0]}") from exc

    def products(self) -> list[Product]:
        out: list[Product] = []
        for page in range(1, MAX_PAGES + 1):
            got = parse_products(self._get(LIST_URL.format(page=page)))
            if not got:
                break
            out.extend(got)
        return out

    def pdf(self, product: Product, base: date) -> PdfDoc | None:
        """기준일의 구성종목. 비었으면 하루씩 거슬러 `BACK_DAYS` 일까지."""
        for back in range(BACK_DAYS + 1):
            day = base - timedelta(days=back)
            doc = parse_pdf(
                self._get(PDF_URL.format(fid=product.fid, ymd=day.strftime("%Y%m%d"))), product.fid, product.ticker
            )
            if doc is not None:
                return doc
        return None
