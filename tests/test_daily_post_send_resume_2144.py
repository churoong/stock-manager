"""리포트를 보낸 뒤 KIS 단계에서 실행이 죽으면 예비 실행이 발송만 건너뛰고 이어 돈다 (docs/infra.md 25.2144,
11-05 결함 회차 [권고]).

예전엔 `reports.sent_exists` 가 참이면 무조건 "이미 보냈다" 로 빠져, 25.1475 가 발송 뒤로 옮긴 KIS모의투자 베끼기(첫째·도전자)와
운용 요약이 그날 끝내 돌지 않았다. 이제 그날 그 시장 운용 요약 칸(`paper_desk.markets.<시장>.trade_date`)이 있을 때만 빠진다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import daily
from batch.jobs import desk as desk_job
from tests.test_portfolio_job import MemClient

지금 = datetime(2026, 11, 5, 0, 35, tzinfo=UTC)  # 09:35 KST 예비


def _desk(mem: MemClient, as_of: str, market: str, trade_date: str) -> None:
    mem.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, 't')",
                     [as_of, json.dumps({"markets": {market: {"trade_date": trade_date}}})])  # fmt: skip


def test_운용_요약_칸으로_발송_뒤_단계가_끝났는지_본다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    assert desk_job.post_send_done(mem, "KR", "2026-11-04", 지금) is False
    _desk(mem, "2026-11-05", "US", "2026-11-04")  # 다른 시장 칸만 — 아직
    assert desk_job.post_send_done(mem, "KR", "2026-11-04", 지금) is False
    _desk(mem, "2026-11-04", "KR", "2026-11-04")  # 전날(자정 넘긴 실행) 줄도 본다
    assert desk_job.post_send_done(mem, "KR", "2026-11-04", 지금) is True


def test_못_읽으면_예전처럼_건너뛴다() -> None:
    class 깨짐:
        def execute(self, *a: object, **k: object) -> None:
            raise RuntimeError("x")

    assert desk_job.post_send_done(깨짐(), "KR", "2026-11-04", 지금) is True


def test_보냈지만_발송_뒤_단계가_안_끝났으면_이어_돈다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from batch.core import calendar as cal

    mem = MemClient()
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]

    monkeypatch.setattr(daily.cal, "decide", lambda market, now=None, force=False: cal.RunDecision(
        should_run=True, reason="예약", trade_date="2026-11-04", session_date="2026-11-05"))  # fmt: skip
    monkeypatch.setattr(daily.db, "has_successful_run", lambda *a, **k: False)
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: True)
    monkeypatch.setattr(desk_job, "post_send_done", lambda *a, **k: False)

    class 이어돎(Exception):
        pass

    def 시작(*a: object, **k: object) -> None:
        raise 이어돎

    monkeypatch.setattr(daily.db, "start_batch_run", 시작)
    with pytest.raises(이어돎):
        daily.run("KR")
    assert "발송 없이 이어 돕니다" in capsys.readouterr().out


def test_이어_돌_때는_발송_블록을_건너뛰고_발송_뒤_단계는_돈다() -> None:
    """발송 블록 첫 줄이 표지를 던지고, 그 except 는 보낸 것으로 적은 뒤 아래 `_kis와_요약()` 로 간다(정적)"""
    글 = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    블록 = 글.index("            if 발송끝남:\n                raise _이미보냄")
    assert 블록 < 글.index("message_ids = telegram.send(message)", 블록)
    잡음 = 글.index("        except _이미보냄:\n            step_log[\"sent\"] = ALREADY_SENT_NOTE")
    assert 잡음 < 글.index("        _kis와_요약()\n        try:\n            for 줄 in _주의_출력", 잡음)
    assert "KIS모의투자 베끼기·운용 요약이 빠졌을 수 있습니다" in daily.timeout_text(15, "KR")



def test_이어_돌기는_보내지도_저장하지도_않고_KIS_와_운용요약만_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2146 — 실제 run 으로(25.2144 교차검증 [권고]: 정적 시험은 `발송끝남 = True` 를 지워 리포트가 다시 나가도 통과했다)"""
    from tests.test_report_store_fail_1180 import _준비
    from tests.test_send_fail_kis_1715 import _kis_desk

    보낸, 저장, _ = _준비(monkeypatch, 0)
    늦음 = daily.cal.RunDecision(True, "테스트", "2026-10-08", "2026-10-09", None, late_minutes=-20)  # 늦은 리포트 경고가 붙는 꼴
    monkeypatch.setattr(daily.cal, "decide", lambda mk, now=None, force=False: 늦음)
    부름 = _kis_desk(monkeypatch)
    닫음: list = []
    monkeypatch.setattr(daily.db, "finish_batch_run", lambda c, rid, **k: 닫음.append(k))
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: True)
    monkeypatch.setattr(desk_job, "post_send_done", lambda *a, **k: False)
    assert daily.run("KR") == 0
    assert 보낸 == [] and 저장 == []  # 다시 보내지도 웹 리포트를 덮지도 않는다
    assert len(부름["kis"]) == 2 and 부름["desk"] == [1]  # 첫째·도전자 베끼기와 운용 요약은 돈다
    로그 = 닫음[-1]["step_log"]
    assert 로그["sent"] == daily.ALREADY_SENT_NOTE
    assert not any("늦은 리포트" in w for w in 로그.get("warnings", []))  # 이 실행은 리포트를 만들지 않았다


def test_발송_뒤_단계까지_끝난_날은_아무것도_돌지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_report_store_fail_1180 import _준비
    from tests.test_send_fail_kis_1715 import _kis_desk

    보낸, 저장, _ = _준비(monkeypatch, 0)
    부름 = _kis_desk(monkeypatch)
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: True)
    monkeypatch.setattr(desk_job, "post_send_done", lambda *a, **k: True)
    assert daily.run("KR") == 0
    assert 보낸 == [] and 부름["kis"] == [] and 부름["desk"] == [] and 부름["paper"] == []


def test_이어_돌다_깨지면_오늘_리포트는_보냈다고_알린다() -> None:
    assert daily._실패제목(True) == daily.RESUME_FAIL_TITLE and "이미 보냈습니다" in daily.RESUME_FAIL_TITLE
    assert daily._실패제목(False) == "배치 실패"


def test_시간_초과_알림은_이어_받는다고_약속하지_않고_미국은_KIS_를_말하지_않는다() -> None:
    kr, us = daily.timeout_text(15, "KR"), daily.timeout_text(45, "US")
    assert "남은 예비 실행이 있으면" in kr and "이어 돕니다)" not in kr
    assert "운용 요약이 빠졌을 수" in us and "KIS모의투자 베끼기는 다시 내지 않습니다" in us


def test_이어_돌다_시세에서_깨지면_실패_알림_제목이_이미_보냈다고_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_report_store_fail_1180 import _준비
    from tests.test_send_fail_kis_1715 import _kis_desk

    _준비(monkeypatch, 0)
    _kis_desk(monkeypatch)
    알림: list = []
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: True)
    monkeypatch.setattr(desk_job, "post_send_done", lambda *a, **k: False)
    monkeypatch.setattr(daily, "collect_prices", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("시세 깨짐")))
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: 알림.append(k.get("title")))
    assert daily.run("KR") == 1
    assert 알림 == [daily.RESUME_FAIL_TITLE]
