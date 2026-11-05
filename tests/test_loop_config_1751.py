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
    assert _돌림("cross-verify", {"품질보증실:1": 좋음, "품질보증실:2": 좋음, "품질보증실:3": 좋음})["r"]["must"] == 0
    assert _돌림("cross-verify", {"품질보증실:1": 좋음, "품질보증실:2": {"must": 2, "items": []}, "품질보증실:3": 좋음})["r"]["must"] == 2
    죽음 = _돌림("cross-verify", {"품질보증실:1": 좋음, "품질보증실:2": None, "품질보증실:3": 좋음})["r"]
    assert 죽음["must"] is None and 죽음["missingLenses"] == 1


def _후보(*ids: str, q: str = "Q1") -> dict:
    return {"candidates": [{"id": i, "name": i, "question": q, "kind": "account", "summary": "x"} for i in ids]}


_통과 = {"verdict": "pass", "reasons": ""}
_질문 = {"questions": [{"id": "Q1", "question": "손절 뒤 반등을 놓치나", "teams": ["selection", "trading", "portfolio"]}]}


def test_발굴_보수적_합침과_동결_규칙() -> None:
    후보 = _후보("P1")
    def 판(a: str, b: str, 지시: bool = False) -> str:  # noqa: E306
        r = _돌림("discovery-round", {"연구기획팀": _질문, "종목선정팀": 후보, "데이터검증팀:P1": {"verdict": a, "reasons": ""},
                                      "계량검증팀:P1": {"verdict": b, "reasons": ""}}, {"userDirected": 지시})  # fmt: skip
        return r["r"]["results"][0]["verdict"]
    assert 판("pass", "fail") == "fail" and 판("pass", "pass") == "pass" and 판("conditional", "pass") == "hold"
    assert 판("conditional", "conditional", 지시=True) == "conditional"  # 사용자 지시분만 조건부가 산다
    # 검증 하나가 죽으면 보류, 후보 팀이 모두 죽고 예비도 죽으면 "후보 0" 이 아니라 error
    assert _돌림("discovery-round", {"연구기획팀": _질문, "종목선정팀": 후보, "데이터검증팀:P1": _통과})["r"]["results"][0][
        "verdict"] == "hold"
    assert "error" in _돌림("discovery-round", {"연구기획팀": _질문})["r"]


def test_연구개발본부_단계_순서_판정_기획_후보_설계_검증() -> None:
    """25.2140 — 성과판정 → 연구기획(질문) → 질문 받은 팀만 → 실험설계 → 검증. 교훈이 기획 지시문에, 명세가 검증 지시문에 실린다"""
    cfg = {"성과판정팀": {"judgments": [{"id": "38-P2", "verdict": "drop", "reason": "DSR", "lesson": "국면 의존"}]},
           "연구기획팀": {"questions": [{"id": "Q1", "question": "하락장 관문", "teams": ["portfolio"]}]},
           "포트폴리오·위험팀": _후보("portfolio-1"),
           "실험설계팀": {"specs": [{"id": "portfolio-1", "prereg": "등록문", "stage": "리플레이", "variant": "v1"}]},
           "데이터검증팀:portfolio-1": _통과, "계량검증팀:portfolio-1": _통과}  # fmt: skip
    out = _돌림("discovery-round", cfg)
    r, 불린 = out["r"], [x["label"] for x in out["불린"]]
    assert 불린[:2] == ["성과판정팀", "연구기획팀"]  # 판정이 먼저, 기획이 그 교훈으로
    assert "종목선정팀" not in 불린 and "매매규칙팀" not in 불린 and "데이터팀" not in 불린  # 질문 없는 팀은 부르지 않는다
    assert 불린.index("실험설계팀") < 불린.index("데이터검증팀:portfolio-1")
    assert r["results"][0]["verdict"] == "pass" and r["results"][0]["spec"]["prereg"] == "등록문"
    assert r["judgments"][0]["verdict"] == "drop" and r["teams"] == ["포트폴리오·위험팀"]


def test_질문이_없으면_후보를_내지_않고_기획이_죽으면_모든_팀이_영역에서() -> None:
    r = _돌림("discovery-round", {"연구기획팀": {"questions": []}})["r"]
    assert r["results"] == [] and "질문 없음" in r["note"]
    out = _돌림("discovery-round", {"종목선정팀": _후보("selection-1")})
    assert out["r"]["plannerMissing"] and len(out["r"]["teams"]) == 4


def test_상한보다_많으면_기획이_거르고_고른_것만_검증한다() -> None:
    cfg = {"연구기획팀": _질문, "종목선정팀": _후보("selection-1", "selection-2"), "매매규칙팀": _후보("trading-1"),
           "포트폴리오·위험팀": _후보("portfolio-1"),
           "연구기획팀:거름": {"selected": ["portfolio-1", "selection-1", "없는-9"], "dropped": [{"id": "trading-1", "why": "보류"}],
                          "merged": [{"into": "selection-1", "from": "selection-2", "why": "같음"}]}}  # fmt: skip
    for c in ("portfolio-1", "selection-1"):
        cfg[f"데이터검증팀:{c}"] = _통과
        cfg[f"계량검증팀:{c}"] = _통과
    out = _돌림("discovery-round", cfg)
    r, 불린 = out["r"], [x["label"] for x in out["불린"]]
    assert [x["id"] for x in r["results"]] == ["portfolio-1", "selection-1"]  # 기획 순서, 모르는 id 는 버림
    assert r["skipped"] == [] and "데이터검증팀:trading-1" not in 불린  # 거른 것은 다음 회차로도 넘기지 않는다
    assert r["designMissing"] and all(x["verdict"] == "pass" for x in r["results"])  # 설계가 죽어도 검증은 돈다


def test_거름이_죽으면_팀_순서대로_셋까지_나머지는_다음_회차() -> None:
    cfg = {"연구기획팀": _질문, "종목선정팀": _후보("selection-1", "selection-2"), "매매규칙팀": _후보("trading-1"),
           "포트폴리오·위험팀": _후보("portfolio-1", "portfolio-2")}  # fmt: skip
    r = _돌림("discovery-round", cfg, {"max": 9})["r"]  # 상한은 3 을 넘기지 않는다
    assert [x["id"] for x in r["results"]] == ["selection-1", "selection-2", "trading-1"] and r["triage"] == {"fallback": "팀 순서"}
    assert [x["id"] for x in r["skipped"]] == ["portfolio-1", "portfolio-2"]
    assert all(x["verdict"] == "hold" for x in r["results"])  # 검증이 없으면 보류


def test_팀마다_질문_수의_두_배까지_사용자_질문은_그대로() -> None:
    q = [{"id": "U1", "question": "사용자 질문", "teams": ["data"]}]
    out = _돌림("discovery-round", {"데이터팀": _후보("data-1", "data-2", "data-3", q="U1")}, {"questions": q, "max": 3})
    assert [x["id"] for x in out["r"]["results"]] == ["data-1", "data-2"]
    assert "연구기획팀" not in [x["label"] for x in out["불린"]]  # 넘긴 질문이 있으면 기획을 부르지 않는다


def test_결함_루프는_죽은_감사와_반박을_버리지_않는다() -> None:
    결함 = {"findings": [{"title": "t", "severity": "must", "where": "a.py:1", "repro": "r"}], "scope": "s"}
    없음 = {"findings": [], "scope": "s"}
    r = _돌림("defect-round", {"내부감사실:1": 결함, "내부감사실:2": 없음}, {"areas": ["가", "나"]})["r"]
    assert r["unrefuted"] and not r["clean"]  # 반박이 죽었다(null)
    r = _돌림("defect-round", {"내부감사실:1": 결함, "내부감사실:2": 없음, "심사팀:1": "__throw"}, {"areas": ["가", "나"]})["r"]
    assert [f["title"] for f in r["unrefuted"]] == ["t"] and not r["clean"]  # 반박이 예외로 죽어도 결함이 남는다
    r = _돌림("defect-round", {"내부감사실:1": 없음, "내부감사실:2": None}, {"areas": ["가", "나"]})["r"]
    assert r["failedAreas"] == ["나"] and not r["clean"]  # 감사가 죽은 영역은 "결함 0" 이 아니다
    r = _돌림("defect-round", {"내부감사실:1": 결함, "내부감사실:2": 없음,
                               "심사팀:1": {"real": "false", "evidence": ""}}, {"areas": ["가", "나"]})["r"]  # fmt: skip
    assert r["clean"] and r["refuted"] == ["t"]


def test_등록_안_된_역할은_정의_파일로_한_번만() -> None:
    r = _돌림("cross-verify", {"__unregistered": True, "품질보증실:1": {"must": 0, "items": []},
                               "품질보증실:2": {"must": 0, "items": []}, "품질보증실:3": {"must": 0, "items": []}})  # fmt: skip
    assert r["r"]["must"] == 0
    대체 = [x for x in r["불린"] if x["agentType"] == "general-purpose"]
    assert len(대체) == 3 and all(x["label"].endswith("*") for x in 대체)
    # 등록돼 있는데 null(죽음)이면 다시 부르지 않는다 — 예산이 두 배가 되지 않게
    r = _돌림("cross-verify", {"품질보증실:1": None, "품질보증실:2": None, "품질보증실:3": None})
    assert len(r["불린"]) == 3


def test_에이전트_부서명() -> None:
    """역할마다 회사 부서명(`department:`)이 있고, 서로 다르며, docs/loops.md 표에 실리고, 가상모의투자 H 지주사의 부서
    (docs/company.md)와 겹치지 않는다 (25.2139 — 2026-11-04 사용자 지시 "각 에이전트를 회사 부서명으로 바꿔줘")"""
    부서 = {f.stem: _머리(f).get("department") for f in sorted(에이전트.glob("*.md"))}
    assert all(부서.values()), f"부서명 없는 역할: {[k for k, v in 부서.items() if not v]}"
    assert len(set(부서.values())) == len(부서), "부서명이 겹친다"
    문서 = 루프문서.read_text(encoding="utf-8")
    for 이름, d in 부서.items():
        assert re.search(rf"\| `{re.escape(이름)}` \| \*\*{re.escape(d)}\*\* \|", 문서), f"loops.md 표에 {이름}·{d} 없음"
    지주사 = set(re.findall(r"\*\*([가-힣]+부)\*\*", (뿌리 / "docs" / "company.md").read_text(encoding="utf-8")))
    assert 지주사, "company.md 부서 정규식이 빗나갔다"
    assert not set(부서.values()) & 지주사
