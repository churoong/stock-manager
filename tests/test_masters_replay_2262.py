"""거장 계좌 거름 — 수익 봉인 소급 재생 (docs/company.md 12.6, docs/infra.md 25.2262).

`scripts/company_replay.py --masters --no-scorecard` 가 뒤 성과를 읽지 않고, 운영과 같은 길로 거장 자료를 붙이고,
12.6 걸림 (c′)(a)(b)(d)(e)를 사전 등록 문턱대로 판정하는지 본다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from batch.services import company as co
from batch.services import masters as ms
from batch.services.company import Opinion


def _m() -> ModuleType:
    경로 = Path(__file__).resolve().parent.parent / "scripts" / "company_replay.py"
    spec = importlib.util.spec_from_file_location("company_replay", 경로)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _item(sid: int, passed: bool, ops: dict[str, int | None] | None = None) -> co.Item:
    의견 = {k: (Opinion(k, 0, 0.0, [], note="자료 없음 — x") if v is None else Opinion(k, v, 1.0, []))
          for k, v in (ops or {}).items()}  # fmt: skip
    return co.Item(sid, f"T{sid:02d}", 의견, 1.0, 2, False, passed, "가결" if passed else "부결: 표 미달")


def _회의(aid: str, day: str, passed: list[int], failed: list[int] = (), ops: dict[str, int | None] | None = None,
         top_n: int = 5) -> tuple[str, str, list[co.Item], list[co.Item]]:  # fmt: skip
    items = [_item(s, True, ops) for s in passed] + [_item(s, False, ops) for s in failed]
    return (aid, day, items, [i for i in items if i.passed][:top_n])


def test_사전_등록_문턱을_그대로_쓴다() -> None:
    m = _m()
    assert (m.NO_DATA_MAX, m.ZERO_DAYS_MAX, m.PASS_RATE_MAX, m.OVER_TOP_N_MAX) == (0.5, 0.8, 0.9, 0.2)
    assert m.OVERLAP_RULE == {"ZM": (("A", "D"), 0.8), "ZN": (("A", "C", "D", "ZM"), 0.5),
                              "ZL": (("A", "C", "D", "ZM"), 0.5)}  # fmt: skip
    assert set(m.OVERLAP_RULE) == set(ms.CHARTERS) and m.MASTER_PEERS == ("A", "C", "D")
    ch = m._charters(True)
    assert list(ch) == ["A", "C", "D", "ZM", "ZN", "ZL"] and ch["ZM"] is ms.CHARTERS["ZM"]
    assert m._charters(False) == co.CHARTERS  # 기본 실행은 예전 그대로


def test_중복은_날별_최댓값의_평균이고_빈_날은_뺀다() -> None:
    m = _m()
    회의 = [
        _회의("ZM", "d1", [1, 2]), _회의("A", "d1", [1]), _회의("D", "d1", [1, 2, 3]),  # max(0.5, 1.0) = 1.0
        _회의("ZM", "d2", [4, 5]), _회의("A", "d2", [9]), _회의("D", "d2", [5]),  # max(0, 0.5) = 0.5
        _회의("ZM", "d3", [], [7]), _회의("A", "d3", [7]),  # ZM 이 빈 날 — 뺀다
    ]  # fmt: skip
    assert m.overlap(회의, "ZM", ("A", "D")) == pytest.approx(0.75)
    assert m.overlap(회의, "ZM", ("C",)) == 0.0  # C 회의가 없는 날은 0
    assert m.overlap([_회의("ZM", "d1", [], [1])], "ZM", ("A",)) is None


def test_걸림을_계좌마다_판정한다() -> None:
    m = _m()
    ch = m._charters(True)
    zm = {"buffett": 1, "lynch": 1, "graham": None, "greenblatt": 1, "oneil_earn": 0, "oneil_price": 0}
    회의 = []
    for i, day in enumerate(["d1", "d2", "d3", "d4", "d5"]):
        # ZM: 그레이엄 늘 자료 없음(c′), 하루만 가결 → 가결 0 인 날 4/5 = 80%(a)
        회의.append(_회의("ZM", day, [100 + i] if i == 0 else [], [200 + i, 300 + i], zm))
        # ZN: 안건 10 중 9 가결(b) · top_n 넘침(e)
        회의.append(_회의("ZN", day, list(range(10, 19)), [19], {"graham_price": 1, "graham_fin": 1}))
        # ZL: 날마다 2 가결, 그중 하나는 A 도 집행 → 중복 0.5(d), 나머지는 통과
        회의.append(_회의("ZL", day, [40 + i, 50 + i], [60 + i] * 8, {"lynch": 1, "lynch_check": -1}))
        회의.append(_회의("A", day, [40 + i]))
    판 = m.master_verdicts(회의, ch)
    assert 판["ZM"] == ["(c′) 자료 부족: graham", "(a) 기준 통과 종목 부족"]
    assert 판["ZN"] == ["(b) 늘 가결", "(e) 동률 고르기"]
    assert 판["ZL"] == ["(d) 중복 0.50"]
    줄 = "\n".join(m.master_lines(회의, ch))
    assert "ZL 린치 계좌: 걸림 — (d) 중복 0.50" in 줄 and "lynch~lynch_check 0%" in 줄
    assert "T4" not in 줄  # 종목 이름 없이


def test_회의가_없는_거장_계좌는_통과로_읽히지_않는다() -> None:
    m = _m()
    판 = m.master_verdicts([_회의("A", "d1", [1])], m._charters(True))
    assert all(v == ["(c′) 회의 없음"] for v in 판.values())


def test_수익_봉인이면_뒤_성과를_읽지_않고_거장_자료를_운영처럼_붙인다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import company as job
    from batch.jobs import daily

    m = _m()
    읽음: list[str] = []
    d = co.Dossier(stock_id=1, ticker="T01", as_of="2026-11-20", total_score=80.0)
    monkeypatch.setattr(daily, "load_signal_rows", lambda c, mk, day: ([object()], "2026-11-20"))
    monkeypatch.setattr(job, "population_dossiers", lambda *a: [d])
    monkeypatch.setattr(job, "agenda_rows", lambda rows, n: (읽음.append(f"안건{n}"), rows)[1])
    monkeypatch.setattr(job, "load_dossiers", lambda *a: [d])
    monkeypatch.setattr(job, "master_facts", lambda c, ids, mk, as_of: (읽음.append(f"거장{ids}"), {1: {"x": 1}})[1])
    monkeypatch.setattr(job, "forward_excess", lambda *a: 읽음.append("뒤성과") or {})
    본: list[Any] = []
    monkeypatch.setattr(co, "meet", lambda ds, ch, pop: (본.append((ch["name"], ds[0].masters, pop[0].masters)), ([], []))[1])
    회의, 날, 성적 = m.replay(object(), "KR", 1, 5, m._charters(True), scorecard=False)
    assert "뒤성과" not in 읽음 and 성적 == []
    assert 읽음 == ["안건50", "거장[1]"]  # 가장 큰 안건(ZN·ZL 50) · 모집단+안건 한 번에
    assert len(회의) == 6 and 날 == {"2026-11-20"}
    assert all(a == {"x": 1} and p == {"x": 1} for _n, a, p in 본)
    # 기본 실행(판 2 헌장만)은 거장 자료를 읽지 않고 뒤 성과를 읽는다 — 예전 그대로
    읽음.clear()
    m.replay(object(), "KR", 1, 5)
    assert 읽음 == [f"안건{max(int(c['agenda']) for c in co.CHARTERS.values())}", "뒤성과"]


# ── 25.2264 교차검증 [권고] 보강 — 문턱 경계 ─────────────────────────────────────────


def test_자료_없음은_절반까지는_걸리지_않고_넘으면_걸린다() -> None:
    m = _m()
    ch = m._charters(True)
    zm_반 = [_회의("ZM", f"d{i}", [1, 2], [3], {"buffett": None if i % 2 else 1, "lynch": 1}) for i in range(4)]
    assert not any(x.startswith("(c′)") for x in m.master_verdicts(zm_반, ch)["ZM"])  # 정확히 50%
    zm_넘 = [*zm_반, _회의("ZM", "d9", [1, 2], [3], {"buffett": None, "lynch": 1})]
    assert "(c′) 자료 부족: buffett" in m.master_verdicts(zm_넘, ch)["ZM"]  # 3/5 = 60%


def test_top_n_넘침은_넘을_때만_세고_20퍼센트에서_걸리며_ZM_에는_없다() -> None:
    m = _m()
    ch = m._charters(True)

    def 날들(aid: str, 넘는날: int, 가결: int) -> list:
        return [_회의(aid, f"d{i}", list(range(가결 + (1 if i < 넘는날 else 0))), list(range(100, 140)))
                for i in range(5)]  # fmt: skip

    assert not any(x.startswith("(e)") for x in m.master_verdicts(날들("ZN", 0, 5), ch)["ZN"])  # 가결 = top_n
    assert "(e) 동률 고르기" in m.master_verdicts(날들("ZN", 1, 5), ch)["ZN"]  # 1/5 = 20%
    assert not any(x.startswith("(e)") for x in m.master_verdicts(날들("ZM", 5, 5), ch)["ZM"])  # ZM 은 (e) 밖


def test_ZL_의_늘_가결은_열지_않음이_아니라_고침_대상이다() -> None:
    m = _m()
    ch = m._charters(True)
    for z in ("ZL", "ZN"):
        판 = m.master_verdicts([_회의(z, "d1", list(range(5)))], ch)[z]
        assert (판 == ["(b) 늘 가결" + m.retune_mark("ZL", ch["ZL"])]) is (z == "ZL")
        assert any(x.startswith("(b) 늘 가결") for x in 판)
    assert m.RETUNE_B == {"ZL": 2.6} and "threshold 2.6 " in m.retune_mark("ZL", ch["ZL"])
    # 표시는 고칠 값에서 만든다 — 값을 바꾸면 표시도 바뀐다(날글자 2.6 이면 깨진다, 25.2268)
    with pytest.MonkeyPatch.context() as mp:  # 실패해도 원복(25.2269)
        mp.setitem(m.RETUNE_B, "ZL", 2.7)
        assert "threshold 2.7 " in m.retune_mark("ZL", ch["ZL"])
    # 고친 헌장으로 다시 돌린 두 번째 재생에서도 (b) 면 표시 없이 걸림(열지 않음) — 고침은 한 번뿐
    고침 = m._charters(True, retune=True)
    assert 고침["ZL"]["threshold"] == 2.6 and 고침["ZN"] is ms.CHARTERS["ZN"] and ms.CHARTERS["ZL"]["threshold"] == 2.0
    assert m.master_verdicts([_회의("ZL", "d1", list(range(5)))], 고침)["ZL"] == ["(b) 늘 가결"]


def test_재생은_운영처럼_가중_0_인_거장_키로는_거장_자료를_읽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import company as job
    from batch.jobs import daily

    m = _m()
    읽음: list[object] = []
    d = co.Dossier(stock_id=1, ticker="T01", as_of="2026-11-20")
    monkeypatch.setattr(daily, "load_signal_rows", lambda c, mk, day: ([object()], "2026-11-20"))
    for 이름 in ("population_dossiers", "load_dossiers"):
        monkeypatch.setattr(job, 이름, lambda *a: [d])
    monkeypatch.setattr(job, "agenda_rows", lambda rows, n: rows)
    monkeypatch.setattr(job, "master_facts", lambda *a: 읽음.append(a) or {})
    영 = {"Z0": {**ms.CHARTERS["ZM"], "weights": {k: 0 for k in ms.CHARTERS["ZM"]["weights"]}}}
    m.replay(object(), "KR", 1, 5, {"A": co.CHARTERS["A"], **영}, scorecard=False)
    assert 읽음 == []


def test_수익_봉인이면_성적표_행을_만들지_않고_찍지도_않는다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import sys

    from batch.core import client as cl
    from batch.jobs import company as job
    from batch.jobs import daily

    m = _m()
    d = co.Dossier(stock_id=1, ticker="T01", as_of="2026-11-20")
    monkeypatch.setattr(daily, "load_signal_rows", lambda c, mk, day: ([object()], "2026-11-20"))
    for 이름 in ("population_dossiers", "load_dossiers"):
        monkeypatch.setattr(job, 이름, lambda *a: [d])
    monkeypatch.setattr(job, "agenda_rows", lambda rows, n: rows)
    monkeypatch.setattr(job, "master_facts", lambda *a: {})
    monkeypatch.setattr(job, "forward_excess", lambda *a: {1: 0.1})
    monkeypatch.setattr(co, "meet", lambda ds, ch, pop: ([_item(1, True)], [_item(1, True)]))
    monkeypatch.setattr(co, "records_from", lambda *a: [{"행": 1}])
    assert m.replay(object(), "KR", 1, 5, m._charters(True), scorecard=False)[2] == []
    assert m.replay(object(), "KR", 1, 5, m._charters(True))[2] != []  # 켜면 만든다

    class 가짜:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(cl, "TursoClient", 가짜)
    monkeypatch.setattr(co, "scorecard", lambda rows: {})
    monkeypatch.setattr(sys, "argv", ["x", "--masters", "--no-scorecard", "--days", "1"])
    m.main()
    assert "부서 성적표" not in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["x", "--days", "1"])
    m.main()
    assert "부서 성적표" in capsys.readouterr().out


def test_retune_은_main_에서_재생까지_간다(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from batch.core import client as cl

    m = _m()
    받음: list[dict] = []
    monkeypatch.setattr(m, "replay", lambda c, mk, d, e, charters, scorecard: (받음.append(charters), ([], set(), []))[1])

    class 가짜:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(cl, "TursoClient", 가짜)
    monkeypatch.setattr(sys, "argv", ["x", "--masters", "--retune", "--no-scorecard", "--days", "1"])
    m.main()
    monkeypatch.setattr(sys, "argv", ["x", "--masters", "--no-scorecard", "--days", "1"])
    m.main()
    assert [c["ZL"]["threshold"] for c in 받음] == [2.6, 2.0]
