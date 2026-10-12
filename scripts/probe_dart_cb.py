"""DART 전환사채·신주인수권부사채 발행결정 응답 필드 탐침 (docs/factors.md 12.20 후보 3, docs/infra.md 25.1177).

18회차 보류 3 "전환사채 잠재 물량(`cb_overhang`)" 을 **사실 줄**로 만들기 전에, DART 주요사항보고서 API
(`cvbdIsDecsn`·`bdwtIsDecsn`)가 어떤 칸을 주는지 — 권면총액·전환(행사)가액·최저 조정가액(리픽싱 하한)·청구 시작일이
어느 칸에 오는지 — 한 번 본다. 엔드포인트·칸 이름은 `[확인필요]` 이고 이 탐침이 확인한다. 발행 회사는 그날 시장 전체
B 목록(`list.json`)에서 제목으로 고른다. 운영 DB 를 읽지도 쓰지도 않는다. 공시 내용(회사 정보)만 찍는다.

    python scripts/probe_dart_cb.py --days 20 --max 6
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

DART = "https://opendart.fss.or.kr/api"
#: (제목 낱말, 엔드포인트 후보 `[확인필요]`)
KINDS = (("전환사채권발행결정", "cvbdIsDecsn.json"), ("신주인수권부사채권발행결정", "bdwtIsDecsn.json"))
#: 값을 찍을 칸을 고르는 낱말 — 칸 이름을 모르니 이름의 조각으로 고른다(가액 prc · 일자 de/bgd/edd · 총액 fta · 비율 rt)
#: 목록 쪽 상한 — 20일치 B 는 대개 5쪽 안이다
MAX_PAGES = 10
FIELD_HINTS = ("prc", "fta", "bgd", "edd", "rt", "cnt", "stk", "lwtr", "rfx", "adj")


def pick_issuers(rows: list, limit: int) -> list[tuple[str, str, str, str]]:
    """그날 목록 → (회사 고유번호, 종목코드, 엔드포인트, 접수일 YYYYMMDD) 를 회사마다 하나씩. 정정은 뺀다."""
    out: list[tuple[str, str, str, str]] = []
    본 = set()
    for d in rows:
        t = d.title.replace(" ", "")
        if "정정" in t:
            continue
        for 낱말, ep in KINDS:
            if 낱말 in t and (d.corp_code, ep) not in 본:
                본.add((d.corp_code, ep))
                out.append((d.corp_code, d.stock_code, ep, d.disclosed_at.replace("-", "")))
        if len(out) >= limit:
            break
    return out[:limit]


def summarize(body: dict) -> list[str]:
    """응답 JSON → 사람이 읽을 줄. 칸 이름 전부와, 낱말 조각이 든 칸의 값."""
    out = [f"status {body.get('status')} {body.get('message', '')}"]
    rows = body.get("list") or []
    if not isinstance(rows, list) or not rows:
        return out
    keys = sorted({k for r in rows if isinstance(r, dict) for k in r})
    out.append(f"행 {len(rows)} · 칸 {keys}")
    for r in rows[:3]:
        if isinstance(r, dict):
            out.append("  - " + str({k: str(v)[:60] for k, v in r.items() if any(h in k for h in FIELD_HINTS)}))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=20, help="오늘부터 거슬러 볼 날 수")
    p.add_argument("--max", type=int, default=6, help="부를 발행 회사 수 상한")
    args = p.parse_args()
    if not os.environ.get("DART_API_KEY"):
        print("DART_API_KEY 가 비었다")
        return 1
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from batch.sources import dart_disclosures as dd

    key = os.environ["DART_API_KEY"]
    calls = 0
    # 기간 전체를 한 번에(쪽마다 100건) — 하루씩 받으면 15분 제한에 걸렸다(10-12 첫 실행 취소)
    끝, 시작 = date.today(), date.today() - timedelta(days=max(1, args.days))
    rows: list = []
    for page in range(1, MAX_PAGES + 1):
        r = requests.get(f"{DART}/list.json", timeout=30, params={
            "crtfc_key": key, "bgn_de": 시작.strftime("%Y%m%d"), "end_de": 끝.strftime("%Y%m%d"),
            "pblntf_ty": "B", "page_no": page, "page_count": 100})  # fmt: skip
        calls += 1
        body = r.json()
        if str(body.get("status")) != "000":
            print(f"목록 {page}쪽: status {body.get('status')} {body.get('message', '')}")
            break
        rows += dd.parse_market(body, "B")
        if page >= int(body.get("total_page") or 1):
            break
    고른 = pick_issuers(rows, args.max)
    print(f"고른 발행 {len(고른)}건")
    for corp, ticker, ep, de in 고른[: args.max]:
        r = requests.get(f"{DART}/{ep}", timeout=30,
                         params={"crtfc_key": key, "corp_code": corp, "bgn_de": de, "end_de": de})  # fmt: skip
        calls += 1
        try:
            body = r.json()
        except ValueError:
            print(f"{ticker} {ep}: HTTP {r.status_code} JSON 아님 {r.text[:120]!r}")
            continue
        print(f"# {ticker} {ep} {de}")
        for line in summarize(body):
            print(line)
    print(f"호출 {calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
