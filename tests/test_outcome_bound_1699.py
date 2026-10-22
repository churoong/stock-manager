"""결정 원장 성과 채우기 — 위쪽은 실제로 받은 시세 날짜까지, 밀림 경고는 경계 결정일로 (docs/infra.md 25.1699).

25.1692 교차검증 [권고] 1·2.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client, _시세, _하루, client  # noqa: F401 — 픽스처

_INS = ("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
        " created_at) VALUES ('A', 'KR', ?, ?, 'skip', 'x', 10000, ?, 't')")  # fmt: skip


def _지수(c: Client, days: list[str]) -> None:
    for i, d in enumerate(days):
        c.conn.execute("INSERT OR IGNORE INTO index_prices (index_code, date, close, source, fetched_at)"
                       " VALUES ('KOSPI', ?, ?, 't', 't')", [d, 2000 + i])  # fmt: skip


def test_위쪽은_받은_시세_날짜까지(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """10-15 낮 손 실행인데 시세는 10-13 까지(한국 날짜의 앞 장 10-14 종가가 아직 없음). 10-06 결정의 5번째 장은 10-14 라
    아직 못 찬다 — 이것이 상한(1)을 먼저 먹어 10-02 결정이 안 읽혔다. 받은 시세 날짜로 위쪽을 끊으면 10-02 가 찬다"""
    c = client
    monkeypatch.setattr(job, "OUTCOME_BATCH", 1)
    for d in ("2026-10-02", "2026-10-06"):
        c.conn.execute(_INS, [d, 1, d])
    날 = ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13"]
    for i, d in enumerate(날):
        _시세(c, d, 10_000 + i * 100, 10_000 + i * 100)
    _지수(c, 날)
    job.fill_outcomes(c, "KR", "2026-10-15", "now")  # 받은 날짜를 모르면 예전대로 — 10-06 이 자리를 먹는다
    assert c.q("SELECT decided_on FROM paper_decisions WHERE ret_5d IS NOT NULL") == []
    기록 = job.fill_outcomes(c, "KR", "2026-10-15", "now", "2026-10-13")
    assert c.q("SELECT decided_on FROM paper_decisions WHERE ret_5d IS NOT NULL") == [("2026-10-02",)]
    assert 기록["filled"] == 1


def test_가상모의투자_단계가_받은_시세_날짜를_넘긴다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    받음: list[Any] = []

    def 가짜(*a: Any, **k: Any) -> dict[str, int]:
        받음.append((a, k))
        return {"pending_read": 0, "filled": 0, "gave_up": 0, "full_bands": 0, "close_backlog": 0}

    monkeypatch.setattr(job, "fill_outcomes", 가짜)
    _하루(client, monkeypatch, "2026-10-14")
    assert 받음 and 받음[0][0][4] == "2026-10-14"


@pytest.mark.parametrize("순서", [(1, 3), (3, 1)])
def test_같은_결정일에_끊긴_행과_찰_행이_섞이면_밀림(client: Client, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                     순서: tuple[int, int]) -> None:  # fmt: skip
    """상한 2 — 10-02 의 두 행(3 번 종목은 시세 끊김)을 읽고 10-01 행은 못 읽었다. 경계 결정일(10-02)에 찬 행이 있으니
    그 날·그 너머에 찰 행이 남았다. 마지막 한 행만 보던 때는 그 행이 끊긴 행이면 경고가 빠졌다(같은 날 안 순서는 DB 가
    정한다 — 두 순서 모두)"""
    c = client
    monkeypatch.setattr(job, "OUTCOME_BATCH", 2)
    c.conn.execute(_INS, ["2026-10-01", 1, "2026-10-01"])
    for sid in 순서:
        c.conn.execute(_INS, ["2026-10-02", sid, "2026-10-02"])
    날 = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12"]
    for i, d in enumerate(날):
        _시세(c, d, 10_000 + i * 100, 10_000 + i * 100)
    _지수(c, 날)
    기록 = job.fill_outcomes(c, "KR", "2026-10-15", "now", "2026-10-14")
    assert 기록["filled"] == 1 and 기록["full_bands"] == 1


def test_포기_구간도_경계_결정일로_센다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """포기 구간(200일 넘음, 오래된 순) 상한 2 — 3-02·3-03 끊긴 행을 닫고 3-04 찰 행이 못 읽혔다. 닫기만 밀린 것도
    밀림이다: 정지가 길면 끊긴 행 뒤에서 밀려 200일까지 온 찰 행이 그 뒤에 남는다(25.1699 교차검증 [권고] 2 — 25.1699 는
    이것을 빼 경고 없이 늦었다, 25.1703)"""
    c = client
    monkeypatch.setattr(job, "OUTCOME_OLD_BATCH", 2)
    for d, sid in (("2026-03-02", 3), ("2026-03-03", 3), ("2026-03-04", 1)):
        c.conn.execute(_INS, [d, sid, d])
    기록 = job.fill_outcomes(c, "KR", "2026-10-15", "now", "2026-10-14")
    assert (기록["gave_up"], 기록["full_bands"]) == (2, 1)
    assert c.q("SELECT ret_5d FROM paper_decisions WHERE decided_on = '2026-03-04'") == [(None,)]  # 다음 배치


def test_경계_날_결정은_그날_찬다(client: Client) -> None:  # noqa: F811
    """10-15 아침, 시세 10-14 까지 — 10-06 결정의 5번째 장이 바로 10-14 라 오늘 찬다. 위쪽을 한 장 더 보수적으로
    잡으면(교차검증 변이 `range(n+1)`) 하루씩 늦는데 앞 시험들은 그것을 못 잡았다"""
    c = client
    c.conn.execute(_INS, ["2026-10-06", 1, "2026-10-06"])
    날 = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14"]
    for i, d in enumerate(날):
        _시세(c, d, 10_000 + i * 100, 10_000 + i * 100)
    _지수(c, 날)
    assert job.fill_outcomes(c, "KR", "2026-10-15", "now", "2026-10-14")["filled"] == 1


def test_기준가_없어_닫은_경계_행도_처리로_센다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """5일 구간 상한 1 — 경계 행이 기준 종가가 없어 바로 닫혔다(성과를 적지 않은 처리). 그 뒤에 찰 행이 남았으니 밀림"""
    c = client
    monkeypatch.setattr(job, "OUTCOME_BATCH", 1)
    c.conn.execute(_INS, ["2026-10-02", 1, "2026-10-02"])
    c.conn.execute("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, created_at)"
                   " VALUES ('A', 'KR', '2026-10-05', 1, 'skip', 'x', 't')")  # fmt: skip
    기록 = job.fill_outcomes(c, "KR", "2026-10-15", "now", "2026-10-14")
    assert (기록["gave_up"], 기록["filled"], 기록["full_bands"]) == (1, 0, 1)
