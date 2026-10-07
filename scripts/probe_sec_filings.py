"""SEC submissions 응답의 공시 목록 모양을 확인한다 (docs/data-sources.md 14.4·16.3).

두 가지를 한 번에 본다. 같은 파일이 둘 다 답한다.
  1. 미국 공시 수집(`jobs/disclosures_us`)이 전제한 `filings.recent` 의 열 이름과 길이
  2. Form 4(내부자 매매)가 이 목록에 있는지, 본문 XML 이 어디 있는지

인증키는 없다. SEC 는 연락처가 든 User-Agent 를 요구한다(`SEC_USER_AGENT` 시크릿).
**UA 는 찍지 않는다** — 이메일이 들어 있다.

실행
  SEC_USER_AGENT="..." python scripts/probe_sec_filings.py --ticker AAPL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
INDEX_JSON = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/index.json"
INTERVAL = 0.15  # 초당 10회 한도 (docs/data-sources.md 14절)


def get(session: requests.Session, url: str) -> requests.Response:
    time.sleep(INTERVAL)
    return session.get(url, timeout=30)


def find_cik(session: requests.Session, ticker: str) -> str | None:
    body = get(session, TICKERS).json()
    for row in body.values():
        if str(row.get("ticker", "")).upper() == ticker.upper():
            return f"{int(row['cik_str']):010d}"
    return None


def show_recent(recent: dict) -> None:
    keys = sorted(recent.keys())
    print(f"   filings.recent 열 {len(keys)}개: {keys}")
    lengths = {k: len(v) for k, v in recent.items() if isinstance(v, list)}
    distinct = sorted(set(lengths.values()))
    print(f"   열 길이 {distinct} (같아야 열 단위 배열이라는 전제가 맞다)")

    columns = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument", "primaryDocDescription")
    for i in range(min(2, len(recent.get("accessionNumber", [])))):
        row = {c: (recent.get(c) or [None] * (i + 1))[i] for c in columns}
        print("   " + json.dumps(row, ensure_ascii=False))

    forms = recent.get("form") or []
    counts: dict[str, int] = {}
    for form in forms:
        counts[str(form)] = counts.get(str(form), 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
    print(f"   폼 종류 {len(counts)}개, 많은 순 {top}")
    print(f"   Form 4 건수 {counts.get('4', 0)} (내부자 매매)")


def check_url(session: requests.Session, cik: str, recent: dict) -> None:
    """수집기가 만드는 본문 주소가 실제로 열리는지. 형식이 틀리면 화면의 링크가 전부 깨진다."""
    accns = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    for i, accn in enumerate(accns[:5]):
        doc = (docs[i] if i < len(docs) else "") or ""
        if not doc:
            continue
        bare = str(accn).replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{bare}/{doc}"
        response = get(session, url)
        print(f"   본문 주소 HTTP {response.status_code}: {url}")
        return
    print("   본문 주소를 시험할 건이 없다")


def show_form4(session: requests.Session, cik: str, recent: dict) -> None:
    """Form 4 한 건의 파일 목록. 내부자 매매 XML 이 어디 있는지 확인한다 (16.3)."""
    forms = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    for i, form in enumerate(forms):
        if str(form) != "4" or i >= len(accns):
            continue
        accn = str(accns[i])
        bare = accn.replace("-", "")
        url = INDEX_JSON.format(cik=cik.lstrip("0"), accn=bare)
        response = get(session, url)
        print(f"== Form 4 {accn} 파일 목록 HTTP {response.status_code}")
        if response.status_code != 200:
            return
        items = ((response.json().get("directory") or {}).get("item") or [])
        names = [str(it.get("name")) for it in items]
        print(f"   파일 {len(items)}개: {names[:12]}")
        show_form4_xml(session, cik, bare, names)
        return
    print("== Form 4 가 최근 목록에 없다")


def show_form4_xml(session: requests.Session, cik: str, bare: str, names: list[str]) -> None:
    """Form 4 본문 XML 의 뼈대. 내부자 매매 수집기를 만들 수 있는지 판단할 근거다 (16.3).

    목록의 primaryDocument 는 `xslF345X06/form4.xml`(사람이 보는 변환본)이다. 원본 XML 은
    같은 폴더의 `form4.xml` 이라는 것이 이 확인의 요점이다.
    """
    import xml.etree.ElementTree as ET

    xml_names = [n for n in names if n.endswith(".xml")]
    if not xml_names:
        print("   XML 파일이 없다")
        return
    url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{bare}/{xml_names[0]}"
    response = get(session, url)
    print(f"   원본 XML HTTP {response.status_code}, {len(response.content):,} bytes: {url}")
    if response.status_code != 200:
        return
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        print(f"   XML 해석 실패: {exc}")
        return
    print(f"   루트 <{root.tag}>, 자식 {[child.tag for child in root]}")
    for path in ("reportingOwner/reportingOwnerId/rptOwnerName", "issuer/issuerTradingSymbol"):
        node = root.find(path)
        print(f"   {path} = {node.text.strip() if node is not None and node.text else None}")
    for table in ("nonDerivativeTable", "derivativeTable"):
        node = root.find(table)
        if node is None:
            print(f"   {table} 없음")
            continue
        rows = list(node)
        print(f"   {table} 행 {len(rows)}개, 첫 행 자식 {[c.tag for c in rows[0]] if rows else []}")
        if rows:
            for path in (
                "transactionDate/value",
                "transactionCoding/transactionCode",
                "transactionAmounts/transactionShares/value",
                "transactionAmounts/transactionPricePerShare/value",
                "transactionAmounts/transactionAcquiredDisposedCode/value",
            ):
                node2 = rows[0].find(path)
                print(f"      {path} = {node2.text.strip() if node2 is not None and node2.text else None}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    args = parser.parse_args()
    agent = os.environ.get("SEC_USER_AGENT", "")
    if "@" not in agent:
        print("SEC_USER_AGENT 에 연락처(이메일)가 없습니다. SEC 는 403 으로 막는다")
        return 1
    print(f"SEC_USER_AGENT 길이 {len(agent)}")  # 값은 찍지 않는다 (이메일)

    session = requests.Session()
    session.headers.update({"User-Agent": agent, "Accept-Encoding": "gzip, deflate"})

    cik = find_cik(session, args.ticker)
    print(f"== {args.ticker} CIK {cik}")
    if not cik:
        return 1

    response = get(session, SUBMISSIONS.format(cik=cik))
    print(f"   submissions HTTP {response.status_code}, {len(response.content):,} bytes")
    if response.status_code != 200:
        return 1
    payload = response.json()
    print(f"   최상위 키 {sorted(payload.keys())}")
    filings = payload.get("filings") or {}
    print(f"   filings 키 {sorted(filings.keys())}")
    older = filings.get("files") or []
    print(f"   오래된 묶음(files) {len(older)}개" + (f", 첫 항목 {older[0]}" if older else ""))

    recent = filings.get("recent") or {}
    show_recent(recent)
    check_url(session, cik, recent)
    show_form4(session, cik, recent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
