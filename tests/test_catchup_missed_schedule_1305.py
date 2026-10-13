"""따라잡기가 빠진 예약의 cron 을 넘겨 예약마다 다른 일(전체 백업·미국 백테스트)이 그대로 돈다 (docs/infra.md 25.1305)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import schedule_catchup as sc  # noqa: E402

WF = ROOT / ".github" / "workflows"


def test_월_전체_백업을_따라잡으면_전체를_넘긴다() -> None:
    text = (WF / "backup.yml").read_text(encoding="utf-8")
    body = sc.dispatch_body(text, datetime(2026, 11, 2, 2, 0, tzinfo=UTC))
    assert body == {"ref": "main", "inputs": {"missed_schedule": "47 16 1 * *"}}
    body = sc.dispatch_body(text, datetime(2026, 11, 9, 1, 0, tzinfo=UTC))
    assert body["inputs"]["missed_schedule"] == "47 16 8 * *"


def test_입력이_없는_워크플로는_예전처럼() -> None:
    assert sc.dispatch_body('on:\n  schedule:\n    - cron: "0 1 * * *"\n  workflow_dispatch:\n', datetime.now(UTC)) == {
        "ref": "main"
    }


def test_워크플로가_넘긴_예약을_예약처럼_본다() -> None:
    """입력을 선언하고, 예약을 고르는 식이 `inputs.missed_schedule` 도 본다 — 선언만 하고 안 쓰면 소용없다."""
    for name, 식 in (("backup.yml", "SCOPE: ${{ ((github.event.schedule || inputs.missed_schedule) == '47 16 1 * *'"),
                     ("backtest.yml", "MARKET: ${{ (github.event.schedule || inputs.missed_schedule) == '11 19 3 * *'"),
                     ("financials.yml", '"${{ github.event.schedule || inputs.missed_schedule }}" = "17 16 * * 1"')):
        text = (WF / name).read_text(encoding="utf-8")
        assert "missed_schedule:" in text and 식 in text, name
