"""계좌 화면 이름 (docs/paper.md 1장, docs/infra.md 25.1889 — 2026-10-28 사용자 "각 가상계좌는 알파벳이 아닌 계좌특성을 이름으로")."""

from __future__ import annotations

from batch.services import paper as pp


def test_계좌마다_이름이_있고_겹치지_않는다() -> None:
    아는 = {"A", "B", "C", "D", "E", "G", "H", "R1", "R2", *pp.SEPARATE_IDS}
    assert 아는 <= set(pp.ACCOUNT_NAMES)
    assert len(set(pp.ACCOUNT_NAMES.values())) == len(pp.ACCOUNT_NAMES)
    assert pp.account_name("H") == "지주사" and pp.account_name("Z9") == "Z9"
