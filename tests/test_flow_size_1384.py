"""수급 "반대" 의 크기 — 같은 날들의 거래대금 대비 (docs/infra.md 25.1384, 25회차 P4)."""

from __future__ import annotations

import pytest

from batch.services import divergence as dv


def test_단위를_맞춘_몫() -> None:
    # 순매도 −50 백만원 ÷ 5일 거래대금 5×10¹⁰원 = −0.1%
    날 = ["2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08"]
    값 = dict.fromkeys(날, 1e10)
    assert dv.turnover_share(-50, 값, 날) == pytest.approx(-0.001)
    # 한 날이라도 거래대금이 없으면 모름
    assert dv.turnover_share(-50, {**값, 날[2]: None}, 날) is None
    assert dv.turnover_share(-50, 값, []) is None


def test_합은_최근_다섯_거래일과_그_날짜() -> None:
    행 = [(1, f"2026-10-{d:02d}", -10, -5) for d in (6, 7, 8, 9, 12, 13, 14)]
    합 = dv.sum_flows(행, "2026-10-08")
    assert 합[1] == (5, -50, -25, ["2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08"])


def test_반대일_때만_크기를_붙이고_규칙은_그대로() -> None:
    v = dv.View("가", "000001", 5, -40, -10, 0, 0, -0.001)
    assert v.flow_against
    assert "5일 거래대금의 -0.10%" in dv.render([v])[1]
    assert "5일 거래대금의 -0.10%" in dv.against_lines(v)[0]
    # 몫을 모르면 예전 문장 그대로
    assert "거래대금" not in dv.render([dv.View("가", "000001", 5, -40, -10, 0, 0)])[1]
