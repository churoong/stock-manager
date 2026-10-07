"""SEC N-PORT 어댑터 — 미국 ETF 의 **전 종목** 보유 (docs/data-sources.md 13.5, docs/etf.md 11.2, docs/infra.md 25.967).

2026-10-06 실측 (실행 37417155827)
  company_tickers_mf.json  28,608행 {fields: [cik, seriesId, classId, symbol], data: [...]}. SPY 없음(단위형 신탁)
  시리즈 공시 목록         cgi-bin/browse-edgar?CIK=<시리즈>&type=NPORT-P&output=atom — 새것부터
  본문 primary_doc.xml     VOO 520행 0.5MB · VTI 3,546행 3.2MB. pctVal 은 **% 단위**, 합 ≈ 100
  결제실패 파일            files/data/fails-deliver-data/cnsfailsYYYYMM{a,b}.zip — `날짜|CUSIP|SYMBOL|수량|이름|가격`
                           CUSIP → 심볼로 VOO 99.6% · VTI 99.3% · IVV 96.9% (비중 기준)

키는 없고 연락처 든 User-Agent 가 필수다 — `sec_edgar.SecClient` 를 그대로 쓴다(초당 약 7회).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date

from batch.sources.sec_edgar import SecClient, SecFailed

SOURCE = "sec_nport"
FUND_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
SERIES_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series}&type=NPORT-P&dateb=&owner=include"
    "&count=10&output=atom"
)
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/primary_doc.xml"
FTD_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{ym}{half}.zip"
#: 결제실패 파일을 거슬러 볼 달 수. 한 번이라도 실패가 있던 종목만 실리므로 여러 달을 합친다 (3달 = 16,543 CUSIP, 13.5)
FTD_MONTHS = 3


@dataclass(frozen=True)
class Holding:
    name: str
    cusip: str
    isin: str
    pct: float  # 순자산 대비 % (pctVal)


@dataclass(frozen=True)
class NportDoc:
    accession: str
    filed: str  # 공시일 YYYY-MM-DD
    report_date: str | None  # 기준일 repPdDate
    holdings: list[Holding]


def parse_fund_tickers(payload: dict) -> dict[str, tuple[str, str]]:
    """심볼 → (신고 주체 CIK 10자리, 시리즈 ID). 먼저 온 것을 남긴다."""
    fields = payload.get("fields") or []
    i_cik, i_series, i_sym = fields.index("cik"), fields.index("seriesId"), fields.index("symbol")
    out: dict[str, tuple[str, str]] = {}
    for row in payload.get("data") or []:
        sym = str(row[i_sym] or "").upper()
        if sym and row[i_series]:
            out.setdefault(sym, (f"{int(row[i_cik]):010d}", str(row[i_series])))
    return out


def parse_series_feed(text: str) -> list[tuple[str, str]]:
    """시리즈 공시 목록(atom) → [(공시 번호, 공시일)] 새것부터. 정정(NPORT-P/A)은 뺀다."""
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", text, flags=re.S):
        kind = re.search(r"<filing-type>([^<]+)</filing-type>", entry)
        # SEC atom 태그는 `accession-nunber`(오타 그대로)로 알려져 있다 — 둘 다 받는다 `[확인필요: 첫 운영 실행]`
        accn = re.search(r"<accession-n[a-z]+>([0-9-]{20})</accession-n[a-z]+>", entry)
        filed = re.search(r"<filing-date>(\d{4}-\d{2}-\d{2})</filing-date>", entry)
        if kind and accn and filed and kind.group(1).strip() == "NPORT-P":
            out.append((accn.group(1), filed.group(1)))
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_doc(xml_bytes: bytes, accession: str, filed: str) -> NportDoc:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SecFailed(f"N-PORT {accession} 본문이 XML 이 아닙니다") from exc
    report = next((e.text for e in root.iter() if _local(e.tag) == "repPdDate"), None)
    holdings: list[Holding] = []
    for h in (e for e in root.iter() if _local(e.tag) == "invstOrSec"):
        kids = {_local(c.tag): c for c in h}
        try:
            pct = float(kids["pctVal"].text) if "pctVal" in kids and kids["pctVal"].text else 0.0
        except ValueError:
            pct = 0.0
        cusip = (kids["cusip"].text or "").strip() if "cusip" in kids else ""
        isin = ""
        ids = kids.get("identifiers")
        if ids is not None:
            isin = next((c.attrib.get("value") or "" for c in ids if _local(c.tag) == "isin"), "")
        name = (kids["name"].text or "").strip() if "name" in kids else ""
        holdings.append(Holding(name=name, cusip=cusip, isin=isin, pct=pct))
    return NportDoc(accession=accession, filed=filed, report_date=report, holdings=holdings)


def parse_ftd(text: str) -> dict[str, str]:
    """결제실패 파일 한 장 → {CUSIP: 심볼}. 머리줄·꼬리줄은 버린다."""
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) >= 3 and len(parts[1].strip()) == 9 and parts[2].strip():
            out.setdefault(parts[1].strip(), parts[2].strip().upper())
    return out


def _months_back(today: date, n: int) -> list[str]:
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


class NportClient:
    def __init__(self, sec: SecClient) -> None:
        self.sec = sec

    def fund_tickers(self) -> dict[str, tuple[str, str]]:
        response = self.sec._get(FUND_TICKERS_URL)
        if response.status_code != 200:
            raise SecFailed(f"펀드 티커 목록 HTTP {response.status_code}")
        return parse_fund_tickers(response.json())

    def ftd_cusips(self, today: date) -> dict[str, str]:
        """최근 `FTD_MONTHS` 달의 결제실패 파일을 합친 CUSIP → 심볼. 아직 안 나온 반달(404)은 건너뛴다."""
        out: dict[str, str] = {}
        for ym in _months_back(today, FTD_MONTHS):
            for half in ("b", "a"):  # 새것 먼저 — setdefault 라 새 심볼이 남는다
                response = self.sec._get(FTD_URL.format(ym=ym, half=half))
                if response.status_code == 404:
                    continue
                if response.status_code != 200:
                    raise SecFailed(f"결제실패 파일 {ym}{half} HTTP {response.status_code}")
                with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                    text = z.read(z.namelist()[0]).decode("latin-1")
                for k, v in parse_ftd(text).items():
                    out.setdefault(k, v)
        return out

    def latest(self, cik: str, series: str) -> NportDoc | None:
        """이 시리즈의 가장 새 NPORT-P. 공시가 없으면 None."""
        feed = self.sec._get(SERIES_FEED_URL.format(series=series))
        if feed.status_code != 200:
            raise SecFailed(f"시리즈 {series} 공시 목록 HTTP {feed.status_code}")
        filings = parse_series_feed(feed.text)
        if not filings:
            return None
        accession, filed = filings[0]
        nodash = accession.replace("-", "")
        # 공시 번호 앞자리가 대행사(filer agent)면 신고 주체 CIK 밑에 있다 (IVV 실측)
        for owner in dict.fromkeys((int(accession.split("-")[0]), int(cik))):
            doc = self.sec._get(DOC_URL.format(cik=owner, nodash=nodash))
            if doc.status_code == 200:
                return parse_doc(doc.content, accession, filed)
            if doc.status_code != 404:
                raise SecFailed(f"N-PORT {accession} HTTP {doc.status_code}")
        raise SecFailed(f"N-PORT {accession} 본문을 찾지 못했습니다")
