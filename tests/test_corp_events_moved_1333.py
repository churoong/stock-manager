"""기준일이 옮겨진 기업행위의 옛 날짜가 리포트에 함께 실리지 않는다 (docs/infra.md 25.1333, 배당 감사 #2).

`kr_corp_events` 는 (종목, 종류, 기준일) 로 쌓기만 한다 — 유상증자 기준일이 10-20 에서 11-10 으로 밀리면 옛 행이 남아
"유상증자 기준일 10-20" 이 실렸다. 같은 종목·종류에 더 나중에 받은 앞날 행이 있으면 옛 행은 뺀다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from batch.jobs import daily
from tests.test_corp_events_989 import _Client


def _con(rows: list[tuple[str, str, str, str]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    con.executescript((Path(__file__).resolve().parent.parent / "migrations" / "0047_kr_opinions_events.sql").read_text())
    for code, kind, day, fetched in rows:
        con.execute("INSERT INTO kr_corp_events VALUES (?, ?, ?, NULL, '{}', 'kis_openapi', ?)", [code, kind, day, fetched])
    return con


def test_옮겨진_옛_기준일은_빠지고_새_기준일만() -> None:
    con = _con([("005930", "rights", "2026-10-20", "2026-10-05T09:17:00+00:00"),
                ("005930", "rights", "2026-10-22", "2026-10-13T09:17:00+00:00")])  # fmt: skip
    lines = daily._corp_event_lines(_Client(con), "2026-10-13", {"005930": ("삼성전자", "보유")})
    assert lines[1:] == ["· 삼성전자(005930) 유상증자 기준일 10-22 (권리락 10-21, 보유)"]


def test_같은_날_받은_두_일정은_둘_다() -> None:
    con = _con([("005930", "dividend", "2026-10-16", "2026-10-13T09:17:00+00:00"),
                ("005930", "dividend", "2026-10-22", "2026-10-13T09:17:00+00:00")])  # fmt: skip
    lines = daily._corp_event_lines(_Client(con), "2026-10-13", {"005930": ("삼성전자", "보유")})
    assert len(lines) == 3


def test_다른_종류의_새_행은_가리지_않는다() -> None:
    con = _con([("005930", "bonus", "2026-10-20", "2026-10-05T09:17:00+00:00"),
                ("005930", "dividend", "2026-10-22", "2026-10-13T09:17:00+00:00")])  # fmt: skip
    lines = daily._corp_event_lines(_Client(con), "2026-10-13", {"005930": ("삼성전자", "보유")})
    assert len(lines) == 3
