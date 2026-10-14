"""종목 분석 진단 줄(31~34·40~42장)의 수익률·비율 글자도 .5 를 0 에서 멀어지게 (docs/infra.md 25.1334, 25.1105 규칙)."""

from __future__ import annotations

from batch.services import event_history as ev
from batch.services import history as hi


def test_수익률은_반_올림() -> None:
    assert ev._p(0.0125) == "+1.3%"  # .1f 는 "+1.2%" 가 나오는 값
    assert ev._p(-0.0125) == "-1.3%"
    assert hi._p(0.0125) == "+1.3%"


def test_오른_비율도_같은_규칙() -> None:
    줄 = ev.lines({"short": {"events": 3, "since": "2026-01-01",
                             "h": {"5": {"median": 0.01, "up": 0.625}}, "base": {"5": {"median": 0.0, "up": 0.125}}}})
    assert "오른 비율 63%" in 줄[0] and "평소 +0.0%·13%" in 줄[0]  # .0f 는 62%·12% (반은 짝수로)
