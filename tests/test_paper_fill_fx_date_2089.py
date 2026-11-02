"""미국 체결 환율은 **체결 세션 날**의 것 (25.2089, 25.2086~2088 교차검증 [권고] — 25.2087 의 "체결 환율 날짜 고르기는 시험 밖").

운영 `fill_orders` 는 달러 주문의 원화 현금을 체결 세션 날(`fill_session`)의 USDKRW 로 잰다(`_fx(client, 날)`). 일치 시험의
고정 환율 판(2073·2079·2087)은 날마다 같은 환율이라, 이것을 실행일(`trade_date`) 환율로 바꿔도 모든 시험이 지났다(교차검증
재현). 체결 세션 날과 실행일이 다른 실행 — 어제 시세가 늦어 다음 실행이 어제 장을 판정하는 날(25.2091) — 에서
엉뚱한 날 환율로 원화 현금을 빼면 장부 현금·실현 환차손익이 어긋난다. 서로 다른 환율 행(세션 날 1,300 / 실행일 1,400)을 두고
체결 한 건의 `fx_rate`·`fx_date`·`cash_krw` 를 본다.
"""

from __future__ import annotations

import pytest

from tests.test_paper_fill_1620 import 시세, 주문, 판정
from tests.test_paper_job_1445 import Client, client  # noqa: F401 — 픽스처


def _환율(c: Client, day: str, rate: float) -> None:
    c.conn.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')",
                   [day, rate])  # fmt: skip


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_체결_환율은_실행일이_아니라_체결_세션_날의_것(client: Client, side: str) -> None:  # noqa: F811
    c = client
    if side == "sell":
        # 팔 보유 — 10-14 에 1주 산 체결(그날 환율 1,250)
        _환율(c, "2026-10-14", 1250.0)
        b = 주문(c, market="US", sid=2, side="buy", typ="market", q=1, cur="USD", decided="2026-10-13",
                session="2026-10-14", tranche=1, ref=90.0)  # fmt: skip
        시세(c, 2, "2026-10-14", 90.0, 90.0, cur="USD")
        판정(c, "US", "2026-10-14")
        assert c.q("SELECT COUNT(*) FROM paper_fills WHERE order_id = ?", [b]) == [(1,)]
    _환율(c, "2026-10-15", 1300.0)
    _환율(c, "2026-10-16", 1400.0)
    o = 주문(c, market="US", sid=2, side=side, typ="market", q=1, cur="USD", decided="2026-10-14", session="2026-10-15",
            tranche=1 if side == "buy" else 0, ref=100.0)  # fmt: skip
    시세(c, 2, "2026-10-15", 100.0, 100.0, cur="USD")
    시세(c, 2, "2026-10-16", 120.0, 120.0, cur="USD")
    판정(c, "US", "2026-10-16")  # 실행일 10-16 이 10-15 장 주문을 판정한다
    (fx, fx_date, cash, price, fee, slip) = c.q(
        "SELECT fx_rate, fx_date, cash_krw, price, fee, slippage FROM paper_fills WHERE order_id = ?", [o])[0]  # fmt: skip
    assert (fx, fx_date) == (1300.0, "2026-10-15"), (fx, fx_date)
    assert price == 100.0 and (cash < 0) == (side == "buy")
    # 원화 현금 = (대금 ∓ 비용) × 세션 날 환율 — 비용은 대금의 1% 안이라, 실행일 환율(1,400)이면 이 범위를 벗어난다
    assert 1300.0 * 0.99 <= abs(cash) / price <= 1300.0 * 1.01, (cash, fee, slip)
