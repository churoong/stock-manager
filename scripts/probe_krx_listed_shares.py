"""KRX 일별매매정보가 **과거 기준일에 그날의 상장주식수**를 주는지 확인한다.

docs/factors.md 12.7 A, docs/infra.md 25.794. 기법 발굴 5회차 A(국내 시점 상장주식수)의 선행 조건이다.
검증 1 이 조건으로 걸었다: "과거 `basDd` 가 그날의 `LIST_SHRS` 를 주는지(오늘 값이 아닌지) 알려진 분할일
앞뒤를 한 번씩 불러 확인한다". 오늘 값을 돌려준다면 A 의 수집 설계(바뀐 날만 쌓기)는 쓸 수 없다.

보는 것
  1. 같은 종목의 LIST_SHRS 가 기준일마다 다른가 (모두 같으면 오늘 값일 수 있다 — 증자·분할·소각이 있던 종목을 고른다)
  2. 그날 MKTCAP ≈ 그날 종가 × 그날 LIST_SHRS 인가 (같은 날 짝이면 그날 값이다)

DB 를 건드리지 않는다. 호출 수는 (기준일 수 × 시장 수) 뿐이다.

실행
  KRX_API_KEY=... python scripts/probe_krx_listed_shares.py --tickers 005930,247540 --dates 20211001,20240102,20260929
"""

from __future__ import annotations

import argparse
import sys

from batch.sources import krx


def main() -> int:
    parser = argparse.ArgumentParser(description="KRX 과거 기준일의 상장주식수 확인")
    parser.add_argument("--tickers", required=True, help="단축코드, 쉼표로")
    parser.add_argument("--dates", required=True, help="기준일 YYYYMMDD, 쉼표로 (분할·증자 앞뒤를 고른다)")
    args = parser.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]

    값: dict[str, list[tuple[str, int | None, bool | None]]] = {t: [] for t in tickers}
    for bas_dd in dates:
        for market in ("KOSPI", "KOSDAQ"):
            result = krx.fetch_daily(market, bas_dd)
            if not result.ok:
                print(f"{bas_dd} {market}: 실패 {result.error}")
                continue
            for row in result.data or []:
                if row.isu_cd not in 값:
                    continue
                짝 = None
                if row.close and row.listed_shares and row.market_cap:
                    짝 = abs(row.close * row.listed_shares - row.market_cap) <= 0.001 * row.market_cap
                값[row.isu_cd].append((bas_dd, row.listed_shares, 짝))

    다름 = False
    for t, rows in 값.items():
        print(f"\n{t}")
        for bas_dd, shares, 짝 in rows:
            판 = "예" if 짝 else "아니오" if 짝 is False else "모름"
            print(f"  {bas_dd}  LIST_SHRS {shares}  시총=종가×주식수 {판}")
        if len({s for _, s, _ in rows}) > 1:
            다름 = True
    print(
        "\n판정: "
        + (
            "기준일마다 주식수가 다르다 — 그날 값으로 보인다"
            if 다름
            else "모든 기준일에서 같다 — 주식수가 바뀐 종목을 골랐는데도 같다면 오늘 값을 돌려주는 것이다"
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
