"""자기 채점에 시장 지수 대비·서로 다른 종목 수 (docs/infra.md 25.1404, docs/factors.md 12.29 P6)."""

from __future__ import annotations

import pytest

from batch.services import self_grade as sg


def _행(sid: int, d: str, r20: float | None, b20: float | None) -> dict:
    return {"stock_id": sid, "trade_date": d, "ret_5d": 0.01, "ret_20d": r20, "bench_ret_20d": b20}


def test_연속으로_실린_종목은_종목_수로_드러난다() -> None:
    # 2종목이 3일씩 실렸다 — 6건·2종목
    rows = [_행(s, f"2026-09-{d:02d}", 0.05, 0.03) for s in (1, 2) for d in (17, 18, 19)]
    g = sg.grade(rows)
    w20 = next(w for w in g.windows if w.window == 20)
    assert (w20.n, w20.stocks) == (6, 2)
    assert w20.excess == pytest.approx(0.02)
    줄 = sg.line(g)
    assert "20일 뒤 평균 +5.0% · 시장 지수 대비 +2.0%p · 이긴 100% (6건·2종목)" in 줄
    assert sg.stats(g) == {"n20": 6, "k20": 2, "bench_cov": 1.0}


def test_지수가_95퍼센트_미만이면_대비를_내지_않는다() -> None:
    rows = [_행(s, "2026-09-17", 0.05, 0.03 if s <= 9 else None) for s in range(1, 11)]  # 10건 중 9건
    w20 = next(w for w in sg.grade(rows).windows if w.window == 20)
    assert w20.excess is None and w20.bench_n == 9
    assert "지수 대비" not in sg.line(sg.grade(rows))


def test_5일_창에는_지수_대비가_없다() -> None:
    rows = [_행(s, "2026-09-17", None, None) for s in range(1, 7)]
    w5 = next(w for w in sg.grade(rows).windows if w.window == 5)
    assert w5.excess is None and w5.stocks == 6
