"""국내 상장 ETF 구성종목(PDF)을 운용사 홈페이지에서 받을 수 있는지 살핀다 (docs/etf.md 11.5, infra 25.970).

한국거래소 Open API 에는 구성종목이 없고 정보데이터시스템은 로그인이 필요하다(data-sources 13.2). 운용사는 ETF 마다
구성종목(PDF)을 홈페이지에 매일 공시한다. 상품 페이지를 받아 **구성종목을 내려받는 주소**(엑셀·JSON·ajax)가 무엇인지
찾는다. 이 세션 컨테이너에서는 운용사 도메인이 막혀 Actions 에서 돈다. DB 를 건드리지 않는다.

실행
  python scripts/probe_kr_etf_pdf.py
"""

from __future__ import annotations

import re
import sys

import requests

PAGES = {
    "KODEX 메인(이용 조건)": "https://www.samsungfund.com/etf/main.do",
    "삼성펀드 메인(이용 조건)": "https://www.samsungfund.com/fund/main.do",
    "TIGER 200": "https://investments.miraeasset.com/tigeretf/ko/product/search/detail/index.do?ksdFund=KR7102110004",
    "TIGER 미국S&P500": "https://investments.miraeasset.com/tigeretf/ko/product/search/detail/index.do?ksdFund=KR7360750004",
    "KODEX 200": "https://www.samsungfund.com/etf/product/view.do?id=2ETF01",
    "KODEX 문서": "https://www.samsungfund.com/etf/product/library/pdf.do",
}
UA = {"User-Agent": "Mozilla/5.0 (personal research; stock-manager)"}
WORDS = re.compile(r"(pdf|Pdf|PDF|excel|Excel|xls|구성종목|ajax|json|api/)", re.I)
URLISH = re.compile(r"""["'](/[^"'\s]{4,200}|https?://[^"'\s]{8,200})["']""")


def main() -> int:
    s = requests.Session()
    s.headers.update(UA)
    for label, url in PAGES.items():
        print(f"\n== {label}\n   {url}")
        try:
            r = s.get(url, timeout=30)
        except requests.RequestException as exc:
            print(f"   실패: {type(exc).__name__}: {exc}")
            continue
        print(f"   HTTP {r.status_code} · {len(r.content):,}B · {r.headers.get('content-type')}")
        text = r.text
        found = sorted({u for u in URLISH.findall(text) if WORDS.search(u)})
        print(f"   주소 후보 {len(found)}개")
        for u in found[:40]:
            print(f"     {u}")
        if "이용 조건" in label:
            for m in list(re.finditer(r"무단|데이터베이스|사전\s*(허가|승인|동의)|저작권|크롤링", text))[:8]:
                ctx = re.sub(r"<[^>]+>", " ", text[max(0, m.start() - 200) : m.end() + 200])
                flat = re.sub(r"\s+", " ", ctx)
                print(f"   조건 …{flat}…")
        # 화면 스크립트가 부르는 API 주소 — 단일 페이지 앱이라 구성종목은 스크립트가 따로 불러온다
        if "samsungfund" in url and label == "KODEX 200":
            scripts = sorted(set(re.findall(r'<script[^>]+src="([^"]+)"', text)))
            print(f"   스크립트 {len(scripts)}개")
            apis: set[str] = set()
            for src in scripts:
                full = src if src.startswith("http") else "https://www.samsungfund.com" + src
                try:
                    js = s.get(full, timeout=30).text
                except requests.RequestException:
                    continue
                apis |= set(re.findall(r"/api/v1/[A-Za-z0-9_/.-]+", js))
            for src in scripts:  # 호출 모양 — 앞뒤 글자
                full = src if src.startswith("http") else "https://www.samsungfund.com" + src
                try:
                    js = s.get(full, timeout=30).text
                except requests.RequestException:
                    continue
                for m in list(re.finditer(r"product-pdf/", js))[:4]:
                    ctx = re.sub(r"\s+", " ", js[max(0, m.start() - 400) : m.end() + 400])
                    print(f"   호출 …{ctx}…")
            for cand in (
                "https://www.samsungfund.com/api/v1/kodex/product-pdf/2ETF01.do",
                "https://www.samsungfund.com/api/v1/kodex/product-pdf/2ETF01",
                "https://www.samsungfund.com/api/v1/kodex/product-pdf-top10/2ETF01.do",
                "https://www.samsungfund.com/api/v1/kodex/product-pdf/2ETF01.do?gijunYMD=20261002",
                "https://www.samsungfund.com/api/v1/kodex/product/list.do",
                "https://www.samsungfund.com/api/v1/kodex/product.do",
            ):
                try:
                    r2 = s.get(cand, timeout=30, headers={"Referer": url, "Accept": "application/json"})
                    print(f"   시도 {cand} → HTTP {r2.status_code} · {len(r2.content):,}B · {r2.text[:600]!r}")
                except requests.RequestException as exc:
                    print(f"   시도 {cand} → {exc}")
            for q in ("", "?pageNo=2", "?page=2", "?pageIndex=2", "?pageSize=300", "?rowCnt=300", "?size=300"):
                try:
                    lst = "https://www.samsungfund.com/api/v1/kodex/product.do" + q
                    r3 = s.get(lst, timeout=30, headers={"Referer": url})
                    body = r3.json()
                    tickers = [x.get("stkTicker") for x in body] if isinstance(body, list) else []
                    cols = sorted(body[0])[:40] if tickers else ""
                    print(f"   목록{q or ' (인자 없음)'} → {len(tickers)}개 · 앞 {tickers[:3]} · 열 {cols}")
                except Exception as exc:  # noqa: BLE001 — 조사 스크립트
                    print(f"   목록{q} → {type(exc).__name__}")
            print(f"   API 전체 {len(apis)}개 (위는 구성종목 관련으로 보이는 것)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
