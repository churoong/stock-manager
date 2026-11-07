"""예정된 실험 계좌 id 가 이미 쓰인 계좌 id 와 겹치지 않는다 (docs/infra.md 25.2177).

37회차(docs/factors.md 12.40)가 보류해 둔 "목표가 상향 계좌 X" 의 id 를 2026-11-06 ETF 전술 국내ETF(0094)가 먼저 썼다. 같은 id 로
열면 마이그레이션의 `INSERT OR IGNORE` 가 조용히 무시하고, 상황실 사전 등록 `desk.PREREG (X,1)` 이 다른 가설을 가리킨다.
예정 id 는 여기 한 곳에 적고 — 계좌를 실제로 열 때 이 표에서 빼고 마이그레이션·`ACCOUNT_NAMES` 에 넣는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from batch.services import desk
from batch.services import paper as pp

ROOT = Path(__file__).resolve().parent.parent

#: 아직 열지 않은 실험 계좌 — id → 어느 회차의 무엇(원장 번호)
PLANNED = {
    "P": "37회차 B″ 실적 발표 반응 계좌 (원장 #39)",
    "W": "37회차 C 목표가 상향 계좌 (원장 #40 — 원문은 X, 25.2177 에서 W 로)",
}


def _마이그레이션_계좌() -> set[str]:
    ids: set[str] = set()
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        글 = path.read_text(encoding="utf-8")
        for 문 in re.findall(r"INSERT(?: OR IGNORE)? INTO paper_accounts[^;]*;", 글, flags=re.S):
            ids |= set(re.findall(r"\(\s*'([A-Z][A-Z0-9]?)'", 문))
    return ids


def test_마이그레이션이_여는_계좌를_읽는다() -> None:
    """빈 집합이면 아래 검사가 공짜로 통과한다"""
    assert {"A", "B", "H", "R1", "S", "T", "X", "Y"} <= _마이그레이션_계좌()


def test_예정_id_는_이미_쓰인_id_와_겹치지_않는다() -> None:
    쓰인 = _마이그레이션_계좌() | set(pp.ACCOUNT_NAMES) | {a for a, _판 in desk.PREREG}
    assert not set(PLANNED) & 쓰인, sorted(set(PLANNED) & 쓰인)


def test_원장과_회차_문단이_새_id_를_적는다() -> None:
    글 = (ROOT / "docs" / "factors.md").read_text(encoding="utf-8")
    줄 = next(x for x in 글.splitlines() if x.startswith("| 40 | 실험 계좌"))
    assert "id 는 W" in 줄 and "25.2177" in 줄
    assert "**C. 목표가 상향 계좌 X** (판 1, 국내 — **열 때 id 는 W**" in 글
    assert next(x for x in 글.splitlines() if x.startswith("| 39 | 실험 계좌 P"))
