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
    assert desk.gate_line(_관문(passed=True)).endswith("· 통과(다음 단계는 사용자 결정)")  # type: ignore[union-attr]
    끝 = desk.gate_line(_관문(next_look=None, looks=[{"n": 126}]))
    assert 끝 is not None and 끝.endswith("· 판정 시점 끝 — 새 판 등록 전엔 통과 없음")
    assert desk.gate_line(None) is None and desk.gate_line({"items": None}) is None  # 관문을 비운 날·옛 줄


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
    ])  # fmt: skip
    글 = rs.render_portfolio(view, [])
    assert "  투자 여력 부족 (남은 0원): 가 · 나" in 글 and "  다 — 종목 비중 상한 도달 (보유 7.4%)" in 글
