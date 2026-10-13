"""상승의 출처는 정정 접수일이 아니라 회계연도 순으로 끝 해를 고른다 (docs/infra.md 25.1290, 재무 감사 재현)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.services import event_history as eh


def test_옛_해_정정이_끝_해가_되지_않는다() -> None:
    ni = {2021: 100, 2022: 110, 2023: 120, 2024: 130, 2025: 200}
    first = {2021: "2022-03-15", 2022: "2023-03-14", 2023: "2024-03-12", 2024: "2025-03-11", 2025: "2026-03-10"}
    fin = [{"stock_id": 1, "fiscal_year": fy, "report_code": "11011", "consolidated": 1,
            "report_date": "2026-05-20" if fy == 2024 else first[fy],  # FY2024 정정 — 접수일이 덮였다
            "first_date": first[fy], "operating_income": v, "net_income": v, "currency": "KRW"}
           for fy, v in ni.items()]  # fmt: skip
    d, dates, closes = date(2022, 1, 3), [], []
    while d <= date(2026, 10, 12):
        if d.weekday() < 5:
            dates.append(d.isoformat())
            closes.append(100 + len(dates) * 0.1)
        d += timedelta(days=1)
    out = eh.return_sources(fin, [], dates, closes, shares=None)
    assert out is not None and (out["from_year"], out["to_year"], out["since"]) == (2022, 2025, "2023-03-14")
    assert abs(out["earnings"] - (200 / 110 - 1)) < 1e-3
