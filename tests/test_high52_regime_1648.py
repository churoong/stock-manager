"""37회차 A 공매도 국면 집계 SQL — 11-03 전에 고정, 공통 구간 시작 다음 달부터 (docs/infra.md 25.1648·25.1651, factors.md 12.40).

25.1651: 대상은 **판정 실행**(12장 머리 — 그 달 trade_date 3~7일 기본·성공 국내 실행 가운데 첫 실행)만, 'first'(처음 통과·탈락)와
'2028-04'(다시 보기) 두 갈래. 곡선은 윈도 함수로 한 번만 훑는다(상관 하위 질의는 곡선 길이의 네제곱으로 읽었다).
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

DIAG = Path(__file__).resolve().parent.parent / "scripts/diag"
SQL = (DIAG / "round37_high52_regime.sql").read_text(encoding="utf-8")
DAYS = ("2023-08-30", "2023-09-27", "2023-10-30", "2023-11-28", "2025-03-28", "2025-04-29")


def _db() -> sqlite3.Connection:
    k = sqlite3.connect(":memory:")
    k.executescript(
        "CREATE TABLE backtest_runs (id INTEGER PRIMARY KEY, market TEXT, strategy TEXT, group_id TEXT, top_n INT,"
        " created_at TEXT); CREATE TABLE backtest_curves (id INTEGER PRIMARY KEY, run_id INT, date TEXT, equity REAL);"
        "CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT, market TEXT, status TEXT, trade_date TEXT,"
        " started_at TEXT, step_log TEXT);"
    )
    return k


def _실행(k: sqlite3.Connection, 번호: int, 그룹: str, 시작: str, 판정: str, 거래일: str, 기본: bool = True,
         높음: float = 0.03, 날들: tuple[str, ...] = DAYS) -> None:  # fmt: skip
    k.execute("INSERT INTO batch_runs (job_name, market, status, trade_date, started_at, step_log) VALUES"
              " ('backtest', 'KR', 'success', ?, ?, ?)",
              [거래일, 거래일 + "T17:11:00", json.dumps({"group_id": 그룹, "params": {"default": 기본},
                                                     "high52_judge": {"start": 시작, "verdict": 판정}})])  # fmt: skip
    for j, (전략, r) in enumerate((("high52", 높음), ("momentum", 0.01), ("composite", 0.0)), 1):
        rid = 번호 * 10 + j
        k.execute("INSERT INTO backtest_runs VALUES (?, 'KR', ?, ?, 20, ?)", [rid, 전략, 그룹, 거래일])
        eq = 1.0
        for d in 날들:
            k.execute("INSERT INTO backtest_curves (run_id, date, equity) VALUES (?, ?, ?)", [rid, d, eq])
            eq *= 1 + r


def _행(k: sqlite3.Connection) -> dict:
    return {(lb, c, g): (n, round(m, 6), p) for lb, c, g, n, m, p in k.execute(SQL)}


def test_판정_실행의_첫_통과_탈락만_시작_다음_달부터() -> None:
    k = _db()
    _실행(k, 1, "nov", "2023-08-01", "판정 불가", "2026-11-03")  # 판정 불가 → 다음 달로
    _실행(k, 5, "nov_late", "2023-08-01", "통과", "2026-11-04")  # 같은 달 두 번째 실행 — 판정 실행이 아니다
    _실행(k, 6, "nov_hand", "2023-08-01", "통과", "2026-11-20")  # 3~7일 밖 손 실행
    _실행(k, 4, "nd", "2023-08-01", "통과", "2026-11-05", 기본=False)  # 기본 실행이 아니면 아니다
    _실행(k, 2, "dec", "2023-09-01", "통과", "2026-12-03")
    _실행(k, 3, "jan", "2023-08-01", "탈락", "2027-01-03")  # 그 뒤 판정 실행은 'first' 가 아니다
    rows = _행(k)
    # dec 실행: 시작 2023-09 → 10월부터. 10월(부분 허용)·11월(금지)·2025-03(금지)·2025-04(재개) = 4달
    assert rows[("first", "high52", "0_전체")] == (4, 0.02, 4)
    assert rows[("first", "high52", "1_부분허용")][0] == 1 and rows[("first", "high52", "2_전면금지")][0] == 2
    assert rows[("first", "high52", "3_전면재개")][0] == 1
    assert {lb for lb, _c, _g in rows} == {"first"}


def test_2028_04_다시_보기는_따로() -> None:
    k = _db()
    _실행(k, 2, "dec", "2023-09-01", "통과", "2026-12-03")
    _실행(k, 7, "apr28", "2023-08-01", "통과", "2028-04-04", 높음=0.0)
    rows = _행(k)
    assert rows[("first", "high52", "0_전체")] == (4, 0.02, 4)
    assert rows[("2028-04", "high52", "0_전체")] == (5, -0.01, 0)  # 시작 2023-08 → 9월부터 다섯 달


def test_5년_곡선도_한_번만_훑는다() -> None:
    """상관 하위 질의 꼴이면 1,250일 곡선에서 수백억 단계 — 윈도 함수 꼴은 1초 안(25.1651)."""
    날들, d = [], date(2021, 9, 1)
    while len(날들) < 1250:
        if d.weekday() < 5:
            날들.append(d.isoformat())
        d += timedelta(days=1)
    k = _db()
    _실행(k, 2, "dec", "2022-09-01", "통과", "2026-12-03", 날들=tuple(날들))
    t = time.perf_counter()
    rows = _행(k)
    assert time.perf_counter() - t < 2.0
    assert rows[("first", "high52", "0_전체")][0] == len({x[:7] for x in 날들 if x[:7] > "2022-09"})


def test_28회차_고정본은_정의가_그대로이고_high52_가_없다() -> None:
    옛 = (DIAG / "round28_short_regime.sql").read_text(encoding="utf-8")
    assert "high52" not in 옛 and "momentum_sector" in 옛 and "ROW_NUMBER()" in 옛


def test_db_status_는_머리_주석을_걷고_받는다() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("db_status", Path(__file__).resolve().parent.parent / "scripts/db_status.py")
    m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    assert m.is_read_only(SQL) and m.is_read_only((DIAG / "round28_short_regime.sql").read_text(encoding="utf-8"))
    assert not m.is_read_only("-- 주석\nDELETE FROM batch_runs")
    assert not m.is_read_only("-- 주석만")


def test_3_7일_밖_실행만_있는_달은_판정_실행이_없다() -> None:
    k = _db()
    _실행(k, 6, "nov_hand", "2023-08-01", "통과", "2026-11-20")
    assert _행(k) == {}


def _계획(sql: str) -> list[str]:
    """실제 마이그레이션 스키마(곡선 표 UNIQUE(run_id, date)) 위의 실행 계획."""
    k = sqlite3.connect(":memory:")
    for f in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
        k.executescript(f.read_text(encoding="utf-8"))
    return [str(r[-1]) for r in k.execute("EXPLAIN QUERY PLAN " + sql)]


def test_곡선_표는_실행_번호로만_찾는다() -> None:
    """28회차 꼴이 `JOIN runs` 로 곡선 표 전체를 세 번 훑었다(382,500행 표에서 114만 회, 25.1655 교차검증)."""
    for 파일 in ("round28_short_regime.sql", "round37_high52_regime.sql"):
        계획 = _계획((DIAG / 파일).read_text(encoding="utf-8"))
        훑기 = [p for p in 계획 if p.startswith("SCAN c") or "SCAN backtest_curves" in p]
        assert not 훑기, (파일, 계획)


def test_db_status_는_줄바꿈_흉내로_속지_않는다() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("db_status", Path(__file__).resolve().parent.parent / "scripts/db_status.py")
    m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    for 끊음 in ("\r", "\f", "\x85", " "):
        assert not m.is_read_only(f"-- 머리{끊음}SELECT 1\nANALYZE")
    assert not m.is_read_only("ANALYZE") and not m.is_read_only("SELECT 1 FROM x; ANALYZE")
    assert m.is_read_only("-- 머리\n-- 둘째\nSELECT 1")
    # 줄 자르기 고침만 따로 붙잡는다 — 쓰기 낱말이 없는 몸(25.1657, 교차검증 [권고])
    assert not m.is_read_only("-- x\rSELECT 1\nBEGIN")
    # Windows 줄끝으로 붙여 넣어도 받는다
    assert m.is_read_only("-- 머리\r\n-- 둘째\r\nSELECT 1\r\nFROM t\r\n")


def test_db_status_는_pragma_표_함수를_막는다() -> None:
    """`pragma_optimize` 는 `\\bpragma\\b` 를 비켜 SELECT 한 문장 안에서 ANALYZE 를 돌렸다(25.1657, 교차검증 [필수])."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("db_status", Path(__file__).resolve().parent.parent / "scripts/db_status.py")
    m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    for 나쁨 in ("SELECT * FROM pragma_optimize", "SELECT (SELECT 1) AS n, * FROM PRAGMA_OPTIMIZE",
                 "SELECT * FROM pragma_table_info('x')", "SELECT load_extension('x')"):  # fmt: skip
        assert not m.is_read_only(나쁨), 나쁨
    assert m.is_read_only("SELECT pragmatic FROM t") is False  # 애매하면 거절(보수적)
    assert m.is_read_only("SELECT job_name FROM batch_runs WHERE job_name = 'analyze_stock'")


def test_2028_04_판정_불가여도_시작이_있으면_다시_본다() -> None:
    k = _db()
    _실행(k, 2, "dec", "2023-09-01", "통과", "2026-12-03")
    _실행(k, 7, "apr28", "2023-08-01", "판정 불가", "2028-04-04", 높음=0.0)  # 예: 무위험수익률 없음
    assert _행(k)[("2028-04", "high52", "0_전체")][0] == 5
