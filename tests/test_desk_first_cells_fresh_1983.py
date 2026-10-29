"""25.1983 (결함 루프 10-29) — KIS모의 체결 기준 보정은 첫째(챔피언) 계좌의 체결 칸도 최근 줄만 쓴다.

도전자 칸(25.1517 검토 3)·관문 실현율(25.1511 검토 7)은 `KIS_RECENT_DAYS` 보다 오래된 줄을 뺐는데 첫째 칸만 빠져, 미국
베끼기를 끄거나 키를 빼도 마지막 28일 칸이 영영 모든 계좌의 보정에 들어갔다
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from batch.jobs import desk as job
from tests.test_paper_job_1445 import Client

칸 = {"cells": {"sell_market": {"days": 30, "excess": 0.8, "se": 0.1, "measured": True}}}


def _줄(c: Client, broker: str, as_of: str) -> None:
    c.conn.execute("INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source,"
                   " fetched_at, summary_json) VALUES (?, ?, 'H', '[]', '{}', ?, 't', ?)",
                   [broker, as_of, broker, json.dumps(칸)])  # fmt: skip


@pytest.mark.parametrize(("국내", "미국", "기대"), [
    ("2026-10-16", "2026-09-01", (True, False)),  # 미국 베끼기를 끈 지 오래 — 미국 칸은 안 쓴다
    ("2026-09-01", "2026-10-16", (False, True)),
    # 경계 — 최근 = 10-16 23:40 UTC − 7일의 KST 날짜 10-10. 그날 줄은 쓰고 하루 앞은 뺀다(25.1984)
    ("2026-10-10", "2026-10-09", (True, False)),
])  # fmt: skip
def test_오래된_첫째_줄_칸은_보정에_안_쓴다(monkeypatch: pytest.MonkeyPatch, 국내: str, 미국: str,
                                    기대: tuple[bool, bool]) -> None:  # fmt: skip
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    _줄(c, job.bmj.BROKER, 국내)
    _줄(c, job.bmj.BROKER_US, 미국)
    본: list[Any] = []
    monkeypatch.setattr(job, "_보정판", lambda client, 순위, 평가, 손익, h, 요약, 요약_us, *a: 본.append((요약, 요약_us)) or {})
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    assert [bool(x.get("cells")) for x in 본[0]] == list(기대)
    줄 = json.loads(c.conn.execute("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'").fetchone()[0])
    assert 줄["kis_account"]["as_of"] == 국내  # KIS 계좌 카드는 as_of 와 함께 그대로
