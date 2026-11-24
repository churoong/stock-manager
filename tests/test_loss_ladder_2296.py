"""계좌 손실 사다리 — 그림자 (docs/desk.md 6장, docs/roadmap.md 1장 #3, docs/infra.md 25.2296)."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from batch.jobs import desk as job
from batch.services import loss_ladder as ll
from tests.test_paper_job_1445 import Client

뿌리 = Path(__file__).resolve().parent.parent


def _계열(값들: list[float]) -> list[tuple[str, float]]:
    return [(f"2026-01-{i + 1:02d}", v) for i, v in enumerate(값들)]


def test_한_주와_고점_대비를_손으로_센다() -> None:
    # 지수 100 → 102 → 99 → 97 → 96 → 95: 한 주(행 5개 앞) = 95/100 − 1 = −5.0%, 고점 102 대비 95/102 − 1 = −6.86%
    m = ll.measures(_계열([100, 102, 99, 97, 96, 95]))
    assert [x["week"] for x in m[:5]] == [None] * 5 and m[5]["week"] == pytest.approx(-5.0)
    assert m[5]["dd"] == pytest.approx((95 / 102 - 1) * 100) and m[1]["dd"] == 0 and m[0]["dd"] == 0
    assert all(x["month"] is None for x in m) and ll.WINDOWS == {"week": 5, "month": 21}
    # 차례에 기대지 않는다(날짜로 세운다) · 0 이하 지수는 버린다
    뒤섞음 = list(reversed(_계열([100, 102, 99, 97, 96, 95])))
    assert ll.measures(뒤섞음) == m
    assert len(ll.measures([("2026-01-01", 0.0), ("2026-01-02", 100.0), ("2026-01-03", -1.0)])) == 1


def test_한_달은_행_스물하나_앞과_견준다() -> None:
    값들 = [100.0] * 21 + [90.0, 95.0]
    m = ll.measures(_계열(값들))
    assert m[20]["month"] is None and m[21]["month"] == pytest.approx(-10.0) and m[22]["month"] == pytest.approx(-5.0)


def test_정확히_문턱인_날은_걸린_날이_아니다() -> None:
    """하루 손실 한도와 같은 꼴(25.1632) — "넘게" 잃은 날만"""
    found = ll.ladder(_계열([100, 102, 99, 97, 96, 95]))  # 한 주 정확히 −5.0%
    주 = {문턱: (날, 번) for 문턱, 날, 번 in found["hits"]["week"]}
    assert 주 == {-3.0: (1, 1), -5.0: (0, 0), -7.0: (0, 0)}
    assert ll._걸림([-5.0, -5.01], -5.0) == (1, 1)
    # 부동소수: 95 ÷ 100 − 1 은 −5.000000000000004% 다 — 여유(`EPS`) 없이는 정확히 −5% 가 걸린 날로 세어졌다
    assert (95 / 100 - 1) * 100 < -5.0 and ll._걸림([(95 / 100 - 1) * 100], -5.0) == (0, 0) and 0 < ll.EPS <= 1e-6
    assert found["days"] == 6 and found["now"]["week"] == pytest.approx(-5.0) and found["now"]["month"] is None
    assert found["now"]["dd"] == pytest.approx((95 / 102 - 1) * 100)


def test_이어진_날은_한_번이고_끊겼다_다시_걸리면_새_번이다() -> None:
    assert ll._걸림([None, -4, -2, -4, -4, None, -6], -3) == (4, 3)
    assert ll._걸림([], -3) == (0, 0) and ll._걸림([None, None], -3) == (0, 0)
    # 고점 대비: 100 → 88(−12%) → 89 → 100(회복) → 80(−20%, 정확히 문턱) → 79
    found = ll.ladder(_계열([100, 88, 89, 100, 80, 79]))
    고점 = {문턱: (날, 번) for 문턱, 날, 번 in found["hits"]["dd"]}
    assert 고점 == {-10.0: (4, 2), -15.0: (2, 1), -20.0: (1, 1)}


def test_빈_계열과_줄() -> None:
    빈 = ll.ladder([])
    assert 빈 == {"days": 0, "now": {"week": None, "month": None, "dd": None},
                 "hits": {k: [[t, 0, 0] for t in ll.GRID[k]] for k in ll.GRID}}  # fmt: skip
    줄 = ll.lines("H", ll.ladder(_계열([100, 102, 99, 97, 96, 95])))
    assert 줄[0] == "계좌 손실 사다리(그림자) H: 평가 6일 · 한 주 -5.0% · 한 달 모름 · 고점 대비 -6.9%"
    assert 줄[1] == "  한 주 걸렸을 날/번: -3% 1일/1번 · -5% 0일/0번 · -7% 0일/0번"
    assert len(줄) == 4 and "그림자" in 줄[0]
    assert ll.lines("H", {})[0] == "계좌 손실 사다리(그림자) H: 평가 0일 · 한 주 모름 · 한 달 모름 · 고점 대비 모름"


def test_격자는_갈래마다_얕은_것부터_깊은_것으로() -> None:
    assert set(ll.GRID) == {"week", "month", "dd"} and set(ll.WINDOWS) < set(ll.GRID)
    for k, 문턱들 in ll.GRID.items():
        assert all(t < 0 for t in 문턱들) and list(문턱들) == sorted(문턱들, reverse=True), k


def test_운용_요약에_베끼는_계좌_하나만_실린다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for aid, as_of, twr in (("B", "2026-10-15", 1.0), ("B", "2026-10-16", 0.9), ("A", "2026-10-16", 0.5)):
        c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
                       " twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1000, 400, 600, 1000, 0, ?, '[]', 1, 't')",
                       [aid, as_of, twr])  # fmt: skip
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    사다리 = s["loss_ladder"]
    assert 사다리["account"] == "B" and 사다리["days"] == 2 and 사다리["now"]["dd"] == pytest.approx(-10.0)
    assert 사다리["hits"]["dd"] == [[-10.0, 0, 0], [-15.0, 0, 0], [-20.0, 0, 0]]  # 정확히 −10% 는 안 걸림
    assert s["gate_error"] is None
    # 사다리가 던져도 관문·위험판은 남는다
    monkeypatch.setattr(job.loss_ladder, "ladder", lambda v: 1 / 0)
    job.write(c, "KR", datetime(2026, 10, 16, 23, 50, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    assert s["loss_ladder"] == {"account": "B", "error": "ZeroDivisionError: division by zero"}
    assert s["gate"] is not None and s["gate_error"] is None


def test_관문판이_통째로_깨진_실행은_앞_실행의_사다리를_지운다() -> None:
    """요약은 같은 날 앞 실행의 것에 덮어 합친다(25.2285) — 실패 갈래가 칸을 싣지 않으면 낡은 값이 남는다"""
    판, 글 = job._관문판(object(), datetime(2026, 10, 16, tzinfo=UTC), {}, "B", set(), [], since="", version=None)
    assert 글 and "loss_ladder" in 판 and 판["loss_ladder"] is None


def test_읽지도_쓰지도_않고_주문_경로가_부르지_않는다_그리고_문서() -> None:
    글 = inspect.getsource(ll)
    for 금지 in ("execute", "client", "SELECT", "INSERT", "UPDATE", "import requests"):
        assert 금지 not in 글, 금지
    # 그림자다 — 주문·장부·판정 경로가 이 모듈을 부르지 않는다(운용 요약 작업만)
    부르는곳 = sorted(p.relative_to(뿌리).as_posix() for p in (뿌리 / "batch").rglob("*.py")
                   if "loss_ladder" in p.read_text() and p.name != "loss_ladder.py")  # fmt: skip
    assert 부르는곳 == ["batch/jobs/desk.py"]
    문서 = (뿌리 / "docs" / "desk.md").read_text()
    for 조각 in ("**계좌 손실 사다리 — 그림자**", "25.2296", "`loss_ladder`", "규칙을 만들지 않는다", "문턱과 켜기는 사용자 결정",
               f"평가 행 {ll.WINDOWS['week']}개", f"{ll.WINDOWS['month']}개", "걸렸을 날", "걸렸을 번", "관찰용",
               " · ".join(f"{t:g}" for t in ll.GRID["week"]), " · ".join(f"{t:g}" for t in ll.GRID["month"]),
               " · ".join(f"{t:g}" for t in ll.GRID["dd"])):  # fmt: skip
        assert 조각 in 문서, 조각
