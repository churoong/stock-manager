"""전략 깔때기 0단계 — 위원회 회사(장기보유 D·안정 E)의 리플레이 변형 (docs/funnel.md 4장 ②, docs/infra.md 25.1904).

핵심 H 판정 변형(`VARIANTS`)은 그대로 두고 탐색 변형(`EXPLORE_VARIANTS`)에 넣는다. 위원회 회사는 하루 재료의 같은 안건·모집단으로
운영 `committee_step` 과 같은 `co.meet` 결과(가결 행)를 미리 고름으로 넘겨 산다.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from batch.services import company as co
from batch.services import core_strategy as cs
from batch.services import paper as pp
from tests.test_core_strategy_1532 import _끝까지, 모의


def test_핵심_판정_변형은_그대로_다섯이다() -> None:
    """11-03 기본 실행·시도 수(`CORE_REGISTERED_TRIALS`)가 보는 목록 — 탐색 변형이 섞이지 않는다."""
    assert sorted(cs.VARIANTS) == ["core_b", "core_g", "core_h", "core_r1", "core_r2"]
    assert not set(cs.VARIANTS) & set(cs.EXPLORE_VARIANTS)


def test_탐색_변형_칸은_운영_계좌와_같다() -> None:
    """마이그레이션 0075 의 D·E 칸(매도 규칙·진입) — 상위 몇 종목은 운영처럼 헌장 `top_n` 이 정한다.
    A·C 는 0077 이 매도 규칙을 바꿨다 — tests/test_core_prebuy_1934.py 가 본다(25.1934)"""
    글 = Path("migrations/0075_paper_more_accounts.sql").read_text(encoding="utf-8")
    for 이름, v in cs.EXPLORE_VARIANTS.items():
        if v.account in ("A", "C"):
            assert v.selection == "committee" and v.account in co.CHARTERS
            continue
        줄 = 글.split(f"('{v.account}', ")[1].split("\n")[0]
        assert f"'{v.sell_rule}', 1, " in 줄 and f"'{v.entry}', " in 줄, (이름, 줄)
        assert v.selection == "committee" and v.account in co.CHARTERS


def test_모르는_변형은_두_목록을_다_알려_준다() -> None:
    with pytest.raises(ValueError, match="core_d"):
        cs.make_core_strategy("core_x", object(), sessions=[])  # type: ignore[arg-type]


@pytest.mark.parametrize("variant", sorted(cs.EXPLORE_VARIANTS))
def test_위원회_변형이_가결_종목만_사고판다(variant: str) -> None:
    s, r, 주문, _셈 = _끝까지(variant)
    v = cs.EXPLORE_VARIANTS[variant]
    assert s.v.top_n == int(co.CHARTERS[v.account]["top_n"])  # 헌장이 덮는다
    assert len(r.curve) == len(모의) and all(x > 0 and math.isfinite(x) for _d, x in r.curve)
    assert sum(len(o.buys) for o in 주문.values()) >= 1  # 합성 자료에서 E 는 가결이 드물다(산 날 2)
    사유 = {c for lg in s.log for cs_ in lg.sells.values() for c in cs_}
    assert 사유 <= set(pp.SELL_RULES[v.sell_rule]) | {"미룬 매도"}, 사유
    # 산 결정은 모두 "위원회 가결" — 점수 상위가 아니라 가결 행만 배분했다
    산 = [d for lg in s.log for d in lg.decisions if d["kind"] == "buy"]
    assert 산 and {d["reason"] for d in 산} == {"위원회 가결"}
    # 부결·표 순 밖 안건은 운영과 같은 사유의 skip 으로 원장에 남는다(결정 단위 잣대의 "안 산 새 후보")
    안산 = [d for lg in s.log for d in lg.decisions if d["kind"] == "skip"]
    assert any(d["reason"].startswith("위원회") or "부결" in d["reason"] or "주관" in d["reason"] for d in 안산), 안산[:3]


def _하루_재료(variant: str) -> list:
    """끝까지 돌며 위원회 변형이 본 하루 재료(`DayView`)와 그날 보유를 모은다."""
    본: list = []
    원래 = cs.CoreStrategy._buys

    def 엿봄(self, client, view, dv, w, *a, **k):  # type: ignore[no-untyped-def]
        본.append((dv, {sid for sid, x in w.items() if x > 0}))
        return 원래(self, client, view, dv, w, *a, **k)

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(cs.CoreStrategy, "_buys", 엿봄)
        _끝까지(variant)
    finally:
        mp.undo()
    return 본


def _운영_위원회(dv, charter: dict, held: set[int]) -> tuple[list, list[tuple]]:  # type: ignore[no-untyped-def]
    """운영 `jobs/paper.committee_step` 을 같은 하루 재료로 — (가결 행, skip 결정 (종목, 사유, held))."""
    import json

    from batch.jobs import paper as jp

    캐시 = {"agenda": dv.agenda, "population": dv.population, "dossiers_written": True}
    행, _기록, 문장, _메모 = jp.committee_step(
        None, market="KR", account="E", charter=charter, charters=dict(co.CHARTERS), signal_rows=dv.rows,
        trade_date=dv.day, cache=캐시, now="t", rule_version=2, held=frozenset(held),
    )  # fmt: skip
    skips = [(a[3], a[5], json.loads(a[6])["held"]) for sql, a in 문장 if sql == jp.DECISION_INSERT_SQL and a[4] == "skip"]
    return 행, skips


def test_위원회_가결_행과_skip_은_운영_committee_step_과_같다() -> None:
    """일치 시험(funnel.md 4장 ③의 첫 조각, 25.1908 — 25.1904 교차검증 [권고]) — 리플레이가 본 날마다 같은 하루 재료를
    운영 `committee_step` 에 넣어 가결 행(종목·기간 순)과 skip 결정(종목·사유·held)이 같은지 본다."""
    헌장 = dict(co.CHARTERS["E"])
    날들 = _하루_재료("core_e")
    assert len(날들) > 100
    for dv, held in 날들:
        행, skips = _운영_위원회(dv, 헌장, held)
        리플 = [(d["stock_id"], d["reason"], d["detail"]["held"]) for d in cs.committee_skips(dv, 헌장, held)]
        assert 리플 == skips, dv.day
        assert [(r.stock_id, r.horizon) for r in cs.committee_rows(dv, "E")] == [(r.stock_id, r.horizon) for r in 행]


def test_위원회_skip_갈래는_운영과_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """합성 자료로는 하루 가결이 한 건을 넘지 않아 "가결이나 표 순 밖" 갈래를 못 지난다 — 회의(`co.meet`)를 통제된 결과로
    바꿔 끼워(운영·리플레이가 같은 함수를 부른다) 세 갈래를 함께 본다: ① 부결 사유 ② 가결이나 표 순 상위 N 밖
    ③ 이미 든 종목의 held. 그리고 두 쪽이 회의에 넘긴 안건 수(헌장 `agenda` 로 자름)가 같은지."""
    from types import SimpleNamespace

    from tests.test_core_strategy_1532 import _행

    # 합성 하루 재료 — 안건 다섯(헌장 agenda 4 로 잘린다), 신호 행은 그 다섯
    dv = SimpleNamespace(day="2024-01-05", population=[], agenda=[SimpleNamespace(stock_id=s, ticker=f"{s:06d}")
                                                                 for s in range(1, 6)],
                         rows=[_행(s, "2024-01-05", 10_000.0) for s in range(1, 6)])  # fmt: skip
    받은: list[int] = []

    def 회의(agenda, charter, population=None):  # type: ignore[no-untyped-def]
        받은.append(len(agenda))
        items = [co.Item(stock_id=d.stock_id, ticker=d.ticker, opinions={}, vote=1.0 - k / 10, favor=3, issue=False,
                         passed=k != 2, reason="가결" if k != 2 else "위원회: 표 0.1 < 문턱 1")
                 for k, d in enumerate(agenda)]  # fmt: skip
        return items, [i for i in items if i.passed][: int(charter["top_n"])]

    monkeypatch.setattr(co, "meet", 회의)
    헌장 = {**co.CHARTERS["E"], "top_n": 1, "agenda": 4}
    든 = {dv.agenda[1].stock_id}
    _행, 운영 = _운영_위원회(dv, 헌장, 든)
    받은_운영 = list(받은)
    리플 = [(d["stock_id"], d["reason"], d["detail"]["held"]) for d in cs.committee_skips(dv, 헌장, 든)]
    assert 리플 == 운영 and len(운영) == 3, 운영
    assert 받은 == 받은_운영 * 2 and 받은_운영 == [4]  # 같은 안건 수로 잘라 넘겼다
    사유 = {r for _s, r, _h in 운영}
    assert "위원회: 가결이나 표 순 상위 1 밖" in 사유 and "위원회: 표 0.1 < 문턱 1" in 사유
    assert [h for s, _r, h in 운영 if s == dv.agenda[1].stock_id] == [True]


@pytest.mark.parametrize(("top_n", "agenda"), [(2, 3), (2, 1)])
def test_리플레이_가결_행도_자르기·상위_n_을_운영과_같이(monkeypatch: pytest.MonkeyPatch, top_n: int, agenda: int) -> None:
    """리플레이 쪽 가결 행은 시장 판단기(`CoreMarket.at`)가 하루 재료에 넣은 `approved` 에서 온다. 합성 자료로는 하루 가결이
    한 건을 넘지 않아 안건 자르기(헌장 `agenda`)·상위 n 이 지나지 않았다(25.1909, 25.1908 교차검증 [권고]). 회의를
    "모두 가결, 표는 안건 순" 으로 바꿔 끼워 끝까지 돈 뒤, 날마다 리플레이 가결 행이 운영 `committee_step` 의 것과 같고
    헌장 top_n 개인지 본다."""
    받은: list[int] = []

    def 회의(agenda, charter, population=None):  # type: ignore[no-untyped-def]
        if charter.get("name") == co.CHARTERS["E"]["name"]:  # 다른 회사(A·C·D)의 회의는 세지 않는다
            받은.append(len(agenda))
        items = [co.Item(stock_id=d.stock_id, ticker=d.ticker, opinions={}, vote=2.0 - k / 100, favor=3, issue=False,
                         passed=True, reason="가결") for k, d in enumerate(agenda)]  # fmt: skip
        return items, items[: int(charter["top_n"])]

    monkeypatch.setattr(co, "meet", 회의)
    # (2, 3) 은 하루 가결 둘(상위 n) 갈래, (2, 1) 은 안건 자르기 갈래 — 자르지 않으면 리플레이가 둘을 가결해 운영(하나)과 갈린다
    헌장 = {**co.CHARTERS["E"], "top_n": top_n, "agenda": agenda}
    monkeypatch.setitem(co.CHARTERS, "E", 헌장)
    날들 = _하루_재료("core_e")
    둘, 넘침 = 0, 0
    for dv, held in 날들:
        행, _skips = _운영_위원회(dv, 헌장, held)
        리플 = cs.committee_rows(dv, "E")
        assert [(r.stock_id, r.horizon) for r in 리플] == [(r.stock_id, r.horizon) for r in 행], dv.day
        둘 += len({r.stock_id for r in 리플}) == 2
        넘침 += len(dv.agenda) > agenda
    if agenda >= top_n:
        assert 둘 > 0  # 하루 가결 둘(상위 n)인 날을 실제로 지났다
    else:
        assert 넘침 > 0  # 안건이 헌장 agenda 보다 많은 날을 실제로 지났다(자르기가 일했다)
    assert 받은 and max(받은) <= agenda  # 회의에는 헌장 agenda 개까지만 넘겼다


def test_헌장_top_n_이_변형_칸을_덮는다() -> None:
    """변형 칸의 top_n 이 헌장과 달라도 헌장 값이 쓰인다(운영 run_step 의 `종목수 = 헌장 top_n`)."""
    from batch.services import core_backtest as cb
    from tests.test_core_strategy_1532 import _자료
    from tests.test_core_strategy_1532 import 날들 as 전체날

    universe, _p, _t, snaps, idx = _자료()
    mp = pytest.MonkeyPatch()
    try:
        mp.setitem(cs.EXPLORE_VARIANTS, "core_e", cs.Variant("E", "core", "tranche", 99, "committee"))
        s = cs.make_core_strategy("core_e", cb.CoreMarket(universe, snaps, idx), sessions=전체날)
    finally:
        mp.undo()
    assert s.v.top_n == int(co.CHARTERS["E"]["top_n"]) != 99


def test_장기보유는_목표·손절에_팔지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """D 의 매도 규칙 `time`(운영 0075) — 기간초과만. 모의 중간부터 가격을 세 배·5분의 1 로 흔들어(1532 의 `_미끼`)
    목표·손절이 서게 한 뒤, 같은 자료를 매도 규칙만 `core` 로 바꾼 D 가 그 사유로 판다는 것을 먼저 보고(시험이 헛돌지
    않음), `time` D 는 그 사유로 팔지 않음을 본다(25.1908 — 25.1904 교차검증 [권고]: 예전 시험은 손절이 한 번도 서지
    않아 아무것도 잡지 못했다)."""
    from tests.test_core_strategy_1532 import _미끼

    풀기 = lambda s: {c for lg in s.log for 사유 in lg.sells.values() for c in 사유}  # noqa: E731
    흔든 = _미끼(모의[200])
    time_d = _끝까지("core_d", 흔든)[0]
    monkeypatch.setitem(cs.EXPLORE_VARIANTS, "core_d", cs.Variant("D", "core", "tranche", 5, "committee"))
    core_d = _끝까지("core_d", 흔든)[0]
    assert 풀기(core_d) & {"목표도달", "손절"}, 풀기(core_d)
    assert not 풀기(time_d) & {"목표도달", "손절"}, 풀기(time_d)


def test_가결_행은_표_순이다() -> None:
    """운영 `committee_step` 의 `가결행` — 가결(표 순) 종목마다 기간 순 신호 행."""
    from types import SimpleNamespace

    from tests.test_core_strategy_1532 import _행

    rows = [_행(1, "2024-01-05", 10_000.0, horizon=h) for h in ("long", "short")]
    rows += [_행(2, "2024-01-05", 10_000.0, horizon="mid"), _행(3, "2024-01-05", 10_000.0)]
    dv = SimpleNamespace(rows=rows, approved={"E": [SimpleNamespace(stock_id=2), SimpleNamespace(stock_id=1)]})
    고른 = cs.committee_rows(dv, "E")  # type: ignore[arg-type]
    assert [(r.stock_id, r.horizon) for r in 고른] == [(2, "mid"), (1, "short"), (1, "long")]
    assert cs.committee_rows(dv, "D") == []  # type: ignore[arg-type]


# ── 탐색 실행(전략 깔때기 ①, 25.1906) ─────────────────────────────────────────


def test_탐색_실행은_따로_싣고_시도_수_하한은_누적이다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--explore` 는 `backtest_explore` 로 — 웹 핵심 H 화면(`backtest_core`)·판정과 섞이지 않는다.
    DSR 시도 수 하한 = 코호트 1(`REGISTERED_TRIALS`) + 탐색 변형 수 + S·T 둘(funnel.md 2.1 — `--st` 없이도 늘, 25.2051).
    H 진단·짝 판정은 내지 않는다."""
    import json

    from batch.jobs import backtest as jb
    from batch.jobs import core_backtest as cj
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)
    # 헌장 top_n 을 변형 칸과 다르게 — 저장 top_n 이 헌장 값인지 실제로 가린다(25.1908)
    monkeypatch.setitem(co.CHARTERS, "E", {**co.CHARTERS["E"], "top_n": 7})
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True) == 0
    행 = mem.execute("SELECT strategy, top_n, warnings_json, metrics_json FROM backtest_runs ORDER BY id").dicts()
    assert [r["strategy"] for r in 행] == list(cs.EXPLORE_VARIANTS)  # 등록 순서(25.1934 A·C 가 뒤에 붙었다)
    # 위원회 변형의 저장 top_n 은 헌장 값(E 는 위에서 7 로 바꿈, D 5)
    assert {r["strategy"]: r["top_n"] for r in 행} == {"core_a": 5, "core_c": 5, "core_d": 5, "core_e": 7}
    # DSR 이 실제로 쓴 시도 수 하한 — 실행 기록 칸만이 아니라 판정 계산이 받은 값(25.1908)
    assert all(json.loads(r["metrics_json"])["luck"]["n_trials"] >= cj.explore_trial_floor() for r in 행)
    assert all(cj.WARN_EXPLORE in json.loads(r["warnings_json"]) for r in 행)
    assert mem.execute("SELECT COUNT(*) FROM batch_runs WHERE job_name = ?", [cj.JOB_NAME]).scalar() == 0
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?",
                                 [cj.EXPLORE_JOB_NAME]).scalar())  # fmt: skip
    assert 로그["params"]["explore"] is True
    # S·T 둘도 늘 센다(25.2051 — `--st` 없이 돈 실행도 하한이 같다)
    assert 로그["params"]["trial_floor"] == jb.REGISTERED_TRIALS + len(cs.EXPLORE_VARIANTS) + len(cj.ST_VARIANTS)
    assert 로그["params"]["trial_floor"] == cj.explore_trial_floor()
    assert 로그["verdict"].startswith("탐색 실행") and "skipped" in 로그["holding_eq"]


def test_두_목록을_섞어_돌리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import core_backtest as cj
    from tests.test_core_backtest_job_1534 import _db

    _db(monkeypatch)  # 막이가 깨져도 실제 DB 로 가지 않게

    with pytest.raises(ValueError):
        cj.run_core("KR", variants=("core_b", "core_d"))
    with pytest.raises(ValueError):
        cj.run_core("KR", variants=("core_h",), explore=True)
