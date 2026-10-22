"""KIS모의투자 실현손익 — 우리 주문 기록의 평균 원가 (docs/paper.md 15장, docs/infra.md 25.1694, 2026-10-22 사용자 지시)."""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm


def _o(i: int, d: str, side: str, qty: int, price: float | None, sid: int = 1, market: str = "KR") -> dict:
    return {"id": i, "trade_date": d, "side": side, "stock_id": sid, "filled_qty": qty, "avg_price": price,
            "market": market, "realized_krw": None, "realized_fx_krw": None}  # fmt: skip


def test_국내_평균_원가_부분_매도() -> None:
    # 10주 @10,000 + 10주 @12,000 → 평균 11,000. 5주 @13,000 → +10,000, 남은 15주 @9,000 → −30,000
    r = bm.realized([_o(1, "2026-10-15", "buy", 10, 10_000), _o(2, "2026-10-16", "buy", 10, 12_000),
                     _o(3, "2026-10-19", "sell", 5, 13_000), _o(4, "2026-10-20", "sell", 15, 9_000)], lambda d: None)  # fmt: skip
    assert r == {3: (10_000.0, 0.0), 4: (-30_000.0, 0.0)}


def test_미국은_체결일_환율로_원화_환차를_가른다() -> None:
    환율 = {"2026-10-15": 1_300.0, "2026-10-20": 1_400.0}
    r = bm.realized([_o(1, "2026-10-15", "buy", 10, 100.0, market="US"), _o(2, "2026-10-20", "sell", 10, 110.0, market="US")],
                    환율.get)  # fmt: skip
    # 대금 $1,100 × 1,400 = 1,540,000 − 원가 1,300,000 = 240,000 = 주가 (1,100 − 1,000) × 1,400 = 140,000 + 환차 100,000
    assert r == {2: (240_000.0, 100_000.0)}


def test_원가를_모르면_다_팔기_전까지_모름() -> None:
    r = bm.realized([_o(1, "2026-10-15", "buy", 10, 100.0, market="US"),  # 그날 환율 모름
                     _o(2, "2026-10-16", "sell", 5, 110.0, market="US"), _o(3, "2026-10-19", "sell", 5, 120.0, market="US"),
                     _o(4, "2026-10-20", "buy", 2, 100.0, market="US"), _o(5, "2026-10-21", "sell", 2, 101.0, market="US")],
                    {"2026-10-16": 1_300.0, "2026-10-19": 1_300.0, "2026-10-20": 1_300.0, "2026-10-21": 1_300.0}.get)  # fmt: skip
    assert r[2] == (None, None) and r[3] == (None, None)  # 다 판 뒤 새로 산 것은 다시 센다
    assert r[5] == (2_600.0, 0.0)
    # 장부보다 많이 판 매도(기록에 없는 보유)도 모름
    assert bm.realized([_o(1, "2026-10-15", "sell", 3, 10_000)], lambda d: None) == {1: (None, None)}


class _Rs:
    def __init__(self, rows: list, cols: list[str] | None = None) -> None:
        self.rows, self._cols = rows, cols or []

    def dicts(self) -> list[dict]:
        return [dict(zip(self._cols, r, strict=False)) for r in self.rows]


class _Client:
    def __init__(self, orders: list[dict]) -> None:
        self.orders, self.batches = orders, []

    def execute(self, sql: str, args: list | None = None) -> _Rs:
        if sql == job.REALIZED_ROWS_SQL:
            cols = ["id", "trade_date", "side", "stock_id", "filled_qty", "avg_price", "realized_krw", "realized_fx_krw"]
            return _Rs([[o[c] for c in cols] for o in self.orders], cols)
        if sql == job.REALIZED_FX_SQL:
            return _Rs([(1_300.0, "2026-10-15")])
        raise AssertionError(sql)

    def batch(self, statements: list) -> None:
        self.batches.append(statements)


def test_바뀐_줄만_적는다() -> None:
    orders = [_o(1, "2026-10-15", "buy", 10, 10_000), _o(2, "2026-10-16", "sell", 10, 11_000)]
    c = _Client(orders)
    assert job.update_realized(c, "kis_vts", "KR") == 1
    assert c.batches == [[(job.REALIZED_SET_SQL, [10_000.0, 0.0, 2])]]
    orders[1]["realized_krw"], orders[1]["realized_fx_krw"] = 10_000.0, 0.0
    assert job.update_realized(c, "kis_vts", "KR") == 0  # 그대로면 쓰지 않는다


def test_묵은_환율은_모름() -> None:
    orders = [_o(1, "2026-10-30", "buy", 1, 100.0, market="US"), _o(2, "2026-10-31", "sell", 1, 110.0, market="US")]
    c = _Client(orders)  # 환율 표의 마지막이 10-15 — 7일 넘게 묵음
    assert job.update_realized(c, "kis_vts_us", "US") == 0  # (None, None) 은 지금 값과 같아 쓰지 않는다
