"""미국 ETF 전 종목 보유를 SEC N-PORT 공시로 받을 수 있는지 확인한다 (docs/etf.md 11장 계획, docs/data-sources.md 13.5).

야후 `topHoldings` 는 상위 10개뿐이다(13.3). "우리 장기 점수 가중평균" 을 내려면 전 종목이 필요하다.
N-PORT(NPORT-P)는 펀드가 분기마다 내는 전 종목 보유 공시다. 확인하는 것:
  1. ETF 티커 → 신고 주체 CIK·시리즈 ID 를 SEC 의 펀드 티커 목록(`company_tickers_mf.json`)으로 찾을 수 있나
  2. 그 CIK 의 NPORT-P 가운데 이 시리즈의 가장 최근 것을 찾을 수 있나 (한 신탁이 여러 시리즈를 낸다)
  3. 본문 XML 의 보유 행 수·비중 합·식별자(티커·ISIN·CUSIP) 채움 비율·기준일·공시일·파일 크기

인증키는 없다. SEC 는 연락처가 든 User-Agent 를 요구한다(`SEC_USER_AGENT` 시크릿). **UA 는 찍지 않는다.**
DB 를 건드리지 않는다.

실행
  SEC_USER_AGENT="..." python scripts/probe_etf_nport.py --tickers VOO,QQQ,SPY,VTI,SCHD
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import requests

FUND_TICKERS = "https://www.sec.gov/files/company_tickers_mf.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
HEADERS = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{accn}-index-headers.html"
DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/primary_doc.xml"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
#: 결제실패(FTD) 공개 파일 — CUSIP·심볼·이름이 함께 있다. 보름마다 나온다. CUSIP → 티커 매핑 후보
FTD = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{ym}{half}.zip"
#: 시리즈 단위 공시 목록 (atom). 공시가 수천 건인 신탁(iShares)에서 시리즈를 바로 찾는다
SERIES_FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series}&type=NPORT-P&count=10&output=atom"
INTERVAL = 0.15  # 초당 10회 한도 (docs/data-sources.md 14절)
#: 시리즈를 찾으려고 헤더를 훑는 NPORT-P 개수 상한 — 큰 신탁은 분기마다 수십 시리즈를 낸다
SCAN_LIMIT = 80


def get(session: requests.Session, url: str) -> requests.Response:
    time.sleep(INTERVAL)
    return session.get(url, timeout=60)


def fund_index(session: requests.Session) -> dict[str, tuple[str, str]]:
    """티커 → (CIK 10자리, 시리즈 ID). 응답은 {fields: [...], data: [[cik, seriesId, classId, symbol], ...]}."""
    body = get(session, FUND_TICKERS).json()
    fields = body.get("fields") or []
    print(f"펀드 티커 목록: 열 {fields}, 행 {len(body.get('data') or [])}")
    i_cik, i_series, i_sym = fields.index("cik"), fields.index("seriesId"), fields.index("symbol")
    return {str(r[i_sym]).upper(): (f"{int(r[i_cik]):010d}", str(r[i_series])) for r in body.get("data") or []}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def summarize_doc(xml_bytes: bytes) -> list[tuple[str, str, str, float]]:
    root = ET.fromstring(xml_bytes)
    rep = next((e.text for e in root.iter() if local(e.tag) == "repPdDate"), None)
    end = next((e.text for e in root.iter() if local(e.tag) == "repPdEnd"), None)
    holdings = [e for e in root.iter() if local(e.tag) == "invstOrSec"]
    pct_sum, with_ticker, with_isin, with_cusip, equity = 0.0, 0, 0, 0, 0
    samples = []
    rows: list[tuple[str, str, str, float]] = []
    for h in holdings:
        kids = {local(c.tag): c for c in h}
        pct = float(kids["pctVal"].text) if "pctVal" in kids and kids["pctVal"].text else 0.0
        pct_sum += pct
        cusip = (kids.get("cusip").text or "") if "cusip" in kids else ""
        if cusip and cusip not in ("N/A", "000000000"):
            with_cusip += 1
        ids = kids.get("identifiers")
        if ids is not None:
            names = {local(c.tag): c.attrib for c in ids}
            if "ticker" in names and names["ticker"].get("value"):
                with_ticker += 1
            if "isin" in names and names["isin"].get("value"):
                with_isin += 1
        if "assetCat" in kids and (kids["assetCat"].text or "") == "EC":
            equity += 1
        isin = ""
        if ids is not None:
            isin = next((c.attrib.get("value") or "" for c in ids if local(c.tag) == "isin"), "")
        rows.append((kids["name"].text if "name" in kids else "", cusip, isin, pct))
        if len(samples) < 3:
            name = kids["name"].text if "name" in kids else None
            tick = ids is not None and next((c.attrib.get("value") for c in ids if local(c.tag) == "ticker"), None)
            samples.append(f"{name} ticker={tick} pct={pct}")
    n = len(holdings) or 1
    print(f"   기준일 repPdDate={rep} 회계연도말 repPdEnd={end}")
    print(f"   보유 행 {len(holdings)} · 비중 합 {pct_sum:.2f}% · 주식(EC) {equity}")
    print(f"   식별자 채움: 티커 {with_ticker / n:.1%} · ISIN {with_isin / n:.1%} · CUSIP {with_cusip / n:.1%}")
    for s in samples:
        print(f"   예: {s}")
    return rows


def norm(name: str) -> str:
    """회사 이름 비교용 — 대문자, 구두점·법인 꼬리 제거."""
    x = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    꼬리 = (
        r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|PLC|HOLDINGS|HOLDING|GROUP|THE"
        r"|CLASS [A-Z]|CL [A-Z]|NV|SA|AG|LLC|LP)\b"
    )
    x = re.sub(꼬리, " ", x)
    return " ".join(x.split())


def load_maps(session: requests.Session) -> tuple[dict[str, str], dict[str, str]]:
    """(CUSIP → 심볼, 정규화 이름 → 심볼). FTD 는 최근 두 달치 네 파일을 합친다."""
    cusip: dict[str, str] = {}
    from datetime import date

    today = date.today()
    months = [(today.year, today.month - k) for k in range(1, 4)]
    for y, m in months:
        if m <= 0:
            y, m = y - 1, m + 12
        for half in ("a", "b"):
            r = get(session, FTD.format(ym=f"{y}{m:02d}", half=half))
            if r.status_code != 200:
                print(f"   FTD {y}{m:02d}{half} HTTP {r.status_code}")
                continue
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                text = z.read(z.namelist()[0]).decode("latin-1")
            for line in text.splitlines()[1:]:
                parts = line.split("|")
                if len(parts) >= 4 and parts[1] and parts[2]:
                    cusip.setdefault(parts[1].strip(), parts[2].strip())
    names: dict[str, str] = {}
    for row in get(session, TICKERS).json().values():
        names.setdefault(norm(str(row.get("title", ""))), str(row.get("ticker", "")))
    print(f"매핑표: FTD CUSIP {len(cusip):,}개 · SEC 이름 {len(names):,}개")
    return cusip, names


def match_rate(rows: list[tuple[str, str, str, float]], cusip: dict[str, str], names: dict[str, str]) -> None:
    tot = sum(p for *_, p in rows) or 1.0
    by_cusip = sum(p for _, c, _, p in rows if c in cusip)
    by_name = sum(p for n, c, _, p in rows if c not in cusip and norm(n) in names)
    miss = [(n, p) for n, c, _, p in rows if c not in cusip and norm(n) not in names]
    miss.sort(key=lambda x: -x[1])
    합 = (by_cusip + by_name) / tot
    print(f"   매칭(비중 기준): CUSIP {by_cusip / tot:.1%} + 이름 {by_name / tot:.1%} = {합:.1%}")
    print(f"   못 맞춘 큰 것: {[f'{n} {p:.2f}%' for n, p in miss[:5]]}")


def probe(session: requests.Session, index: dict[str, tuple[str, str]], ticker: str) -> list | None:
    print(f"\n== {ticker}")
    if ticker not in index:
        print("   펀드 티커 목록에 없음")
        return
    cik, series = index[ticker]
    print(f"   CIK {cik} · 시리즈 {series}")
    sub = get(session, SUBMISSIONS.format(cik=cik)).json()
    recent = sub["filings"]["recent"]
    rows = [
        (recent["accessionNumber"][i], recent["filingDate"][i], recent.get("reportDate", [None] * (i + 1))[i])
        for i in range(len(recent["form"]))
        if recent["form"][i] == "NPORT-P"
    ]
    print(f"   신고 주체 {sub.get('name')} — recent 안 NPORT-P {len(rows)}건")
    for accn, filed, report in rows[:SCAN_LIMIT]:
        nodash = accn.replace("-", "")
        head = get(session, HEADERS.format(cik=int(cik), nodash=nodash, accn=accn))
        if head.status_code != 200:
            print(f"   {accn} 헤더 HTTP {head.status_code}")
            continue
        if series not in head.text:
            continue
        found = re.findall(r"<SERIES-ID>(S\d+)", head.text)
        print(f"   찾음: {accn} 공시일 {filed} 기준 {report} (헤더의 시리즈 {sorted(set(found))[:3]})")
        doc = get(session, DOC.format(cik=int(cik), nodash=nodash))
        print(f"   primary_doc.xml HTTP {doc.status_code} · {len(doc.content) / 1e6:.1f}MB")
        return summarize_doc(doc.content) if doc.status_code == 200 else None
    print(f"   최근 NPORT-P {min(len(rows), SCAN_LIMIT)}건 안에서 이 시리즈를 못 찾음 — 시리즈 단위 목록으로")
    feed = get(session, SERIES_FEED.format(series=series))
    accns = re.findall(r"accession-n[ou]m(?:ber)?>([0-9-]{20})<", feed.text)
    print(f"   시리즈 목록 HTTP {feed.status_code} · 공시 {len(accns)}건 {accns[:2]}")
    if not accns:
        print(f"   atom 앞부분: {feed.text[:400]!r}")
        return None
    accn = accns[0]
    filer = accn.split("-")[0]
    doc = get(session, DOC.format(cik=int(filer), nodash=accn.replace("-", "")))
    if doc.status_code != 200:  # 공시 대행(filer agent) 번호면 신고 주체 CIK 밑에 있다
        doc = get(session, DOC.format(cik=int(cik), nodash=accn.replace("-", "")))
    print(f"   primary_doc.xml HTTP {doc.status_code} · {len(doc.content) / 1e6:.1f}MB")
    return summarize_doc(doc.content) if doc.status_code == 200 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="VOO,QQQ,SPY,VTI,SCHD")
    args = ap.parse_args()
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        print("SEC_USER_AGENT 가 없다")
        return 1
    session = requests.Session()
    session.headers["User-Agent"] = ua
    index = fund_index(session)
    try:
        cusip, names = load_maps(session)
    except Exception as exc:  # noqa: BLE001 — 매핑표 없이도 보유 확인은 한다
        print(f"매핑표 실패: {type(exc).__name__}: {exc}")
        cusip, names = {}, {}
    for t in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        try:
            rows = probe(session, index, t)
            if rows:
                match_rate(rows, cusip, names)
        except Exception as exc:  # noqa: BLE001 — 조사 스크립트다. 다음 티커로 간다
            print(f"   실패: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
