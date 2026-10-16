"""아침 운용 보고 — 리포트 머리 (docs/desk.md 7장, docs/infra.md 25.1510)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import desk as job
from batch.notify import formatter
from batch.services import desk
from tests.test_paper_job_1445 import Client


def _칸(**over: object) -> dict:
    return {"trade_date": "2026-10-16", "session_date": "2026-10-19", "checks": [], "fills_h": [], "orders_h": [],
            "fills_by_account": {}, "orders_by_account": {}, **over}  # fmt: skip


def test_report_lines_by_hand() -> None:
    board = {"window_start": "2026-10-16", "rows": [
        {"id": "C", "window_pct": 3.0, "vs_r_pp": 2.5}, {"id": "A", "window_pct": 2.0, "vs_r_pp": 1.5},
        {"id": "B", "window_pct": 1.0, "vs_r_pp": 0.5}, {"id": "H", "window_pct": -1.0, "vs_r_pp": -1.5},
        {"id": "N", "window_pct": 9.0, "vs_r_pp": 8.0, "young": True},  # 갓 연 계좌는 순위 줄에서 뺀다
    ]}  # fmt: skip
    칸 = _칸(
        checks=[{"level": "high", "text": "멈춤"}, {"level": "normal", "text": "a"}, {"level": "normal", "text": "b"},
                {"level": "normal", "text": "c"}],
        fills_h=[{"side": "buy", "name": "삼성", "quantity": 3, "price": 9800, "currency": "KRW"}],
        orders_h=[{"side": "buy", "name": f"종목{i}", "quantity": 1, "order_type": "limit", "limit_price": 10.5,
                   "currency": "USD"} for i in range(7)],
        fills_by_account={"H": 1, "A": 2, "C": 1}, orders_by_account={"H": 7, "B": 3},
    )  # fmt: skip
    줄 = desk.report_lines(칸, board, "H", prev_kis=[{"status": "accepted"}, {"status": "accepted"}, {"status": "rejected"}])
    assert 줄[0].startswith("📊 운용 보고")
    assert 줄[1] == "확인할 일: ❗멈춤 / a / b 외 1건"
    assert 줄[2] == "순위(공통 구간 2026-10-16부터): C +3.00%(R 대비 +2.50%p) · A +2.00%(R 대비 +1.50%p) · B +1.00%(R 대비 +0.50%p) … H(KIS) -1.00%(R 대비 -1.50%p)"
    assert 줄[3] == "H 체결(판정 2026-10-16): 매수 삼성 3주 @ 9,800원"
    assert 줄[4].startswith("H 다음 장(2026-10-19) 주문: 매수 종목0 1주 $10.50 · ") and 줄[4].endswith("외 2건")
    assert 줄[5] == "다른 계좌 — 체결 A 2 · C 1 / 주문 B 3"
    assert 줄[6] == "KIS모의투자: 이 주문을 접수 시간에 그대로 냅니다 · 지난 실행 접수 2 · 거부 1"
    # 비었으면 "없음", 지난 KIS 를 모르면 그 줄을 뺀다
    빈 = desk.report_lines(_칸(), {"rows": []}, "H")
    assert 빈[1:] == ["확인할 일: 없음", "H 체결(판정 2026-10-16): 없음", "H 다음 장(2026-10-19) 주문: 없음",
                      "다른 계좌 — 체결 없음 / 주문 없음"]  # fmt: skip


def test_with_head_keeps_title_on_top() -> None:
    m = "[국내] 2026-10-16 종가\n생성 08:40\n요약\n\n1부"
    assert formatter.with_head(m, ["📊 운용 보고", "x"]) == "[국내] 2026-10-16 종가\n생성 08:40\n\n📊 운용 보고\nx\n요약\n\n1부"


def test_report_head_reads_last_kis(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, tranche, created_at)"
        " VALUES (1, 'B', 'KR', 1, 'buy', 'limit', 9800, 3, 'KRW', '2026-10-16', '2026-10-19', '배분', 1, 1, 't')"
    )  # fmt: skip
    c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES ('2026-10-16', ?, 't')",
                   [json.dumps({"markets": {"KR": {"kis_orders": [{"status": "rejected"}]}}})])  # fmt: skip
    줄 = job.report_head(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                        session_date="2026-10-19", step_log={"paper": {"accounts": {}}}, warnings=[])  # fmt: skip
    assert "B 다음 장(2026-10-19) 주문: 매수 종목1 3주 9,800원" in 줄
    assert 줄[-1] == "KIS모의투자: 이 주문을 접수 시간에 그대로 냅니다 · 지난 실행 거부 1"
    assert c.q("SELECT COUNT(*) FROM paper_desk")[0][0] == 1  # 운용 보고는 읽기만 — 운용 요약은 리포트 뒤 단계가 적는다


def test_daily_puts_head_after_paper_step() -> None:
    """일일 배치가 가상모의투자 단계 뒤·보내기 전에 머리를 끼운다(dry-run·단계 실패는 뺀다)"""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    단계, 머리, 보냄 = 글.index("paper_job.run_step("), 글.index("desk_job.report_head("), 글.index("telegram.send(message)")
    assert 단계 < 머리 < 보냄
    assert 'not dry_run and "paper" in step_log and not (step_log.get("paper") or {}).get("error")' in 글
