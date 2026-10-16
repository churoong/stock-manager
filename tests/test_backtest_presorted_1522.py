"""백테스트 가격 창 미리 정렬 — 결과는 예전 경로와 같다 (docs/backtest.md 10장 단계 0, docs/infra.md 25.1522)."""

from __future__ import annotations

import random

from batch.services import backtest as bt


def _무작위_시세(seed: int) -> dict[int, dict[str, float]]:
    rnd = random.Random(seed)
    out: dict[int, dict[str, float]] = {}
    for sid in range(1, 30):
        날 = sorted({f"2026-{m:02d}-{d:02d}" for m in range(1, 7) for d in range(1, 29) if rnd.random() < 0.7})
        out[sid] = {d: round(100 + rnd.random() * 50, 2) for d in 날}
    out[99] = {}  # 시세 없는 종목
    return out


def test_presorted_view_equals_plain_view() -> None:
    값, 대금, 수준 = _무작위_시세(1), _무작위_시세(2), _무작위_시세(3)
    미리 = (bt.SortedSeries(값), bt.SortedSeries(대금), bt.SortedSeries(수준))
    for cutoff in ("2025-12-31", "2026-01-01", "2026-03-15", "2026-06-28", "2026-12-31"):
        a = bt.PriceView(값, cutoff, 대금, 수준)
        b = bt.PriceView(값, cutoff, 대금, 수준, presorted=미리)
        assert a._series == b._series and a._levels == b._levels
        assert {k: v for k, v in a._turnover.items() if v} == b._turnover
        for sid in (1, 5, 99):
            assert a.closes(sid) == b.closes(sid) and a.values(sid) == b.values(sid)
            assert a.level_closes(sid) == b.level_closes(sid) and a.last_close(sid) == b.last_close(sid)
        assert a.stocks() == b.stocks() and a.broad_last() == b.broad_last()


def test_slices_are_copies() -> None:
    """잘라 낸 것은 사본 — 판단 함수가 바꿔도 다음 cutoff 의 창이 오염되지 않는다(볼 것이 없다 규칙)"""
    s = bt.SortedSeries({1: {"2026-01-01": 1.0, "2026-01-02": 2.0}})
    첫 = s.upto("2026-01-01")
    첫[1].append(("2026-01-02", 999.0))
    assert s.upto("2026-01-01") == {1: [("2026-01-01", 1.0)]}
