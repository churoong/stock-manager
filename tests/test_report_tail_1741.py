"""리포트 뒤쪽 절 — 매도 플래그 표시, 일정 묶기, 공시 머리, 위원회 종목 이름, 공시 끝 절 (docs/infra.md 25.1741).

2026-10-23 사용자 "오늘 아침에 온 리포트보면 여전히 내용이 가독성이 떨어지는데" — 실물 찍기 검토에서 [적]·[녹] 뜻을 몰랐고,
같은 날 배당 넷이 넉 줄, 공시 줄마다 같은 꼬리, 위원회 줄에 6자리 코드가 나갔다.
"""

from __future__ import annotations

import sqlite3

from batch.jobs import daily
from batch.jobs import paper as pj
from batch.notify import report_sections as rs
from batch.notify import telegram as tg
from tests.test_corp_events_989 import _Client


def test_매도_플래그는_색_동그라미() -> None:
    줄 = rs.render_sell_flags([{"level": "red", "new": True, "rationale": "카카오: 손절선"},
                              {"level": "green", "rationale": "SK하이닉스: 목표"}])  # fmt: skip
    assert 줄[1:] == ["  🔴 NEW 카카오: 손절선", "  🟢 SK하이닉스: 목표"]


def test_같은_날_같은_일정은_한_줄에_종목을_모은다() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    from pathlib import Path

    con.executescript((Path(__file__).resolve().parent.parent / "migrations" / "0047_kr_opinions_events.sql").read_text())
    for code in ("005930", "000660", "035720"):
        con.execute("INSERT INTO kr_corp_events VALUES (?, 'dividend', '2026-10-31', NULL, '{}', 'kis_openapi', 't')", [code])
    con.execute("INSERT INTO kr_corp_events VALUES ('263750', 'bonus', '2026-10-29', NULL, '{}', 'kis_openapi', 't')")
    lines = daily._corp_event_lines(_Client(con), "2026-10-22", {
        "005930": ("삼성전자", "보유"), "000660": ("SK하이닉스", "보유"), "035720": ("카카오", "보유"),
        "263750": ("펄어비스", "보유")})  # fmt: skip
    assert lines[1:] == ["· 펄어비스(263750) 무상증자 기준일 10-29 (권리락 10-28, 보유)",
                         "· 배당 기준일 10-31 (배당락 10-29, 보유) — SK하이닉스(000660)·삼성전자(005930)·카카오(035720)"]


def test_공시_기준일이_다르면_줄마다() -> None:
    assert daily._공시_절("DART", [("2026-10-14", "· 가"), ("2026-10-15", "· 나")]) == [
        "📰 신호 기준일 뒤에 접수된 공시 (DART)", "· 가 — 신호는 10-14 종가로 계산", "· 나 — 신호는 10-15 종가로 계산"]
    assert daily._공시_절("DART", []) == []


class _Rows:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def execute(self, sql, args=None):  # noqa: ANN001, ANN201
        class R:
            rows = self.con.execute(sql, args or []).fetchall()

        return R()


def test_위원회_가장_높은_표는_종목_이름으로() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (ticker TEXT, country TEXT, name_ko TEXT, name_en TEXT)")
    con.execute("INSERT INTO stocks VALUES ('097950', 'KR', 'CJ제일제당', NULL), ('097950', 'US', NULL, 'X')")
    줄 = ["🏛 A 가치투자사 위원회: 안건 20", "   가장 높은 표 097950 2.40 (찬성 리서치부)", "   가장 높은 표 999999 1.00 (찬성 없음)"]
    기록: dict = {}
    assert pj._표_이름(_Rows(con), "KR", 줄, 기록) == [
        "🏛 A 가치투자사 위원회: 안건 20", "   가장 높은 표 CJ제일제당 2.40 (찬성 리서치부)", "   가장 높은 표 999999 1.00 (찬성 없음)"]
    # 미국은 짧은 이름 + 티커
    con.execute("INSERT INTO stocks VALUES ('LTC', 'US', NULL, 'LTC Properties, Inc.')")
    assert pj._표_이름(_Rows(con), "US", ["   가장 높은 표 LTC 1.82 (찬성 퀀트부)"], 기록) == [
        "   가장 높은 표 LTC Properties(LTC) 1.82 (찬성 퀀트부)"]

    class 고장:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            raise RuntimeError("x")

    assert pj._표_이름(고장(), "KR", 줄, 기록) == 줄 and 기록["top_vote_names_error"] == "RuntimeError"


def test_공시_절은_끝_절이라_2부_이어서를_달지_않는다() -> None:
    조각 = tg._carry_headers(["2부 포트폴리오\n가  100만원 (3.3%)", "📰 신호 기준일 뒤에 접수된 공시 (SEC)\n· 가"])
    assert 조각[1].startswith("📰")
