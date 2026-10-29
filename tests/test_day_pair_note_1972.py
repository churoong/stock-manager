"""25.1972 (T 첫 매매 점검 [참고]) — 당일 단타(T)의 종가 짝 매도 체결 까닭은 "단타 짝 매도 — 종가" 다. 장 열기 전에 매수와
함께 낸 주문이라 "장 열린 뒤 낸 주문" 이 아니다. 다른 계좌의 종가 잣대 매도(늦은 실행)는 그대로"""

from __future__ import annotations

from dataclasses import replace

from batch.services import paper as pp


def _매도(account: str, reason: str | None = pp.DAY_CLOSE_CODE) -> pp.Order:
    return pp.Order(id=1, account_id=account, stock_id=1, side="sell", order_type="market", limit_price=None,
                    quantity=10, currency="KRW", fill_session="2026-10-30", fill_basis="close", reason=reason)  # fmt: skip


def test_짝_매도_까닭() -> None:
    bar = pp.Bar(date="2026-10-30", open=100.0, close=102.0, volume=1000, change_pct=2.0)
    assert pp.judge(_매도("T"), bar) == (102.0, "단타 짝 매도 — 종가")
    assert pp.judge(_매도("B", "trend_exit"), bar) == (102.0, "장 열린 뒤 낸 주문 — 종가")
    # 25.1973 (25.1972 교차검증 [권고]) — 늦은 실행이 낸 T 안전망 매도(단타잔여)도 종가 잣대지만 짝이 아니다
    assert pp.judge(_매도("T", pp.DAY_LEFT_CODE), bar) == (102.0, "장 열린 뒤 낸 주문 — 종가")
    # 미뤄진 짝 매도(하한가 다음 장)는 시가 — 그대로
    assert pp.judge(replace(_매도("T"), deferrals=1), bar) == (100.0, "시가")
