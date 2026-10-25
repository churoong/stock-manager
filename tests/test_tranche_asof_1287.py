"""다음 분할 차수의 기준 신호는 매수일 전 신호이고, 1차가 창 밖으로 밀려도 다시 잡지 않는다 (docs/infra.md 25.1287, 장중 감사 재현)."""

from __future__ import annotations

import json
from datetime import date, timedelta

from batch.jobs import monitor_targets as mt
from tests.test_report_picks import SqliteClient


def _db() -> SqliteClient:
    c = SqliteClient()
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                   " VALUES (1, '000001', 'KOSPI', 'KR', 'X', 'KRW', 'active', 't', 't')")  # fmt: skip
    return c


def _신호(c: SqliteClient, d: str, hz: str, 값: list[float]) -> None:
    plan = [{"step": i + 1, "ratio": 1 / 3, "price": p} for i, p in enumerate(값)]
    c.conn.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,"
                   " currency, tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at)"
                   " VALUES (1, ?, ?, 't', ?, ?, 'KRW', ?, 1, '', '{}', 1, 't')",
                   [d, hz, 값[2], 값[0], json.dumps(plan)])  # fmt: skip


def _매수(c: SqliteClient, d: str, hz: str = "long") -> None:
    c.conn.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                   " horizon, created_at, updated_at) VALUES (1, 'buy', ?, 100, 1, 'KRW', 1, 'none', ?, 't', 't')",
                   [d, hz])  # fmt: skip


def test_매수_당일_기준_신호는_계획이_아니다() -> None:
    c = _db()
    _신호(c, "2026-10-12", "long", [103_000, 84_100, 65_100])
    _매수(c, "2026-10-13")  # 10-12 기준 카드를 보고 산다
    _신호(c, "2026-10-13", "short", [100_000, 99_000, 98_000])  # 10-14 아침에야 나온 신호
    assert mt.next_tranche(c, 1, date(2026, 10, 14)) == (84_100.0, 2, "2026-10-12")


def test_1차가_창_밖으로_밀려도_2차를_1차로_세지_않는다() -> None:
    c = _db()
    d = date(2026, 9, 1)
    while d <= date(2026, 10, 5):  # 장기 신호가 매일 다시 써진다
        if d.weekday() < 5:
            _신호(c, d.isoformat(), "long", [100_000 - (d.day % 7) * 100, 90_000, 80_000])
        d += timedelta(days=1)
    _매수(c, "2026-09-02")
    _매수(c, "2026-09-20")
    assert mt.next_tranche(c, 1, date(2026, 9, 25))[1] == 3
    # 기준 신호(9/01)가 30일 넘게 묵었다 — 계획이 끝났다. 예전엔 9/20 을 1차로 세어 "2차" 를 다시 권했다
    assert mt.next_tranche(c, 1, date(2026, 10, 5)) == (None, None, None)


def test_다_팔고_다시_산_보유는_새_계획의_차수로() -> None:
    """25.1789 (결함 루프, 25.1551 [참고]) — 매도를 보지 않아 지난 보유의 매수까지 세어 계획에 없는 "3차 80,000" 을 냈다"""
    c = _db()
    _신호(c, "2026-10-01", "long", [100_000, 90_000, 80_000])
    _매수(c, "2026-10-02")
    c.conn.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                   " horizon, created_at, updated_at) VALUES (1, 'sell', '2026-10-06', 100, 1, 'KRW', 1, 'none', 'long',"
                   " 't', 't')")  # fmt: skip
    _신호(c, "2026-10-07", "long", [95_000, 85_000, 75_000])
    _매수(c, "2026-10-08")  # 새 보유의 1차
    assert mt.next_tranche(c, 1, date(2026, 10, 12)) == (85_000.0, 2, "2026-10-07")


def test_일부만_판_보유는_계획을_이어_센다() -> None:
    """25.1791 (25.1789 교차검증 [필수]) — 열린 날을 남은 FIFO 로트의 가장 이른 날(`positions.first_buy_date`)로 잡으면
    일부 매도로 1차 로트가 빠질 때 2차 매수일로 밀려 다른 계획의 "2차 85,000" 을 다시 권했다"""
    c = _db()
    _신호(c, "2026-10-01", "long", [100_000, 90_000, 80_000])
    _매수(c, "2026-10-02")
    _신호(c, "2026-10-04", "long", [95_000, 85_000, 75_000])
    _매수(c, "2026-10-05")
    c.conn.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                   " horizon, created_at, updated_at) VALUES (1, 'sell', '2026-10-06', 100, 1, 'KRW', 1, 'none', 'long',"
                   " 't', 't')")  # fmt: skip  # 1주만 — 보유는 이어진다
    assert mt.next_tranche(c, 1, date(2026, 10, 8)) == (80_000.0, 3, "2026-10-01")


def test_같은_날_사고_판_보유는_장부처럼_매수_먼저() -> None:
    """25.1792 (25.1791 교차검증 [권고]) — 실제 장부는 같은 날 매수 먼저(`portfolio.same_day_order`). 매도 먼저로 세면
    10주 들고 같은 날 5주 사고 10주 판 보유를 0 에서 다시 연 것으로 봐 차수를 처음부터 셌다"""
    c = _db()
    _신호(c, "2026-10-01", "long", [100_000, 90_000, 80_000])
    c.conn.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                   " horizon, created_at, updated_at) VALUES (1, 'buy', '2026-10-02', 100, 10, 'KRW', 1, 'none', 'long',"
                   " 't', 't')")  # fmt: skip
    _신호(c, "2026-10-04", "long", [95_000, 85_000, 75_000])
    c.conn.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                   " horizon, created_at, updated_at) VALUES (1, 'sell', '2026-10-05', 100, 10, 'KRW', 1, 'none', 'long',"
                   " 't', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                   " horizon, created_at, updated_at) VALUES (1, 'buy', '2026-10-05', 100, 5, 'KRW', 1, 'none', 'long',"
                   " 't', 't')")  # fmt: skip
    assert mt.next_tranche(c, 1, date(2026, 10, 12)) == (80_000.0, 3, "2026-10-01")  # 보유는 이어졌다(10→15→5)


def test_감시_목록은_FIFO_최소_로트일을_넘기지_않는다() -> None:
    """25.1791 — `positions.first_buy_date`(남은 FIFO 로트의 가장 이른 날)를 다시 넘기면 일부 매도 퇴행이 돌아온다"""
    import inspect

    assert "first_buy_date" not in inspect.signature(mt.next_tranche).parameters
    assert 'next_tranche(client, int(r["stock_id"]), cal.local_today(market))' in inspect.getsource(mt.build_targets)
