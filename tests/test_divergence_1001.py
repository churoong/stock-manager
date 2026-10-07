"""점수 × 수급 × 증권사 의견 엇갈림 (docs/reports.md 3.9, docs/infra.md 25.1001) — 손으로 셀 수 있는 고정 데이터."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from batch.jobs import daily
from batch.services import divergence as dv

MIG = Path(__file__).resolve().parent.parent / "migrations"


def test_목표가는_같은_증권사의_바로_앞과_비교한다() -> None:
    rows = [
        ("2026-05-01", "A", 100.0), ("2026-09-20", "A", 90.0),   # A 내림(기간 안)
        ("2026-09-01", "B", 100.0), ("2026-09-25", "B", 120.0),  # B 올림
        ("2026-09-10", "C", 100.0), ("2026-09-28", "C", 100.2),  # C 반올림 차이 — 세지 않음
        ("2026-09-15", "D", 80.0),                               # D 앞 목표가 없음
        ("2026-08-01", "E", 100.0), ("2026-08-20", "E", 50.0),   # E 기간 밖 내림
        ("2026-09-21", "A", None),                               # 빈 목표가 — 건너뜀
    ]  # fmt: skip
    assert dv.target_changes(rows, "2026-09-07") == (1, 1)


def test_반대인_종목만_적는다() -> None:
    같은 = dv.View("가", "000001", 5, 1_000, 500, 1, 0)
    수급 = dv.View("나", "000002", 5, -30_000, 10_000, 0, 0)
    둘다 = dv.View("다", "000003", 4, -100, -200, 0, 2)
    적음 = dv.View("라", "000004", 2, -9_999, -9_999, 0, 0)  # 수급 2일 — 판단 안 함
    lines = dv.render([같은, 수급, 둘다, 적음])
    assert lines[0].startswith("🔀") and "추천 4종목 중 2" in lines[0]
    assert lines[1] == "· 나(000002) ⚠ 외국인 -300억·기관 +100억 (5일)"
    assert lines[2] == "· 다(000003) ⚠ 외국인 -1.0억·기관 -2.0억 (4일) · ⚠ 목표가 내림 2·올림 0 (30일) — 둘 다 반대"
    assert lines[3] == "  나머지 2종목은 반대 신호 없음(자료 없음 포함)"
    assert dv.render([같은, 적음]) == []


class _Client:
    def __init__(self, con: sqlite3.Connection | None) -> None:
        self.con = con

    def execute(self, sql, args=None):
        if self.con is None:
            raise RuntimeError("no such table: kr_flows")
        return SimpleNamespace(rows=self.con.execute(sql, args or []).fetchall())


def test_리포트_절은_실제_스키마에서_돈다() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    for f in ("0046_kr_flows.sql", "0047_kr_opinions_events.sql"):
        con.executescript((MIG / f).read_text())
    con.executemany("INSERT INTO stocks (id) VALUES (?)", [(1,), (2,), (3,)])
    # 종목 1: 6일 중 최근 5일만 — 외국인 -10·기관 -10 × 5 = -100 (6번째 날 +1000 은 빠진다)
    for i, day in enumerate(("2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06")):
        f = 1000 if i == 0 else -10
        con.execute("INSERT INTO kr_flows (stock_id, date, frgn_net_amt, orgn_net_amt, source, fetched_at)"
                    " VALUES (1, ?, ?, -10, 'kis', 't')", [day, f])  # fmt: skip
    # 리포트 날 뒤의 행은 보지 않는다
    con.execute("INSERT INTO kr_flows (stock_id, date, frgn_net_amt, orgn_net_amt, source, fetched_at)"
                " VALUES (1, '2026-10-08', 99999, 99999, 'kis', 't')")  # fmt: skip
    for day, broker, tp in (("2026-06-01", "X", 100.0), ("2026-09-20", "X", 80.0)):
        con.execute("INSERT INTO kr_opinions (stock_id, date, broker, target_price, source, fetched_at)"
                    " VALUES (2, ?, ?, ?, 'kis', 't')", [day, broker, tp])  # fmt: skip
    picks = [SimpleNamespace(stock_id=1, name="가", ticker="000001"), SimpleNamespace(stock_id=1, name="가", ticker="000001"),
             SimpleNamespace(stock_id=2, name="나", ticker="000002"), SimpleNamespace(stock_id=3, name="다", ticker="000003")]  # fmt: skip
    lines = daily._divergence_lines(_Client(con), "2026-10-07", picks)
    assert "추천 3종목 중 2" in lines[0]
    assert lines[1] == "· 가(000001) ⚠ 외국인 -0.5억·기관 -0.5억 (5일)"
    assert lines[2] == "· 나(000002) ⚠ 목표가 내림 1·올림 0 (30일)"
    assert lines[3] == "  나머지 1종목은 반대 신호 없음(자료 없음 포함)"


def test_표가_없으면_조용히_빠진다() -> None:
    warnings: list[str] = []
    picks = [SimpleNamespace(stock_id=1, name="가", ticker="000001")]
    assert daily._divergence_lines(_Client(None), "2026-10-07", picks, warnings) == []
    assert warnings == []
