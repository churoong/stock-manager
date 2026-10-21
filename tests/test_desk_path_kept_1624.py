"""35회차 곁가지 1 (docs/desk.md 6장, docs/infra.md 25.1624).

지키는 것: 관문 KIS 칸에 **경로 포함 실현율**(모름·거부를 못 찬 것으로 분모에 더한 최악 경계)을 참고로 싣는다 —
판정(`ok`)은 지금 실현율 그대로
"""

from __future__ import annotations

from batch.services import desk


def test_경로_포함_실현율은_참고_칸이다() -> None:
    k = desk.combine_kept([{"both": 18, "internal_only": 2, "unknown": 4, "rejected": 1},
                           {"both": 9, "internal_only": 1, "unknown": 0, "rejected": 0}])  # fmt: skip
    assert k["kept_pct"] == 27 / 30 * 100 and k["n"] == 30
    assert k["path_pct"] == 27 / 35 * 100 and k["path_n"] == 35
    assert desk.combine_kept([])["path_pct"] is None


def test_경로_실패는_가상이_찬_주문만_센다() -> None:
    """25.1627 — 가상도 안 찬 주문(만료)의 모름·거부까지 분모에 넣으면 최악 경계보다 낮게 나왔다"""
    from batch.services import broker_mirror as bm

    def 줄(status: str, i_status: str, filled: int = 0) -> dict:
        return {"status": status, "i_status": i_status, "trade_date": "2026-10-15",
                "i_fill_date": "2026-10-15" if i_status == "filled" else None, "side": "buy", "filled_qty": filled,
                "reason": "베낌", "i_fill_basis": "open", "i_order_type": "limit"}  # fmt: skip

    s = bm.compare([줄("filled", "filled", 1), 줄("rejected", "expired"), 줄("unknown", "expired"),
                    줄("unknown", "filled")])  # fmt: skip
    assert (s["unknown"], s["rejected"], s["path_fail"]) == (2, 1, 1)
    k = desk.combine_kept([s])
    assert k["kept_pct"] == 100 and k["path_pct"] == 50 and k["path_n"] == 2
