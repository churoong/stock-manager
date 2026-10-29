"""25.1967 (25.1963 교차검증 [필수]) — 관문 판정 시점 기록은 관문을 비운 실행 한 줄에 끊기지 않는다.

판 이력 읽기 실패·관문 계산 예외로 그날 줄의 관문이 None 이면, 예전엔 다음 실행이 그 줄을 읽어 앞관문 None →
판정 기록이 처음부터라 이미 지난 126 시점을 그날 칸으로 다시 판정했다(거짓 통과·통과 소실). 이제
(1) 이어받을 줄은 판정 시점 기록이 있는 가장 늦은 줄(오늘 줄 포함), (2) 관문을 비운 실행은 받았던 관문을
`gate_carry` 로 남긴다
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from batch.jobs import desk as job
from batch.services import desk
from tests.test_paper_job_1445 import Client

앞 = {"account": "B", "version": None, "looks": [{"n": 126, "at": 126, "date": "2026-10-15", "passed": False,
                                                 "failed": ["kis"]}]}  # fmt: skip


def _줄(c: Client, as_of: str, body: Any) -> None:
    c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, 't')",
                   [as_of, body if isinstance(body, str) else json.dumps(body)])  # fmt: skip


def _본(monkeypatch: pytest.MonkeyPatch, c: Client) -> list[Any]:
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    본: list[Any] = []
    원래 = desk.gate

    def 엿봄(*a: Any, **k: Any) -> dict[str, Any]:
        본.append(k.get("prev"))
        return 원래(*a, **k)

    monkeypatch.setattr(job.desk, "gate", 엿봄)
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    return 본


def test_관문을_비운_줄은_건너뛰고_그_앞_기록을_잇는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    _줄(c, "2026-10-14", {"gate": 앞, "markets": {}})
    _줄(c, "2026-10-15", {"gate": None, "gate_error": "x", "markets": {}})  # 25.1967 전 꼴 — 받았던 관문 없음
    _줄(c, "2026-10-16", "{깨진")  # 깨진 줄도 건너뛴다
    assert _본(monkeypatch, c) == [앞]


def test_관문을_비운_줄의_gate_carry_를_잇는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    _줄(c, "2026-10-14", {"gate": {"account": "B", "version": None, "looks": []}, "markets": {}})
    _줄(c, "2026-10-16", {"gate": None, "gate_carry": 앞, "markets": {}})
    assert _본(monkeypatch, c) == [앞]


def test_오늘_줄도_읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 날 두 번째 실행(국내 뒤 미국, 같은 as_of)은 오늘 이미 내린 판정을 잇는다 — `<=` 의 '='"""
    c = Client(accounts=None)
    _줄(c, "2026-10-16", {"gate": {"account": "B", "version": None, "looks": []}, "markets": {}})
    _줄(c, "2026-10-17", {"gate": 앞, "markets": {}})  # as_of 10-17(KST) — 오늘 줄
    _줄(c, "2026-10-18", {"gate": {"account": "B", "version": None, "looks": []}, "markets": {}})  # 뒤 날은 아니다
    assert _본(monkeypatch, c) == [앞]


def test_관문_계산이_깨지면_받았던_관문을_gate_carry_로_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    _줄(c, "2026-10-16", {"gate": 앞, "markets": {}})
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")

    def 깨짐(*a: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError("관문 계산 실패")

    monkeypatch.setattr(job.desk, "gate", 깨짐)
    out = job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                    session_date="2026-10-19", step_log={}, warnings=[])  # fmt: skip
    assert "관문 계산 실패" in out["gate_error"]
    줄 = json.loads(c.conn.execute("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'").fetchone()[0])
    assert 줄["gate"] is None and 줄["gate_carry"] == 앞
    # 다음 실행은 그 줄의 gate_carry 를 잇는다
    assert job._앞관문(줄) == 앞


def test_앞관문_고르기() -> None:
    assert job._앞관문({"gate": 앞, "gate_carry": None}) == 앞
    assert job._앞관문({"gate": None, "gate_carry": 앞}) == 앞
    assert job._앞관문({"gate": {"account": "B"}}) is None  # 판정 시점 기록 없는 옛 관문
    assert job._앞관문({}) is None


def test_판_이력을_못_읽은_실행도_받았던_관문을_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1970 (25.1967 교차검증 [권고]) — 판오류 갈래. 그날 `_관문판` 은 성공해 그 결과의 `gate_carry` 는 None 이므로
    판오류 갈래가 받았던 관문으로 덮지 않으면 같은 날 앞 실행이 오늘 내린 판정이 사라진다"""
    c = Client(accounts=None)
    _줄(c, "2026-10-17", {"gate": 앞, "markets": {}})  # 같은 날(as_of 10-17) 앞 실행이 적은 관문
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    monkeypatch.setattr(job, "_판이력", lambda *_a, **_k: ({}, "OperationalError: 표 없음"))
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    줄 = json.loads(c.conn.execute("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'").fetchone()[0])
    assert 줄["gate"] is None and 줄["gate_carry"] == 앞 and job._앞관문(줄) == 앞


def test_좋은_실행은_gate_carry_를_비운다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    _줄(c, "2026-10-17", {"gate": None, "gate_carry": 앞, "markets": {}})
    _본(monkeypatch, c)
    줄 = json.loads(c.conn.execute("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'").fetchone()[0])
    assert isinstance(줄["gate"], dict) and 줄["gate_carry"] is None


def test_looks_가_배열이_아닌_줄은_건너뛴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1970 (25.1967 교차검증 [참고]) — SQL 고르기와 `_앞관문` 의 규칙을 같게(배열만)"""
    c = Client(accounts=None)
    _줄(c, "2026-10-14", {"gate": 앞, "markets": {}})
    _줄(c, "2026-10-16", {"gate": {"account": "B", "looks": "깨짐"}, "markets": {}})
    assert _본(monkeypatch, c) == [앞]


def test_앞_관문_읽기가_깨지면_관문만_비우고_요약은_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1971 — 이어받기 SELECT(JSON 함수)가 원격에서 깨져도 운용 요약 전체를 잃지 않는다. 앞 기록 없이 판정하면
    지난 시점을 다시 판정하므로 관문만 비우고 까닭을 적는다. 다음 실행은 기록 있는 줄을 다시 찾는다"""
    c = Client(accounts=None)
    _줄(c, "2026-10-16", {"gate": 앞, "markets": {}})
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    monkeypatch.setattr(job, "DESK_UPTO_SQL", "SELECT no_such_fn(summary_json) FROM paper_desk WHERE as_of <= ?")
    out = job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                    session_date="2026-10-19", step_log={}, warnings=[])  # fmt: skip
    assert "판정 시점 기록을 못 읽어 관문 안 냄" in out["gate_error"]
    줄 = json.loads(c.conn.execute("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'").fetchone()[0])
    assert 줄["gate"] is None and 줄["gate_carry"] is None and "leaderboard" in 줄 and "champion" in 줄
    # 다음 실행(원래 SQL)은 10-16 의 기록을 잇는다
    monkeypatch.undo()
    assert _본(monkeypatch, c) == [앞]


def test_앞_관문_읽기가_깨져도_오늘_줄의_판정_기록은_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1973 (25.1971 교차검증 [필수]) — 같은 as_of 의 앞 실행(국내)이 오늘 줄에 126 판정을 적은 뒤 둘째 실행(미국)의
    이어받기 읽기가 깨지면, 예전엔 merge 가 관문·`gate_carry` 를 None 으로 덮어 그 판정이 사라졌고 다음 실행은 어제 줄을
    이어받아 126 을 다시 판정했다. 이제 오늘 줄의 판정 기록을 `gate_carry` 로 남긴다"""
    c = Client(accounts=None)
    _줄(c, "2026-10-14", {"gate": {"account": "B", "version": None, "looks": []}, "markets": {}})
    _줄(c, "2026-10-17", {"gate": 앞, "markets": {}})  # 오늘(as_of 10-17) 앞 실행의 판정
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    monkeypatch.setattr(job, "DESK_UPTO_SQL", "SELECT no_such_fn(summary_json) FROM paper_desk WHERE as_of <= ?")
    out = job.write(c, "US", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                    session_date="2026-10-19", step_log={}, warnings=[])  # fmt: skip
    assert "판정 시점 기록을 못 읽어 관문 안 냄" in out["gate_error"]
    줄 = json.loads(c.conn.execute("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'").fetchone()[0])
    assert 줄["gate"] is None and 줄["gate_carry"] == 앞
    monkeypatch.undo()
    assert _본(monkeypatch, c) == [앞]


def test_앞_관문_읽기가_한도로_깨지면_올린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1973 — Turso 한도 오류는 삼키지 않는다(이 파일의 다른 곁다리와 같게)"""
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    monkeypatch.setattr(job.db, "quota_reason", lambda exc: "한도")
    monkeypatch.setattr(job, "DESK_UPTO_SQL", "SELECT no_such_fn(summary_json) FROM paper_desk WHERE as_of <= ?")
    with pytest.raises(Exception, match="no_such_fn"):
        job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                  session_date="2026-10-19", step_log={}, warnings=[])  # fmt: skip
