"""계좌 화면 이름 (docs/paper.md 1장, docs/infra.md 25.1889 — 2026-10-28 사용자 "각 가상계좌는 알파벳이 아닌 계좌특성을 이름으로")."""

from __future__ import annotations

from batch.services import paper as pp


def test_계좌마다_이름이_있고_겹치지_않는다() -> None:
    아는 = {"A", "B", "C", "D", "E", "G", "H", "R1", "R2", *pp.SEPARATE_IDS}
    assert 아는 <= set(pp.ACCOUNT_NAMES)
    assert len(set(pp.ACCOUNT_NAMES.values())) == len(pp.ACCOUNT_NAMES)
    assert pp.account_name("H") == "지주사" and pp.account_name("Z9") == "Z9"


def test_마이그레이션의_계좌는_모두_화면_이름이_있다() -> None:
    """손으로 적은 목록이 아니라 마이그레이션이 만든 계좌 행에서 — 새 계좌를 열고 이름을 빠뜨리면 알파벳이 그대로
    화면에 나간다(25.1895, 25.1890 교차검증 [권고])."""
    from tests.test_paper_job_1445 import Client

    c = Client(accounts=None)
    ids = {r[0] for r in c.q("SELECT id FROM paper_accounts")}
    assert ids >= {"A", "S", "T"}
    assert ids <= set(pp.ACCOUNT_NAMES), ids - set(pp.ACCOUNT_NAMES)
