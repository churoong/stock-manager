"""37회차 A 공매도 국면 집계 SQL — 11-03 전에 고정, 공통 구간 시작 다음 달부터 (docs/infra.md 25.1648, factors.md 12.40)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "scripts/diag/round37_high52_regime.sql").read_text(encoding="utf-8")


def _db() -> sqlite3.Connection:
    k = sqlite3.connect(":memory:")
    k.executescript(
        "CREATE TABLE backtest_runs (id INTEGER PRIMARY KEY, market TEXT, strategy TEXT, group_id TEXT, top_n INT,"
        " created_at TEXT); CREATE TABLE backtest_curves (id INTEGER PRIMARY KEY, run_id INT, date TEXT, equity REAL);"
        "CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT, market TEXT, status TEXT, started_at TEXT,"
        " step_log TEXT);"
    )
    return k


def _실행(k: sqlite3.Connection, 번호: int, 그룹: str, 시작: str, 판정: str, 시각: str, 기본: bool = True) -> None:
    k.execute("INSERT INTO batch_runs (job_name, market, status, started_at, step_log) VALUES"
              " ('backtest', 'KR', 'success', ?, ?)",
              [시각, json.dumps({"group_id": 그룹, "params": {"default": 기본},
                                "high52_judge": {"start": 시작, "verdict": 판정}})])  # fmt: skip
    for j, (전략, r) in enumerate((("high52", 0.03), ("momentum", 0.01), ("composite", 0.0)), 1):
        rid = 번호 * 10 + j
        k.execute("INSERT INTO backtest_runs VALUES (?, 'KR', ?, ?, 20, ?)", [rid, 전략, 그룹, 시각])
        eq = 1.0
        for d in ("2023-08-30", "2023-09-27", "2023-10-30", "2023-11-28", "2025-03-28", "2025-04-29"):
            k.execute("INSERT INTO backtest_curves (run_id, date, equity) VALUES (?, ?, ?)", [rid, d, eq])
            eq *= 1 + r


def test_공통_구간_시작_다음_달부터_첫_판정_실행만() -> None:
    k = _db()
    _실행(k, 1, "early", "2023-08-01", "판정 불가", "2026-11-03T17:11:00")  # 판정 불가는 건너뛴다
    _실행(k, 2, "g", "2023-09-01", "통과", "2026-12-03T17:11:00")
    _실행(k, 3, "late", "2023-08-01", "탈락", "2027-01-03T17:11:00")  # 그 뒤 실행은 아니다
    _실행(k, 4, "nd", "2023-08-01", "통과", "2026-11-20T17:11:00", 기본=False)  # 기본 실행이 아니면 아니다
    rows = {(c, g): (n, round(m, 6), p) for c, g, n, m, p in k.execute(SQL)}
    # g 실행: 시작 2023-09 → 10월부터. 10월(부분 허용)·11월(금지)·2025-03(금지)·2025-04(재개) = 4달
    assert rows[("high52", "0_전체")] == (4, 0.02, 4)
    assert rows[("high52", "1_부분허용")][0] == 1 and rows[("high52", "2_전면금지")][0] == 2
    assert rows[("high52", "3_전면재개")][0] == 1
    assert {c for c, _g in rows} == {"high52"}


def test_28회차_고정본은_건드리지_않았다() -> None:
    옛 = (Path(__file__).resolve().parent.parent / "scripts/diag/round28_short_regime.sql").read_text(encoding="utf-8")
    assert "high52" not in 옛
