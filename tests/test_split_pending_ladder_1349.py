"""분할 재수집 대기 미국 종목은 단기 가격 사다리(20일선)도 내지 않는다 (docs/infra.md 25.1349, 22회차 검증 A 곁가지)."""

from __future__ import annotations

from dataclasses import replace

from batch.services import signals as sg
from tests.test_signals import short_input


def test_분할_대기면_단기_사다리가_없다() -> None:
    inp = short_input()
    assert sg.price_levels(inp)["short"]  # 평소에는 20일선 두 칸
    assert sg.price_levels(replace(inp, split_pending=True))["short"] == []  # 예전: 분할 전후가 섞인 20일선
