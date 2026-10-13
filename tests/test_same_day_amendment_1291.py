"""같은 날 원공시·정정이 함께 접수되면 연간 시점 재무도 접수번호가 큰 정정을 쓴다 — SUE 와 같은 판 (docs/infra.md 25.1291)."""

from __future__ import annotations

import json

from batch.jobs import backtest as bj
from batch.services import pit
from batch.services import quarterly_earnings as qe
from tests.test_report_picks import SqliteClient


def _db() -> SqliteClient:
    c = SqliteClient()
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                   " VALUES (1, '000001', 'KOSPI', 'KR', 'x', 'KRW', 'active', 't', 't')")  # fmt: skip
    # 원공시(오전) 순이익 100 을 먼저 저장하고, 같은 날 오후 기재정정 순이익 40 을 나중에 저장한다
    for rcept, ni in (("20260310000100", 100), ("20260310000900", 40)):
        c.conn.execute(
            "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code, consolidated,"
            " payload, source, fetched_at) VALUES (1, '2026-03-10', ?, 2025, '11011', 1, ?, 't', 't')",
            [rcept, json.dumps({"net_income": ni, "operating_income": ni, "total_equity": 5000, "revenue": 1000})],
        )
    return c


def test_백테스트_연간_재무가_정정을_쓴다() -> None:
    annual = bj.load_snapshots(_db(), "KR")[1]
    cur, _, _ = bj.pit_financials(annual, "2026-04-01")
    assert cur is not None and cur["values"]["net_income"] == 40
    assert bj.year_values(annual, "2026-04-01", cur)["net_income"] == 40


def test_시점_재무_조회가_정정을_쓴다() -> None:
    got = pit.financials_as_of(_db(), 1, "2026-04-01")
    assert got is not None and got.values["net_income"] == 40


def test_SUE_와_같은_판() -> None:
    c = _db()
    known, _ = qe._latest(bj.load_quarter_snapshots(c, "KR")[1], "2026-04-01")
    cur, _, _ = bj.pit_financials(bj.load_snapshots(c, "KR")[1], "2026-04-01")
    assert cur is not None and known[(2025, "11011")]["values"]["net_income"] == cur["values"]["net_income"]


def test_입력_순서와_무관() -> None:
    rows = [{"as_of_date": "2026-03-10", "fiscal_year": 2025, "receipt_no": r, "values": {"net_income": v}}
            for r, v in (("20260310000900", 40), ("20260310000100", 100))]  # fmt: skip
    for order in (rows, rows[::-1]):
        cur, _, _ = bj.pit_financials(order, "2026-04-01")
        assert cur is not None and cur["values"]["net_income"] == 40
