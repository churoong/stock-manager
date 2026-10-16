"""운용 상황실 요약 (docs/desk.md 2장, docs/infra.md 25.1501) — 순수 함수와 배치 기록."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import desk as job
from batch.services import desk
from tests.test_paper_job_1445 import Client


def test_leaderboard_common_window_and_vs_r_by_hand() -> None:
    """공통 구간 = 첫 평가일 가운데 가장 늦은 10-16. A: 1.05 ÷ 1.03 − 1 = 1.9417% · H: 1% · R1: 0.99 ÷ 1.0(10-16 이하 마지막
    10-15) − 1 = −1% · R2: 0% → 대조 평균 −0.5% · A R 대비 +2.4417%p · H +1.5%p"""
    lb = desk.leaderboard({
        "A": [("2026-10-14", 1.0), ("2026-10-15", 1.02), ("2026-10-16", 1.03), ("2026-10-19", 1.05)],
        "H": [("2026-10-16", 1.0), ("2026-10-19", 1.01)],
        "R1": [("2026-10-15", 1.0), ("2026-10-19", 0.99)],
        "R2": [("2026-10-16", 1.0), ("2026-10-19", 1.0)],
        "X": [],
    }, {"A": "가치 회사", "H": "지주사"})  # fmt: skip
    assert lb["window_start"] == "2026-10-16" and lb["control_pct"] == pytest.approx(-0.5)
    rows = {r["id"]: r for r in lb["rows"]}
    assert set(rows) == {"A", "H", "R1", "R2"}  # 기록 없는 계좌는 뺀다
    assert rows["A"]["window_pct"] == pytest.approx((1.05 / 1.03 - 1) * 100)
    assert rows["A"]["since_open_pct"] == pytest.approx(5.0) and rows["A"]["name"] == "가치 회사"
    assert rows["A"]["vs_r_pp"] == pytest.approx((1.05 / 1.03 - 1) * 100 + 0.5)
    assert rows["H"]["vs_r_pp"] == pytest.approx(1.5) and rows["H"]["kind"] == "지주사(KIS가 베낌)"
    assert rows["R1"]["vs_r_pp"] is None  # 대조끼리는 견주지 않는다
    assert [r["id"] for r in lb["rows"]] == ["A", "H", "R2", "R1"]
    assert desk.leaderboard({}) == {"window_start": None, "rows": []}


def test_checks_order_and_text() -> None:
    paper = {"frozen": [1, 2], "accounts": {"A": {"committee": {"error": "부서 실패"}}, "H": {"holding": {"error": "표 모자람"}},
                                             "C": {"charter_errors": ["가중 합 0"]}, "B": {"committee": {"memo": "가결 2"}}}}
    broker = {"sent": {"rejected": 2}, "missing": ["KIS_VTS_APP_SECRET"], "send": "장 끝난 뒤 — 보내지 않음",
              "not_sent": {"코드 없음": 1}}  # fmt: skip
    c = desk.checks(halted=True, paper=paper, broker=broker, recon_bad=["B"], warnings=10, h_orders=3)  # fmt: skip
    높음 = [x["text"] for x in c if x["level"] == "high"]
    assert [x["level"] for x in c[: len(높음)]] == ["high"] * len(높음)  # 높음이 먼저
    글 = " / ".join(x["text"] for x in c)
    assert "멈춤 스위치" in 글 and "KIS_VTS_APP_SECRET" in 글 and "장부 검산 어긋남: 계좌 B" in 글 and "거부 2건" in 글
    assert "KIS모의투자에 보내지 않음: 장 끝난 뒤 — 보내지 않음 — 베낄 주문 3건이 남았습니다" in 높음
    assert "KIS모의투자에 안 낸 주문: 코드 없음 1건" in 글
    assert "가상 A 투자위원회를 열지 못함 — 오늘 사지 않았습니다: 부서 실패" in 높음
    assert "가상 H 지주사가 고르지 못함 — 오늘 사지 않았습니다: 표 모자람" in 높음 and "가상 C 헌장 오류: 가중 합 0" in 높음
    assert "가상 B" not in 글  # 위원회가 열린 계좌는 없다
    assert "이 실행의 경고 10줄 — 시스템 상태 화면에서" in 글 and "경고0" not in 글  # 줄 자체는 싣지 않는다
    # 쉬는 날은 안 보낸 것이 정상, 베낄 주문이 없으면 보통
    assert desk.checks(halted=False, paper=None, broker={"send": "오늘은 장이 없다"}, recon_bad=[], warnings=0) == []
    낮음 = desk.checks(halted=False, paper=None, broker={"send": "접수 시작 전 — 보내지 않음"}, recon_bad=[], warnings=0)
    assert 낮음 == [{"level": "normal", "text": "KIS모의투자에 보내지 않음: 접수 시작 전 — 보내지 않음"}]
    assert desk.checks(halted=False, paper=None, broker=None, recon_bad=[], warnings=0) == []


def test_leaderboard_young_account_does_not_reset_window() -> None:
    """A·R1·R2 는 30번 평가(자리 잡음), N 은 3번(갓 엶). 공통 구간은 A·R 의 첫날 — N 의 첫날로 끌려가지 않는다.
    N 은 제 개설일부터 1.0 → 1.02 = +2%, 같은 구간 R1 (1.10 → 1.12 = +1.8182%) 와 R2 (0%) 평균 0.9091% → R 대비 +1.0909%p"""
    날 = [f"2026-11-{d:02d}" for d in range(1, 31)]
    a = [(d, 1 + i / 100) for i, d in enumerate(날)]
    r1 = [(d, 1.0 if i < 27 else (1.10 if i == 27 else (1.11 if i == 28 else 1.12))) for i, d in enumerate(날)]
    r2 = [(d, 1.0) for d in 날]
    n = [(날[27], 1.0), (날[28], 1.01), (날[29], 1.02)]
    lb = desk.leaderboard({"A": a, "R1": r1, "R2": r2, "N": n})
    assert lb["window_start"] == "2026-11-01"
    rows = {r["id"]: r for r in lb["rows"]}
    assert rows["A"]["window_pct"] == pytest.approx(29.0) and rows["A"]["young"] is False
    assert rows["N"]["young"] is True and rows["N"]["window_pct"] == pytest.approx(2.0)
    assert rows["N"]["vs_r_pp"] == pytest.approx(2.0 - (1.12 / 1.10 - 1) * 100 / 2)
    assert [r["id"] for r in lb["rows"]][-1] == "N"  # 갓 연 계좌는 뒤에
    # 처음 한 달(자리 잡은 계좌가 없다)은 모두를 자리 잡은 것으로 — 앞 시험과 같은 잣대
    assert all(not r["young"] for r in desk.leaderboard({"A": a[:5], "N": n})["rows"])


def test_merge_keeps_other_market() -> None:
    a = desk.merge(None, "KR", {"x": 1}, {"leaderboard": "old"})
    b = desk.merge(a, "US", {"y": 2}, {"leaderboard": "new"})
    assert b["markets"] == {"KR": {"x": 1}, "US": {"y": 2}} and b["leaderboard"] == "new"


def test_write_merges_both_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for aid, as_of, twr in (("B", "2026-10-15", 1.0), ("B", "2026-10-16", 1.02), ("R1", "2026-10-16", 1.0)):
        c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
                       " twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1, 1, 0, 1, 0, ?, '[]', ?, 't')",
                       [aid, as_of, twr, 0 if aid == "R1" else 1])  # fmt: skip
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, tranche, created_at)"
        " VALUES (1, 'B', 'KR', 1, 'buy', 'limit', 9800, 3, 'KRW', '2026-10-15', '2026-10-16', '2부 배분 — 점수 상위', 1, 1, 't')"
    )  # fmt: skip
    지금 = datetime(2026, 10, 15, 23, 40, tzinfo=UTC)  # 10-16 08:40 KST
    기록 = job.write(c, "KR", 지금, trade_date="2026-10-15", session_date="2026-10-16",
                   step_log={"broker": {"sent": {"accepted": 1}}, "paper": {"accounts": {"R1": {"recon_ok": False},
                                                                                       "B": {"recon_ok": True, "holding": {
                                                                                           "weights": {"A": 1.1}}}}}},
                   warnings=["포트폴리오 경고"])  # fmt: skip
    assert 기록 == {"as_of": "2026-10-16", "accounts": 2, "checks": 2}
    job.write(c, "US", datetime(2026, 10, 16, 12, 30, tzinfo=UTC), trade_date="2026-10-15", session_date="2026-10-16",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-16'")[0][0])
    assert set(s["markets"]) == {"KR", "US"} and s["last_market"] == "US"
    kr = s["markets"]["KR"]
    assert kr["orders_h"][0]["name"] == "종목1" and kr["orders_h"][0]["reason"].startswith("2부 배분")
    assert kr["orders_by_account"] == {"B": 1}
    assert kr["holding"] == {"weights": {"A": 1.1}}  # 베끼는 계좌의 지주사 기록 — 연구실 가중치 추이(25.1506)
    assert s["markets"]["US"]["holding"] is None
    assert any("장부 검산 어긋남: 계좌 R1" in x["text"] for x in kr["checks"])
    assert [r["id"] for r in s["leaderboard"]["rows"]][0] in ("B", "R1")
    # 판 이력 표를 처음 채운다 — 계좌마다 지금 판, since '' (계좌의 처음부터). B 는 1.0 → 1.02 = +2%, R1 은 0%
    assert s["rule_history"]["B"] == [{"version": 1, "from": "", "to": None, "orders": 1, "pct": pytest.approx(2.0),
                                        "control_pct": pytest.approx(0.0), "vs_r_pp": pytest.approx(2.0)}]
    assert {"A", "B", "H", "R1"} <= s["rule_history"].keys()  # 주문이 없어도 판은 있다
    # 판을 올리면 그 실행의 판정일이 새 판의 since
    c.conn.execute("UPDATE paper_accounts SET rule_version = 2 WHERE id = 'B'")
    job.write(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    assert c.q("SELECT rule_version, since FROM paper_rule_versions WHERE account_id = 'B' ORDER BY rule_version") == [
        (1, ""), (2, "2026-10-16")]


def test_rule_history_spans_by_hand() -> None:
    """검토(25.1505) 1 의 예 — `paper_daily.as_of` 는 배치를 돌린 한국 날짜, 그 행은 그때까지 나온 종가 평가.
    금요일(10-16) 장에 판 1 보유가 +5%, 판 2 첫 결정은 판정일 10-16(월요일 10-19 아침 실행). 10-16 행 = 목요일 종가,
    10-19 행 = 금요일 종가(1.05), 10-20 행 = 월요일 종가(1.05). 판 1: 처음 1.0 → 10-16 보다 늦은 첫 평가 1.05 = +5%,
    판 2: 1.05 → 마지막 1.05 = 0% — 판 2 가 판 1 의 금요일을 가져가지 않는다.
    R1 같은 구간: 1.0 → 1.01 = +1% · 1.01 → 1.0 = −0.990099%"""
    series = {"A": [("2026-10-16", 1.0), ("2026-10-19", 1.05), ("2026-10-20", 1.05)],
              "R1": [("2026-10-16", 1.0), ("2026-10-19", 1.01), ("2026-10-20", 1.0)]}  # fmt: skip
    h = desk.rule_history([("A", 2, "2026-10-16"), ("A", 1, ""), ("R1", 1, "")], series, {("A", 2): 3})
    a1, a2 = h["A"]
    assert (a1["version"], a1["from"], a1["to"], a1["orders"]) == (1, "", "2026-10-16", 0)
    assert a1["pct"] == pytest.approx(5.0) and a1["control_pct"] == pytest.approx(1.0) and a1["vs_r_pp"] == pytest.approx(4.0)
    assert (a2["version"], a2["to"], a2["orders"]) == (2, None, 3)
    assert a2["pct"] == pytest.approx(0.0) and a2["control_pct"] == pytest.approx((1.0 / 1.01 - 1) * 100)
    assert h["R1"][0]["vs_r_pp"] is None  # 대조끼리는 견주지 않는다
    assert desk.rule_history([("X", 1, "")], {})["X"][0]["pct"] is None
    assert desk.rule_history([("X", 1, "")], {}, None)["X"][0]["orders"] is None
    # 아직 그 결정 뒤 평가가 없으면 모름
    assert desk.rule_history([("A", 3, "2026-10-20")], series)["A"][0]["pct"] is None


def test_new_versions_first_seen() -> None:
    assert desk.new_versions({"A": 2, "B": 1}, [], "2026-10-16") == [("A", 2, ""), ("B", 1, "")]
    assert desk.new_versions({"A": 3, "B": 1}, [("A", 2, ""), ("B", 1, "")], "2026-10-20") == [("A", 3, "2026-10-20")]
    assert desk.new_versions({"A": 2}, [("A", 2, "")], "2026-10-20") == []


def _주문(c: Client, oid: int, aid: str, *, market: str = "KR", sid: int = 1, decided: str, session: str,
         status: str = "open", closed: str | None = None) -> None:
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, tranche, status, closed_on, created_at)"
        " VALUES (?, ?, ?, ?, 'buy', 'limit', 100, 1, 'KRW', ?, ?, '배분', 1, 1, ?, ?, 't')",
        [oid, aid, market, sid, decided, session, status, closed],
    )  # fmt: skip


def test_write_review_fixes_1503(monkeypatch: pytest.MonkeyPatch) -> None:
    """검토(25.1503): 밀린 체결(4)·장 끝난 뒤 손 실행 주문(5)·직전 줄 이어받기(3)·깨진 줄(7)"""
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    # 10-14 에 결정해 10-15 장에 찼어야 할 주문이 배치가 빠져 10-16 판정(closed_on 10-16)에서 찼다 — 체결일 10-15
    _주문(c, 1, "B", decided="2026-10-14", session="2026-10-15", status="filled", closed="2026-10-16")
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (1, 'B', 1, 'buy', '2026-10-15', 100, 1,"
                   " 'KRW', 1, 0, 0, 0, -100, 't')")  # fmt: skip
    # 다른 날 닫힌 체결은 이 실행 것이 아니다
    _주문(c, 2, "B", sid=3, decided="2026-10-13", session="2026-10-14", status="filled", closed="2026-10-14")
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (2, 'B', 3, 'buy', '2026-10-14', 100, 1,"
                   " 'KRW', 1, 0, 0, 0, -100, 't')")  # fmt: skip
    # 장 끝난 뒤 손 실행 — 10-16 판정의 주문이 10-20 장으로
    _주문(c, 3, "B", sid=3, decided="2026-10-16", session="2026-10-20")
    # 직전 줄(10-16)에 미국 칸, 오늘(10-17) 줄은 아직 없다
    c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES ('2026-10-16', ?, 't')",
                   [json.dumps({"markets": {"US": {"run_at": "2026-10-16T13:20:00+00:00", "checks": []}}})])  # fmt: skip
    c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES ('2026-10-10', '{', 't')")
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'")[0][0])
    assert s["markets"]["US"]["run_at"] == "2026-10-16T13:20:00+00:00"  # 이어받음 — 제 실행 시각 그대로
    kr = s["markets"]["KR"]
    assert [(f["name"], f["fill_date"]) for f in kr["fills_h"]] == [("종목1", "2026-10-15")]
    assert [(o["name"], o["fill_session"]) for o in kr["orders_h"]] == [("종목3", "2026-10-20")]
    # 오늘 줄이 깨져 있어도 다시 쓴다 — 날마다 실패하지 않게
    c.conn.execute("UPDATE paper_desk SET summary_json = '[' WHERE as_of = '2026-10-17'")
    job.write(c, "US", datetime(2026, 10, 17, 13, 20, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-17",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'")[0][0])
    assert set(s["markets"]) == {"US"}  # 깨진 줄은 없는 셈 — 이번 시장 칸부터 다시
    # 체결 질의는 체결 색인을 탄다(체결 장부 전체를 훑지 않는다)
    plan = " | ".join(r[3] for r in c.q(f"EXPLAIN QUERY PLAN {job.FILLS_SQL}", ["2026-10-02", "KR", "2026-10-16"]))
    assert "idx_paper_fills_account" in plan
