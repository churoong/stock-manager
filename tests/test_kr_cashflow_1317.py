"""국내 영업현금흐름 채우기 (docs/infra.md 25.1317, 19회차 A1) — 접수번호·자기정합·시작일·백테스트 문."""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.core import db
from batch.jobs import backtest as bj
from batch.jobs import kr_cashflow as job
from batch.sources import dart
from batch.sources.yfinance_src import FetchResult
from tests.test_portfolio_job import MemClient

행 = [
    {"rcept_no": "20260310000100", "sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities",
     "account_nm": "영업활동현금흐름", "thstrm_amount": "1,200"},
    {"rcept_no": "20260310000100", "sj_div": "CF", "account_id": "dart_CashGeneratedFromOperations",
     "account_nm": "영업에서 창출된 현금", "thstrm_amount": "1,500"},
    {"rcept_no": "20260310000100", "sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "thstrm_amount": "900"},
    {"rcept_no": "20260310000100", "sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "10,000"},
]  # fmt: skip


def test_합계_행과_접수번호만() -> None:
    p = dart.parse_full_statement(행)
    assert p == {"receipt_no": "20260310000100", "operating_cash_flow": 1200, "net_income": 900, "total_assets": 10000}
    섞임 = [*행, {**행[0], "rcept_no": "20260520000300"}]
    assert dart.parse_full_statement(섞임)["receipt_no"] is None  # 판을 모르면 None


def test_접수번호가_같고_정합할_때만_붙인다() -> None:
    p = dart.parse_full_statement(행)
    assert job.matched_value("20260310000100", {"net_income": 900, "total_assets": 10000}, p) == (1200, "채움")
    assert job.matched_value("20260310000999", {}, p)[0] is None  # 정정본 — 미래 정보
    assert job.matched_value("20260310000100", {"net_income": 901}, p)[1] == "자기정합 실패(net_income)"


def test_시작일_전에는_아무것도_하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job, "TursoClient", lambda: (_ for _ in ()).throw(AssertionError("DB 를 열었다")))
    assert job.run(date(2026, 11, 3)) == 0


def _db() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, dart_corp_code)"
              " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '00126380')")  # fmt: skip
    c.execute("INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code, consolidated,"
              " payload, source, fetched_at) VALUES (1, '2026-03-10', '20260310000100', 2025, '11011', 1, ?, 't', 't')",
              [json.dumps({"net_income": 900, "total_assets": 10000})])  # fmt: skip
    return mem


def test_채우고_끝을_적고_백테스트가_그때부터_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    assert "operating_cash_flow" not in bj.load_snapshots(mem, "KR")[1][0]["values"]  # type: ignore[arg-type]
    monkeypatch.setattr(dart, "fetch_full_statement",
                        lambda *a, **k: FetchResult(ok=True, source=dart.SOURCE, data=행))  # fmt: skip
    assert job.run(date(2026, 11, 4)) == 0
    payload = json.loads(mem.conn.execute("SELECT payload FROM financial_snapshots").fetchone()[0])
    assert payload["operating_cash_flow"] == 1200
    assert db.get_setting(mem, job.DONE_KEY) == "2026-11-04"  # type: ignore[arg-type]
    assert bj.load_snapshots(mem, "KR")[1][0]["values"]["operating_cash_flow"] == 1200  # type: ignore[arg-type]


def test_끝_열쇠가_같다() -> None:
    assert bj.KR_CFO_DONE_KEY == job.DONE_KEY


def test_끝_기록이_없으면_백테스트가_국내_영업현금흐름을_지운다() -> None:
    mem = _db()
    mem.conn.execute("UPDATE financial_snapshots SET payload = json_set(payload, '$.operating_cash_flow', 1200)")
    assert "operating_cash_flow" not in bj.load_snapshots(mem, "KR")[1][0]["values"]  # type: ignore[arg-type]
    db.set_setting(mem, job.DONE_KEY, "2026-11-20")  # type: ignore[arg-type]
    assert bj.load_snapshots(mem, "KR")[1][0]["values"]["operating_cash_flow"] == 1200  # type: ignore[arg-type]
