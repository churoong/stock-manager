"""시점 조회를 실제 데이터로 확인한다.

접수일 하루 전에는 안 보이고 접수일부터 보이는지를 눈으로 본다.
백테스트가 미래를 보지 않는다는 것을 실제 데이터로 확인하는 절차다.

실행
  python scripts/check_pit.py               삼성전자
  python scripts/check_pit.py 000660        종목코드 지정
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core.client import TursoClient  # noqa: E402
from batch.services import pit  # noqa: E402
from batch.sources import dart  # noqa: E402


def _report_label(fiscal_year: int, report_code: str) -> str:
    """회계연도만 찍으면 연간인지 분기인지 알 수 없다.

    2025 사업보고서와 2025 3분기보고서는 회계연도가 같지만 전혀 다른 숫자다.
    보고서 종류를 함께 찍지 않으면 값을 오해한다.
    """
    from batch.sources.dart import REPORT_CODES

    name = REPORT_CODES.get(report_code, (report_code,))[0]
    return f"{fiscal_year} {name}"


def _money(value) -> str:
    if value is None:
        return "-"
    return f"{int(value) / 1_000_000_000_000:,.1f}조"


def main() -> int:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "005930"

    with TursoClient() as client:
        rs = client.execute(
            "SELECT id, name_ko, dart_corp_code FROM stocks"
            " WHERE ticker = ? AND country = 'KR'",
            [ticker],
        )
        rows = rs.dicts()
        if not rows:
            print(f"{ticker} 종목을 찾지 못했습니다")
            return 1

        stock_id = int(rows[0]["id"])
        name = rows[0]["name_ko"]
        print(f"{name} ({ticker})  stock_id={stock_id}  고유번호={rows[0]['dart_corp_code']}")
        print()

        # 어떤 보고서를 언제 알 수 있게 됐는지
        rs = client.execute(
            "SELECT as_of_date, fiscal_year, report_code, receipt_no, consolidated"
            " FROM financial_snapshots WHERE stock_id = ?"
            " ORDER BY as_of_date DESC LIMIT 12",
            [stock_id],
        )
        snapshots = rs.dicts()
        if not snapshots:
            print("이 종목의 스냅샷이 없습니다. 재무 수집을 먼저 돌리세요")
            return 1

        print("=== 보관된 스냅샷 (공개일 최신순) ===")
        print(f"{'공개일':<12} {'회계연도':>6} {'보고서':>8} {'연결':>4}  접수번호")
        for s in snapshots:
            kind = "연결" if s["consolidated"] else "별도"
            print(
                f"{s['as_of_date']:<12} {s['fiscal_year']:>6} "
                f"{s['report_code']:>8} {kind:>4}  {s['receipt_no']}"
            )
        print()

        # 접수일 경계에서 답이 바뀌는지
        annual = [s for s in snapshots if s["report_code"] == dart.ANNUAL_REPORT_CODE and s["consolidated"]]
        if not annual:
            print("연결 사업보고서 스냅샷이 없어 경계 확인을 건너뜁니다")
            return 0

        target = annual[0]
        published = date.fromisoformat(str(target["as_of_date"]))

        print(f"=== 접수일 경계 확인 ({target['fiscal_year']} 사업보고서, {published}) ===")
        print(f"{'기준일':<12}  {'보이는 보고서':<22} {'매출':>10} {'순이익':>10}")

        for offset in (-2, -1, 0, 1):
            as_of = published + timedelta(days=offset)
            result = pit.financials_as_of(client, stock_id, as_of.isoformat())
            if result is None:
                print(f"{as_of.isoformat():<12}  {'없음':<22} {'-':>10} {'-':>10}")
                continue
            label = _report_label(result.fiscal_year, result.report_code)
            print(
                f"{as_of.isoformat():<12}  {label:<22} "
                f"{_money(result.values.get('revenue')):>10} "
                f"{_money(result.values.get('net_income')):>10}"
            )

        print()
        print("접수일 하루 전과 당일의 보고서가 다르면 시점 조회가 제대로 도는 것이다.")
        print("연간 실적은 사업보고서가 접수된 뒤에야 보인다.")

        # 한 종목이 옳다는 것과 **백테스트가 쓸 만큼 덮인다**는 것은 다르다.
        # 덮임이 낮으면 그 기간 백테스트는 재무 팩터를 거의 못 쓰고, 그래도 결과는 나온다 —
        # 그것이 가장 위험하다. 여기서 숫자로 보여 준다.
        # (`pit.coverage`·`pit.latest_known_date` 는 이 자리에 쓰라고 만들어 두고
        #  정작 아무도 부르지 않고 있었다. 2026-09-21, docs/infra.md 25.48)
        print()
        오늘 = date.today().isoformat()
        덮임 = pit.coverage(client, 오늘)
        비율 = (덮임["covered"] * 100 / 덮임["universe"]) if 덮임["universe"] else 0
        print("=== 유니버스 덮임 ===")
        print(f"{오늘} 기준 재무를 알 수 있는 종목 {덮임['covered']:,} / 유니버스 {덮임['universe']:,} ({비율:.0f}%)")
        마지막 = pit.latest_known_date(client, stock_id)
        print(f"이 종목이 가진 가장 최근 공개일: {마지막 or '없음'}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
