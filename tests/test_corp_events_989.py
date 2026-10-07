"""리포트의 기업행위 일정 줄 (docs/reports.md 3.7, docs/infra.md 25.989) — 실제 SQLite 로 돌린다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from batch.jobs import daily


class _Client:
    def __init__(self, con: sqlite3.Connection | None) -> None:
        self.con = con

    def execute(self, sql, args=None):
        if self.con is None:
            raise RuntimeError("no such table: kr_corp_events")
        cur = self.con.execute(sql, args or [])
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

        class R:
            def dicts(self):
                return [dict(zip(cols, r, strict=True)) for r in rows]

        return R()


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    con.executescript((Path(__file__).resolve().parent.parent / "migrations" / "0047_kr_opinions_events.sql").read_text())
    for code, kind, day in (("052400", "bonus", "2026-10-30"), ("005930", "dividend", "2026-10-12"),
                            ("000660", "dividend", "2026-10-12"), ("005930", "split", "2026-11-30"),
                            ("111111", "bonus", "2026-10-09")):  # fmt: skip
        con.execute("INSERT INTO kr_corp_events VALUES (?, ?, ?, NULL, '{}', 'kis_openapi', 't')", [code, kind, day])
    return con


def test_10일_안의_보유_추천_일정만_배당은_보유만() -> None:
    lines = daily._corp_event_lines(_Client(_con()), "2026-10-07", {
        "005930": ("삼성전자", "보유"), "000660": ("SK하이닉스", "추천"), "052400": ("코나아이", "추천")})
    assert lines == ["📅 기업행위 일정 (10일 안, 예탁원·KIS)", "· 삼성전자(005930) 배당 기준일 10-12 (보유)"]


def test_추천의_무상증자는_싣는다() -> None:
    lines = daily._corp_event_lines(_Client(_con()), "2026-10-21", {"052400": ("코나아이", "추천")})
    assert lines[1] == "· 코나아이(052400) 무상증자 기준일 10-30 (추천)"


def test_표가_없으면_절만_빠진다() -> None:
    assert daily._corp_event_lines(_Client(None), "2026-10-07", {"005930": ("삼성전자", "보유")}) == []


def test_표가_없지_않은_실패는_경고로() -> None:
    class 깨짐(_Client):
        def execute(self, sql, args=None):
            raise RuntimeError("HTTP 500")

    경고: list[str] = []
    assert daily._corp_event_lines(깨짐(None), "2026-10-07", {"005930": ("삼성전자", "보유")}, 경고) == []
    assert 경고 and "기업행위 일정" in 경고[0]
