"""1부 국내 종목의 신호 기준일 뒤에 접수된 공시 사실 줄 (docs/infra.md 25.1428, docs/factors.md 12.30 P2)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from batch.jobs import daily
from tests.test_corp_events_989 import _Client


def _con(rows: list[tuple[int, str, str]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE disclosures (id INTEGER PRIMARY KEY, stock_id INT, corp_code TEXT, receipt_no TEXT, title TEXT,"
        " disclosed_at TEXT, url TEXT, report_name TEXT, is_material INT, source TEXT, fetched_at TEXT)"
    )
    for i, (sid, title, day) in enumerate(rows):
        con.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, source, fetched_at)"
                    " VALUES (?, 'c', ?, ?, ?, 'dart', 't')", [sid, str(i), title, day])  # fmt: skip
    return con


def _pick(sid: int, as_of: str) -> SimpleNamespace:
    return SimpleNamespace(stock_id=sid, name=f"종목{sid}", ticker=f"00000{sid}", as_of_date=as_of)


def test_기준일_뒤_접수만_싣고_같은_날은_센다() -> None:
    con = _con([
        (1, "주요사항보고서(유상증자결정)", "2026-10-15"),
        (1, "[기재정정]주요사항보고서(유상증자결정)", "2026-10-15"),  # 정정 — 뺀다
        (1, "단일판매ㆍ공급계약체결", "2026-10-16"),
        (2, "현금ㆍ현물배당결정", "2026-10-14"),  # 기준일과 같은 날 — 모름, 싣지 않음
        (3, "분기보고서", "2026-10-13"),  # 기준일 전
    ])  # fmt: skip
    기록: dict[str, int] = {}
    picks = [_pick(1, "2026-10-14"), _pick(1, "2026-10-14"), _pick(2, "2026-10-14"), _pick(3, "2026-10-14")]
    lines = daily._post_close_lines(_Client(con), picks, [], 기록)
    assert lines[1:] == ["· 종목1(000001) '주요사항보고서(유상증자결정)' 외 1건 (10-15 접수) — 신호는 10-14 종가로 계산"]
    assert 기록 == {"picks": 3, "after": 1, "same_day_unknown": 1}


def test_표가_없으면_조용히() -> None:
    con = sqlite3.connect(":memory:")
    경고: list[str] = []
    assert daily._post_close_lines(_Client(con), [_pick(1, "2026-10-14")], 경고) == [] and 경고 == []
