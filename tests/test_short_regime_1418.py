"""28회차 P5 공매도 국면 집계 SQL — 11-03 전에 고정한 그대로 도는가 (docs/infra.md 25.1418, docs/factors.md 12장 "켜는 기준")."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "scripts/diag/round28_short_regime.sql").read_text(encoding="utf-8")


def _db() -> sqlite3.Connection:
    k = sqlite3.connect(":memory:")
    k.executescript(
        "CREATE TABLE backtest_runs (id INTEGER PRIMARY KEY, market TEXT, strategy TEXT, group_id TEXT, top_n INT,"
        " created_at TEXT); CREATE TABLE backtest_curves (id INTEGER PRIMARY KEY, run_id INT, date TEXT, equity REAL);"
    )
    return k


def test_국면은_월말_날짜로_가르고_비교_기준은_짝대로() -> None:
    k = _db()
    월수익 = {"composite": 0.01, "momentum": 0.02, "momentum_sector": 0.05, "ic_weighted": 0.0, "shrink_missing": 0.03}
    for i, (전략, r) in enumerate(월수익.items(), 1):
        k.execute("INSERT INTO backtest_runs VALUES (?, 'KR', ?, 'g', 20, '2026-11-03T01:00:00')", [i, 전략])
        eq = 1.0
        # 2023-10(부분 허용) · 2023-11(월말 11-28 → 전면 금지) · 2025-03(월말 03-28 → 금지) · 2025-04(재개)
        for d in ("2023-09-27", "2023-10-30", "2023-11-02", "2023-11-28", "2025-03-28", "2025-04-29"):
            k.execute("INSERT INTO backtest_curves (run_id, date, equity) VALUES (?, ?, ?)", [i, d, eq])
            eq *= 1 + r
    # 다른 실행(11-03 전, 상위 30)은 고르지 않는다
    k.execute("INSERT INTO backtest_runs VALUES (99, 'KR', 'momentum_sector', 'old', 20, '2026-10-03T01:00:00')")
    k.execute("INSERT INTO backtest_curves (run_id, date, equity) VALUES (99, '2025-04-29', 5.0)")
    rows = {(c, g): (n, round(m, 6), p) for c, g, n, m, p in k.execute(SQL)}
    assert rows[("momentum_sector", "1_부분허용")][0] == 1
    assert rows[("momentum_sector", "2_전면금지")][0] == 2  # 11월(11-02 는 10월 끝이 아니다)·2025-03
    assert rows[("momentum_sector", "3_전면재개")][0] == 1
    # momentum_sector 는 momentum 과, ic_weighted 는 composite 와 견준다
    assert rows[("ic_weighted", "0_전체")][2] == 0 and rows[("shrink_missing", "0_전체")][2] == 4
    assert rows[("momentum_sector", "0_전체")][0] == 4
