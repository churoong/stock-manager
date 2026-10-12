"""DART 전환사채 필드 탐침 (docs/infra.md 25.1177) — 발행 회사 고르기와 칸 요약."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from batch.sources.dart_disclosures import MarketDisclosure

_spec = importlib.util.spec_from_file_location(
    "probe_dart_cb", Path(__file__).resolve().parent.parent / "scripts" / "probe_dart_cb.py"
)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def _d(corp: str, title: str) -> MarketDisclosure:
    return MarketDisclosure(corp, "000001", f"2026100800{corp}", title, "2026-10-08", "B")


def test_발행_회사는_제목으로_고르고_정정은_뺀다() -> None:
    줄 = [_d("1", "주요사항보고서(전환사채권발행결정)"), _d("1", "주요사항보고서(전환사채권발행결정)"),
         _d("2", "[기재정정]주요사항보고서(전환사채권발행결정)"), _d("3", "주요사항보고서(신주인수권부사채권발행결정)"),
         _d("4", "주요사항보고서(유상증자결정)")]  # fmt: skip
    assert probe.pick_issuers(줄, 6) == [("1", "000001", "cvbdIsDecsn.json", "20261008"),
                                         ("3", "000001", "bdwtIsDecsn.json", "20261008")]  # fmt: skip
    assert len(probe.pick_issuers(줄, 1)) == 1


def test_요약은_칸_이름과_가액·일자·총액_칸() -> None:
    body = {"status": "000", "message": "정상", "list": [{"corp_name": "x", "bd_fta": "10,000,000,000", "cv_prc": "5,000",
                                                          "cvrqpd_bgd": "2027년 10월 08일", "rcept_no": "1"}]}  # fmt: skip
    줄 = probe.summarize(body)
    assert 줄[0] == "status 000 정상" and "bd_fta" in 줄[1]
    assert "cv_prc" in 줄[2] and "cvrqpd_bgd" in 줄[2] and "corp_name" not in 줄[2]
    assert probe.summarize({"status": "013", "message": "없음"}) == ["status 013 없음"]


def test_워크플로는_손으로만_돌고_결과는_비공개로() -> None:
    src = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "probe-dart-cb.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in src and "schedule" not in src
    assert "ops_tee.py" in src and "publish_output.py" in src and "probe_dart_cb.py" in src
    # 적는 파일과 올리는 파일이 같아야 한다 — 첫 실행은 옛 이름을 올리려다 "내보낼 것이 없다" 로 끝났다(10-12)
    import re

    적는 = re.search(r"ops_tee\.py (\S+)", src)
    올리는 = re.search(r"--file (\S+)", src)
    assert 적는 and 올리는 and 적는.group(1) == 올리는.group(1)
