"""미국 1부 종목의 신호 기준일 뒤 SEC 공시와 8-K 중대 항목 기록 (docs/infra.md 25.1429, docs/factors.md 12.30 P2·P3)."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from batch.jobs import daily
from batch.sources import sec_edgar as se
from tests.test_corp_events_989 import _Client

오늘 = date(2026, 10, 15)


def _응답(rows: list[tuple[str, str, str]], n_extra: int = 0) -> dict:
    forms = [f for f, _, _ in rows] + ["4"] * n_extra
    filed = [d for _, d, _ in rows] + ["2026-10-01"] * n_extra
    items = [i for _, _, i in rows] + [""] * n_extra
    return {"filings": {"recent": {"form": forms, "filingDate": filed, "items": items}}}


def test_접수일로만_가르고_정정본은_뺀다() -> None:
    r = se.post_close_and_items(_응답([
        ("8-K", "2026-10-15", "2.02,9.01"), ("10-Q", "2026-10-16", ""), ("8-K/A", "2026-10-16", "5.02"),
        ("8-K", "2026-10-14", "5.02"), ("4", "2026-10-16", ""), ("8-K", "2026-03-02", "4.02"),
        ("8-K", "2025-09-01", "3.01"),  # 1년 밖
    ]), "2026-10-14", 오늘)  # fmt: skip
    assert r["after"] == [("2026-10-15", "8-K"), ("2026-10-16", "10-Q")]
    assert r["same_day"] == 1 and r["material"] == [("4.02", "2026-03-02")] and r["covered"] is True


def test_1000건을_채웠는데_1년을_못_덮으면_모름() -> None:
    r = se.post_close_and_items(_응답([], n_extra=se.RECENT_CAP), "2026-10-14", 오늘)
    assert r["covered"] is False


class _Sec:
    calls = 0

    def __init__(self, today: date) -> None:
        pass

    def ticker_map(self) -> dict[str, str]:
        return {"BBB": "0000000002"}

    def submissions(self, cik: str) -> dict:
        _Sec.calls += 1
        return {"0000000001": _응답([("8-K", "2026-10-15", "2.02"), ("8-K", "2026-10-16", "")]),
                "0000000002": _응답([("8-K", "2026-05-01", "3.01")])}[cik]  # fmt: skip


def test_사실_줄과_중대_항목_기록(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily.config, "SETTINGS", dataclasses.replace(daily.config.SETTINGS, offline_mode=False))
    monkeypatch.setattr(se, "SecClient", _Sec)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, sec_cik TEXT)")
    con.execute("INSERT INTO stocks VALUES (1, '0000000001'), (2, NULL)")
    picks = [SimpleNamespace(stock_id=1, name="에이", ticker="AAA", as_of_date="2026-10-14"),
             SimpleNamespace(stock_id=2, name="비", ticker="BBB", as_of_date="2026-10-14")]  # fmt: skip
    기록: dict = {}
    중대: dict = {}
    lines = daily._sec_post_close_lines(_Client(con), picks, 오늘, [], 기록, 중대)
    assert lines[1:] == ["· 에이(AAA) 8-K 외 1건 (10-15 접수, 미국 동부 날짜) — 신호는 10-14 종가로 계산"]
    assert 기록["after"] == 1 and 기록["unknown"] == 0
    # CIK 가 없던 비는 티커표로 찾았고, 3.01 은 리포트 줄이 아니라 기록에만
    assert 중대 == {"picks": 2, "flagged": 1, "unknown": 0, "by_item": {"3.01": 1}}
    assert "3.01" not in "\n".join(lines)


def test_SEC_를_못_쓰면_절만_빠진다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily.config, "SETTINGS", dataclasses.replace(daily.config.SETTINGS, offline_mode=False))

    def 없음(today: date) -> None:
        raise RuntimeError("SEC_USER_AGENT 에 연락처(이메일)가 없습니다")

    monkeypatch.setattr(se, "SecClient", 없음)
    기록: dict = {}
    picks = [SimpleNamespace(stock_id=1, name="에이", ticker="AAA", as_of_date="2026-10-14")]
    assert daily._sec_post_close_lines(_Client(sqlite3.connect(":memory:")), picks, 오늘, [], 기록) == []
    assert 기록["skipped"] == "SEC_USER_AGENT 없음"
