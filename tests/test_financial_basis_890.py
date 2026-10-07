"""재무 기준(연결·별도) 고르기를 종목마다 한 번만 — **결과는 예전과 같다** (docs/infra.md 25.890).

예전 질의는 재무 행마다 같은 종목의 기준 고르기를 다시 돌려 나라 전체 질의 하나가 재무 표를 7배쯤 읽었다.
고친 질의가 같은 행을 돌려주는지, 기준이 섞인 종목(별도만 있다가 연결이 생긴 해)으로 견준다.
"""

from __future__ import annotations

import pytest

from batch.jobs import scores, signals, valuation_bands
from tests.test_scores_job import _sqlite_client

_예전_조건 = (
    " WHERE s.country = ? AND f.report_code = ? AND f.consolidated ="
    " (SELECT fb.consolidated FROM financials fb"
    " WHERE fb.stock_id = f.stock_id AND fb.report_code = f.report_code AND fb.report_date <= ?"
    " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
)
예전 = {
    "scores": "SELECT f.stock_id, f.fiscal_year, f.net_income, f.total_equity, f.total_assets, f.total_liabilities,"
    " f.revenue, f.operating_income, f.current_assets, f.current_liabilities, f.noncurrent_liabilities, f.report_date, f.currency"
    " FROM financials f JOIN stocks s ON s.id = f.stock_id" + _예전_조건 + " AND f.report_date <= ?",
    "signals": "SELECT f.stock_id, f.fiscal_year, f.revenue, f.operating_income, f.consolidated, f.report_date, f.currency"
    " FROM financials f JOIN stocks s ON s.id = f.stock_id" + _예전_조건 + " AND f.report_date <= ?",
    "valuation_bands": "SELECT f.stock_id, f.report_date, f.total_equity, f.fiscal_year FROM financials f"
    " JOIN stocks s ON s.id = f.stock_id" + _예전_조건 + " AND f.total_equity IS NOT NULL AND f.report_date <= ?",
}


def _재무():
    c = _sqlite_client()
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (3, 'T3', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    행 = [
        # 종목 1: 별도만 있다가 2024년부터 연결 — 기준일에 따라 고르는 기준이 바뀐다
        (1, 2022, 0, "2023-03-20"), (1, 2023, 0, "2024-03-20"), (1, 2024, 1, "2025-03-20"), (1, 2024, 0, "2025-03-21"),
        # 종목 3: 연결만
        (3, 2023, 1, "2024-03-15"), (3, 2024, 1, "2025-03-15"),
        # 종목 2(미국): 나라가 달라 빠져야 한다
        (2, 2024, 1, "2025-02-01"),
    ]  # fmt: skip
    for sid, fy, cons, rd in 행:
        c.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, revenue,"
            " operating_income, net_income, total_equity, total_assets, total_liabilities, report_date, receipt_no,"
            " currency, unit, source, fetched_at)"
            " VALUES (?, ?, '11011', 'annual', ?, 100, 10, 5, 50, 200, 150, ?, ?, 'KRW', 'KRW', 't', 't')",
            [sid, fy, cons, rd, f"r{sid}{fy}{cons}"],
        )
    return c


@pytest.mark.parametrize("as_of", ["2024-06-30", "2025-03-20", "2025-06-30"])
@pytest.mark.parametrize("이름", list(예전))
def test_고친_질의가_예전과_같은_행을_돌려준다(이름: str, as_of: str) -> None:
    c = _재무()
    잡힌: list = []
    원래 = c.execute

    def 엿보기(sql, args=None):
        rs = 원래(sql, args)
        if "FROM b CROSS JOIN financials f" in sql:
            잡힌.append(sorted(rs.rows))
        return rs

    c.execute = 엿보기  # type: ignore[method-assign]
    {
        "scores": lambda: scores.load_financials(c, "KR", as_of),
        "signals": lambda: signals.load_growth(c, "KR", as_of),
        "valuation_bands": lambda: valuation_bands.load_equities(c, "KR", as_of),
    }[이름]()
    assert len(잡힌) == 1, "고친 질의를 거치지 않았다"
    예전_행 = sorted(원래(예전[이름], ["KR", "11011", as_of, as_of]).rows)
    assert 잡힌[0] == 예전_행
    assert 예전_행, "견줄 행이 있어야 한다"
