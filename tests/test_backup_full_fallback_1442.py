"""전체 백업 8일 예비는 진도선을 보지 않고, 상태 화면이 전체 백업을 따로 본다 (docs/infra.md 25.1442, 백업 감사)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("turso_read_gate_1442", ROOT / "scripts" / "turso_read_gate.py")
gate = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(gate)  # type: ignore[union-attr]


def test_no_pace_를_넘기면_진도선을_보지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    본것: list[bool] = []

    class _C:
        def close(self) -> None:
            pass

    monkeypatch.setattr(gate.backend, "resolved_backend", lambda: gate.backend.TURSO)
    monkeypatch.setattr(gate.backend, "TursoClient", _C)
    monkeypatch.setattr(gate.db, "remaining_read_budget_or_none", lambda c: 1)
    monkeypatch.setattr(gate, "decide", lambda u, e, now, pace=True: (본것.append(pace), (True, "ok"))[1])
    for argv, 기대 in ((["g", "backup", "100"], True), (["g", "backup", "100", "--no-pace"], False)):
        monkeypatch.setattr(gate.sys, "argv", argv)
        assert gate.main() == 0 and 본것[-1] is 기대


def test_8일_예비만_no_pace() -> None:
    wf = (ROOT / ".github" / "workflows" / "backup.yml").read_text(encoding="utf-8")
    assert 'if [ "$SCHED" = "47 16 8 * *" ]; then PACE="--no-pace"; fi' in wf
    assert "turso_read_gate.py backup 8500000 $PACE" in wf


def test_상태_화면에_전체_백업_칸() -> None:
    src = (ROOT / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
    assert 'key: "backup_full"' in src and "json_extract(step_log, '$.scope') = 'full'" in src
