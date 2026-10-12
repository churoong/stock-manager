"""유상증자 권리락 뒤 매도 플래그·분할 매수 다음 차수 (docs/infra.md 25.1158·25.1159, 감사 재현).

권리락(계수 0.9) 날 종가 90,000·평균 100,000 은 실질 0% 다. 장중 손절선은 25.1140 부터 평균단가에 계수를 곱하는데
매도 플래그는 원가를 그대로 써 "단기 손절선 −7% 에 닿았습니다 (−10.0%)" 가 났다."""

from __future__ import annotations

import json
from datetime import date

from batch.jobs import monitor_targets as job
from batch.jobs import sell_flags as sfjob
from batch.services import sell_flags as sf
from tests.test_monitor_targets import Test유상증자_권리락은_막지_않는다_25_1139 as T


def _준비():
    mem = T._준비([("rights", "2026-09-16")])
    c = mem.conn
    c.execute("UPDATE stocks SET yahoo_symbol = '005930.KS', asset_type = 'stock' WHERE id = 1")
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'KRW', 100000, 1, 1000000, 1000000, '2026-09-14', 'short', '2026-09-15', 90000, 900000, 't')"
    )
    계획 = [{"step": i, "ratio": 1 / 3, "price": p, "amount": 1} for i, p in ((1, 100000.0), (2, 95000.0), (3, 90000.0))]
    c.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at)"
        " VALUES (1, '2026-09-14', 'short', 'buy', 95000, 100000, 'KRW', ?, 0, 'r', '{}', 1, 't')",
        [json.dumps(계획)],
    )
    c.execute(
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " horizon, created_at, updated_at)"
        " VALUES (1, 1, 'buy', '2026-09-14', 100000, 10, 'KRW', 1, 'none', 'short', 't', 't')"
    )
    return mem


def test_권리락_날_매도_플래그는_원가에_계수를_곱해_손절이_아니다() -> None:
    mem = _준비()
    배율 = job.action_scale(mem, 1, "KR")  # type: ignore[arg-type]
    assert 배율 is not None and abs(배율 - 0.9) < 0.02
    holdings, _ = sfjob.load_holdings(mem, date(2026, 10, 12))  # type: ignore[arg-type]
    h = holdings[0]
    assert h.corporate_action_recent is False and h.rights_scale == 배율
    flags = sf.evaluate(h, date(2026, 9, 16))
    assert not [f for f in flags if f.reason_code == "손절"], [f.rationale_text for f in flags]


def test_권리락_계수를_반영해도_진짜_손절은_뜨고_근거표에_적힌다() -> None:
    h = sf.HoldingInput(stock_id=1, name="가", horizon="short", first_buy_date="2026-09-14", currency="KRW",
                        cost=1_000_000, market_value=800_000, price_date="2026-09-16", rights_scale=0.9)  # fmt: skip
    [손절] = [f for f in sf.evaluate(h, date(2026, 9, 16)) if f.reason_code == "손절"]
    assert "(-11.1%)" in 손절.rationale_text  # 800,000 / 900,000 − 1
    assert any(c["label"] == "유상증자 권리락" for c in 손절.criteria)


def test_계획_뒤_권리락이면_다음_차수_가격도_계수로_옮긴다() -> None:
    """9/14 계획(2차 95,000) 뒤 권리락 — 옛 단위 그대로라 실질 1차 가격과 같은 시세에 "2차 도달" 이 났다 (25.1159)."""
    mem = _준비()
    배율 = job.action_scale(mem, 1, "KR")  # type: ignore[arg-type]
    [행] = job.build_targets(mem, "KR", "2026-10-12T00:00:00+00:00")  # type: ignore[arg-type]
    차수가격, 차수 = 행[14], 행[15]
    assert 차수 == 2 and abs(차수가격 - 95000 * 배율) < 1e-6


def test_권리락_뒤에_낸_계획은_옮기지_않는다() -> None:
    mem = _준비()
    계수들 = job.action_factors(mem, 1, "KR")  # type: ignore[arg-type]
    assert 계수들 and 계수들[0][0] == "2026-09-15"
    # 계획일이 계수 날과 같거나 뒤면 그대로 (그날 종가로 낸 계획은 이미 권리락 뒤 시세다)
    mem.conn.execute("UPDATE signals SET as_of_date = '2026-09-15'")
    mem.conn.execute("UPDATE trades SET trade_date = '2026-09-15'")
    [행] = job.build_targets(mem, "KR", "2026-10-12T00:00:00+00:00")  # type: ignore[arg-type]
    assert 행[14] == 95000.0
