"""실전 준비 목록 진도 (docs/paper.md 14장, docs/desk.md 6장, docs/infra.md 25.1513)."""

from __future__ import annotations

import re
from pathlib import Path

from batch.services import desk

뿌리 = Path(__file__).resolve().parent.parent


def test_readiness_matches_doc_table() -> None:
    """코드의 상태 표와 docs/paper.md 14장 진도 표가 같다 — 한쪽만 고치면 화면이 문서와 다르게 말한다"""
    글 = (뿌리 / "docs" / "paper.md").read_text(encoding="utf-8")
    절 = 글[글.index("**진도**(2026-10-16, 25.1513"):]
    표 = dict(re.findall(r"^\| ([^|]+?) \| (done|partial|rehearsal|todo|user) \|", 절, re.M))
    assert 표 == {label: status for _k, label, status, _n in desk.READINESS}
    assert len(desk.READINESS) == 7


def test_readiness_evidence() -> None:
    r = {x["key"]: x for x in desk.readiness(runs={"n": 40, "late": 3, "max_delay": 1800},
                                               kis=[{"n": 30, "unknown": 1, "rejected": 2}, {"n": 5}])}  # fmt: skip
    assert r["always_on"]["evidence"] == "최근 30일 일일 배치 40회 중 10분 넘게 늦음 3회 · 가장 늦음 30분"
    assert r["broker"]["evidence"] == "최근 28일 끝난 베낀 주문 35 · 모름 1 · 거부 2"
    assert r["audit"]["status"] == "done" and r["audit"]["evidence"] is None
    빈 = {x["key"]: x for x in desk.readiness(runs=None, kis=[])}
    assert 빈["always_on"]["evidence"] is None and 빈["broker"]["evidence"] is None
