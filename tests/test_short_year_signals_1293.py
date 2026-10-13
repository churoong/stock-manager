"""짧은 사업연도는 신호 중기 성장률·재무 건전성 Z″ 에서도 12개월 해와 견주지 않는다 (docs/infra.md 25.1293)."""

from __future__ import annotations

from batch.jobs import signals
from batch.services import insights
from tests.test_financial_basis_890 import _sqlite_client


def _재무(days_2024: int | None) -> object:
    c = _sqlite_client()
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
              " VALUES (9, 'T9', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    # 2023 은 분할 신설 첫해(days_2023) — 매출 60, 2024 는 12개월 매출 100
    for fy, rev, days, rd in ((2023, 60, days_2024, "2024-03-20"), (2024, 100, 366, "2025-03-20")):
        c.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, revenue,"
            " operating_income, report_date, receipt_no, currency, unit, source, fetched_at, period_days)"
            " VALUES (9, ?, '11011', 'annual', 1, ?, ?, ?, ?, 'KRW', 'KRW', 't', 't', ?)",
            [fy, rev, rev / 10, rd, f"r{fy}", days],
        )
    return c


def test_전년이_짧은_해면_성장률을_내지_않는다() -> None:
    g = signals.load_growth(_재무(214), "KR", "2025-06-30")[9]
    assert g["revenue_growth"] is None and g["operating_income_growth"] is None


def test_12개월_해끼리는_예전처럼() -> None:
    for days in (365, None):  # None = 기간을 모르는 옛 행
        g = signals.load_growth(_재무(days), "KR", "2025-06-30")[9]
        assert abs(g["revenue_growth"] - (100 / 60 - 1)) < 1e-9


def _건전성(days: int | None) -> dict:
    row = {"fiscal_year": 2024, "report_code": "11011", "consolidated": 1, "report_date": "2025-03-20",
           "current_assets": 50, "current_liabilities": 30, "total_assets": 200, "total_liabilities": 100,
           "retained_earnings": 40, "total_equity": 100, "operating_income": 10, "period_days": days}  # fmt: skip
    h = insights.health([row], None)
    assert h is not None
    return h


def test_짧은_해는_Z를_내지_않고_부채비율은_낸다() -> None:
    h = _건전성(214)
    assert h["z"] is None and "214일" in h["note"] and h["debt_ratio"] == 1.0
    assert _건전성(365)["z"] is not None and _건전성(None)["z"] is not None
