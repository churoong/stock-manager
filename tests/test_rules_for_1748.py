"""기간 목표·손절 합치기 식의 정의처 하나 — `signals.rules_for` (docs/infra.md 25.1748, 중복 규칙 감사 ⑦)."""

from __future__ import annotations

import re
from pathlib import Path

from batch.services import signals

뿌리 = Path(__file__).resolve().parent.parent


def test_칸별로_덮고_모르는_기간은_장기() -> None:
    assert signals.rules_for("short") == {"target_pct": 10.0, "stop_pct": -7.0}
    # 한 칸만 설정해도 다른 칸은 기본값(통째로 바꾸지 않는다)
    assert signals.rules_for("mid", {"mid": {"stop_pct": -12.0}}) == {"target_pct": 25.0, "stop_pct": -12.0}
    assert signals.rules_for(None) == signals.rules_for("long") == {"target_pct": 50.0, "stop_pct": -25.0}
    assert signals.rules_for("weird", {"long": {"stop_pct": -20.0}})["stop_pct"] == -20.0


def test_합치기_식을_그_자리에_다시_적지_않는다() -> None:
    """`{**DEFAULT_TARGETS[기간], **(targets…)}` 를 다시 적으면 한쪽만 고쳐져 가상모의투자 매도와 화면이 다른 손절선을 말한다"""
    따로 = [f"{f.relative_to(뿌리)}:{k}" for f in (뿌리 / "batch").rglob("*.py") if f.name != "signals.py"
            for k, 줄 in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\{\*\*(sf\.)?DEFAULT_TARGETS\[", 줄)]  # fmt: skip
    assert not 따로, f"목표·손절 합치기 식을 따로 적었다 — signals.rules_for 를 쓴다: {따로}"
