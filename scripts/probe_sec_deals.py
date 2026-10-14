"""SEC 일별 양식 색인에 인수·합병 공시가 어떻게 실리는지 본다 — 20회차 P5 탐침 (docs/factors.md 12.22, infra 25.1329).

검증 둘의 조건: (1) 색인 경로·형식, (2) 공개매수(SC TO-T)·대상 회사 의견(SC 14D9)·합병 위임장(DEFM14A·PREM14A)이
**대상 회사 CIK 로** 실리는지(한 접수번호가 여러 CIK 에 실리는지), (3) 2024~25 양식 이름 변경 뒤 지금 이름 `[확인필요]`.
이 탐침은 해석하지 않고 센다 — 날짜마다 양식 이름(인수·합병 관련 낱말이 든 것)별 행 수, 접수번호 하나가 몇 CIK 에
실렸는지, 지금 상장 심볼로 이어지는 CIK 비율. 운영 DB 를 읽지도 쓰지도 않는다. 출력은 비공개 운영 저장소 이슈로만.

실행: python scripts/probe_sec_deals.py --dates 20250602,20250715
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from batch.sources import sec_edgar as sec  # noqa: E402

INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d}.idx"
#: 양식 이름에 이 낱말이 들면 센다 — 공개매수·대상 의견·합병 위임장·비공개화 [확인필요: 지금 이름]
WORDS = ("14D", "TO-T", "TO-C", "TO-I", "DEFM14", "PREM14", "13E3", "425")


def index_url(day: str) -> str:
    y, m = int(day[:4]), int(day[4:6])
    return INDEX_URL.format(y=y, q=(m - 1) // 3 + 1, d=day)


def parse_index(text: str) -> list[tuple[str, str, str, str]]:
    """(양식, 회사, CIK, 파일 경로). 고정 폭이다 — 머리 줄의 "Company Name" 자리까지가 양식 칸(양식 이름에 빈칸이
    있다: "SC 14D9"). 뒤 세 칸(CIK·날짜·경로)은 오른쪽에서 끊는다(회사 이름에 빈칸이 있다). 머리를 못 찾으면 빈 목록."""
    out: list[tuple[str, str, str, str]] = []
    자리: int | None = None
    본문 = False
    for line in text.splitlines():
        if not 본문:
            if "Company Name" in line:
                자리 = line.index("Company Name")
            본문 = line.startswith("---") and 자리 is not None
            continue
        parts = line[자리:].split()
        if len(parts) < 4 or not parts[-3].isdigit():
            continue
        out.append((line[:자리].strip(), " ".join(parts[:-3]), parts[-3], parts[-1]))
    return out


def relevant(form: str) -> bool:
    return any(w in form for w in WORDS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dates", default="20250602,20250715,20250910")
    args = ap.parse_args()
    client = sec.SecClient()
    상장 = set(client.ticker_map().values())
    for day in [d.strip() for d in args.dates.split(",") if d.strip()]:
        r = client._get(index_url(day))
        print(f"\n[{day}] HTTP {r.status_code} {index_url(day)}")
        if r.status_code != 200:
            continue
        rows = [x for x in parse_index(r.text) if relevant(x[0])]
        print(f"  관련 행 {len(rows)} — 양식별 {dict(Counter(f for f, *_ in rows).most_common(12))}")
        by_path: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for form, _name, cik, path in rows:
            by_path[path].append((form, cik))
        print(f"  접수번호당 CIK 수 {dict(Counter(len(v) for v in by_path.values()))}")
        for form in sorted({f for f, *_ in rows}):
            ciks = [cik for f, _n, cik, _p in rows if f == form]
            이어짐 = sum(1 for c in ciks if c.zfill(10) in 상장 or c in 상장)
            print(f"  {form}: 행 {len(ciks)}, 지금 상장 심볼로 이어지는 CIK {이어짐}")
        for path, v in list(by_path.items())[:6]:
            print(f"  보기 {path}: {v}")
    print(f"\nSEC 호출 {client.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
