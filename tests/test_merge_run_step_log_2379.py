"""닫힌 실행 기록에 칸만 덧쓴다 (docs/infra.md 25.2379, 25.2377 교차검증 [필수]).

25.2377 의 발송 실패 경로가 KIS·요약 뒤 `finish_batch_run` 을 한 번 더 불러, 첫 닫기가 붙인 시세 손댐(`prices_touched` —
시세 사본이 그날 고친 범위를 따라가는 유일한 기록, 25.888)·실행 지문(25.2277)·읽은 행 수(25.885)를 통째로 갈아 끼웠다.
"""

from __future__ import annotations

import inspect
import json

import pytest

from batch.core import db
from batch.jobs import daily
from tests.test_portfolio_job import MemClient


def _닫힌_실행(monkeypatch: pytest.MonkeyPatch) -> tuple[MemClient, int]:
    monkeypatch.setattr(db, "_열린_실행", [])
    monkeypatch.setattr(db, "_읽기_시작", {})
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "_마지막_적은_손댐", [None])
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    rid = db.start_batch_run(mem, job_name="daily_kr", market="KR", trade_date="2026-11-25")  # type: ignore[arg-type]
    db.note_prices_touched([(1, "2026-11-25")])
    db.persist_prices_touched(mem)  # type: ignore[arg-type]
    db.finish_batch_run(mem, rid, status="failed", step_log={"sent": False, "paper": {}}, error_text="발송 실패: x")  # type: ignore[arg-type]
    return mem, rid


def _칸(mem: MemClient, rid: int) -> dict:
    return json.loads(mem.conn.execute("SELECT step_log FROM batch_runs WHERE id = ?", [rid]).fetchone()[0])


def test_덧써도_첫_닫기의_칸이_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem, rid = _닫힌_실행(monkeypatch)
    앞 = _칸(mem, rid)
    assert {db.PRICES_TOUCHED_KEY, db.RUN_FINGERPRINT_KEY, db.ROWS_READ_KEY} <= set(앞)
    db.merge_run_step_log(mem, rid, {"broker": {"sent": {"accepted": 1}}, "desk": {"checks": 2}, "broker2": None})  # type: ignore[arg-type]
    뒤 = _칸(mem, rid)
    assert 뒤[db.PRICES_TOUCHED_KEY] == 앞[db.PRICES_TOUCHED_KEY] and 뒤[db.RUN_FINGERPRINT_KEY] == 앞[db.RUN_FINGERPRINT_KEY]
    assert 뒤["broker"] == {"sent": {"accepted": 1}} and 뒤["desk"] == {"checks": 2} and "broker2" not in 뒤
    assert 뒤["sent"] is False and mem.conn.execute("SELECT status FROM batch_runs WHERE id = ?", [rid]).fetchone()[0] == "failed"


def test_두번_닫으면_지워졌다(monkeypatch: pytest.MonkeyPatch) -> None:
    """고치기 전 꼴의 재현 — 왜 덧쓰기여야 하나(finish_batch_run 을 다시 부르면 첫 닫기의 칸이 사라진다)"""
    mem, rid = _닫힌_실행(monkeypatch)
    db.finish_batch_run(mem, rid, status="failed", step_log={"sent": False, "broker": {}}, error_text="발송 실패: x")  # type: ignore[arg-type]
    assert db.PRICES_TOUCHED_KEY not in _칸(mem, rid)


def test_빈_칸_깨진_기록(monkeypatch: pytest.MonkeyPatch) -> None:
    mem, rid = _닫힌_실행(monkeypatch)
    db.merge_run_step_log(mem, rid, {"broker": None})  # type: ignore[arg-type]  # 실을 것이 없으면 쓰지 않는다
    mem.conn.execute("UPDATE batch_runs SET step_log = '{' WHERE id = ?", [rid])
    db.merge_run_step_log(mem, rid, {"broker": {}})  # type: ignore[arg-type]
    assert mem.conn.execute("SELECT step_log FROM batch_runs WHERE id = ?", [rid]).fetchone()[0] == "{"  # 옛 글 그대로
    mem.conn.execute("UPDATE batch_runs SET step_log = NULL WHERE id = ?", [rid])
    db.merge_run_step_log(mem, rid, {"broker": {"a": 1}})  # type: ignore[arg-type]
    assert _칸(mem, rid) == {"broker": {"a": 1}}


def test_발송_실패_경로는_다시_닫지_않는다() -> None:
    src = inspect.getsource(daily.run)
    블록 = src[src.index("return _send_failed(client, run_id, step_log, market, name, exc, dry_run)"):]
    블록 = 블록[: 블록.index("_kis와_요약()\n        try:")]
    assert "db.merge_run_step_log(" in 블록 and "db.finish_batch_run(" not in 블록  # 주석의 이름은 세지 않는다
