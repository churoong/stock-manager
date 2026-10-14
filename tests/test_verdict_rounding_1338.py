"""종목 의견 카드의 숫자도 .5 를 0 에서 멀어지게 — 같은 화면 점수 카드(`toFixed`)와 같은 글자 (docs/infra.md 25.1338)."""

from __future__ import annotations

from batch.services import verdict as vd


def test_팩터_점수_반은_올림() -> None:
    assert vd._n(72.5) == "73" and vd._n(71.5) == "72"  # 파이썬 서식은 "72"·"72"
    assert vd._n(1234.5) == "1,235" and vd._n(12.345, 2) == "12.35"
    assert vd._n(None) == "-"


def test_의견_카드의_팩터_줄() -> None:
    sc = {"as_of": "2026-10-14", "total": 70.25, "rank": 3, "ranked": 800,
          "factors": {"value": 72.5, "quality": 40.5, "growth": 55}}  # fmt: skip
    r = vd.build(vd.Inputs(name="가", ticker="000001", score=sc))
    assert any("가장 강한 팩터 밸류 73 · 가장 약한 팩터 퀄리티 41" in x for x in r["reasons"]), r["reasons"]
