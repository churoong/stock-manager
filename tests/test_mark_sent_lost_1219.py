"""보낸 뒤 발송 시각을 못 적으면 — 새 연결로 한 번 더, 그래도 안 되면 경고로 partial (docs/infra.md 25.1219)."""

from __future__ import annotations

import pytest

from batch.core import client as client_mod
from batch.jobs import daily
from tests.test_report_store_fail_1180 import _가짜, _준비


def _닫힘(monkeypatch: pytest.MonkeyPatch) -> list:
    닫힘: list = []
    monkeypatch.setattr(daily.db, "finish_batch_run", lambda c, rid, **k: 닫힘.append(k))
    return 닫힘


def test_두_번_다_못_적으면_경고로_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    보낸, _, _ = _준비(monkeypatch, 0)
    닫힘 = _닫힘(monkeypatch)
    부름: list = []

    def 실패(c, rid, ids, sent_at=None):  # noqa: ANN001, ANN202
        부름.append(c)
        raise RuntimeError("Turso 시간 초과")

    monkeypatch.setattr(daily.reports, "mark_sent", 실패)
    monkeypatch.setattr(client_mod, "TursoClient", _가짜)
    assert daily.run("KR") == 0
    assert 보낸 and len(부름) == 2  # 처음 연결 + 새 연결
    assert 닫힘[-1]["status"] == "partial"
    assert daily.SENT_AT_LOST in 닫힘[-1]["step_log"]["warnings"]


def test_새_연결로_적으면_경고_없음(monkeypatch: pytest.MonkeyPatch) -> None:
    _준비(monkeypatch, 0)
    닫힘 = _닫힘(monkeypatch)
    부름: list = []

    def 처음만_실패(c, rid, ids, sent_at=None):  # noqa: ANN001, ANN202
        부름.append(c)
        if len(부름) == 1:
            raise RuntimeError("Turso 시간 초과")

    monkeypatch.setattr(daily.reports, "mark_sent", 처음만_실패)
    monkeypatch.setattr(client_mod, "TursoClient", _가짜)
    assert daily.run("KR") == 0
    assert len(부름) == 2
    assert daily.SENT_AT_LOST not in 닫힘[-1]["step_log"].get("warnings", [])
