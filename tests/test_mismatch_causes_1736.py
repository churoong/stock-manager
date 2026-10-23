"""KIS모의투자와 가상모의투자가 어긋난 주문의 까닭 (docs/infra.md 25.1736, 2026-10-23 사용자 "그렇게 진행해줘" — 아이디어 ⑤)."""

from __future__ import annotations

from batch.services import broker_mirror as bm


def _줄(status: str, filled: int, i_status: str, low: float | None, close: float | None, limit: float | None = 10_000,
        order_type: str = "limit", side: str = "buy") -> dict:
    return {"trade_date": "2026-10-20", "side": side, "status": status, "filled_qty": filled, "quantity": 10,
            "avg_price": 10_000 if filled else None, "message": None, "reason": "베낌", "i_status": i_status,
            "i_note": None, "i_order_type": order_type, "i_fill_basis": "open", "i_price": 10_000 if i_status == "filled"
            else None, "i_fill_date": "2026-10-20" if i_status == "filled" else None, "i_limit": limit, "p_low": low,
            "p_close": close}  # fmt: skip


def test_어긋난_주문을_일봉으로_가른다() -> None:
    rows = [
        _줄("unfilled", 0, "filled", 9_900, 9_950),     # 지정가 아래로 내려왔는데 KIS 안 참
        _줄("unfilled", 0, "filled", 10_000, 10_000),   # 딱 닿음 — 대기 순서
        _줄("filled", 10, "expired", 9_950, 10_100),    # 장중 닿았는데 종가는 위 — 가상 보수
        _줄("filled", 10, "expired", None, None),       # 일봉 없음
        _줄("filled", 10, "filled", 9_900, 9_950),      # 둘 다 참 — 까닭 없음
        _줄("unfilled", 0, "filled", 9_000, 9_000, limit=None, order_type="market", side="sell"),  # 시장가 미체결
    ]
    s = bm.compare(rows)
    assert (s["internal_only"], s["kis_only"], s["both"]) == (3, 2, 1)
    assert s["mismatch_causes"] == {"through_unfilled": 1, "exact_touch": 1, "intraday_touch": 1, "no_bar": 1,
                                    "market_unfilled": 1}  # fmt: skip
    줄 = bm.compare_lines(s, 0, "2026-10-21")
    i = 줄.index("  어긋난 까닭:")
    assert any("장중엔 지정가에 닿았는데 종가는 위" in x for x in 줄[i + 1:])
