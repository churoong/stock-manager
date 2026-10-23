"""루프 엔지니어링 설정이 썩지 않게 (docs/loops.md, docs/infra.md 25.1751).

역할 에이전트(`.claude/agents/*.md`)와 루프 workflow(`.claude/workflows/*.js`)는 예약 실행이 이름으로 부른다 — 이름이
어긋나거나 문법이 깨지면 예약 회차가 조용히 실패한다. 여기서 본다: 에이전트 머리(이름 = 파일 이름·설명·도구),
읽기 전용 역할에 쓰기 도구가 없는지, workflow 가 부르는 `agentType` 이 모두 있는지, meta 의 phase 제목과 `phase()` 가
맞는지, 문법(node 가 있으면), docs/loops.md 가 모두를 적었는지.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
에이전트 = 뿌리 / ".claude" / "agents"
흐름 = 뿌리 / ".claude" / "workflows"
루프문서 = 뿌리 / "docs" / "loops.md"


def _머리(f: Path) -> dict[str, str]:
    글 = f.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", 글, re.S)
    assert m, f"{f.name}: 머리(---) 없음"
    return dict(re.findall(r"^(\w+):\s*(.+)$", m.group(1), re.M))


def test_에이전트_머리() -> None:
    파일 = sorted(에이전트.glob("*.md"))
    assert len(파일) >= 6
    for f in 파일:
        h = _머리(f)
        assert h.get("name") == f.stem, f"{f.name}: name 이 파일 이름과 다르다"
        assert h.get("description") and h.get("tools"), f"{f.name}: description·tools 없음"
        # 모든 역할은 읽기 전용 — 고치기·기록·푸시는 주 세션이 한다(찾는 쪽과 고치는 쪽을 가른다)
        도구 = {x.strip() for x in h["tools"].split(",")}
        assert not 도구 & {"Edit", "Write", "NotebookEdit"}, f"{f.name}: 읽기 전용 역할에 쓰기 도구"


def _쓰는_에이전트(js: str) -> set[str]:
    return set(re.findall(r"agentType:\s*'([^']+)'", js))


@pytest.mark.parametrize("f", sorted(흐름.glob("*.js")), ids=lambda f: f.stem)
def test_workflow_이름_phase_문법(f: Path) -> None:
    js = f.read_text(encoding="utf-8")
    assert js.startswith("export const meta = {"), "meta 가 맨 앞이어야 한다"
    assert re.search(rf"name:\s*'{re.escape(f.stem)}'", js), "meta.name 이 파일 이름과 다르다"
    없는 = _쓰는_에이전트(js) - {p.stem for p in 에이전트.glob("*.md")}
    assert not 없는, f"없는 에이전트를 부른다: {없는}"
    meta_phase = set(re.findall(r"\{ title: '([^']+)'", js.split("\n}\n", 1)[0]))
    쓰는_phase = set(re.findall(r"phase\('([^']+)'\)", js)) | set(re.findall(r"phase: '([^']+)'", js))
    assert 쓰는_phase <= meta_phase, f"meta 에 없는 phase: {쓰는_phase - meta_phase}"
    # Date.now()·Math.random()·인자 없는 new Date() 는 workflow 에서 던진다(이어 돌리기를 깬다)
    assert not re.search(r"Date\.now\(\)|Math\.random\(\)|new Date\(\)", js)
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음 — 문법은 건너뜀")
    몸 = js.replace("export const meta", "const meta", 1)
    감쌈 = ("const agent=async()=>null,parallel=async()=>[],pipeline=async()=>[],phase=()=>{},log=()=>{},args={};\n"
             f"(async function(){{\n{몸}\n}})()\n")  # fmt: skip
    r = subprocess.run([node, "--check", "-"], input=감쌈, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr[:500]


def test_루프_문서가_모두를_적었다() -> None:
    글 = 루프문서.read_text(encoding="utf-8")
    for p in [*에이전트.glob("*.md"), *흐름.glob("*.js")]:
        assert p.stem in 글, f"docs/loops.md 에 {p.stem} 이 없다"
