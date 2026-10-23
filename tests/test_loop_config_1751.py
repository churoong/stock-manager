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
        도구 = {x.strip(" []\"'") for x in h["tools"].split(",")}  # `[Read, Write]` 꼴도
        assert not 도구 & {"Edit", "Write", "NotebookEdit"}, f"{f.name}: 읽기 전용 역할에 쓰기 도구"


def _쓰는_에이전트(js: str) -> set[str]:
    """`역할('이름', …)` 으로 부르는 역할 — 등록 이름이 없으면 범용 에이전트가 그 정의 파일을 읽는다(25.1751)"""
    return set(re.findall(r"역할\('([^']+)'", js)) | (set(re.findall(r"agentType:\s*'([^']+)'", js)) - {"general-purpose"})


@pytest.mark.parametrize("f", sorted(흐름.glob("*.js")), ids=lambda f: f.stem)
def test_workflow_이름_phase_문법(f: Path) -> None:
    js = f.read_text(encoding="utf-8")
    assert js.startswith("export const meta = {"), "meta 가 맨 앞이어야 한다"
    assert re.search(rf"name:\s*'{re.escape(f.stem)}'", js), "meta.name 이 파일 이름과 다르다"
    쓰는 = _쓰는_에이전트(js)
    assert 쓰는, "역할을 하나도 부르지 않는다 — 정규식이 빗나갔나"
    없는 = 쓰는 - {p.stem for p in 에이전트.glob("*.md")}
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


# ----------------------------------------------------------------------
# 문(gate) 규칙을 실제로 돌린다 (25.1752, 25.1751 교차검증 [권고] — 이름·문법만 봐서 "렌즈가 죽어도 must 0",
# "보수적 합침을 뒤집음", "동결 규칙을 지움" 돌연변이가 모두 통과했다). workflow 본문을 가짜 agent·parallel·
# pipeline 으로 감싸 node 로 돌린다 — 가짜 agent 는 label 로 응답을 고른다
# ----------------------------------------------------------------------

_가짜 = r"""
const 응답 = __CFG__;
const 불린 = [];
async function agent(prompt, opts) {
  불린.push({ label: opts.label, agentType: opts.agentType });
  if (응답.__unregistered && String(opts.agentType || '').startsWith('stock-')) throw new Error('agent type not found');
  const k = Object.keys(응답).find((x) => !x.startsWith('__') && String(opts.label).replace(/\*$/, '') === x);
  if (k === undefined) return null;
  const v = 응답[k];
  if (v === '__throw') throw new Error('죽음');
  return v;
}
async function parallel(thunks) { return Promise.all(thunks.map((t) => t().catch(() => null))); }
async function pipeline(items, ...stages) {
  return Promise.all(items.map(async (it, i) => {
    let v = it;
    try { for (const s of stages) v = await s(v, it, i); return v; } catch (e) { return null; }
  }));
}
function phase() {}
function log() {}
const args = __ARGS__;
(async function () {
  const r = await (async function () {
__BODY__
  })();
  console.log(JSON.stringify({ r, 불린 }));
})();
"""


def _돌림(이름: str, cfg: dict, args: dict | None = None) -> dict:
    import json

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    몸 = (흐름 / f"{이름}.js").read_text(encoding="utf-8").replace("export const meta", "const meta", 1)
    글 = (_가짜.replace("__CFG__", json.dumps(cfg, ensure_ascii=False))
          .replace("__ARGS__", json.dumps(args or {}, ensure_ascii=False)).replace("__BODY__", 몸))  # fmt: skip
    r = subprocess.run([node, "-"], input=글, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr[:800]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_교차검증_렌즈_하나가_죽으면_통과로_치지_않는다() -> None:
    좋음 = {"must": 0, "items": []}
    assert _돌림("cross-verify", {"xv:1": 좋음, "xv:2": 좋음, "xv:3": 좋음})["r"]["must"] == 0
    assert _돌림("cross-verify", {"xv:1": 좋음, "xv:2": {"must": 2, "items": []}, "xv:3": 좋음})["r"]["must"] == 2
    죽음 = _돌림("cross-verify", {"xv:1": 좋음, "xv:2": None, "xv:3": 좋음})["r"]
    assert 죽음["must"] is None and 죽음["missingLenses"] == 1


def test_발굴_보수적_합침과_동결_규칙() -> None:
    후보 = {"candidates": [{"id": "P1", "name": "가", "kind": "ic", "summary": "x"}]}
    def 판(a: str, b: str, 지시: bool = False) -> str:  # noqa: E306
        r = _돌림("discovery-round", {"discover": 후보, "A:P1": {"verdict": a, "reasons": ""},
                                      "B:P1": {"verdict": b, "reasons": ""}}, {"userDirected": 지시})  # fmt: skip
        return r["r"]["results"][0]["verdict"]
    assert 판("pass", "fail") == "fail" and 판("pass", "pass") == "pass" and 판("conditional", "pass") == "hold"
    assert 판("conditional", "conditional", 지시=True) == "conditional"  # 사용자 지시분만 조건부가 산다
    # 검증 하나가 죽으면 보류, 발굴이 죽으면 "후보 0" 이 아니라 error
    assert _돌림("discovery-round", {"discover": 후보, "A:P1": {"verdict": "pass", "reasons": ""}})["r"]["results"][0][
        "verdict"] == "hold"
    assert "error" in _돌림("discovery-round", {"discover": None})["r"]


def test_결함_루프는_죽은_감사와_반박을_버리지_않는다() -> None:
    결함 = {"findings": [{"title": "t", "severity": "must", "where": "a.py:1", "repro": "r"}], "scope": "s"}
    없음 = {"findings": [], "scope": "s"}
    r = _돌림("defect-round", {"audit:1": 결함, "audit:2": 없음}, {"areas": ["가", "나"]})["r"]
    assert r["unrefuted"] and not r["clean"]  # 반박이 죽었다(null)
    r = _돌림("defect-round", {"audit:1": 결함, "audit:2": 없음, "refute:1": "__throw"}, {"areas": ["가", "나"]})["r"]
    assert [f["title"] for f in r["unrefuted"]] == ["t"] and not r["clean"]  # 반박이 예외로 죽어도 결함이 남는다
    r = _돌림("defect-round", {"audit:1": 없음, "audit:2": None}, {"areas": ["가", "나"]})["r"]
    assert r["failedAreas"] == ["나"] and not r["clean"]  # 감사가 죽은 영역은 "결함 0" 이 아니다
    r = _돌림("defect-round", {"audit:1": 결함, "audit:2": 없음,
                               "refute:1": {"real": "false", "evidence": ""}}, {"areas": ["가", "나"]})["r"]  # fmt: skip
    assert r["clean"] and r["refuted"] == ["t"]


def test_등록_안_된_역할은_정의_파일로_한_번만() -> None:
    r = _돌림("cross-verify", {"__unregistered": True, "xv:1": {"must": 0, "items": []},
                               "xv:2": {"must": 0, "items": []}, "xv:3": {"must": 0, "items": []}})  # fmt: skip
    assert r["r"]["must"] == 0
    대체 = [x for x in r["불린"] if x["agentType"] == "general-purpose"]
    assert len(대체) == 3 and all(x["label"].endswith("*") for x in 대체)
    # 등록돼 있는데 null(죽음)이면 다시 부르지 않는다 — 예산이 두 배가 되지 않게
    r = _돌림("cross-verify", {"xv:1": None, "xv:2": None, "xv:3": None})
    assert len(r["불린"]) == 3
