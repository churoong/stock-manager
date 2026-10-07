"""KIS 휴장일 조회로 우리 달력을 대조한다 (docs/infra.md 25.985)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from batch.core import calendar as cal
from batch.jobs import daily
from batch.sources import kis

응답 = {"rt_cd": "0", "msg_cd": "KIOK0500", "output": [
    {"bass_dt": "20261007", "opnd_yn": "Y"},
    {"bass_dt": "20261008", "opnd_yn": "Y"},
    {"bass_dt": "20261009", "opnd_yn": "N"},  # 한글날
    {"bass_dt": "20261010", "opnd_yn": "N"},
]}  # fmt: skip


def test_응답을_날짜별_개장_여부로() -> None:
    got = kis.parse_holidays(응답)
    assert got[0] == kis.DayStatus(date(2026, 10, 7), True) and got[2] == kis.DayStatus(date(2026, 10, 9), False)
    with pytest.raises(kis.KisFailed):
        kis.parse_holidays({"rt_cd": "1", "msg_cd": "EGW00123"})


def test_같으면_아무_말_없고_다르면_그날을_말한다() -> None:
    오늘 = date(2026, 10, 7)
    assert cal.compare_with_kis("KR", kis.parse_holidays(응답), 오늘) == []
    임시공휴일 = [kis.DayStatus(date(2026, 10, 8), False)]
    assert cal.compare_with_kis("KR", 임시공휴일, 오늘) == ["2026-10-08 우리 달력 개장 · KIS 휴장"]


def test_창_밖의_날은_보지_않는다() -> None:
    먼날 = [kis.DayStatus(date(2026, 10, 7) + timedelta(days=cal.KIS_COMPARE_DAYS), False)]
    assert cal.compare_with_kis("KR", 먼날, date(2026, 10, 7)) == []


class _Client:
    def __init__(self, token_row=None) -> None:
        self.row = token_row
        self.writes: list = []

    def execute(self, sql, args=None):
        class R:
            def __init__(self, rows):
                self._rows = rows

            def dicts(self):
                return self._rows

        if sql.startswith("SELECT"):
            return R([self.row] if self.row else [])
        self.writes.append((sql, args))
        return R([])


def test_토큰은_DB_것을_쓰고_곧_끝나면_새로(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    now = datetime(2026, 10, 7, 23, 30, tzinfo=UTC)
    넉넉 = _Client({"token": "db", "expires_at": (now + timedelta(hours=5)).isoformat()})
    assert kis.access_token(넉넉, now) == ("db", False)

    class Resp:
        status_code = 200

        def json(self):
            return {"access_token": "new", "expires_in": 86400}

    monkeypatch.setattr(kis.requests, "post", lambda *a, **k: Resp())
    곧 = _Client({"token": "db", "expires_at": (now + timedelta(minutes=30)).isoformat()})
    assert kis.access_token(곧, now) == ("new", True)
    assert 곧.writes and 곧.writes[0][1][1] == "new"


def test_키가_없으면_조용히_실패하면_한_줄(monkeypatch) -> None:
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    assert daily._kis_calendar_check(_Client(), date(2026, 10, 7)) == []
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setattr(kis, "holidays", lambda c, d: (_ for _ in ()).throw(kis.KisFailed("KIS 토큰 HTTP 403")))
    [줄] = daily._kis_calendar_check(_Client(), date(2026, 10, 7))
    assert "대조를 못 했습니다" in 줄 and "403" in 줄


def test_다르면_경고(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setattr(kis, "holidays", lambda c, d: ([kis.DayStatus(date(2026, 10, 8), False)], 1))
    monkeypatch.setattr(daily.db, "record_api_call", lambda *a, **k: None)
    [줄] = daily._kis_calendar_check(_Client(), date(2026, 10, 7))
    assert "KIS 공식 조회와 다릅니다" in 줄 and "2026-10-08" in 줄 and "EXTRA_HOLIDAYS" in 줄
