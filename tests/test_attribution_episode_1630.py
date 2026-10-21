"""성과 분해 — 견줄 skip 을 거른 뒤 에피소드로 묶는다 (docs/infra.md 25.1630, docs/paper.md 10장).

예전엔 묶은 뒤 걸러, 에피소드 첫 줄이 견주지 않는 skip("판 뒤"·이미 든 종목)이면 28일 안에 다시 새 후보가 된 같은 종목의
skip 까지 "안 낸 새 후보" 에서 빠졌다 — 판 뒤 다시 산 줄은 buy 라 남아 선정 비교가 한쪽으로 기울었다.
"""

from __future__ import annotations

import json
from typing import Any

from batch.services import paper as pp


def _d(sid: int, day: str, kind: str, reason: str, ret: float, held: bool | None = None) -> dict[str, Any]:
    return {"stock_id": sid, "market": "KR", "decided_on": day, "kind": kind, "reason": reason, "ret_20d": ret,
            "bench_20d": 0.0, "detail_json": json.dumps({} if held is None else {"held": held})}  # fmt: skip


def test_판_뒤_skip_이_먼저_와도_다시_새_후보가_된_skip_은_센다() -> None:
    rows = [
        _d(1, "2026-03-02", "skip", "판 뒤 5거래일 안", 1.0, held=False),
        _d(1, "2026-03-12", "skip", "투자 여력 부족", 9.0, held=False),
        _d(2, "2026-03-12", "skip", "투자 여력 부족", -3.0, held=False),
        _d(3, "2026-03-12", "buy", "2부 배분", 2.0, held=False),
    ]
    a = pp.attribution(rows, [])
    assert a["skipped"]["n"] == 2 and a["skipped"]["avg"] == 3.0
    # 까닭별 칸도 까닭마다 따로 — "투자 여력 부족" 둘, "판 뒤" 하나
    assert a["skip_by_reason"]["투자 여력 부족"]["n"] == 2 and a["skip_by_reason"]["판 뒤 5거래일 안"]["n"] == 1


def test_이미_든_종목_skip_이_먼저_와도_판_뒤_새_후보는_센다() -> None:
    rows = [
        _d(1, "2026-03-02", "skip", "투자 여력 부족", 1.0, held=True),
        _d(1, "2026-03-20", "skip", "투자 여력 부족", 5.0, held=False),
    ]
    assert pp.attribution(rows, [])["skipped"]["n"] == 1


def test_견줄_skip_은_여전히_28일에_한_번() -> None:
    rows = [_d(1, "2026-03-02", "skip", "투자 여력 부족", 1.0, held=False),
            _d(1, "2026-03-10", "skip", "섹터 상한 도달", 7.0, held=False),
            _d(1, "2026-04-06", "skip", "투자 여력 부족", 3.0, held=False)]  # fmt: skip
    a = pp.attribution(rows, [])
    assert a["skipped"]["n"] == 2 and a["skipped"]["avg"] == 2.0  # 3-10 은 같은 에피소드, 4-06 은 새 에피소드
    assert a["skip_by_reason"]["섹터 상한 도달"]["n"] == 1


def test_까닭별_칸도_같은_종목은_28일에_한_번() -> None:
    """교차검증 [권고] 2 — 까닭별 칸의 묶기가 빠지면 날마다 한 건씩 세여 겹친 표본(25.1453)이 되살아난다."""
    rows = [_d(1, "2026-03-02", "skip", "투자 여력 부족", 1.0, held=False),
            _d(1, "2026-03-05", "skip", "투자 여력 부족", 2.0, held=False)]  # fmt: skip
    assert pp.attribution(rows, [])["skip_by_reason"]["투자 여력 부족"]["n"] == 1


def test_까닭별_상위_5는_입력_순서와_무관() -> None:
    """교차검증 [권고] 1 — n 이 같은 까닭이 여럿이면 이름 순. 결정 원장 질의엔 순서가 없다."""
    import random

    rows = [_d(i, "2026-03-02", "skip", f"까닭{i}", float(i), held=False) for i in range(1, 8)]
    본 = set()
    for seed in range(10):
        섞음 = rows[:]
        random.Random(seed).shuffle(섞음)
        본.add(tuple(pp.attribution(섞음, [])["skip_by_reason"]))
    assert 본 == {("까닭1", "까닭2", "까닭3", "까닭4", "까닭5")}
