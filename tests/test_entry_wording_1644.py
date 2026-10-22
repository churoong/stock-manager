"""25.1644 — 성과 분해 진입 줄: 체결가가 종가보다 비쌌으면 "비쌌다" 로(예전 "평균 -0.85% 쌌다" 는 뜻이 뒤집혀 읽혔다)."""

from batch.services import paper as pp


def _진입줄(avg: float | None, n: int) -> str:
    a = {"selection": {}, "skipped": {}, "missed": {}, "sold": {}, "held_flag": {}, "entry": {"n": n, "avg": avg}}
    return next(x for x in pp.attribution_lines("A", a) if " 진입: " in x)


def test_싸면_쌌다_비싸면_비쌌다_같으면_같았다() -> None:
    assert "평균 0.50% 쌌다 (n=2)" in _진입줄(0.5, 2)
    assert "평균 0.85% 비쌌다 (n=3)" in _진입줄(-0.85, 3)
    assert "-0.85" not in _진입줄(-0.85, 3)
    assert "종가와 평균 같았다 (n=4)" in _진입줄(-0.001, 4)
    assert "체결 표본 없음" in _진입줄(None, 0)
