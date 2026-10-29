"""25.1968 (25.1963 교차검증 [권고]) — 주간 요약 실전 준비도 머리는 칸 수가 아니라 판정 시점의 판정으로 '통과' 를 말한다.

126 시점에 못 넘은 뒤 칸이 다 차면 예전 머리는 "통과 9/9" 였지만 관문은 불통이다. 반대로 126 에 통과한 뒤 칸이
나빠지면 "통과 7/9" 로 통과 사실이 머리에 없었다. 웹 상황실 관문 카드와 같은 말로
"""

from __future__ import annotations

from batch.services import desk
from tests.test_gate_looks_1963 import _관문


def test_못_넘은_뒤_칸이_다_차도_통과라_하지_않는다() -> None:
    못 = _관문(130, 좋음=False)
    뒤 = _관문(150, prev=못)
    assert all(i["ok"] for i in 뒤["items"]) and not 뒤["passed"]
    n = len(뒤["items"])
    머리 = desk.readiness_lines(뒤, [])[0]
    assert f"1단계 관문(모의 성적·무사고) {n}/{n} 충족 · " in 머리 and "통과" not in 머리


def test_통과한_뒤_칸이_나빠져도_머리에_통과() -> None:
    g = _관문(130)
    뒤 = _관문(140, prev=g, 좋음=False)
    assert 뒤["passed"] and not all(i["ok"] for i in 뒤["items"])
    assert "1단계 관문(모의 성적·무사고) 통과(다음 단계는 사용자 결정) · " in desk.readiness_lines(뒤, [])[0]
