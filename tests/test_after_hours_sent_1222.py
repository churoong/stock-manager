"""시간외 알림: 보낸 뒤 발송 시각 기록이 실패해도 잡음을 풀지 않는다 — 장중 경로가 다시 보내지 않게 (docs/infra.md 25.1222)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from batch.jobs import kis_flows
from batch.notify import telegram
from batch.services import after_hours as ah


class _DB:
    def __init__(self, 기록_실패: int) -> None:
        self.alerts: dict[int, object] = {}
        self.남은실패 = 기록_실패

    def execute(self, sql, args=None):  # noqa: ANN001, ANN201
        if sql.startswith("SELECT p.stock_id"):
            return SimpleNamespace(rows=[(1, "005930", "가")])
        if sql.startswith("SELECT value FROM settings"):
            return SimpleNamespace(scalar=lambda: None)
        if sql.startswith("INSERT INTO alerts"):
            self.alerts[args[0]] = args[-1]
            return SimpleNamespace(affected_rows=1)
        if sql.startswith("UPDATE alerts SET sent_at = ?"):
            if self.남은실패 > 0:
                self.남은실패 -= 1
                raise RuntimeError("일시 오류")
            for k in self.alerts:
                self.alerts[k] = args[0]
            return SimpleNamespace(affected_rows=1)
        if sql.startswith("UPDATE alerts SET sent_at = NULL"):
            for k, v in self.alerts.items():
                if v == args[2]:
                    self.alerts[k] = None
            return SimpleNamespace(affected_rows=1)
        raise AssertionError(sql)

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *a: object) -> None: ...


def _돌림(monkeypatch: pytest.MonkeyPatch, db: _DB, 새연결: _DB) -> tuple[list, dict, dict]:
    보냄: list = []
    monkeypatch.setattr(telegram, "send", lambda t, *a, **k: 보냄.append(t) or [1])
    # 지금(KST)의 날짜 — 25.1846 이 하루보다 오래된 시간외를 거른다(예전 고정 10-12 는 그 뒤 날짜엔 걸렸다)
    monkeypatch.setattr(ah, "session_day", lambda now: now.astimezone(ah.KST).date())
    monkeypatch.setattr(ah, "quiet_state", lambda raw, now: (False, False))
    monkeypatch.setattr(ah, "spike_pct", lambda raw: 5.0)
    monkeypatch.setattr(ah, "hits", lambda 보유, 시세, 문턱: [{"stock_id": 1, "message": "가 +7%", "data": {}}])
    monkeypatch.setattr(kis_flows, "TursoClient", lambda: 새연결)
    failed: dict = {}
    out = kis_flows._after_hours(db, SimpleNamespace(after_hours=lambda c: {"x": 1}), date(2026, 10, 12), failed)
    return 보냄, out, failed


def test_기록이_끝내_실패해도_잡음을_풀지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _DB(기록_실패=1)
    보냄, out, failed = _돌림(monkeypatch, db, _DB(기록_실패=1))
    assert len(보냄) == 1 and out["sent"] == 1
    assert "시간외:발송" not in failed and "시간외:발송기록" in failed
    assert db.alerts[1] is not None  # NULL 이면 장중 경로가 다시 보낸다


def test_새_연결로_적으면_조용하다(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _DB(기록_실패=1)
    새것 = _DB(기록_실패=0)
    새것.alerts = db.alerts
    보냄, out, failed = _돌림(monkeypatch, db, 새것)
    assert len(보냄) == 1 and not failed
    assert not str(db.alerts[1]).startswith(kis_flows.CLAIM_PREFIX)  # 잡음이 보낸 시각으로 바뀌었다
