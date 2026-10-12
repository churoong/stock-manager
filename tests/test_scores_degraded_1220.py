"""점수·신호가 0 으로 끝났지만 덜 된 채 계산한 것이 리포트 경고로 올라온다 (docs/infra.md 25.1220, 감사 재현)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from batch.jobs import daily
from tests.test_report_subjob_warnings import _문맥


def _기록(db, job: str, log: dict, started: str = "2026-10-08T00:00:01+00:00") -> None:  # noqa: ANN001
    db.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at, step_log) VALUES (?, 'KR', 'success', ?, ?)",
        [job, started, json.dumps(log, ensure_ascii=False)],
    )


def test_점수_신호의_덜_된_계산을_리포트에_올린다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    _기록(db, "scores", {"scored": 10, "sentiment_read_error": "x", "input_read_errors": ["배당 못 읽음", "지수"],
                         "setting_warnings": ["가중치 범위 밖"]})  # fmt: skip
    _기록(db, "signals", {"checks_error": "y", "unverifiable": ["005930 mid"]})
    _문맥(monkeypatch, db)
    got = daily._계산_저하("scores", "signals", "2026-10-08T00:00:00+00:00")
    assert any("감성 없이" in g for g in got)
    assert any("재료 2건" in g for g in got)
    assert any("가중치 설정 1건" in g for g in got)
    assert any("판정표를 적지 못했습니다" in g for g in got)
    assert any("근거표를 만들지 못한 1건" in g for g in got)
    assert not any("005930" in g or "배당 못 읽음" in g for g in got)  # 건수만 — 자세한 것은 실행 기록


def test_옛_기록이나_깨끗한_기록은_말하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    _기록(db, "scores", {"sentiment_read_error": "어제"}, started="2026-10-07T00:00:00+00:00")
    _기록(db, "signals", {"unverifiable": []})
    _문맥(monkeypatch, db)
    assert daily._계산_저하("scores", "signals", "2026-10-08T00:00:00+00:00") == []


def test_점수_신호_단계가_경고를_붙인다(monkeypatch: pytest.MonkeyPatch) -> None:
    부름: list = []
    monkeypatch.setattr(daily, "_건너뛴_까닭", lambda j, s: None)
    monkeypatch.setattr(daily, "_계산_저하", lambda a, b, s: 부름.append((a, b)) or ["점수: 감성 없이"])
    sc = SimpleNamespace(JOB_NAME="scores", run=lambda m, as_of=None: 0)
    sg = SimpleNamespace(JOB_NAME="signals", run=lambda m, as_of=None: 0)
    assert daily._scores_and_signals("KR", "2026-10-08", [], sc, sg) == ["점수: 감성 없이"]
    assert 부름 == [("scores", "signals")]
