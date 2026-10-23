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


def test_미국_소수_끝자리_오차는_딱_닿음으로_25_1737() -> None:
    """yfinance 저가 `123.44999694824219` 와 지정가 `123.45` — 날값 비교면 "아래로 내려옴" 으로 갔다(교차검증 [권고] 1)"""
    가상만 = _줄("unfilled", 0, "filled", 123.44999694824219, 123.44999694824219, limit=123.45)
    kis만 = _줄("filled", 10, "expired", 123.45000457763672, 124.0, limit=123.45)
    assert bm.mismatch_cause(가상만, True) == "exact_touch"
    assert bm.mismatch_cause(kis만, False) == "intraday_touch"


def test_분할_뒤_다시_받은_일봉은_가르지_않는다_25_1737() -> None:
    """2:1 분할 뒤 일봉은 지정가의 절반 — 까닭을 지어내지 않고 "분할 의심" 으로(교차검증 [권고] 2)"""
    assert bm.mismatch_cause(_줄("filled", 10, "expired", 4_950, 5_050), False) == "bar_suspect"
    assert bm.mismatch_cause(_줄("unfilled", 0, "filled", 5_000, 5_000), True) == "bar_suspect"
    # 운용 화면 지정가 개선 칸의 같은 방어와 같은 값 — 한쪽만 바뀌면 같은 분할을 두 화면이 다르게 거른다
    from batch.services import desk

    assert bm.BAR_RATIO_FLOOR == desk.PI_CLOSE_FLOOR


def test_KIS_에_보낸_지정가가_잣대다_25_1737() -> None:
    """미국 시장가 매도는 KIS 에 종가 −10% 지정가로 나간다 — 갭 하락으로 고가가 못 미쳤으면 갭(교차검증 [권고] 3)"""
    갭 = _줄("unfilled", 0, "filled", 8_500, 8_700, limit=None, order_type="market", side="sell") | {
        "b_limit": 9_000, "p_high": 8_800}
    assert bm.mismatch_cause(갭, True) == "gap_beyond_limit"
    닿음 = 갭 | {"p_high": 9_100}
    assert bm.mismatch_cause(닿음, True) == "market_unfilled"
    # 지정가 매수 — 보낸 값(호가 단위로 내림)이 내부 값보다 낮으면 그 값으로 견준다
    내림 = _줄("unfilled", 0, "filled", 9_995, 9_995, limit=10_000) | {"b_limit": 9_990}
    assert bm.mismatch_cause(내림, True) == "unexplained"
