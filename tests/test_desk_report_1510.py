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
    줄 = desk.report_lines(칸, board, "H", prev_kis=[{"status": "accepted"}, {"status": "accepted"}, {"status": "rejected"}],
                         kis_state="이 주문을 접수 시간에 그대로 냅니다")  # fmt: skip
    assert 줄[0].startswith("📊 운용 보고")
    # 항목마다 줄을 나누고 들여 쓴다(25.1721 — 예전엔 한 줄에 " · " 로 이어 폰에서 한 문단이 됐다)
    assert 줄[1:6] == ["▸ 확인할 일", "  ❗멈춤", "  a", "  b", "  … 외 1건"]
    assert 줄[6:11] == ["▸ 순위 (공통 구간 2026-10-16부터)", "  1. C +3.00% (R 대비 +2.50%p)",
                        "  2. A +2.00% (R 대비 +1.50%p)", "  3. B +1.00% (R 대비 +0.50%p)",
                        "  … H(KIS) -1.00% (R 대비 -1.50%p)"]  # fmt: skip
    assert 줄[11:13] == ["▸ H 체결 (판정 2026-10-16)", "  매수 삼성 3주 @ 9,800원"]
    assert 줄[13] == "▸ H 다음 장 주문 (2026-10-19)" and 줄[14] == "  매수 종목0 1주 $10.50" and 줄[19] == "  … 외 2종목"
    # 다른 계좌 건수 줄은 뺐다(25.1738 — 가상모의투자 절이 계좌마다 체결 수를 싣는다)
    assert not any("다른 계좌" in x for x in 줄)
    assert 줄[20:] == ["▸ KIS모의투자: 이 주문을 접수 시간에 그대로 냅니다",
                       "  지난 실행 보낸 결과: 접수 2 · 거부 1 (체결은 다음 실행이 맞춤)"]  # fmt: skip
    # 비었으면 "없음", KIS 상태를 모르면 그 줄을 뺀다
    빈 = desk.report_lines(_칸(), {"rows": []}, "H")
    assert 빈[1:] == ["▸ 확인할 일: 없음", "▸ H 체결 (판정 2026-10-16): 없음", "▸ H 다음 장 주문 (2026-10-19): 없음"]


def test_with_head_keeps_header_block_on_top() -> None:
    """머리 블록(제목·생성·요약·지연 — 첫 빈 줄까지) 뒤에 끼운다 — 요약·지연 줄이 운용 보고 끝에 붙지 않게(25.1512 검토 2)"""
    m = "[국내] 2026-10-16 종가\n생성 08:40\n국내 900종목 저장\n예정보다 15분 늦게 실행됨\n\n1부"
    assert formatter.with_head(m, ["📊 운용 보고", "x"]) == (
        "[국내] 2026-10-16 종가\n생성 08:40\n국내 900종목 저장\n예정보다 15분 늦게 실행됨\n\n📊 운용 보고\nx\n\n1부")
    # 빈 줄이 없으면 끝에 — 머리와 붙지 않게 빈 줄을 두고(25.1738)
    assert formatter.with_head("제목", ["x"]) == "제목\n\nx"


def test_report_lines_kis_state_and_young_champion() -> None:
    """검토(25.1512) 1·4·5 — KIS 상태 글, 주문의 체결 장, 갓 연 챔피언도 순위 줄에"""
    board = {"window_start": "2026-10-16", "rows": [
        {"id": "C", "window_pct": 3.0, "vs_r_pp": 2.5}, {"id": "H", "window_pct": 1.0, "vs_r_pp": None, "young": True}]}  # fmt: skip
    칸 = _칸(orders_h=[{"side": "buy", "name": "가", "quantity": 1, "order_type": "market", "limit_price": None,
                      "currency": "KRW", "fill_session": "2026-10-20"}])  # fmt: skip
    줄 = desk.report_lines(칸, board, "H", prev_kis=None, kis_state="멈춤 스위치가 켜져 있어 내지 않습니다")
    assert 줄[2:5] == ["▸ 순위 (공통 구간 2026-10-16부터)", "  1. C +3.00% (R 대비 +2.50%p)",
                       "  … H(KIS) +1.00% — 갓 엶, 개설 후"]  # fmt: skip
    # 장 끝난 뒤 손 실행 — 주문의 장
    assert 줄[6:8] == ["▸ H 다음 장 주문 (2026-10-20)", "  매수 가 1주 시장가"]
    assert 줄[-1] == "▸ KIS모의투자: 멈춤 스위치가 켜져 있어 내지 않습니다"  # 지난 실행을 모르면 그 부분을 뺀다


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
    for k in ("KIS_VTS_APP_KEY", "KIS_VTS_APP_SECRET", "KIS_VTS_ACCOUNT"):
        monkeypatch.delenv(k, raising=False)
    줄 = job.report_head(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                        session_date="2026-10-19", step_log={"paper": {"accounts": {}}}, warnings=[])  # fmt: skip
    i = 줄.index("▸ B 다음 장 주문 (2026-10-19)")
    assert 줄[i + 1] == "  매수 종목1 3주 9,800원"
    # 키가 없으면 "꺼짐(키 없음)" — 늘 "냅니다" 라 하지 않는다(25.1512 검토 1)
    assert 줄[-2:] == ["▸ KIS모의투자: 꺼짐(키 없음)", "  지난 실행 보낸 결과: 거부 1 (체결은 다음 실행이 맞춤)"]
    monkeypatch.setenv("KIS_VTS_APP_KEY", "k")
    monkeypatch.setenv("KIS_VTS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_VTS_ACCOUNT", "12345678-01")
    켜짐 = job.report_head(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                          session_date="2026-10-19", step_log={"paper": {"accounts": {}}}, warnings=[], after_close=True)  # fmt: skip
    assert 켜짐[-2].startswith("▸ KIS모의투자: 장 끝난 뒤 실행이라 오늘은 내지 않습니다")
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', '{\"on\": true}', 't')")
    assert job.report_head(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                           session_date="2026-10-19", step_log={"paper": {"accounts": {}}}, warnings=[]
                           )[-2].startswith("▸ KIS모의투자: 멈춤 스위치가 켜져 있어 내지 않습니다")  # fmt: skip
    # 운용 요약 줄이 없으면 지난 실행은 모름 — "낸 주문 없음" 이라 하지 않는다
    c.conn.execute("DELETE FROM paper_desk")
    assert "지난 실행" not in job.report_head(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC),
                                             trade_date="2026-10-16", session_date="2026-10-19",
                                             step_log={"paper": {"accounts": {}}}, warnings=[])[-1]  # fmt: skip
    assert c.q("SELECT COUNT(*) FROM paper_desk")[0][0] == 0  # 운용 보고는 읽기만 — 운용 요약은 리포트 뒤 단계가 적는다


def test_daily_puts_head_after_paper_step() -> None:
    """일일 배치가 가상모의투자 단계 뒤·보내기 전에 머리를 끼운다(dry-run·단계 실패는 뺀다)"""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    단계, 머리, 보냄 = 글.index("paper_job.run_step_locked("), 글.index("desk_job.report_head("), 글.index("telegram.send(message)")
    assert 단계 < 머리 < 보냄
    assert 'not dry_run and "paper" in step_log and "error" not in 모의 and not 모의.get("skipped")' in 글
    assert "after_close=_장_끝남(market, decision.session_date)" in 글[머리:보냄]
