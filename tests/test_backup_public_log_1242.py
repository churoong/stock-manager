"""백업·되살리기의 표별 행 수가 공개 Actions 로그에 찍히지 않는다 (docs/infra.md 25.1242, 백업 감사).

리허설(restore-drill.yml)은 처음부터 ops_tee → 비공개 운영 이슈였는데 백업·되살리기는 그대로 찍었다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _단계(wf: str, 이름: str) -> str:
    doc = yaml.safe_load((ROOT / ".github/workflows" / wf).read_text(encoding="utf-8"))
    for job in doc["jobs"].values():
        for s in job["steps"]:
            if s.get("name", "").startswith(이름):
                return s["run"]
    raise AssertionError(f"{wf} 에 {이름} 단계가 없다")


@pytest.mark.parametrize(("wf", "단계", "스크립트"), [
    ("backup.yml", "덤프", "scripts/backup_db.py"),
    ("restore-backup.yml", "되살리기", "scripts/restore_backup.py"),
    ("restore-drill.yml", "리허설", "scripts/restore_drill.py"),
])  # fmt: skip
def test_출력은_ops_tee_로(wf: str, 단계: str, 스크립트: str) -> None:
    글 = _단계(wf, 단계)
    줄 = next(x for x in 글.splitlines() if 스크립트 in x)
    assert "| python scripts/ops_tee.py" in 줄 and "set -o pipefail" in 글
    assert "publish_output.py" in (ROOT / ".github/workflows" / wf).read_text(encoding="utf-8")
