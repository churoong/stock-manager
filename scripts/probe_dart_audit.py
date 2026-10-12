"""DART 감사의견 응답 필드 탐침 (docs/factors.md 12.20, docs/infra.md 25.1138).

17회차(12.19) 보류 5 "계속기업 불확실성·비적정 감사의견" 을 **사실 줄**로 만들기 전에, DART 정기보고서 주요정보
"회계감사인의 명칭 및 감사의견" 응답에 어떤 칸이 오는지(감사의견·강조사항·계속기업 문구가 어디에 오나) 한 번 본다.
엔드포인트 이름과 칸 이름은 `[확인필요]` — 이 탐침이 확인한다. 운영 DB 를 읽지도 쓰지도 않는다.
공시 내용(회사 정보)만 찍는다.

    python scripts/probe_dart_audit.py --tickers 005930,000660 --year 2025
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

DART = "https://opendart.fss.or.kr/api"
#: 엔드포인트 후보 `[확인필요]` — 앞에서부터 불러 보고 status 000 이 오는 것을 적는다
ENDPOINTS = ("accnutAdtorNmNdAdtOpinion.json",)
#: 기본 표본 — 대형·중형 섞어 열(사용자 보유와 무관)
TICKERS = ("005930", "000660", "005380", "035420", "051910", "068270", "096770", "034020", "058470", "007660")
#: 감사의견·계속기업을 찾을 낱말 — 칸 이름을 모르니 값에서 찾는다
KEYWORDS = ("적정", "한정", "부적정", "의견거절", "계속기업", "강조", "특기")


def summarize(body: dict) -> list[str]:
    """응답 JSON → 사람이 읽을 줄. 칸 이름 목록과, 낱말이 든 값만."""
    status = str(body.get("status"))
    out = [f"status {status} {body.get('message', '')}"]
    rows = body.get("list") or []
    if not isinstance(rows, list) or not rows:
        return out
    keys = sorted({k for r in rows if isinstance(r, dict) for k in r})
    out.append(f"행 {len(rows)} · 칸 {keys}")
    for r in rows[:6]:
        if not isinstance(r, dict):
            continue
        hits = {k: str(v)[:120] for k, v in r.items() if any(w in str(v) for w in KEYWORDS)}
        out.append(f"  - {r.get('bsns_year', '')} {r.get('adtor', '') or r.get('adtor_nm', '')} {hits}")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", default="")
    p.add_argument("--year", default="2025")
    args = p.parse_args()
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 가 비었다")
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from batch.sources import dart

    codes = dart.fetch_corp_codes()
    if not codes.ok:
        print(f"고유번호 실패: {codes.error}")
        return 1
    by_ticker = {c.stock_code: c for c in codes.data}
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip().isdigit()] or list(TICKERS)
    calls = 0
    for ticker in tickers:
        corp = by_ticker.get(ticker)
        if corp is None:
            print(f"{ticker}: 고유번호 없음")
            continue
        for ep in ENDPOINTS:
            params = {"crtfc_key": key, "corp_code": corp.corp_code, "bsns_year": args.year,
                      "reprt_code": dart.ANNUAL_REPORT_CODE}
            r = requests.get(f"{DART}/{ep}", timeout=30, params=params)
            calls += 1
            try:
                body = r.json()
            except ValueError:
                print(f"{ticker} {ep}: HTTP {r.status_code} JSON 아님 {r.text[:120]!r}")
                continue
            print(f"# {corp.corp_name} ({ticker}) {ep}")
            for line in summarize(body):
                print(line)
    print(f"호출 {calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
