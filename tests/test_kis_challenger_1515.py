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
    # 따로 재는 실험 계좌(스윙 S)는 베끼지 않는다 — 계좌 행이 있어도(25.1879)
    c.conn.execute("INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)"
                   " VALUES ('S', 'S', 'swing_close', 1, 't', 'open', 10, 'short')")  # fmt: skip
    db.set_setting(c, "kis_challenger", {"account": "S", "at": "t"})
    assert _run(c, _Sess()) == {"off": "도전자 S 는 따로 재는 실험 계좌라 베끼지 않음"}
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


# ── 검토 반영 (25.1517) ──────────────────────────────────────────────────────


def test_hold_send_follows_main(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """첫째가 보내지 못한 날은 둘째도 — 두 계좌의 주문 날이 어긋나지 않게(검토 8)"""
    _켬(c, monkeypatch)
    s = _Sess()
    기록 = _run(c, s, hold_send="접수 시작 전 — 보내지 않음")
    assert s.orders == [] and 기록["send"] == "접수 시작 전 — 보내지 않음"
    import inspect

    assert "hold_send=str(막힘2) if 막힘2 else None" in inspect.getsource(daily)


def test_switch_keeps_compare_apart_and_cleanup_row_names_buyer(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """바꾼 뒤 비교·칸은 새 계좌 줄만, 정리 줄은 산 계좌(B)로 적는다(검토 6)"""
    _켬(c, monkeypatch)
    _run(c, _Sess())
    칸 = [r[1] for r in c.q("PRAGMA table_info(paper_accounts)") if r[1] != "id"]
    c.conn.execute(f"INSERT INTO paper_accounts (id, {', '.join(칸)}) SELECT 'C', {', '.join(칸)} FROM paper_accounts"
                   " WHERE id = 'B'")  # fmt: skip
    db.set_setting(c, "kis_challenger", {"account": "C", "at": "t"})
    c.conn.execute("UPDATE paper_orders SET status = 'expired' WHERE id IN (101, 103)")  # B 의 내부 판정이 끝났다
    다음 = _Sess(holdings=[kv.Holding("005930", 3, 3, 9_800.0)],
               executions=[kv.Execution("20261016", "N1", "005930", "buy", 3, 3, 9_800.0, 0, False),
                           kv.Execution("20261016", "N2", "005930", "buy", 2, 0, None, 0, False)])  # fmt: skip
    _run(c, 다음, "2026-10-19")
    assert c.q("SELECT account_id, reason FROM paper_broker_orders WHERE trade_date = '2026-10-19'") == [("B", "정리")]
    요약 = json.loads(c.q("SELECT summary_json FROM paper_broker_daily WHERE as_of = '2026-10-19'")[0][0])
    assert 요약["n"] == 0  # B 를 베끼던 10-16 주문은 C 의 비교에 들지 않는다


def test_weekly_summary_includes_challenger(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _켬(c, monkeypatch)
    _run(c, _Sess())
    c.conn.execute("UPDATE paper_orders SET status = 'expired' WHERE id IN (101, 103)")  # 내부 판정이 끝났다
    s = _Sess(executions=[kv.Execution("20261016", "N1", "005930", "buy", 3, 3, 9_800.0, 0, False)])
    _run(c, s, "2026-10-19")
    줄 = job.summary_lines(c)
    assert any(x.startswith("[KIS모의투자 — 도전자 B 주문 베끼기") for x in 줄), 줄


def test_desk_challenger_cells_freshness_and_measured_first() -> None:
    k = Client(accounts=None)
    ins = ("INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source, fetched_at,"
           " summary_json) VALUES (?, ?, 'C', '[]', '{}', ?, 't', ?)")  # fmt: skip
    k.conn.execute(ins, ["kis_vts2", "2026-11-20", "kis_vts2", json.dumps({"cells": {
        "sell_market": {"days": 40, "excess": None, "se": None, "measured": False},
        "buy_market": {"days": 25, "excess": 0.3, "se": 0.1, "measured": True}}})])
    요약 = {"cells": {"sell_market": {"days": 30, "excess": 0.1, "se": 0.05, "measured": True}}}
    칸 = desk_job._칸합침(k, 요약, {}, "2026-11-13")
    assert 칸["KR"]["sell_market"]["excess"] == 0.1  # 거래일만 많은 안 잰 칸이 잰 칸을 밀지 않는다
    assert 칸["KR"]["buy_market"]["excess"] == 0.3
    assert "buy_market" not in desk_job._칸합침(k, 요약, {}, "2026-11-21")["KR"]  # 오래된 도전자 줄은 안 쓴다


def test_h_check_start_skips_pre_link_days() -> None:
    """10-15 에는 H 가 KIS 에 없는 종목 2 를 들었고 10-16 에는 다 같다 — 시작은 10-16(검토 2)"""
    k = Client(accounts=None)
    for d, pos in (("2026-10-15", [{"stock_id": 1}, {"stock_id": 2}]), ("2026-10-16", [{"stock_id": 1}])):
        k.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                       " pnl_krw, twr_index, positions_json, recon_ok, updated_at) VALUES ('H', ?, 1, 1, 0, 100, 0, 1, ?,"
                       " 1, 't')", [d, json.dumps(pos)])  # fmt: skip
        k.conn.execute("INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source,"
                       " fetched_at) VALUES ('kis_vts', ?, 'H', ?, '{}', 'kis_vts', 't')",
                       [d, json.dumps([{"id": 1, "code": "005930"}])])  # fmt: skip
    평가 = {"H": [("2026-10-15", 100.0), ("2026-10-16", 100.0)]}
    손익 = {"H": [("2026-10-15", 0.0), ("2026-10-16", 0.0), ("2026-10-17", 1.0)]}
    x = desk_job._대보기(k, "H", (["2026-10-15", "2026-10-16"], "2026-10-17"),
                        {"2026-10-15": 1.0, "2026-10-16": 1.0, "2026-10-17": 2.0}, 평가, 손익, {}, {})  # fmt: skip
    assert x is not None and (x["from"], x["pre_link"]) == ("2026-10-16", 0)


def test_cells_keep_late_limit_buys_and_token_no_redirect() -> None:
    from batch.services import broker_mirror as bm
    from batch.sources import kis

    줄 = {"trade_date": "2026-10-16", "side": "buy", "status": "filled", "filled_qty": 1, "avg_price": 99.0,
          "reason": "베낌", "i_status": "filled", "i_price": 100.0, "i_fill_date": "2026-10-16",
          "i_order_type": "limit", "i_fill_basis": "close", "i_slip_pct": 0.0}  # fmt: skip
    assert bm.cells([줄])["cells"]["buy_limit"]["both"] == 1  # 지정가 매수는 판정 잣대와 상관없이(검토 5)
    assert "buy_market" not in bm.cells([줄 | {"i_order_type": "market"}])["cells"]  # 종가 판정 시장가는 뺀다
    import inspect

    assert "allow_redirects=False" in inspect.getsource(kis.access_token)  # 검토 9


def test_first_error_holds_challenger() -> None:
    """첫째 단계가 예외로 끝난 날 둘째도 보내지 않는다 (25.1543 감사 7) — 예전엔 `send` 까닭만 넘겼다"""
    from batch.jobs import daily

    assert daily.challenger_hold({"error": "KisFailed 토큰"}) == "첫째 계좌 단계 실패 — 보내지 않음(두 계좌의 주문 날을 맞춘다)"
    assert daily.challenger_hold({"send": "접수 시작 전 — 보내지 않음"}) == "접수 시작 전 — 보내지 않음"
    assert daily.challenger_hold({"sent": {"accepted": 2}}) is None and daily.challenger_hold({}) is None
