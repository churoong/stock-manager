"""25.1991 (2026-10-29 사용자 "텔레그램으로 보내주는 메시지를 우리 목표에 맞게 내용을 바꿔줘") — 리포트를 목표 순서로.

목표는 로보어드바이저이고 가상모의투자·KIS모의투자가 중심이다(CLAUDE.md). 실제 10-29 리포트는 운용 보고 뒤를 추천 1부가
시장마다 약 50줄 차지하고, 계좌별 평가·위원회는 맨 아래, 실전 관문 진도는 없었다. 이제
- 운용 보고에 **실전 관문 진도** 한 줄
- 가상모의투자 절을 추천 **앞**에, 추천·일정·공시·엇갈림은 "전략 재료" 머리 아래로
- 2부 "배분에서 빠진 종목" 은 같은 까닭끼리 한 줄(까닭은 종목마다 남는다)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from batch.jobs import desk as job
from batch.services import desk
from tests.test_paper_job_1445 import Client


def _관문(**k: object) -> dict:
    items = [{"key": "days", "now": 9, "ok": False}, {"key": "vs_r", "ok": True}, {"key": "incidents", "ok": True}]
    return {"stage": 1, "account": "H", "version": 2, "items": items, "passed": False, "looks": [],
            "next_look": 126} | k  # fmt: skip


def test_관문_진도_줄() -> None:
    assert desk.gate_line(_관문()) == "▸ 실전 관문 1단계 (지주사 판 2): 모의 9/126평가 · 칸 2/3 충족 · 다음 판정 126평가"
    # 판정 시점 결과를 붙인다(25.1992) — 넘은 숫자만 보이면 통과에 가까운 것처럼 읽혔다
    못 = desk.gate_line(_관문(looks=[{"n": 126, "passed": False}], next_look=189))
    assert 못 is not None and 못.endswith("· 126 못 넘음 · 다음 판정 189평가")
    통과 = desk.gate_line(_관문(passed=True, looks=[{"n": 126, "passed": True}], next_look=189))
    assert 통과 is not None and 통과.endswith("· 126 통과 · 다음 단계는 사용자 결정")
    끝 = desk.gate_line(_관문(next_look=None, looks=[{"n": 126}, {"n": 189}, {"n": 252}]))
    assert 끝 is not None and 끝.endswith("· 252 못 넘음 · 판정 시점 끝 — 새 판 등록 전엔 통과 없음")
    assert desk.gate_line(None) is None and desk.gate_line({"items": None}) is None  # 관문도 까닭도 없는 옛 줄
    # 관문을 비운 날은 소리 없이 빠지지 않고 "세지 못함"(25.1992) — 계산 실패 꼬리는 뗀다
    assert desk.gate_line(None, "판 이력을 읽지 못해 관문을 내지 않음 — X" + desk.GATE_CALC_ERROR_SEP + "Y") == (
        "▸ 실전 관문: 세지 못함 — 판 이력을 읽지 못해 관문을 내지 않음 — X (자세히: 웹 운용 화면)")
    assert desk.gate_line({"items": [{"key": "vs_r", "ok": True}]}) == (
        "▸ 실전 관문 1단계 (?): 모의 ?/126평가 · 칸 1/1 충족")  # days 칸이 없으면 ? — 지어내지 않는다


def test_운용_보고에_관문_줄은_확인할_일_바로_아래() -> None:
    줄 = desk.report_lines({"checks": [], "trade_date": "2026-10-28"}, {"rows": []}, "H", gate=_관문())
    assert 줄[1] == "▸ 확인할 일: 없음" and 줄[2].startswith("▸ 실전 관문 1단계 (지주사 판 2)")
    assert not any(x.startswith("▸ 실전 관문") for x in desk.report_lines({"checks": []}, {"rows": []}, "H"))


def test_운용_보고는_운용_요약_마지막_줄의_관문을_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES ('2026-10-16', ?, 't')",
                   [json.dumps({"gate": _관문(account="B"), "markets": {}})])  # fmt: skip
    줄 = job.report_head(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                        session_date="2026-10-19", step_log={"paper": {"accounts": {}}}, warnings=[])  # fmt: skip
    assert any(x.startswith("▸ 실전 관문 1단계 (기준 판 2): 모의 9/126평가") for x in 줄)


def test_가상모의투자_절이_추천보다_앞() -> None:
    """daily 본문 조립 — 가상모의투자 단계의 줄을 붙인 **뒤에** 전략 재료(추천·일정·공시·엇갈림)를 붙인다"""
    글 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    모의 = 글.index('message += "\\n\\n" + "\\n".join(모의줄)')
    재료 = 글.index("message += \"\\n\\n\" + (RECOMMEND_HEAD")
    assert 모의 < 재료
    for 조각 in ("재료.append(composed.text)", '재료.append("\\n".join(일정))', '재료.append("\\n".join(뒤공시))',
               '재료.append("\\n".join(엇갈림))', '재료.append("\\n".join(줄))'):  # fmt: skip
        assert 조각 in 글, 조각
    assert 'message += "\\n\\n" + composed.text' not in 글


def test_빠진_종목은_같은_까닭끼리_한_줄() -> None:
    from batch.notify import report_sections as rs
    from batch.notify.report_sections import Excluded, PortfolioView

    view = PortfolioView(total_budget=1000.0, budget_currency="KRW", allocations=[], excluded=[
        Excluded(ticker="A", name="가", reason="투자 여력 부족", detail="남은 0원"),
        Excluded(ticker="B", name="나", reason="투자 여력 부족", detail="남은 0원"),
        Excluded(ticker="C", name="다", reason="종목 비중 상한 도달", detail="보유 7.4%"),
        Excluded(ticker="D", name="라", reason="종목 비중 상한 도달", detail="보유 9.1%"),  # 사유 같고 수치 다름(25.1992)
    ])  # fmt: skip
    글 = rs.render_portfolio(view, [])
    assert "  투자 여력 부족 (남은 0원): 가 · 나" in 글 and "  다 — 종목 비중 상한 도달 (보유 7.4%)" in 글
    assert "  라 — 종목 비중 상한 도달 (보유 9.1%)" in 글  # 다른 종목의 수치로 바뀌지 않는다



def test_가상모의투자_단계가_깨져도_전략_재료는_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1992 (25.1991 교차검증 [권고]) — 재료 붙이기가 단계 try 안으로 들어가면 단계가 깨진 날 1부·2부가 통째로
    빠진다. 하네스(DB 없음)에서는 가상모의투자 단계가 예외로 끝난다"""
    from batch.jobs import daily
    from tests.test_report_store_fail_1180 import _준비

    보낸, _저장, _알림 = _준비(monkeypatch, 0)
    assert daily.run("KR") == 0
    (글,) = 보낸
    assert daily.RECOMMEND_HEAD in 글 and "1부 개별 종목" in 글 and "가상모의투자 계좌" in 글


def test_가상모의투자_절이_전략_재료보다_앞에_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import daily
    from batch.jobs import desk as desk_job
    from batch.jobs import paper as paper_job
    from tests.test_report_store_fail_1180 import _준비

    보낸, _저장, _알림 = _준비(monkeypatch, 0)
    monkeypatch.setattr(paper_job, "run_step_locked",
                        lambda *a, **k: (["🧪 가상모의투자 (평가)", "  기준 10억원"], {"accounts": {}}))  # fmt: skip
    monkeypatch.setattr(desk_job, "report_head", lambda *a, **k: ["📊 운용 보고", "▸ 확인할 일: 없음"])
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: False)  # KIS 알림 길 — 이 시험 몫이 아니다
    assert daily.run("KR") == 0
    글 = 보낸[0]
    assert 글.index("📊 운용 보고") < 글.index("🧪 가상모의투자") < 글.index(daily.RECOMMEND_HEAD) < 글.index("1부 개별 종목")



def test_운용_보고는_관문을_비운_날_세지_못함을_싣는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1993 (25.1992 교차검증 [권고]) — `report_head` 가 운용 요약 마지막 줄의 `gate_error` 를 넘기는 배선. 함수만
    시험하면 배선이 빠져 관문 줄이 소리 없이 사라져도 몰랐다"""
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    글 = "판 이력을 읽지 못해 관문을 내지 않음 — OperationalError" + desk.GATE_CALC_ERROR_SEP + "X꼬리"
    c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES ('2026-10-16', ?, 't')",
                   [json.dumps({"gate": None, "gate_error": 글, "markets": {}})])  # fmt: skip
    줄 = job.report_head(c, "KR", datetime(2026, 10, 18, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                        session_date="2026-10-19", step_log={"paper": {"accounts": {}}}, warnings=[])  # fmt: skip
    관문 = [x for x in 줄 if x.startswith("▸ 실전 관문")]
    assert len(관문) == 1 and 관문[0].startswith("▸ 실전 관문: 세지 못함 — 판 이력을 읽지 못해") and "X꼬리" not in 관문[0]


def test_전략_재료_머리에서_나뉘면_2부_이어서를_붙이지_않는다() -> None:
    """25.1993 (25.1992 교차검증 [권고]) — `_끝_절` 의 "📋 " 는 🧪 절이 없는 날(가상모의투자 단계가 줄을 안 낸 날)
    "2부 포트폴리오 (이어서)" 가 전략 재료 머리 위에 붙는 것을 막는 유일한 장치다"""
    from batch.jobs import daily
    from batch.notify import telegram as tg

    결과 = tg._carry_headers(["2부 포트폴리오\n종목1  1,000,000원 (3.3%)", f"{daily.RECOMMEND_HEAD}\n\n시장 국면 (…)"])
    assert 결과[1].split("\n")[0] == daily.RECOMMEND_HEAD



def test_조각_끝의_전략_재료_머리는_다음_조각으로() -> None:
    """25.1994 (25.1992 교차검증 [참고]) — 외톨이 머리를 다음 조각으로 넘긴다(1부·기간 머리와 같게)"""
    from batch.jobs import daily
    from batch.notify import telegram as tg

    결과 = tg._carry_headers([f"🧪 가상모의투자\n  기준 10억원\n\n{daily.RECOMMEND_HEAD}\n", "시장 국면 (…)\n1부 개별 종목"])
    assert not 결과[0].rstrip().endswith(daily.RECOMMEND_HEAD)
    assert 결과[1].split("\n")[0] == daily.RECOMMEND_HEAD
