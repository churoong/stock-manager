"""Turso 월 읽기 문 출력은 공개 로그에 찍지 않는다 — `ops_tee` 를 거친다 (docs/infra.md 25.1307, 운영 감사 #5)."""

from __future__ import annotations

import re
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def test_읽기_문은_ops_tee_를_거친다() -> None:
    for path in sorted(WF.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"python scripts/turso_read_gate\.py", line):
                assert "ops_tee.py" in line, f"{path.name}: {line.strip()}"


def test_백업은_읽기_문과_덤프를_같은_파일에_이어_쓴다() -> None:
    text = (WF / "backup.yml").read_text(encoding="utf-8")
    assert "ops_tee.py -a backup.txt || true" in text
    assert '--record-run 2>&1 | python scripts/ops_tee.py -a backup.txt' in text
