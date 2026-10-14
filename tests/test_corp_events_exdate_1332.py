"""아침 리포트 기업행위 일정이 이미 락이 지난 기준일을 "앞으로의 일정" 으로 싣지 않는다 (docs/infra.md 25.1332, 배당 감사 #3).

`trade_date` 는 리포트 전날(데이터의 날)이다. 예전 하한 "기준일 > trade_date" 로는 리포트 날이 기준일인 배당(락은 어제)이 실렸다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from batch.jobs import daily
from tests.test_corp_events_989 import _Client


def _con(rows: list[tuple[str, str, str]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    con.executescript((Path(__file__).resolve().parent.parent / "migrations" / "0047_kr_opinions_events.sql").read_text())
    for code, kind, day in rows:
        con.execute("INSERT INTO kr_corp_events VALUES (?, ?, ?, NULL, '{}', 'kis_openapi', 't')", [code, kind, day])
    return con


def test_리포트_날이_기준일이면_락은_어제라_빠진다() -> None:
    # 데이터의 날 10-14(수) → 리포트 10-15(목). 10-15 기준일의 배당락은 10-14 — 지났다. 10-16 기준일은 락 10-15(오늘)
    con = _con([("005930", "dividend", "2026-10-15"), ("005930", "bonus", "2026-10-16")])
    lines = daily._corp_event_lines(_Client(con), "2026-10-14", {"005930": ("삼성전자", "보유")})
    assert lines[1:] == ["· 삼성전자(005930) 무상증자 기준일 10-16 (권리락 10-15, 보유)"]


def test_월요일_리포트의_월요일_기준일도_빠진다() -> None:
    # 데이터의 날 10-16(금) → 리포트 10-19(월). 월요일 기준일의 락은 금요일 — 지났다
    con = _con([("005930", "dividend", "2026-10-19"), ("005930", "dividend", "2026-10-20")])
    lines = daily._corp_event_lines(_Client(con), "2026-10-16", {"005930": ("삼성전자", "보유")})
    assert lines[1:] == ["· 삼성전자(005930) 배당 기준일 10-20 (배당락 10-19, 보유)"]


def test_액면교체는_락_없이_그대로() -> None:
    con = _con([("005930", "split", "2026-10-15")])
    lines = daily._corp_event_lines(_Client(con), "2026-10-14", {"005930": ("삼성전자", "보유")})
    assert lines[1:] == ["· 삼성전자(005930) 액면교체·합병·분할 기준일 10-15 (보유)"]
