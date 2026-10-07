"""Turso 월 읽기 진도 문 (docs/infra.md 25.886)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import yaml

_spec = importlib.util.spec_from_file_location(
    "turso_read_gate", Path(__file__).resolve().parent.parent / "scripts" / "turso_read_gate.py"
)
gate = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(gate)  # type: ignore[union-attr]

한도 = 500_000_000
WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def test_복귀_날의_속도면_무거운_작업을_미룬다() -> None:
    """2026-10-02 06시 UTC 에 이달 5,545만 행 — 진도선(약 3.9% + 여유 5%)을 넘는다. 전체 백업 850만은 미룬다."""
    ok, 글 = gate.decide(55_450_729, 8_500_000, datetime(2026, 10, 2, 6, tzinfo=UTC), 한도)
    assert not ok and "미룹니다" in 글


def test_달_중순에_진도_안이면_들어간다() -> None:
    ok, _ = gate.decide(150_000_000, 8_500_000, datetime(2026, 10, 16, tzinfo=UTC), 한도)
    assert ok


def test_달_초_전체_백업은_빈_달이면_들어간다() -> None:
    """매월 1일 16:47 UTC 전체 백업 — 그달 첫 작업이 막히면 안 된다(여유 5% = 2,500만)."""
    ok, _ = gate.decide(2_000_000, 8_500_000, datetime(2026, 11, 1, 16, 47, tzinfo=UTC), 한도)
    assert ok


def test_월말에는_남은_날의_일일_배치_몫을_남긴다() -> None:
    """진도선 안이어도 남은 날 × 일일 몫을 뺀 상한을 넘으면 미룬다."""
    # 10-25 00시: 진도 24/31 + 5% → 4.12억, 상한 = 5억 − 7일 × 600만 = 4.58억. 4억 + 850만 → 들어감
    ok, _ = gate.decide(400_000_000, 8_500_000, datetime(2026, 10, 25, tzinfo=UTC), 한도)
    assert ok
    # 10-30 00시: 진도 29/31 + 5% → 4.93억, 상한 = 5억 − 2일 × 600만 = 4.88억.
    # 4.81억 + 850만 = 4.895억 — 진도선 안이지만 상한을 넘어 미룬다(남은 날의 일일 배치 몫)
    ok, _ = gate.decide(481_000_000, 8_500_000, datetime(2026, 10, 30, tzinfo=UTC), 한도)
    assert not ok


def test_모르면_들어간다() -> None:
    ok, 글 = gate.decide(None, 8_500_000, datetime(2026, 10, 2, tzinfo=UTC), 한도)
    assert ok and "재지 못해" in 글


def test_문이_미루면_뒤_단계가_모두_서는지() -> None:
    """문만 두고 뒤 단계에 조건을 안 달면 아무것도 막지 못한다."""
    for 이름, 단계들 in (("backup.yml", ("덤프", "아티팩트로 올리기", "실행 기록 닫기")),
                       ("backtest.yml", ("백테스트", "스트레스", "장기 신호 문턱 비교"))):  # fmt: skip
        data = yaml.safe_load((WORKFLOWS / 이름).read_text(encoding="utf-8"))
        steps = {s.get("name"): s for job in data["jobs"].values() for s in job["steps"]}
        assert any("turso_read_gate.py" in str(s.get("run", "")) for s in steps.values()), 이름
        for 단계 in 단계들:
            assert "steps.gate.outputs.deferred != 'true'" in str(steps[단계].get("if", "")), f"{이름} {단계}"
