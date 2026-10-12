"""실적 반응은 처음 발표일 둘레를 재고, 통화가 바뀐 해끼리는 견주지 않는다 (docs/infra.md 25.1237, DART 감사 재현)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.core import db
from batch.jobs import metrics as metrics_job
from batch.services import event_history as ev
from tests.test_portfolio_job import MemClient


def _날(n: int) -> list[str]:
    return [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def _재무(fy: int, rd: str, oi: float, cur: str = "KRW", first: str | None = None) -> dict:
    r = {"stock_id": 1, "fiscal_year": fy, "report_code": "11011", "consolidated": 1, "report_date": rd,
         "operating_income": oi, "net_income": 100.0, "currency": cur}  # fmt: skip
    if first:
        r["first_date"] = first
    return r


def test_정정일이_아니라_처음_발표일() -> None:
    날 = _날(700)
    c = [100.0] * 700
    i = 날.index("2025-03-14")
    for k in range(i, 700):
        c[k] = 110.0  # 처음 발표 날 +10%
    지수 = {d: 1000.0 for d in 날}
    # 2024 사업보고서 처음 2025-03-14, 정정 2025-06-02 — `financials.report_date` 는 정정일로 덮였다
    재무 = [_재무(2023, "2024-03-15", 100), _재무(2024, "2025-06-02", 150, first="2025-03-14")]
    e = ev.earnings_reactions(재무, 날, c, 지수)
    assert e["recent"][0]["date"] == "2025-03-14" and e["recent"][0]["x5"] == 0.1


def test_통화가_바뀐_해는_증가율이_없다() -> None:
    날 = _날(700)
    재무 = [_재무(2022, "2023-03-15", 1.0e12), _재무(2023, "2024-03-15", 8.0e8, cur="USD")]
    e = ev.earnings_reactions(재무, 날, [100.0] * 700, {d: 1000.0 for d in 날})
    assert all(x["yoy"] is None for x in e["recent"])


def test_처음_접수일을_스냅샷에서_붙인다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
              " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
              " receipt_no, currency, unit, operating_income, net_income, source, fetched_at)"
              " VALUES (1, 2024, '11011', 'A', 1, '2025-06-02', 'r2', 'KRW', 'KRW', 150, 50, 'dart', 't')")  # fmt: skip
    for d, rn in (("2025-03-14", "r1"), ("2025-06-02", "r2")):
        c.execute("INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
                  " consolidated, payload, source, fetched_at) VALUES (1, ?, ?, 2024, '11011', 1, '{}', 'dart', 't')",
                  [d, rn])  # fmt: skip
    경고: list[str] = []
    out = metrics_job.load_event_inputs(mem, "KR", "2026-10-12", 경고)  # type: ignore[arg-type]
    assert out["fin"][1][0]["first_date"] == "2025-03-14" and not 경고
