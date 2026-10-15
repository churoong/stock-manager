"""1부 국내 추천 종목은 금액이 정해진 현금배당만 배당 일정에 싣는다 (docs/infra.md 25.1420, docs/factors.md 12.30 P6)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from batch.jobs import daily
from tests.test_corp_events_989 import _Client


def _con(rows: list[tuple[str, str, dict]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    con.executescript((Path(__file__).resolve().parent.parent / "migrations" / "0047_kr_opinions_events.sql").read_text())
    for code, day, d in rows:
        con.execute("INSERT INTO kr_corp_events VALUES (?, 'dividend', ?, NULL, ?, 'kis_openapi', 't')",
                    [code, day, json.dumps(d)])  # fmt: skip
    return con


def test_금액이_정해진_현금배당만_종가_대비로() -> None:
    con = _con([
        ("000001", "2026-10-20", {"per_sto_divi_amt": "500", "stk_divi_rate": "0"}),
        ("000002", "2026-10-20", {"per_sto_divi_amt": "0", "stk_divi_rate": "0"}),  # 미정
        ("000003", "2026-10-20", {"per_sto_divi_amt": "100", "stk_divi_rate": "0.05"}),  # 주식배당 섞임
        ("000004", "2026-10-20", {"per_sto_divi_amt": "300"}),  # 보유 — 예전처럼 금액 없이
    ])  # fmt: skip
    기록: dict[str, int] = {}
    종목 = {"000001": ("가", "추천"), "000002": ("나", "추천"), "000003": ("다", "추천"), "000004": ("라", "보유")}
    lines = daily._corp_event_lines(_Client(con), "2026-10-14", 종목,
                                    종가={"000001": (41_000.0, "2026-10-14")}, 기록=기록)  # fmt: skip
    assert lines[1:] == [
        "· 가(000001) 배당 기준일 10-20 (배당락 10-19, 주당 500원 = 10-14 종가의 1.2%, 추천)",
        "· 라(000004) 배당 기준일 10-20 (배당락 10-19, 보유)",
    ]
    assert 기록 == {"in_window": 3, "amount_known": 1, "undetermined": 1, "stock_div": 1}


def test_종가가_없으면_싣지_않고_센다() -> None:
    con = _con([("000001", "2026-10-20", {"per_sto_divi_amt": "500"})])
    기록: dict[str, int] = {}
    assert daily._corp_event_lines(_Client(con), "2026-10-14", {"000001": ("가", "추천")}, 기록=기록) == []
    assert 기록 == {"in_window": 1, "no_close": 1}
