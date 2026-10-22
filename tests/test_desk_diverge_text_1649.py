"""25.1649 — 운용 화면 확인할 일의 KIS 괴리 글을 300자에서 자르지 않는다 (25.1641 교차검증 [권고])."""

from batch.services import broker_mirror as bm
from batch.services import desk


def _네_칸_글() -> str:
    요약 = {"days": 28, "n": 30, "match_pct": 70.0, "kept_pct": 80.0, "internal_only": 8, "kis_only": 1,
            "slip_buy": {"n": 25, "avg": 0.8}, "slip_sell": {"n": 22, "avg": 0.7}}  # fmt: skip
    글 = bm.diverge(요약, ["match", "kept", "slip_buy", "slip_sell"], "KR")
    assert 글 is not None
    return 글


def test_네_칸이_다_켜진_글도_끝까지() -> None:
    글 = _네_칸_글()
    assert len(글) > 300  # 예전 상한을 넘는 정상 글
    c = desk.checks(halted=False, paper=None, broker={"diverge": 글}, recon_bad=[], warnings=0)
    assert c == [{"level": "high", "text": 글}] and 글.endswith("가상이 체결가를 낙관")


def test_꼴이_깨진_긴_값만_자르고_표시() -> None:
    c = desk.checks(halted=False, paper=None, broker={"diverge": "가" * 2000}, recon_bad=[], warnings=0)
    assert len(c[0]["text"]) == desk.DIVERGE_TEXT_MAX and c[0]["text"].endswith("…")
