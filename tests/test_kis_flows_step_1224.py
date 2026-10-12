"""KIS 수집이 실패해도 DART 만 쓰는 공시 반응은 돈다 — 단계는 끝에 실패로 (docs/infra.md 25.1224, 감사 재현).

기본 셸 `bash -e` 에서 단계 글을 그대로 돌린다. 파이썬 명령은 대역(kis_flows 는 실패, 나머지는 이름 출력)으로 바꾼다.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from batch.jobs import weekly_summary

ROOT = Path(__file__).resolve().parents[1]


def _단계() -> str:
    wf = yaml.safe_load((ROOT / ".github/workflows/kis-flows.yml").read_text(encoding="utf-8"))
    return next(s["run"] for s in wf["jobs"][next(iter(wf["jobs"]))]["steps"] if s.get("name") == "수집")


def _돌림(kis_rc: int, tmp_path: Path) -> subprocess.CompletedProcess:
    글 = re.sub(r"\$\{\{[^}]*\}\}", "", _단계())
    글 = 글.replace("python scripts/ops_tee.py -a run.log", "cat")
    글 = 글.replace("python -m batch.jobs.kis_flows", f"sh -c 'echo kis_flows; exit {kis_rc}' --")
    글 = re.sub(r"python -m batch\.jobs\.(\w+)", r"echo \1", 글)
    return subprocess.run(["bash", "-e", "-c", 글], capture_output=True, text=True, cwd=tmp_path)


@pytest.mark.skipif(subprocess.run(["which", "bash"], capture_output=True).returncode != 0, reason="bash 없음")
def test_KIS_실패에도_공시_반응은_돌고_단계는_실패(tmp_path: Path) -> None:
    r = _돌림(1, tmp_path)
    assert "disclosure_reaction" in r.stdout
    assert "broker_stats" not in r.stdout  # KIS 수급이 재료라 수집이 된 날만
    assert r.returncode == 1


def test_평소에는_셋_다_돌고_성공(tmp_path: Path) -> None:
    r = _돌림(0, tmp_path)
    assert all(j in r.stdout for j in ("kis_flows", "broker_stats", "disclosure_reaction"))
    assert r.returncode == 0


def test_빠지면_주간_요약이_말한다() -> None:
    assert weekly_summary.EXPECTED_DAYS["disclosure_reaction"] == 4
    assert weekly_summary.EXPECTED_DAYS["broker_stats"] == 10
