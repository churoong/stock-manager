"""DART 임원ㆍ주요주주 소유보고 API 의 실제 응답 모양을 확인한다 (docs/data-sources.md 16.1).

수집기를 만들기 전에 한 번 돌린다. 키 이름과 첫 두 행만 찍는다 — 공시 자료라 비밀이 아니다.
인증키는 찍지 않는다. Actions "DART 내부자 API 확인" 이 이 스크립트를 부른다.

실행
  DART_API_KEY=... python scripts/probe_dart_insider.py --corp-code 00126380   # 삼성전자
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

# 추정 엔드포인트. 틀리면 그 자체가 확인 결과다 (HTTP 상태와 본문을 그대로 찍는다)
CANDIDATES = ("elestock.json", "majorstock.json")


def probe(endpoint: str, corp_code: str, key: str) -> None:
    url = f"https://opendart.fss.or.kr/api/{endpoint}"
    print(f"== {endpoint} corp_code={corp_code}")
    try:
        response = requests.get(url, params={"crtfc_key": key, "corp_code": corp_code}, timeout=30)
    except requests.RequestException as exc:
        print(f"   호출 실패: {exc}")
        return
    print(f"   HTTP {response.status_code}, {len(response.content):,} bytes")
    try:
        body = response.json()
    except ValueError:
        print(f"   JSON 아님. 앞 300자: {response.text[:300]!r}")
        return
    print(f"   status={body.get('status')} message={body.get('message')!r}")
    rows = body.get("list") or []
    print(f"   행 수 {len(rows)}, 최상위 키 {sorted(body.keys())}")
    if not rows:
        return
    print(f"   행 키: {sorted(rows[0].keys())}")
    for row in rows[:2]:
        print("   " + json.dumps(row, ensure_ascii=False))
    describe(rows)


def describe(rows: list[dict]) -> None:
    """파서를 쓰기 전에 알아야 하는 것: 증감의 부호 표기, 빈 값, 날짜 범위, 값의 종류.

    감소(매도) 행이 어떻게 적히는지 모르면 부호를 추측하게 된다.
    """
    delta_key = "sp_stock_lmp_irds_cnt" if "sp_stock_lmp_irds_cnt" in rows[0] else "stkqy_irds"
    dates = sorted(str(r.get("rcept_dt", "")) for r in rows)
    print(f"   접수일 범위 {dates[0]} ~ {dates[-1]}")

    negatives = [r for r in rows if str(r.get(delta_key, "")).lstrip().startswith(("-", "△", "▲", "("))]
    blanks = [r for r in rows if str(r.get(delta_key, "")).strip() in ("", "-", "0")]
    print(f"   {delta_key}: 감소로 보이는 행 {len(negatives)}개, 빈 값·0 {len(blanks)}개")
    for row in negatives[:2]:
        print("   (감소) " + json.dumps(row, ensure_ascii=False))
    for row in blanks[:1]:
        print("   (빈값·0) " + json.dumps(row, ensure_ascii=False))

    for key in ("isu_exctv_rgist_at", "isu_exctv_ofcps", "isu_main_shrholdr", "report_tp"):
        if key in rows[0]:
            values = sorted({str(r.get(key, "")) for r in rows})
            print(f"   {key} 값 {len(values)}종: {values[:8]}")

    # 같은 접수번호가 여러 행으로 오는가 (UNIQUE 를 무엇으로 잡을지에 필요하다)
    receipts = [str(r.get("rcept_no", "")) for r in rows]
    print(f"   접수번호 {len(set(receipts))}종 / 행 {len(receipts)}개")


def probe_period(corp_code: str, key: str) -> None:
    """기간 파라미터가 먹히는지. 먹히면 응답이 작아져 주 1회 수집이 훨씬 가볍다.

    문서에 없는 파라미터라 무시될 수도 있다. 행 수를 전체와 비교해 판단한다.
    """
    url = "https://opendart.fss.or.kr/api/elestock.json"
    for params in ({"bgn_de": "20260101", "end_de": "20261231"}, {"de": "20260101"}):
        try:
            body = requests.get(url, params={"crtfc_key": key, "corp_code": corp_code, **params}, timeout=30).json()
        except (requests.RequestException, ValueError) as exc:
            print(f"== 기간 시도 {params}: 실패 {exc}")
            continue
        rows = body.get("list") or []
        print(f"== 기간 시도 {params}: status={body.get('status')} 행 {len(rows)}개")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corp-code", default="00126380", help="DART 고유번호 (기본 삼성전자)")
    args = parser.parse_args()
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 가 비어 있습니다 (길이 0)")
        return 1
    print(f"DART_API_KEY 길이 {len(key)}")
    for endpoint in CANDIDATES:
        probe(endpoint, args.corp_code, key)
    probe_period(args.corp_code, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
