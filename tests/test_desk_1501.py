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
    c = desk.checks(halted=True, paper={"frozen": [1, 2]}, broker={"sent": {"rejected": 2}, "missing": ["KIS_VTS_APP_SECRET"]},
                    recon_bad=["B"], warnings=[f"경고{i}" for i in range(10)])  # fmt: skip
    assert [x["level"] for x in c[:3]] == ["high", "high", "high"]
    글 = " / ".join(x["text"] for x in c)
    assert "멈춤 스위치" in 글 and "KIS_VTS_APP_SECRET" in 글 and "장부 검산 어긋남: 계좌 B" in 글 and "거부 2건" in 글
    assert "경고 2줄 더" in 글 and "경고8" not in 글
    assert desk.checks(halted=False, paper=None, broker=None, recon_bad=[], warnings=[]) == []


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
                   step_log={"broker": {"sent": {"accepted": 1}}}, warnings=["포트폴리오 경고"])  # fmt: skip
    assert 기록 == {"as_of": "2026-10-16", "accounts": 2, "checks": 2}
    job.write(c, "US", datetime(2026, 10, 16, 12, 30, tzinfo=UTC), trade_date="2026-10-15", session_date="2026-10-16",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-16'")[0][0])
    assert set(s["markets"]) == {"KR", "US"} and s["last_market"] == "US"
    kr = s["markets"]["KR"]
    assert kr["orders_h"][0]["name"] == "종목1" and kr["orders_h"][0]["reason"].startswith("2부 배분")
    assert kr["orders_by_account"] == {"B": 1}
    assert any("장부 검산 어긋남: 계좌 R1" in x["text"] for x in kr["checks"])
    assert [r["id"] for r in s["leaderboard"]["rows"]][0] in ("B", "R1")
