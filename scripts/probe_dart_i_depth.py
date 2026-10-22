"""DART 거래소공시(I 종류) 과거 깊이 탐침 (docs/factors.md 12.40 B 순서 ②, docs/infra.md 25.1674).

37회차 후보 B(실적 발표 반응 `ear_x_mom3`·`pead_event`)는 I 덮은 날 구간 안의 잠정실적 공시가 재료다. 판정 기준이
"I 백필이 2021 년까지 안 되면 판정 불가" 라, 백필을 설계하기 전에 DART `list.json` 이 I 를 **얼마나 과거까지·어느 길로**
주는지 한 번 본다 `[확인필요]`:
  ① 시장 전체 하루치(회사 없음) — 해마다 2월 3일 뒤 첫 국내 거래일 하루씩: status·전체 건수·첫 쪽의 실적 공시 수
  ② 회사 하나 해마다(회사 있음) — 해마다 1-01~12-31 첫 쪽: status·전체 건수·첫 쪽의 실적 공시 수·회사 이름
     (긴 구간 한 번은 DART 기본 정렬이 접수일 내림차순이라 쪽 상한에 걸리면 "가장 이른 해" 가 늦게 나온다 — 25.1680)
운영 DB 를 읽지도 쓰지도 않는다(Turso 0). 공시 제목(회사 공개 정보)과 건수만 찍는다. 호출 수 상한을 둔다.

    python scripts/probe_dart_i_depth.py --corps 00126380,00164779 --years 2015-2026
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import calendar as cal  # noqa: E402
from batch.services.earnings_events import is_result_title  # noqa: E402

DART = "https://opendart.fss.or.kr/api"
#: 이 탐침 전체 호출 상한 — DART 하루 한도(2만, docs/data-sources.md)의 일일 배치 몫을 건드리지 않게
MAX_CALLS = 300
#: 기본 표본 — 삼성전자·SK하이닉스(대형, 잠정실적을 꾸준히 냄) `[확인필요: 고유번호]`. 워크플로 입력으로 바꾼다
DEFAULT_CORPS = "00126380,00164779"


def summarize_rows(rows: list[dict[str, Any]]) -> tuple[Counter, Counter, str | None]:
    """목록 행 → (해마다 건수, 해마다 실적 공시 수, 가장 이른 접수일). 접수일은 접수번호 앞 8자리(없으면 `rcept_dt`)."""
    해, 실적 = Counter(), Counter()
    이른: str | None = None
    for r in rows:
        날 = str(r.get("rcept_no") or "")[:8] or str(r.get("rcept_dt") or "")
        if len(날) != 8 or not 날.isdigit():
            continue
        해[날[:4]] += 1
        if is_result_title(str(r.get("report_nm") or "")):
            실적[날[:4]] += 1
        이른 = 날 if 이른 is None or 날 < 이른 else 이른
    return 해, 실적, 이른


class Caller:
    def __init__(self, key: str, max_calls: int = MAX_CALLS) -> None:
        self.key, self.max_calls, self.calls = key, max_calls, 0

    def page(self, params: dict[str, Any]) -> dict[str, Any] | None:
        if self.calls >= self.max_calls:
            print(f"호출 상한 {self.max_calls} — 멈춤")
            return None
        self.calls += 1
        try:
            r = requests.get(f"{DART}/list.json", timeout=30,
                             params={"crtfc_key": self.key, "pblntf_ty": "I", "page_count": 100, **params})  # fmt: skip
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            # 예외 문구의 URL 에 인증키가 있다 — 종류만 찍는다(25.621)
            print(f"   호출 실패: {type(exc).__name__}")
            return {}


def sample_day(y: int) -> date:
    """그해 2월 3일 뒤 첫 국내 거래일 — 평일만 고르면 2019-02-04(설 연휴)처럼 휴장일이 걸려 그해만 0건으로 "구멍" 처럼
    읽혔다(25.1680, 교차검증 [권고])."""
    return next(date(y, 2, k) for k in range(3, 20) if cal.is_session("KR", date(y, 2, k)))


def market_days(c: Caller, years: range) -> None:
    print("== ① 시장 전체 하루치 (회사 없음, 해마다 2월 3일 뒤 첫 거래일 — 4분기 잠정실적 철)")
    for y in years:
        d = sample_day(y)
        de = d.strftime("%Y%m%d")
        body = c.page({"bgn_de": de, "end_de": de, "page_no": 1})
        if body is None:
            return
        rows = body.get("list") or []
        _, 실적, _ = summarize_rows(rows)
        print(f"   {de}: status {body.get('status')} 전체 {body.get('total_count')}건 {body.get('total_page')}쪽"
              f" · 첫 쪽 {len(rows)}행 중 실적 {sum(실적.values())}")  # fmt: skip


def corp_years(c: Caller, corp: str, years: range) -> None:
    """회사 하나를 해마다 첫 쪽만 — 전체 건수(`total_count`)는 정렬과 상관없고, 첫 쪽(100건)의 실적 공시 수는 그해 I 가
    100건 안이면 전부다."""
    print(f"== ② 회사 {corp} 해마다")
    이름 = None
    for y in years:
        body = c.page({"corp_code": corp, "bgn_de": f"{y}0101", "end_de": f"{y}1231", "page_no": 1})
        if body is None:
            return
        rows = body.get("list") or []
        이름 = 이름 or next((r.get("corp_name") for r in rows if r.get("corp_name")), None)
        _, 실적, 이른 = summarize_rows(rows)
        print(f"   {y}: status {body.get('status')} 전체 {body.get('total_count')}건 {body.get('total_page')}쪽"
              f" · 첫 쪽 {len(rows)}행 중 실적 {sum(실적.values())} · 첫 쪽 가장 이른 {이른}")  # fmt: skip
    # 고유번호가 맞는 회사인지 탐침이 스스로 보인다(기본값 `[확인필요]`)
    print(f"   회사 이름 {이름}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--corps", default=DEFAULT_CORPS, help="쉼표로 나눈 DART 고유번호")
    p.add_argument("--years", default="2015-2026", help="시장 전체 하루치를 볼 해 범위 (예: 2015-2026)")
    p.add_argument("--start", default="2010", help="회사별로 볼 첫 해")
    args = p.parse_args()
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 가 비었다")
        return 1
    a, b = (int(x) for x in args.years.split("-"))
    c = Caller(key)
    market_days(c, range(a, b + 1))
    for corp in [x.strip() for x in args.corps.split(",") if x.strip()]:
        corp_years(c, corp, range(int(args.start[:4]), date.today().year + 1))
    print(f"호출 {c.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
