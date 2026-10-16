"""KIS 체결 칸 · KIS모의 체결 기준 보정(근사·참고) (docs/paper.md 15.9, docs/desk.md 8장, docs/infra.md 25.1514)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from batch.jobs import broker_mirror as bmj
from batch.jobs import desk as job
from batch.services import broker_mirror as bm
from batch.services import desk
from tests.test_paper_job_1445 import Client


def _줄(side: str, st: str, i_st: str, *, typ: str = "market", basis: str = "open", fq: int = 0,
       avg: float | None = None, ip: float | None = None, slip: float = 0.0, day: str = "2026-10-16",
       fd: str | None = None, reason: str = "베낌") -> dict[str, Any]:  # fmt: skip
    return {"trade_date": day, "side": side, "status": st, "filled_qty": fq, "avg_price": avg, "reason": reason,
            "i_status": i_st, "i_price": ip, "i_fill_date": fd or day, "i_order_type": typ, "i_fill_basis": basis,
            "i_slip_pct": slip}  # fmt: skip


def test_cells_net_out_booked_slippage_value_weighted() -> None:
    """시장가 매수 — KIS 0.3% 불리 · 장부가 이미 0.1% 얹음 → 넘음 0.2%. 금액 가중: (100×0.2 + 300×0.6)/400 = 0.5"""
    s = bm.cells([
        _줄("buy", "filled", "filled", fq=1, avg=100.3, ip=100.0, slip=0.1),
        _줄("buy", "filled", "filled", fq=1, avg=300.0 * 1.007, ip=300.0, slip=0.1, day="2026-10-19"),
        # 장 열린 뒤 낸 주문(종가 판정)·정리 주문·모름은 칸에 넣지 않는다(모름은 건수만)
        _줄("buy", "filled", "filled", basis="close", fq=1, avg=200.0, ip=100.0),
        _줄("buy", "filled", "filled", fq=1, avg=200.0, ip=100.0, reason=bm.REASON_CLEANUP),
        _줄("buy", "unknown", "filled", ip=100.0),
    ])  # fmt: skip
    c = s["cells"]["buy_market"]
    assert (c["n"], c["days"], c["both"], c["unknown"], c["n_price"]) == (2, 2, 2, 1, 2)
    w1, w2 = 100.3, 300.0 * 1.007
    assert c["excess"] == pytest.approx((w1 * 0.2 + w2 * 0.6) / (w1 + w2))
    assert c["median"] == pytest.approx(0.4)
    # 거래일 묶음 표준오차 — 날 평균 0.2·0.6 → 표준편차 0.2828 ÷ √2 = 0.2
    assert c["se"] == pytest.approx(0.2)
    assert s["cells_by_month"] == {"2026-10": {"buy_market": pytest.approx(c["excess"])}}


def test_cells_day_cluster_and_miss_extra() -> None:
    """같은 날 두 주문은 한 거래일 — se 는 날 평균으로. 지정가 매수 miss·extra"""
    s = bm.cells([
        _줄("sell", "filled", "filled", fq=1, avg=99.0, ip=100.0, slip=0.1),   # 매도 1% 불리 → 넘음 0.9
        _줄("sell", "filled", "filled", fq=1, avg=99.0, ip=100.0, slip=0.1),
        _줄("buy", "filled", "filled", typ="limit", fq=1, avg=98.0, ip=100.0),  # 지정가보다 싸게 — 음수
        _줄("buy", "unfilled", "filled", typ="limit", ip=100.0),                # 가상만 참
        _줄("buy", "filled", "expired", typ="limit", fq=1, avg=100.0),          # KIS만 참
        _줄("buy", "unfilled", "expired", typ="limit"),                         # 둘 다 안 참
        _줄("buy", "unfilled", "expired", typ="limit"),
    ])  # fmt: skip
    매도 = s["cells"]["sell_market"]
    assert 매도["days"] == 1 and 매도["excess"] == pytest.approx(0.9) and 매도["se"] is None  # 날 하나면 se 없음
    지정 = s["cells"]["buy_limit"]
    assert 지정["miss"] == pytest.approx(50.0) and 지정["extra"] == pytest.approx(100 / 3)
    assert 지정["excess"] == pytest.approx(-2.0)


def test_cells_ignores_other_session_fill() -> None:
    """미룬 매도가 뒤 장에 찬 것은 이 날 주문과 짝이 아니다(compare 와 같은 잣대)"""
    s = bm.cells([_줄("sell", "filled", "filled", fq=1, avg=99.0, ip=100.0, fd="2026-10-19")])
    assert s["cells"]["sell_market"]["kis_only"] == 1 and s["cells"]["sell_market"]["excess"] is None


def _f(side: str, typ: str, d: str, price: float, q: int, *, market: str = "KR", basis: str = "open",
       lim: float | None = None, op: float | None = None, fx: float = 1.0) -> dict[str, Any]:  # fmt: skip
    return {"market": market, "side": side, "order_type": typ, "fill_basis": basis, "fill_date": d, "price": price,
            "quantity": q, "fx_rate": fx, "limit_price": lim, "open": op}  # fmt: skip


def _칸(excess: float, se: float, days: int = 25) -> dict[str, Any]:
    return {"days": days, "excess": excess, "se": se}


def test_fill_adjust_by_hand() -> None:
    """평가 1,000만. 시장가 매수 100만(회전 10%) × 넘음 0.2 = 0.02%p, 폭 10% × 2×0.05 = 0.01.
    매도 200만(국내 칸 표본 부족) → 안 잰 회전 20%. 지정가 매수 100만 — 지정가 100, 시가 98 → 개선 2% × 10% = 0.2%p.
    구간 전 체결은 뺀다. 미국 체결은 원화로(환율)"""
    평가 = {"X": [("2026-10-01", 10_000_000.0), ("2026-10-10", 20_000_000.0)]}
    체결 = {"X": [
        _f("buy", "market", "2026-10-05", 10_000, 100),
        _f("sell", "market", "2026-10-06", 20_000, 100),
        _f("buy", "limit", "2026-10-07", 100, 10_000, lim=100.0, op=98.0),
        _f("buy", "limit", "2026-10-08", 100, 10_000, lim=100.0, op=None),  # 시가 없음 — 못 잼
        _f("buy", "market", "2026-09-30", 10_000, 100),                     # 구간 전
        _f("buy", "market", "2026-10-10", 100, 1, market="US", fx=1_000.0),  # 평가 2,000만 기준 회전 0.5%
    ]}  # fmt: skip
    칸 = {"KR": {"buy_market": _칸(0.2, 0.05), "sell_market": _칸(0.3, 0.1, days=5)},
         "US": {"buy_market": _칸(1.0, 0.0)}}  # fmt: skip
    x = desk.fill_adjust(fills=체결, equity=평가, starts={"X": "2026-10-01"}, cells=칸, min_days=20)["X"]
    assert x["fills"] == 5
    assert x["adj_pp"] == pytest.approx(0.1 * 0.2 + 0.005 * 1.0)
    assert x["band_pp"] == pytest.approx(0.1 * 2 * 0.05)
    assert x["unmeasured_pct"] == pytest.approx(20.0)
    assert x["pi_pp"] == pytest.approx(0.1 * 2.0) and x["pi_missing"] == 1
    assert x["limit_buy_pct"] == pytest.approx(20 / 30.5 * 100)
    assert x["turnover_pct"] == pytest.approx(10 + 20 + 10 + 10 + 0.5)


def test_fill_adjust_skips_limit_buy_cell_and_close_basis() -> None:
    """지정가 매수 칸은 KIS 가 재도 보정에 안 쓴다(가격 개선을 비용으로 두 번 세지 않게) · 종가 판정은 안 잰 회전"""
    평가 = {"X": [("2026-10-01", 1_000.0)]}
    체결 = {"X": [_f("buy", "limit", "2026-10-02", 100, 1, lim=100.0, op=100.0),
                 _f("sell", "market", "2026-10-02", 100, 1, basis="close")]}  # fmt: skip
    칸 = {"KR": {"buy_limit": _칸(-3.0, 0.1), "sell_market": _칸(0.5, 0.1)}}
    x = desk.fill_adjust(fills=체결, equity=평가, starts={"X": "2026-10-01"}, cells=칸, min_days=20)["X"]
    assert x["adj_pp"] == 0 and x["pi_pp"] == 0 and x["unmeasured_pct"] == pytest.approx(10.0)


def test_adjust_rows_keeps_order_and_flags_close_calls() -> None:
    순위 = {"rows": [{"id": "A", "window_pct": 5.0}, {"id": "B", "window_pct": 4.9}, {"id": "C", "window_pct": 1.0},
                    {"id": "Y", "window_pct": 9.0, "young": True}]}  # fmt: skip
    보정 = {a: {"adj_pp": adj, "band_pp": band, "pi_pp": 0.0, "unmeasured_pct": 0.0}
          for a, adj, band in (("A", 0.3, 0.05), ("B", 0.0, 0.05), ("C", 0.0, 0.1), ("Y", 0.0, 9.0))}  # fmt: skip
    out = desk.adjust_rows(순위, 보정)
    assert out["A"]["adjusted_pct"] == pytest.approx(4.7)
    assert out["A"]["close_call"] and out["B"]["close_call"]  # A 하한 4.65 ≤ B 상한 4.95
    assert not out["C"]["close_call"] and not out["Y"]["close_call"]  # 갓 연 계좌는 이웃과 견주지 않는다


def test_h_check() -> None:
    perf = {"start": "2026-10-16", "last": "2026-11-20", "pnl_krw": 90_000.0, "h_pnl_krw": 100_000.0}
    x = desk.h_check(perf, {"adj_pp": 0.08, "pi_pp": 0.01, "band_pp": 0.02, "unmeasured_pct": 3.0}, 10_000_000.0)
    assert x is not None and x["actual_pp"] == pytest.approx(-0.1) and x["explained_pp"] == pytest.approx(-0.07)
    assert desk.h_check({**perf, "h_pnl_krw": None}, {"adj_pp": 0}, 1.0) is None


def test_cells_sql_books_slippage_pct() -> None:
    """장부 슬리피지(금액) ÷ (체결가 × 수량) = % — 시장가 0.1%. 내부 주문 유형·판정 잣대를 붙인다"""
    c = Client()
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, created_at)"
                   " VALUES (90, 'B', 'KR', 3, 'buy', 'market', 2, 'KRW', '2026-10-14', '2026-10-15', 'r', 1,"
                   " 'filled', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (90, 'B', 3, 'buy', '2026-10-15', 20000, 2, 'KRW', 1, 0, 0, 40, -40040, 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, trade_date, reason, status, filled_qty, avg_price, source, created_at)"
                   " VALUES ('kis_vts', 90, 'B', 'KR', 3, '000660', 'buy', 'market', 2, '2026-10-15', '베낌',"
                   " 'filled', 2, 20100, 'kis_vts', 't')")  # fmt: skip
    rows = c.execute(bmj.CELLS_SQL, ["kis_vts", "2026-10-16"]).dicts()
    assert rows[0]["i_slip_pct"] == pytest.approx(0.1) and rows[0]["i_order_type"] == "market"
    assert rows[0]["i_fill_basis"] == "open"
    assert bm.cells(rows)["cells"]["buy_market"]["excess"] == pytest.approx(0.4)  # 0.5% 불리 − 0.1
    assert c.execute(bmj.CELLS_SQL, ["kis_vts", "2026-10-15"]).dicts() == []  # 오늘 낸 주문은 아직 안 끝났다


def test_desk_write_puts_fill_adjust(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for aid in ("A", "B"):
        for d, twr in (("2026-10-15", 1.0), ("2026-10-16", 1.01)):
            c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw,"
                           " equity_krw, pnl_krw, twr_index, positions_json, recon_ok, updated_at)"
                           " VALUES (?, ?, 1000000, 0, 1000000, 1000000, 0, ?, '[]', 1, 't')", [aid, d, twr])  # fmt: skip
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, created_at)"
                   " VALUES (90, 'B', 'KR', 1, 'buy', 'market', 10, 'KRW', '2026-10-15', '2026-10-16', 'r', 1,"
                   " 'filled', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (90, 'B', 1, 'buy', '2026-10-16', 10000, 10, 'KRW', 1, 0, 0, 100, -100100, 't')")  # fmt: skip
    요약 = {"cells": {"buy_market": {"days": 30, "excess": 0.5, "se": 0.1}},
          "perf": {"start": "2026-10-15", "last": "2026-10-16", "pnl_krw": -500.0, "h_pnl_krw": 0.0}}  # fmt: skip
    c.conn.execute("INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, cash_krw, total_krw,"
                   " diff_json, source, fetched_at, summary_json) VALUES ('kis_vts', '2026-10-16', 'B', '[]', 0, 0,"
                   " '{}', 'kis_vts', 't', ?)", [json.dumps(요약)])  # fmt: skip
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    b = s["fill_adjust"]["rows"]["B"]
    assert b["turnover_pct"] == pytest.approx(10.0) and b["adj_pp"] == pytest.approx(0.05)
    assert b["adjusted_pct"] == pytest.approx(1.0 - 0.05)
    assert s["fill_adjust"]["rows"]["A"]["fills"] == 0
    h = s["fill_adjust"]["h_check"]
    assert h["actual_pp"] == pytest.approx(-0.05) and h["explained_pp"] == pytest.approx(-0.05)
    assert s["fill_adjust_error"] is None


def test_desk_fill_adjust_failure_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                   " pnl_krw, twr_index, positions_json, recon_ok, updated_at)"
                   " VALUES ('A', '2026-10-16', 1, 1, 0, 1, 0, 1, '[]', 1, 't')")  # fmt: skip

    def 망(**_k: object) -> None:
        raise RuntimeError("망")

    monkeypatch.setattr(job.desk, "fill_adjust", 망)
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    assert s["fill_adjust"] is None and s["fill_adjust_error"].startswith("RuntimeError")
