"""계좌별 세후 적립 시뮬레이션 (docs/etf.md 11.7, docs/infra.md 25.1003) — 손으로 셀 수 있는 고정 데이터."""

from __future__ import annotations

import pytest

from batch.services import tax_sim as ts

RATES = {"kr_dividend_pct": 15.4, "us_dividend_pct": 15.0, "us_capital_gains_pct": 22.0,
         "pension_income_pct": 5.5, "pension_credit_pct": 13.2}  # fmt: skip


def test_가격만_오르면_달마다_복리() -> None:
    import unittest.mock as um

    with um.patch.multiple(ts, MONTHLY_KRW=100, PRICE_RETURN_PCT=12.0, DIST_YIELD_PCT=0.0):
        v, basis, dt = ts._grow(1, 15.4)
    assert v == pytest.approx(100 * sum(1.01**k for k in range(1, 13)))
    assert basis == 1200 and dt == 0


def test_수익이_없으면_세금은_연금_쪽만() -> None:
    import unittest.mock as um

    with um.patch.multiple(ts, PRICE_RETURN_PCT=0.0, DIST_YIELD_PCT=0.0):
        rows = {(r["key"], r["years"]): r for r in ts.simulate(RATES)["rows"]}
    넣은 = 500_000 * 12 * 10
    assert rows[("general_kr_equity", 10)]["after_tax"] == 넣은
    assert rows[("general_us_listed", 10)]["tax"] == 0
    # 연금: 받을 때 원금 전부 5.5% (전부 세액공제 받음) + 13.2% 공제 환급
    assert rows[("pension_kr_listed_foreign", 10)]["after_tax"] == round(넣은 * (1 - 0.055) + 넣은 * 0.132)


def test_세율이_비거나_범위_밖이면_숫자를_내지_않는다() -> None:
    rows = ts.simulate({**RATES, "us_capital_gains_pct": None, "pension_income_pct": 999})["rows"]
    us = next(r for r in rows if r["key"] == "general_us_listed")
    pension = next(r for r in rows if r["key"] == "pension_kr_listed_foreign")
    assert us["after_tax"] is None and us["missing"] == ["us_capital_gains_pct"]
    assert pension["after_tax"] is None and pension["missing"] == ["pension_income_pct"]
    assert all(r["after_tax"] is None for r in ts.simulate(None)["rows"])


def test_과세_구조_순서가_계좌별_추천과_같다() -> None:
    """docs/etf.md 11.1 — 연금이 해외 ETF 에 가장 유리, 일반계좌는 국내 주식 ETF 가 먼저(매매차익 비과세)."""
    rows = {r["key"]: r["after_tax"] for r in ts.simulate(RATES)["rows"] if r["years"] == 20}
    assert rows["pension_kr_listed_foreign"] > rows["general_kr_equity"] > rows["general_kr_listed_foreign"]
    assert rows["general_kr_listed_foreign"] > rows["general_us_listed"]


def test_포트폴리오_재계산이_요약에_저장한다(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from batch.jobs import portfolio as job
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    from batch.core import db

    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES ('taxes', ?, 't')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [json.dumps({"kr_dividend_pct": 15.4})],
    )
    assert job.run() == 0
    sim = json.loads(mem.conn.execute("SELECT tax_sim_json FROM portfolio_summary WHERE id = 1").fetchone()[0])
    by = {(r["key"], r["years"]): r for r in sim["rows"]}
    assert by[("general_kr_equity", 10)]["after_tax"] is not None  # 국내 배당소득세만 넣었다
    assert by[("general_us_listed", 10)]["missing"] == ["us_dividend_pct", "us_capital_gains_pct"]
