"""KIS Open API 가운데 쓸 만한 것들이 실제로 응답하는지·과거를 얼마나 주는지 살핀다
(docs/data-sources.md 3, infra 25.987).

2026-10-07 사용자 지시("모두 진행해줘") — ETF 구성종목·예탁원 일정·투자자별 매매·공매도·신용잔고·프로그램매매·투자의견·
추정실적·순위·업종지수·시간외·해외 현재가. 경로와 tr_id 는 한국투자증권 공식 예제 저장소(open-trading-api) 기준이고
**이 계정에서 되는지 모른다** — 그래서 한 번씩 불러 본다. 값은 시장 데이터(종목·가격)뿐이고 토큰·키는 찍지 않는다.
결과는 비공개 운영 저장소 이슈로만 간다(`ops_tee`). DB 를 건드리지 않는다. 주문·계좌 API 는 부르지 않는다.

실행
  KIS_APP_KEY=… KIS_APP_SECRET=… python scripts/probe_kis_catalog.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://openapi.koreainvestment.com:9443"
KST = timezone(timedelta(hours=9))
Q = "/uapi/domestic-stock/v1/quotations/"
ETF = "/uapi/etfetn/v1/quotations/inquire-component-stock-price"
K = "/uapi/domestic-stock/v1/ksdinfo/"


def cases(today: str, month_ago: str, year_ago: str, ahead: str) -> list[tuple[str, str, str, dict[str, str]]]:
    """(이름, 경로, tr_id, 인자)"""
    return [
        ("A1 ETF 구성종목(KODEX 200)", ETF, "FHKST121600C0",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "069500", "FID_COND_SCR_DIV_CODE": "11216"}),
        ("A1 ETF 구성종목(TIGER 200)", ETF, "FHKST121600C0",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "102110", "FID_COND_SCR_DIV_CODE": "11216"}),
        ("A1 ETF 구성종목(TIGER 미국S&P500)", ETF, "FHKST121600C0",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "360750", "FID_COND_SCR_DIV_CODE": "11216"}),
        ("A2 예탁원 배당일정(앞으로)", K + "dividend", "HHKDB669102C0",
         {"CTS": "", "GB1": "0", "F_DT": today, "T_DT": ahead, "SHT_CD": "", "HIGH_GB": ""}),
        ("A2 예탁원 액면교체(분할·병합)", K + "rev-split", "HHKDB669104C0",
         {"CTS": "", "SHT_CD": "", "MARKET_GB": "0", "F_DT": year_ago, "T_DT": ahead}),
        ("A2 예탁원 무상증자", K + "bonus-issue", "HHKDB669101C0",
         {"CTS": "", "F_DT": month_ago, "T_DT": ahead, "SHT_CD": ""}),
        ("A2 예탁원 유상증자", K + "paidin-capin", "HHKDB669100C0",
         {"CTS": "", "GB1": "1", "F_DT": month_ago, "T_DT": ahead, "SHT_CD": ""}),
        ("B1 투자자별 매매(005930)", Q + "inquire-investor", "FHKST01010900",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
        ("B2 공매도 일별(005930, 1년)", Q + "daily-short-sale", "FHPST04830000",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_DATE_1": year_ago,
          "FID_INPUT_DATE_2": today}),
        ("B3 신용잔고 일별(005930)", Q + "daily-credit-balance", "FHPST04760000",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20476", "FID_INPUT_ISCD": "005930",
          "FID_INPUT_DATE_1": today}),
        ("B4 프로그램매매 종목별(005930)", Q + "program-trade-by-stock", "FHPPG04650100",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
        ("B5 투자의견(005930, 1년)", Q + "invest-opinion", "FHKST663300C0",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633", "FID_INPUT_ISCD": "005930",
          "FID_INPUT_DATE_1": year_ago, "FID_INPUT_DATE_2": today}),
        ("B5 추정실적(005930)", Q + "estimate-perform", "HHKST668300C0", {"SHT_CD": "005930"}),
        ("C1 거래량 순위", Q + "volume-rank", "FHPST01710000",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": "0000",
          "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
          "FID_TRGT_EXLS_CLS_CODE": "000000", "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
          "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""}),
        ("C2 체결(체결강도, 005930)", Q + "inquire-ccnl", "FHKST01010300",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
        ("C2 호가(005930)", Q + "inquire-asking-price-exp-ccn", "FHKST01010200",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
        ("C3 업종 현재지수(코스피)", Q + "inquire-index-price", "FHPUP02100000",
         {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"}),
        ("C4 시간외 현재가(005930)", Q + "inquire-overtime-price", "FHPST02300000",
         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
        ("A4 해외 현재가(AAPL)", "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
         {"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL"}),
        ("A4 해외 현재가 상세(AAPL)", "/uapi/overseas-price/v1/quotations/price-detail", "HHDFS76200200",
         {"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL"}),
    ]  # fmt: skip


def _rows(body: dict) -> list[dict]:
    for key in ("output", "output1", "output2"):
        v = body.get(key)
        if isinstance(v, list) and v:
            return [r for r in v if isinstance(r, dict)]
        if isinstance(v, dict) and v:
            return [v]
    return []


def _date_span(rows: list[dict]) -> str:
    keys = [k for k in (rows[0] if rows else {}) if k.endswith("_dt") or k.endswith("date") or k in ("stck_bsop_date",)]
    vals = sorted(str(r.get(k)) for r in rows for k in keys[:1] if r.get(k))
    return f"{vals[0]} ~ {vals[-1]}" if vals else "-"


def main() -> int:
    key, secret = os.environ.get("KIS_APP_KEY", ""), os.environ.get("KIS_APP_SECRET", "")
    if not key or not secret:
        print("KIS_APP_KEY·KIS_APP_SECRET 가 비어 있다")
        return 1
    # 발급은 1분 1회 — 앞 단계(`probe_kis.py`)가 막 받았으면 403 이다. 한 번 기다렸다 다시 받는다
    for wait in (0, 65):
        time.sleep(wait)
        r = requests.post(f"{BASE}/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": key,
                          "appsecret": secret}, timeout=20)  # fmt: skip
        if r.status_code != 403:
            break
    token = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("access_token")
    print(f"토큰: HTTP {r.status_code} · 받음={'예' if token else '아니오'}")
    if not token:
        return 1
    now = datetime.now(KST)
    f = "%Y%m%d"
    head = {"authorization": f"Bearer {token}", "appkey": key, "appsecret": secret, "custtype": "P"}
    days = [now + timedelta(days=d) for d in (0, -30, -365, 60)]
    only = [s for s in os.environ.get("ONLY", "").split(",") if s]
    for name, path, tr, params in cases(*(d.strftime(f) for d in days)):
        if only and not any(name.startswith(o) for o in only):
            continue
        time.sleep(0.4)
        try:
            resp = requests.get(BASE + path, params=params, headers=head | {"tr_id": tr}, timeout=20)
            body = resp.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"\n## {name}\n  실패 {type(exc).__name__}")
            continue
        # 출력이 여럿이면(output1 요약 · output2 목록) 모양을 다 찍는다
        for key in ("output1", "output2", "output3"):
            v = body.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                print(f"  [{key}] {len(v)}행 · 날짜 {_date_span(v)} · 열 {', '.join(list(v[0])[:30])}")
                print("    예: " + " · ".join(f"{k}={str(x)[:18]}" for k, x in list(v[0].items())[:14]))
        rows = _rows(body)
        print(f"\n## {name}\n  HTTP {resp.status_code} · rt_cd={body.get('rt_cd')} msg_cd={body.get('msg_cd')} "
              f"msg={str(body.get('msg1', '')).strip()[:50]!r} · 행 {len(rows)} · 날짜 {_date_span(rows)}")  # fmt: skip
        if rows:
            print(f"  열: {', '.join(list(rows[0])[:40])}")
            for row in rows[:2]:
                print("  예: " + " · ".join(f"{k}={str(v)[:20]}" for k, v in list(row.items())[:14]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
