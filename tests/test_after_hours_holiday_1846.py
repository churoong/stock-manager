"""시간외 알림 — 금요일 실행이 자정을 넘겨 토요일 새벽에 돌아도 금요일 시간외를 본다. 마지막 시간외가 하루보다 오래면
보지 않는다 (docs/infra.md 25.1846, 결함 루프 [참고]).

예전엔 바깥에서 `cal.is_session("KR", 오늘)` 로 걸러, 안의 `session_day`(25.1012 — 마감된 가장 최근 시간외의 거래일)와
어긋났다: GitHub 예약이 늦어 토요일 01:25 KST 에 돌면 "휴장일" 로 건너뛰어 그날 보유 종목의 시간외 ±5% 알림이 빠졌다.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from batch.jobs import kis_flows as job


class _Rs:
    def __init__(self, rows=None, scalar=None):  # noqa: ANN001
        self.rows, self._s, self.affected_rows = rows or [], scalar, 1

    def scalar(self):  # noqa: ANN201
        return self._s


def _돌림(monkeypatch: pytest.MonkeyPatch, 지금: datetime) -> tuple[dict, list]:
    from batch.notify import telegram

    넣음: list[list] = []

    class Client:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            if "FROM positions" in sql:
                return _Rs(rows=[(1, "005930", "삼성전자")])
            if "quiet_hours" in sql:
                return _Rs(scalar='{"enabled": false}')
            if sql.startswith("INSERT INTO alerts"):
                넣음.append(args)
            return _Rs()

    class QC:
        def after_hours(self, code):  # noqa: ANN001, ANN201
            return {"price": 70000.0, "change_pct": -8.0, "volume": 10}

    class 고정(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return 지금

    monkeypatch.setattr(job, "datetime", 고정)
    monkeypatch.setattr(telegram, "send", lambda *a, **k: None)
    return job._after_hours(Client(), QC(), None, {}), 넣음  # type: ignore[arg-type]


def test_토요일_새벽_실행은_금요일_시간외를_본다(monkeypatch: pytest.MonkeyPatch) -> None:
    out, 넣음 = _돌림(monkeypatch, datetime(2026, 10, 30, 16, 25, tzinfo=UTC))  # 토 01:25 KST
    assert "skipped" not in out and 넣음 and 넣음[0][1] == "2026-10-30"


def test_마지막_시간외가_하루보다_오래면_보지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    out, 넣음 = _돌림(monkeypatch, datetime(2026, 11, 1, 11, 0, tzinfo=UTC))  # 일 20:00 KST — 금요일 것은 이틀 전
    assert "휴장일" in out["skipped"] and 넣음 == []


def test_바깥에서_오늘_장_여부로_거르지_않는다() -> None:
    # 줄바꿈해 되살려도 잡히게 — run 에 오늘 장 여부 검사가 아예 없다(25.1847, 25.1846 교차검증 [권고])
    assert "is_session" not in inspect.getsource(job.run)
