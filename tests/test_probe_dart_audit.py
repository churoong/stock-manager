"""DART 감사의견 필드 탐침의 요약 (docs/infra.md 25.1138) — 칸 이름을 모르는 응답에서 낱말 든 값만 고른다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "probe_dart_audit", Path(__file__).resolve().parent.parent / "scripts" / "probe_dart_audit.py"
)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def test_요약은_칸_이름과_낱말_든_값만() -> None:
    body = {"status": "000", "message": "정상", "list": [
        {"bsns_year": "제56기", "adtor": "삼정회계법인", "adt_opinion": "적정", "emphs_matter": "-", "x": "무관"},
        {"bsns_year": "제55기", "adtor": "삼정회계법인", "adt_opinion": "적정", "emphs_matter": "계속기업 관련 중요한 불확실성"},
    ]}  # fmt: skip
    줄 = probe.summarize(body)
    assert 줄[0] == "status 000 정상"
    assert "adt_opinion" in 줄[1] and "emphs_matter" in 줄[1]
    assert "'adt_opinion': '적정'" in 줄[2] and "x" not in 줄[2].split("{")[1]
    assert "계속기업" in 줄[3]


def test_빈_응답·점검_응답() -> None:
    assert probe.summarize({"status": "013", "message": "조회된 데이타가 없습니다."}) == ["status 013 조회된 데이타가 없습니다."]
    assert probe.summarize({"status": "800", "message": "점검"}) == ["status 800 점검"]


def test_워크플로는_손으로만_돌고_결과는_비공개로() -> None:
    src = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "probe-dart-audit.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in src and "schedule" not in src
    assert "ops_tee.py" in src and "publish_output.py" in src
