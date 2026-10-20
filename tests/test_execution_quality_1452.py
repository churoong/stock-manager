"""실제 매매의 실행 품질 (docs/paper.md 13장, docs/infra.md 25.1452) — 주문표 준수·1차 가격 대비 체결가·플래그 뒤 매도."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import weekly_summary as ws
from batch.services import execution_quality as eq
from tests.test_paper_job_1445 import Client

계획 = [{"stock_id": 1, "report_date": "2026-09-01", "prices": [10_000, 9_800, 9_600]}]
추천 = [{"stock_id": 2, "report_date": "2026-09-01"}]


def test_buys_split_by_plan_and_limit() -> None:
    매매 = [
        {"stock_id": 1, "side": "buy", "trade_date": "2026-09-02", "price": 9_900},   # 주문표 안 −1%
        {"stock_id": 1, "side": "buy", "trade_date": "2026-09-03", "price": 10_300},  # 주문표 밖으로 +3% 비싸게
        {"stock_id": 1, "side": "buy", "trade_date": "2026-09-04", "price": 10_000},  # 1차 가격 그대로 = 안
        {"stock_id": 1, "side": "buy", "trade_date": "2026-09-20", "price": 9_000},   # 7일 넘음 → 리포트 밖
        {"stock_id": 2, "side": "buy", "trade_date": "2026-09-02", "price": 50_000},  # 1부에만
        {"stock_id": 1, "side": "buy", "trade_date": "2026-08-30", "price": 1},       # 기간 밖
    ]  # fmt: skip
    m = eq.measure(매매, 계획, 추천, [], "2026-09-01", "2026-09-30")
    assert (m["buys_on_plan"], m["buys_within_limit"], m["buys_pick_only"], m["buys_off_plan"]) == (3, 2, 1, 1)
    assert m["vs_first_avg"] == pytest.approx(2 / 3)  # (−1 + 3 + 0) / 3
    줄 = eq.lines(m)
    assert "주문표(2부) 따른 것 3 · 1부에만 있던 것 1 · 리포트 밖 1" in 줄[1]
    assert "1차 가격 이하 2/3건, 체결가는 1차 가격보다 평균 +0.67%" in 줄[2]


def test_sells_after_flag_and_delay() -> None:
    플래그 = [{"stock_id": 1, "reason_code": "손절", "level": "red", "first_seen": "2026-09-05"},
              {"stock_id": 1, "reason_code": "기간초과", "level": "yellow", "first_seen": "2026-09-08"},
              {"stock_id": 3, "reason_code": "손절", "level": "red", "first_seen": "2026-05-01"}]  # fmt: skip
    매매 = [{"stock_id": 1, "side": "sell", "trade_date": "2026-09-10", "price": 1},
            {"stock_id": 3, "side": "sell", "trade_date": "2026-09-10", "price": 1},   # 플래그가 60일 넘게 전 → 없음
            {"stock_id": 4, "side": "sell", "trade_date": "2026-09-11", "price": 1}]  # fmt: skip
    m = eq.measure(매매, [], [], 플래그, "2026-09-01", "2026-09-30")
    assert (m["sells_after_flag"], m["sell_delay_avg"], m["sells_no_flag"]) == (1, 5.0, 2)
    assert m["sells_by_level"] == {"red": 1}
    assert "플래그가 선 뒤 판 것 1(처음 선 날부터 평균 5.0일) · 플래그 없이 판 것 2" in eq.lines(m)[1]


def test_no_trades_no_section() -> None:
    assert eq.lines(eq.measure([], 계획, 추천, [], "2026-09-01", "2026-09-30")) == []
    assert eq.lines(None) == []


def test_loader_reads_reports_trades_flags_on_real_schema() -> None:
    c = Client()
    c.conn.execute("INSERT INTO daily_reports (id, market, trade_date, status, generated_at, summary_text, warnings_json)"
                   " VALUES (1, 'KR', '2026-09-01', 'success', 't', 'x', '[]')")  # fmt: skip
    짐 = {"ticker": "005930", "tranches": [{"price_at_or_below": 10_000}, {"price_at_or_below": 9_800}]}
    c.conn.execute("INSERT INTO report_items (report_id, section, stock_id, rank, payload_json) VALUES (1, 'buy_signal', 1, 1, ?)",
                   [json.dumps(짐)])  # fmt: skip
    now = "2026-09-02T00:00:00+00:00"
    c.conn.execute(
        "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at,"
        " updated_at) VALUES (1, 'buy', '2026-09-02', 10100, 3, 'KRW', 1, 'none', ?, ?)", [now, now],
    )  # fmt: skip
    줄 = ws.execution_lines(c, datetime(2026, 10, 3, tzinfo=UTC))  # 한국 10-03 = 월 첫 주 → 9월을 본다
    assert 줄[0] == "[실행 품질 — 실제 매매 2026-09-01~2026-09-30]"
    assert "체결가는 1차 가격보다 평균 +1.00%" in "\n".join(줄)
    assert ws.execution_lines(c, datetime(2026, 10, 15, tzinfo=UTC)) == []  # 따라잡기 창(14일) 밖이면 없다
    # 8~14일은 이번 달 월간 복기를 아직 안 실었을 때만 따라잡는다(25.1571)
    assert ws.execution_lines(c, datetime(2026, 10, 12, tzinfo=UTC)) != []
    from batch.core import db as _db
    from batch.jobs import paper as _pj

    _db.set_setting(c, _pj.MONTHLY_REVIEW_KEY, "2026-10")
    assert ws.execution_lines(c, datetime(2026, 10, 12, tzinfo=UTC)) == []
