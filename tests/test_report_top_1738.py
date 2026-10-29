"""리포트 첫 화면 — 오늘 볼 것 한 줄, 대표 종목은 끝으로, 운용 보고의 군더더기 줄 빼기 (docs/infra.md 25.1738).

2026-10-23 사용자 "오늘 아침에 온 리포트보면 여전히 내용이 가독성이 떨어지는데 아직 수정안한거야?" — 실물로 찍어 보니
첫 화면을 대표 종목 시세·출처가 차지했고, 보유 손절 플래그는 150번째 줄에 있었다.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

from batch.jobs import daily
from batch.notify import formatter
from batch.services import desk


def _flag(name: str, level: str, new: bool = False) -> dict:
    return {"name": name, "level": level, "new": new, "rationale": "x", "as_of_date": "2026-10-22"}


def test_오늘_볼_것은_플래그를_색마다_묶어_이름과() -> None:
    flags = [_flag("카카오", "red", new=True), _flag("카카오", "red"), _flag("펄어비스", "red"),
             _flag("SK하이닉스", "green"), _flag("가", "yellow"), _flag("나", "yellow"), _flag("다", "yellow"),
             _flag("라", "yellow")]  # fmt: skip
    assert formatter.todo_line(flags) == (
        "🔔 보유 매도 플래그: 🔴손절 2(카카오·펄어비스) · 🟢목표 도달 1(SK하이닉스) · 🟡점검 4(가·나·다 외 1) — 새로 걸림 1")
    assert formatter.todo_line([]) is None
    # 미국 이름은 짧게
    assert formatter.todo_line([_flag("Apple Inc. Common Stock", "green")], "USD") == "🔔 보유 매도 플래그: 🟢목표 도달 1(Apple)"


def test_오늘_볼_것은_제목_바로_아래_대표_종목은_머리에_없다() -> None:
    rows = [{"name": "삼성전자", "close": 61_000.0, "change_pct": -1.85, "volume": 1_840_000, "currency": "KRW",
             "source": "krx_openapi", "trade_date": "2026-10-22"}]  # fmt: skip
    글 = formatter.daily_report(market="KR", trade_date="2026-10-22", rows=rows,
                              started_at=datetime(2026, 10, 22, 23, 31, tzinfo=UTC), summary="국내 2,771종목 저장",
                              todo="🔔 보유 매도 플래그: 손절 1(카카오)", rows_in_head=False)  # fmt: skip
    줄 = 글.split("\n")
    assert 줄[1] == "🔔 보유 매도 플래그: 손절 1(카카오)"
    assert "삼성전자" not in 글 and "출처" not in 글
    # 대표 종목은 끝에 붙이는 블록 — 종목마다 한 줄
    assert formatter.watched_block(rows, "2026-10-22") == (
        "대표 종목 (2026-10-22 종가)\n  삼성전자 61,000원 -1.85% · 거래량 184만주\n출처 krx_openapi")
    # 머리에 둘 때(기본)도 같은 블록
    assert "  삼성전자 61,000원 -1.85% · 거래량 184만주" in formatter.daily_report(
        market="KR", trade_date="2026-10-22", rows=rows, started_at=datetime(2026, 10, 22, 23, 31, tzinfo=UTC))


def test_일일_배치는_대표_종목을_운용_보고_뒤_끝에_붙인다() -> None:
    src = inspect.getsource(daily)
    assert "rows_in_head=False" in src and "todo=formatter.todo_line(sell_flag_rows, currency, cal.user_today().isoformat())" in src
    # 운용 보고(with_head)를 끼운 뒤, 늦은 경고 앞
    assert src.index("formatter.with_head(message, 머리)") < src.index("formatter.watched_block(rows, decision.trade_date)")
    assert src.index("formatter.watched_block(rows, decision.trade_date)") < src.index(
        "formatter.late_warnings(warnings[본문_경고수:])")


def test_운용_보고는_경고_개수_줄을_싣지_않는다() -> None:
    """리포트가 경고를 본문에 그대로 싣는다 — "이 실행의 경고 1줄 — 시스템 상태 화면에서" 는 웹 운용 화면에만"""
    확인 = desk.checks(halted=False, paper=None, broker={"send": "오늘은 장이 없다"}, recon_bad=[], warnings=1)
    assert 확인 == [{"level": "normal", "text": "이 실행의 경고 1줄 — 시스템 상태 화면에서", "kind": "warnings"}]
    칸 = {"checks": 확인,
         "trade_date": "2026-10-22", "session_date": "2026-10-23", "fills_h": [], "orders_h": []}  # fmt: skip
    assert desk.report_lines(칸, {"rows": []}, "H")[1] == "▸ 확인할 일: 없음"
