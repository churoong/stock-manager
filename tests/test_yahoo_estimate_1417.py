"""야후가 스스로 추정이라 표시한 실적일은 날짜가 하나여도 추정 (docs/infra.md 25.1417)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from batch.jobs import earnings_calendar as job
from batch.sources import yfinance_src as yfs

초 = int(datetime(2026, 10, 30, 20, 0, tzinfo=UTC).timestamp())


class _Q:
    def __init__(self, raw: object, 터짐: bool = False) -> None:
        self.raw, self.터짐 = raw, 터짐

    def _fetch(self, modules: list) -> object:
        assert modules == ["calendarEvents"]
        if self.터짐:
            raise AttributeError("판이 바뀜")
        return self.raw


class _T:
    def __init__(self, q: _Q) -> None:
        self._quote = q


def _응답(earnings: dict) -> dict:
    return {"quoteSummary": {"result": [{"calendarEvents": {"earnings": earnings, "exDividendDate": 1}}]}}


def test_원시_응답의_추정_표시를_읽는다() -> None:
    cal, 표시 = yfs._raw_calendar(_T(_Q(_응답({"earningsDate": [초], "isEarningsDateEstimate": True}))))
    assert cal == {"Earnings Date": [date(2026, 10, 30)]} and 표시 is True
    # {"raw": …} 모양도
    cal, 표시 = yfs._raw_calendar(_T(_Q(_응답({"earningsDate": [{"raw": 초}], "isEarningsDateEstimate": False}))))
    assert yfs.parse_earnings_dates(cal) == ["2026-10-30"] and 표시 is False
    # 표시가 없으면 None — 예전대로
    assert yfs._raw_calendar(_T(_Q(_응답({"earningsDate": [초]}))))[1] is None


def test_원시_응답을_못_읽으면_물러난다() -> None:
    assert yfs._raw_calendar(_T(_Q(None, 터짐=True))) == (None, None)
    assert yfs._raw_calendar(_T(_Q(None))) == (None, None)
    # 빈 calendarEvents 는 빈 dict — "빈 응답은 모름"(25.601) 그대로
    assert yfs._raw_calendar(_T(_Q({"quoteSummary": {"result": [{"calendarEvents": {}}]}}))) == ({}, None)
    # 날짜 없이 배당만 왔으면 "답했는데 날짜 없음"
    cal, _ = yfs._raw_calendar(_T(_Q(_응답({}))))
    assert cal == {"Earnings Date": []} and yfs.parse_earnings_dates(cal) == []


def test_하나여도_야후_추정이면_확정이_아니다() -> None:
    [행] = job.yahoo_rows(3, ["2026-10-30"], "2026-10-15", "now", estimate=True)
    assert 행[3] == 0 and "야후 추정" in 행[4]
    assert job.yahoo_rows(3, ["2026-10-30"], "2026-10-15", "now", estimate=False)[0][3] == 1
    assert job.yahoo_rows(3, ["2026-10-30"], "2026-10-15", "now")[0][3] == 1  # 표시 없음 — 예전대로


def test_표시를_받은_수를_센다(monkeypatch) -> None:
    답 = {"A": (["2026-10-30"], True), "B": (["2026-10-31"], None), "C": ([], None)}
    monkeypatch.setattr(yfs, "fetch_earnings_dates",
                        lambda s: yfs.FetchResult(ok=True, data=답[s][0], extra={"estimate": 답[s][1]}))  # fmt: skip
    monkeypatch.setattr(yfs, "EARNINGS_INTERVAL", 0)
    rows, counts = job.collect_yahoo([(1, "A"), (2, "B"), (3, "C")], "2026-10-15", "now")
    assert (counts["estimate_true"], counts["estimate_false"], counts["estimate_missing"]) == (1, 0, 1)
    assert [r[3] for r in rows] == [0, 1]
