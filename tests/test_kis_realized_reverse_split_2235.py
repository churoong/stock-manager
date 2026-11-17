"""KIS모의투자 실현손익 — 병합·감자(역분할)로 KIS 잔고가 줄면 기록 밖 매도로 보지 않는다 (docs/infra.md 25.2235,
11-17 결함 회차 [권고]).

예전엔 수량 감소를 늘 기록 밖 매도로 보고 원가를 같은 비율로 깎아, 1:10 병합 뒤 10주 @100,000 매도(실제 손익 0)에
+900,000 원을 적었다. 개장 전 잔고의 KIS 평균단가가 그 비율만큼 뛰었으면 병합으로 보고 모름."""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm


def _r(i: int, d: str, side: str, q: int, p: float) -> dict:
    return {"id": i, "trade_date": d, "side": side, "stock_id": 1, "code": "000001", "filled_qty": q, "avg_price": p,
            "market": "KR", "reason": None, "limit_price": p}


_줄 = [_r(1, "2026-11-02", "buy", 100, 10_000), _r(2, "2026-11-20", "sell", 10, 100_000)]
_스냅 = [("2026-11-03", {"000001": 100}), ("2026-11-10", {"000001": 10})]


def test_병합_뒤_매도는_거짓_이익이_아니라_모름() -> None:
    평균 = {"2026-11-03": {"000001": 10_000.0}, "2026-11-10": {"000001": 100_000.0}}
    assert bm.realized(_줄, lambda _d: None, None, _스냅, 평균) == {2: (None, None)}


def test_평균단가가_그대로면_기록_밖_매도로_줄인다() -> None:
    """기록 밖에서 90주를 판 것 — 평균단가는 그대로, 남은 10주의 원가로 잰다(25.1704 그대로)"""
    줄 = [_r(1, "2026-11-02", "buy", 100, 10_000), _r(2, "2026-11-20", "sell", 10, 12_000)]
    평균 = {"2026-11-03": {"000001": 10_000.0}, "2026-11-10": {"000001": 10_000.0}}
    assert bm.realized(줄, lambda _d: None, None, _스냅, 평균) == {2: (20_000.0, 0.0)}


def test_평균단가를_모르면_예전처럼_줄인다() -> None:
    assert bm.realized(_줄, lambda _d: None, None, _스냅) == {2: (900_000.0, 0.0)}


def test_개장전_잔고가_평균단가를_모은다() -> None:
    import json

    rows = [{"as_of": "2026-11-10", "fetched_at": "2026-11-09T23:00:00+00:00", "summary_json": "{}",
             "holdings_json": json.dumps([{"code": "000001", "qty": 10, "avg": 100000.0}])}]  # fmt: skip
    평균: dict = {}
    assert job._개장전_잔고(rows, "KR", 평균) == [("2026-11-10", {"000001": 10})]
    assert 평균 == {"2026-11-10": {"000001": 100000.0}}


def test_실현손익_갱신이_평균단가를_넘긴다() -> None:
    import inspect

    src = inspect.getsource(job.update_realized)
    assert "_개장전_잔고(" in src and "스냅평균)" in src and "bm.realized(" in src and "스냅, 스냅평균)" in src
