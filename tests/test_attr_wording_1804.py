"""성과 분해 칸의 비율 글 — "이긴 비율" 이 칸마다 뜻이 뒤집혀 읽혔다 (docs/infra.md 25.1804, 결함 루프 2026-10-26).

판 종목이 다 오르고(판 것이 틀림)·막은 매도가 다 손해였는데(막은 것이 비용) 글은 "이긴 비율 100%·0%" 라 규칙이 매번 이긴
것처럼 읽혔다. 계산(`_초과` 의 hit — 지수 대비 초과 > 0 비율)은 그대로 두고 주어를 칸의 종목으로 바로 쓴다.
"""

from __future__ import annotations

from batch.services import paper as pp


def _칸(n: int, avg: float, hit: float) -> dict:
    return {"n": n, "avg": avg, "hit": hit}


def test_판_뒤·막은_매도·안_찬_회차에_이긴_비율이_나오지_않는다() -> None:
    빈 = {"n": 0}
    a = {"selection": 빈, "skipped": 빈, "missed": _칸(3, 8.0, 100.0), "sold": _칸(3, 7.0, 100.0), "held_flag": 빈,
         "blocked": 빈, "blocked_sell": _칸(3, -10.0, 0.0), "entry": {}, "virtual": {}}  # fmt: skip
    줄 = "\n".join(pp.attribution_lines("H", a))
    assert "이긴" not in 줄
    assert "판 뒤 +7.0%p (n=3, 지수보다 오른 비율 100%, 참고)" in 줄 and "판 뒤가 음수면 판 것이 옳았다" in 줄
    assert "막은 매도의 그 뒤: -10.0%p (n=3, 지수보다 오른 비율 0%, 참고) — 내리면 막은 것이 비용" in 줄
    assert "안 찬 회차의 그 뒤 +8.0%p (n=3, 지수보다 오른 비율 100%, 참고) — 오르면 놓친 기회" in 줄
