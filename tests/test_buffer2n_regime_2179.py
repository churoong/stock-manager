"""38회차 P1′ 공매도 국면 집계 SQL — 코드와 같은 커밋에 고정 (docs/infra.md 25.2179, factors.md 12.41·12장 머리 국면 조항).

37회차 `round37_high52_regime.sql` 과 같은 꼴: 대상은 **판정 실행**(그 달 trade_date 3~7일 기본·성공 국내 실행 가운데 첫 실행)만,
'first'(2026-12 이후 처음 통과·탈락)와 '2028-04'(다시 보기). 공통 구간 시작이 없어 곡선 전 구간(둘째 달부터)을 센다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "scripts/diag/round38_buffer2n_regime.sql").read_text(encoding="utf-8")
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


def _실행(k: sqlite3.Connection, 번호: int, 그룹: str, 판정: str | None, 거래일: str, 기본: bool = True,
         버퍼: float = 0.03, 시장: str = "KR") -> None:  # fmt: skip
    로그 = {"group_id": 그룹, "params": {"default": 기본}}
    if 판정 is not None:
        로그["buffer_2n_judge"] = {"verdict": 판정}
    k.execute("INSERT INTO batch_runs (job_name, market, status, trade_date, started_at, step_log) VALUES"
              " ('backtest', ?, 'success', ?, ?, ?)", [시장, 거래일, 거래일 + "T17:11:00", json.dumps(로그)])  # fmt: skip
    for j, (전략, r) in enumerate((("buffer_2n", 버퍼), ("composite", 0.01), ("momentum", 0.5)), 1):
        rid = 번호 * 10 + j
        k.execute("INSERT INTO backtest_runs VALUES (?, ?, ?, ?, 20, ?)", [rid, 시장, 전략, 그룹, 거래일])
        eq = 1.0
        for d in DAYS:
            k.execute("INSERT INTO backtest_curves (run_id, date, equity) VALUES (?, ?, ?)", [rid, d, eq])
            eq *= 1 + r


def _행(k: sqlite3.Connection) -> dict:
    return {(lb, c, g): (n, round(m, 6), p) for lb, c, g, n, m, p in k.execute(SQL)}


def test_판정_실행의_첫_통과_탈락만_둘째_달부터_composite_대비() -> None:
    k = _db()
    _실행(k, 1, "dec", "판정 불가", "2026-12-03")  # 판정 불가 → 다음 달로
    _실행(k, 5, "dec_late", "통과", "2026-12-04")  # 같은 달 두 번째 실행 — 판정 실행이 아니다
    _실행(k, 6, "dec_hand", "통과", "2026-12-20")  # 3~7일 밖 손 실행
    _실행(k, 4, "nd", "통과", "2027-01-05", 기본=False)  # 기본 실행이 아니면 아니다(그 달 첫 실행도 아님)
    _실행(k, 2, "jan", "통과", "2027-01-04")
    _실행(k, 3, "feb", "탈락", "2027-02-03")  # 그 뒤 판정 실행은 'first' 가 아니다
    rows = _행(k)
    # 여섯 점 → 월수익 다섯(9·10·11월·2025-03·04). 9·10월 부분 허용, 11월·3월 금지, 4월 재개
    assert rows[("first", "buffer_2n", "0_전체")] == (5, 0.02, 5)
    assert rows[("first", "buffer_2n", "1_부분허용")][0] == 2 and rows[("first", "buffer_2n", "2_전면금지")][0] == 2
    assert rows[("first", "buffer_2n", "3_전면재개")][0] == 1
    assert {lb for lb, _c, _g in rows} == {"first"}
    # 비교 대상은 composite — momentum(월 +50%)과 견주지 않는다
    assert rows[("first", "buffer_2n", "0_전체")][1] == 0.02


def test_2026_11_판정_실행은_세지_않는다() -> None:
    """코드가 11-03 판정 실행 뒤에 들어갔다 — 11월 실행에 buffer_2n 판정이 있을 수 없지만, 있어도 'first' 가 아니다"""
    k = _db()
    _실행(k, 1, "nov", "통과", "2026-11-04")
    assert _행(k) == {}


def test_2028_04_다시_보기는_판정_불가여도_기록이_있으면() -> None:
    k = _db()
    _실행(k, 2, "jan", "통과", "2027-01-04")
    _실행(k, 7, "apr28", "판정 불가", "2028-04-04", 버퍼=0.0)
    rows = _행(k)
    assert rows[("2028-04", "buffer_2n", "0_전체")] == (5, -0.01, 0)
    _실행(k, 8, "apr28b", None, "2028-04-03")  # 판정 기록 없는 그 달 첫 실행이면 다시 보기 없음
    assert ("2028-04", "buffer_2n", "0_전체") not in _행(k)


def test_미국_실행은_세지_않는다() -> None:
    k = _db()
    _실행(k, 2, "jan_us", "통과", "2027-01-04", 시장="US")
    assert _행(k) == {}


def test_db_status_가_받는_읽기_전용_질의다() -> None:
    from scripts.db_status import is_read_only

    본문 = "\n".join(줄 for 줄 in SQL.splitlines() if not 줄.lstrip().startswith("--"))
    assert is_read_only(본문)
