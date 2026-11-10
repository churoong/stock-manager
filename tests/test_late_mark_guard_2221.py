"""개장 뒤 보낸 시장가 표시(`mark_late_sends`)도 곁다리 — 실패해도 비교·KIS 주문 보내기를 막지 않고 아침 점검에 말한다
(docs/infra.md 25.2221, 25.2220 교차검증 [권고])."""

from __future__ import annotations

import inspect

from batch.jobs import broker_mirror as job
from batch.services import desk


def test_비교줄_표시는_try_안에서_돌고_까닭을_적는다() -> None:
    글 = inspect.getsource(job.run)
    i = 글.index("mark_late_sends(비교줄)")
    앞 = 글[:i].rsplit("\n", 2)[-2]
    assert 앞.strip().startswith("try:"), 앞
    assert '기록["late_mark_error"]' in 글[i : i + 300]


def test_아침_점검이_표시_실패를_말한다() -> None:
    글 = " / ".join(x["text"] for x in desk.checks(halted=False, paper=None, broker={"late_mark_error": "KeyError"},
                                                  recon_bad=[], warnings=0))  # fmt: skip
    assert "KIS 비교 표본 표시 실패(KeyError)" in 글 and "개장 뒤 보낸 시장가" in 글
