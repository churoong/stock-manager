"""KIS모의투자 도전자 — 둘째 모의 계좌 (docs/paper.md 15.10, docs/infra.md 25.1515)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.core import db
from batch.jobs import broker_mirror as job
from batch.jobs import daily
from batch.jobs import desk as desk_job
from batch.sources import kis_vts as kv
from tests.test_kis_vts_1475 import _at, _Sess, c  # noqa: F401 — c 는 픽스처
from tests.test_paper_job_1445 import Client

둘째키 = {"KIS_VTS2_APP_KEY": "k2", "KIS_VTS2_APP_SECRET": "s2", "KIS_VTS2_ACCOUNT": "87654321"}


def _켬(c: Client, monkeypatch: pytest.MonkeyPatch, account: str | None = "B") -> None:  # noqa: F811
    """픽스처 `c` 는 B 의 주문 셋(101·102·103)을 깔고 첫째가 B 를 베끼게 둔다 — 첫째를 A 로 돌리고 둘째가 B 를 베끼게"""
    for k, v in 둘째키.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(job, "MIRROR_ACCOUNT", "A")
    if account is not None:
        db.set_setting(c, "kis_challenger", {"account": account, "at": "t"})


def _run(c: Client, s: _Sess, day: str = "2026-10-16", hhmm: str = "08:40", **kw: Any) -> dict[str, Any]:  # noqa: F811
    return job.run(c, "KR", _at(hhmm, day), session=s, sleep=lambda _x: None, clock=lambda: _at(hhmm, day),
                   slot=job.CHALLENGER, **kw)  # fmt: skip


def test_keys_are_separate_and_mock_only(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(둘째키) + ["KIS_VTS_APP_KEY", "KIS_VTS_APP_SECRET", "KIS_VTS_ACCOUNT"]:
        monkeypatch.delenv(k, raising=False)
    assert kv.missing(keys=kv.CHALLENGER) == ["KIS_VTS2_APP_KEY", "KIS_VTS2_APP_SECRET", "KIS_VTS2_ACCOUNT"]
    assert kv.account(둘째키, kv.CHALLENGER) == ("87654321", "01")
    assert not kv.configured(keys=kv.MAIN)  # 둘째 키로 첫째가 켜지지 않는다
    assert kv.CHALLENGER.token != kv.MAIN.token and kv.CHALLENGER.source != kv.MAIN.source
    assert all(x.startswith("KIS_VTS") for x in (kv.CHALLENGER.key_env, kv.CHALLENGER.secret_env, kv.CHALLENGER.account_env))


def test_off_states(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    assert _run(c, _Sess()) == {"off": "KIS모의투자 도전자 키 없음"}
    _켬(c, monkeypatch, None)
    assert _run(c, _Sess()) == {"off": "도전자 계좌를 고르지 않음(/kis)"}
    db.set_setting(c, "kis_challenger", {"account": "A", "at": "t"})
    assert _run(c, _Sess()) == {"off": "도전자 A 가 챔피언과 같음"}
    db.set_setting(c, "kis_challenger", {"account": "Z", "at": "t"})
    assert _run(c, _Sess()) == {"off": "베낄 계좌 Z 없음"}
    # 첫째와 같은 모의 계좌번호 — 잔고가 섞인다. 설정 실수로 알린다
    db.set_setting(c, "kis_challenger", {"account": "B", "at": "t"})
    monkeypatch.setenv("KIS_VTS2_ACCOUNT", "12345678")
    꺼짐 = _run(c, _Sess())
    assert 꺼짐["missing"] == ["KIS_VTS2_ACCOUNT(첫째와 같은 계좌)"]
    assert daily._kis_알림문(꺼짐, "KIS모의투자 도전자") == (
        "시크릿 KIS_VTS2_ACCOUNT(첫째와 같은 계좌) 이(가) 비었거나 틀려 KIS모의투자 도전자 주문을 내지 않았습니다")


def test_challenger_mirrors_into_its_own_rows(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _켬(c, monkeypatch)
    s = _Sess()
    기록 = _run(c, s)
    assert [o[0] for o in s.orders] == ["buy", "buy"] and 기록["sent"] == {"accepted": 2}
    assert set(c.q("SELECT broker, account_id, source FROM paper_broker_orders")) == {("kis_vts2", "B", "kis_vts2")}
    assert c.q("SELECT broker, account_id, source FROM paper_broker_daily") == [("kis_vts2", "B", "kis_vts2")]
    # 첫째 칸은 건드리지 않는다 — 첫째는 A(주문 없음)를 베낀다
    첫 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 첫["sent"] == {} and c.q("SELECT COUNT(*) FROM paper_broker_orders WHERE broker = 'kis_vts'") == [(0,)]


def test_switching_challenger_cleans_up_previous_holdings(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """B 를 베껴 산 005930 3주 — 도전자를 C(보유 없음)로 바꾸면 다음 실행이 정리 주문으로 판다"""
    _켬(c, monkeypatch)
    _run(c, _Sess())
    # 픽스처는 A·B 만 남긴다 — B 의 줄을 본떠 보유 없는 C 를 연다
    칸 = [r[1] for r in c.q("PRAGMA table_info(paper_accounts)") if r[1] != "id"]
    c.conn.execute(f"INSERT INTO paper_accounts (id, {', '.join(칸)}) SELECT 'C', {', '.join(칸)} FROM paper_accounts"
                   " WHERE id = 'B'")  # fmt: skip
    db.set_setting(c, "kis_challenger", {"account": "C", "at": "t"})
    다음 = _Sess(holdings=[kv.Holding("005930", 3, 3, 9_800.0)],
               executions=[kv.Execution("20261016", "N1", "005930", "buy", 3, 3, 9_800.0, 0, False),
                           kv.Execution("20261016", "N2", "005930", "buy", 2, 0, None, 0, False)])  # fmt: skip
    기록 = _run(c, 다음, "2026-10-19")
    assert 다음.orders == [("sell", "005930", 3, None)] and 기록["planned"] == {"cleanup": 1, "mirror": 0}
    assert c.q("SELECT account_id FROM paper_broker_daily WHERE as_of = '2026-10-19'") == [("C",)]


def test_challenger_never_raises_diverge(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _켬(c, monkeypatch)
    monkeypatch.setattr(job.bm, "compare", lambda _rows: {"days": 28, "n": 30, "match_pct": 10.0, "kept_pct": 10.0,
                                                          "both": 25, "internal_only": 5, "kis_only": 1,
                                                          "slip_buy": {"n": 0, "avg": None},
                                                          "slip_sell": {"n": 0, "avg": None}})  # fmt: skip
    기록 = _run(c, _Sess())
    assert "diverge" not in 기록 and "diverge_error" not in 기록
    assert db.get_setting(c, "kis_diverge", None) is None


def test_daily_runs_challenger_after_main_and_alerts_misconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    """일일 배치가 첫째 뒤에 둘째를 부르는 자리 — 원문 대조(배치 전체를 돌리지 않는다)"""
    import inspect

    원문 = inspect.getsource(daily)
    첫 = 원문.index('step_log["broker"] = broker_mirror.run(')
    둘 = 원문.index('step_log["broker2"] = _bm2.run(client, market, datetime.now(UTC), slot=_bm2.CHALLENGER')
    assert 첫 < 둘
    assert '_kis_알림문(step_log.get("broker2") or {}, "KIS모의투자 도전자")' in 원문
    assert daily._kis_알림문({"error": "x"}, "KIS모의투자 도전자") == "KIS모의투자 도전자 단계 실패: x"
    assert daily._kis_알림문({"off": "KIS모의투자 도전자 키 없음"}, "KIS모의투자 도전자") is None


def test_desk_takes_challenger_cells_where_main_has_none() -> None:
    """체결 칸 — 챔피언이 안 쓰는 칸(시장가 매수)은 도전자 것, 같은 칸은 거래일이 많은 쪽"""
    k = Client(accounts=None)
    ins = ("INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source, fetched_at,"
           " summary_json) VALUES (?, '2026-11-20', ?, '[]', '{}', ?, 't', ?)")  # fmt: skip
    k.conn.execute(ins, ["kis_vts2", "C", "kis_vts2", json.dumps({"cells": {
        "buy_market": {"days": 25, "excess": 0.3, "se": 0.1}, "sell_market": {"days": 5, "excess": 9.0, "se": 1.0}}})])
    요약 = {"cells": {"sell_market": {"days": 30, "excess": 0.1, "se": 0.05}}}
    칸 = desk_job._칸합침(k, 요약, {})
    assert 칸["KR"]["buy_market"]["excess"] == 0.3 and 칸["KR"]["sell_market"]["excess"] == 0.1
    assert 칸["US"] == {}
