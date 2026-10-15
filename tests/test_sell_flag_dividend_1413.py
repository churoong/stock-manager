"""손절·목표 플래그에 매수 뒤 지난 배당 기준일 참고 행 (docs/infra.md 25.1413, docs/factors.md 12.28 P1)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.core import db
from batch.jobs import portfolio as pf_job
from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf
from tests.test_report_picks import SqliteClient
from tests.test_sell_flags import holding

오늘 = date(2026, 10, 20)


def _e(day: str, amt: str = "0", pay: str = "", stk: str = "0") -> dict:
    return {"record_date": day, "detail": {"per_sto_divi_amt": amt, "divi_pay_dt": pay, "stk_divi_rate": stk}}


def test_사실만_센다() -> None:
    사건 = [_e("2026-06-30", "500", "2026/08/20"), _e("2026-09-30", "300", ""), _e("2026-10-15", "0"),
            _e("2026-12-31", "1000")]  # fmt: skip
    n = sf.dividend_note(사건, ["2026-01-05"], 10, 100_000, "2026-01-05", 오늘, "2026-10-01")
    assert n is not None
    # 앞날 기준일(12-31)은 빠진다. 미정("0") 1건, 지급 전 1건(9-30 은 지급일 없음)
    assert (n["n"], n["per_share"], n["pending"], n["undetermined"]) == (3, 800.0, 1, 1)
    assert n["pct"] == pytest.approx(8.0)  # 800원 × 10주 / 10만 원
    assert n["uncovered_since"] == "2026-10-01"
    글 = sf.dividend_row(n, "2026-10-19")["display"]
    assert "주당 합 800원 = 원가의 8.00%p (지급 전 1건)" in 글 and "금액 미정 1건" in 글 and "수집 전이라 모름" in 글
    assert sf.dividend_row(n, None)["passed"] is None


def test_기준일_앞뒤_매매면_수량을_모른다() -> None:
    n = sf.dividend_note([_e("2026-09-30", "300", "2026/10/01")], ["2026-01-05", "2026-09-25"], 10, 1000,
                         "2026-01-05", 오늘, None)  # fmt: skip
    assert n is not None and n["qty_unknown"] == 1 and n["pct"] is None
    # 매수일 이전 기준일·주식배당
    assert sf.dividend_note([_e("2026-01-05", "300")], [], 10, 1000, "2026-01-05", 오늘, None) is None
    m = sf.dividend_note([_e("2026-09-30", "0", stk="0.05")], [], 10, 1000, "2026-01-05", 오늘, None)
    assert m is not None and m["stock_div"] == 1 and m["undetermined"] == 1


def test_손절_플래그에만_참고_행이_붙고_판정은_그대로() -> None:
    n = sf.dividend_note([_e("2026-09-30", "300")], [], 10, 1000, "2026-01-05", 오늘, None)
    h = holding(cost=1000.0, market_value=700.0, price_date="2026-10-19", dividend_note=n)
    flags = sf.evaluate(h, 오늘)
    손절 = [f for f in flags if f.reason_code == "손절"]
    assert 손절 and any(c["label"] == "매수 뒤 배당 기준일" for c in 손절[0].criteria)
    없음 = sf.evaluate(holding(cost=1000.0, market_value=700.0, price_date="2026-10-19"), 오늘)
    assert [f.reason_code for f in 없음] == [f.reason_code for f in flags]


class _C(SqliteClient):
    def close(self) -> None:
        pass


def test_적재가_예탁원_배당을_읽어_붙인다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _C()
    monkeypatch.setattr(db, "apply_migrations", lambda client: None)
    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(pf_job, "쓰는_중", lambda cl: False)
    k = c.conn
    k.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, asset_type)"
        " VALUES (1, '005930', 'KOSPI', 'KR', 'A', 'KRW', 'active', 't', 't', 'stock')"
    )
    k.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-07-01', 'short', '2026-10-19', 70, 700, 't')"
    )
    k.execute(
        "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " horizon, created_at, updated_at) VALUES (1, 'buy', '2026-07-01', 100, 10, 'KRW', 1, 'none', 'short', 't', 't')"
    )
    k.execute(
        "INSERT INTO kr_corp_events (code, kind, record_date, name, detail, source, fetched_at)"
        " VALUES ('005930', 'dividend', '2026-09-30', 'A', ?, 'kis', '2026-10-07T00:00:00+00:00')",
        [json.dumps({"per_sto_divi_amt": "5", "divi_pay_dt": "2026/11/20", "stk_divi_rate": "0"})],
    )
    job.run(오늘)
    글 = k.execute("SELECT rationale_data FROM sell_flags WHERE reason_code = '손절'").fetchone()[0]
    행 = [r for r in json.loads(글)["criteria"] if r["label"] == "매수 뒤 배당 기준일"]
    assert 행 and "원가의 5.00%p (지급 전 1건)" in 행[0]["display"] and "2026-09-30 전" in 행[0]["display"]
    로그 = k.execute("SELECT step_log FROM batch_runs WHERE job_name = 'sell_flags'").fetchone()[0]
    assert json.loads(로그)["dividend_note"] == {"stop_target": 1, "with_div": 1}
