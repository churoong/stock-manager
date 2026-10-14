"""KIS 종목 상태 대조 탐침의 그룹 나누기·의심 판정 (docs/infra.md 25.1328, 20회차 P4 1단계)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("probe_kis_status", ROOT / "scripts" / "probe_kis_status.py")
probe = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(probe)  # type: ignore[union-attr]


def test_그룹은_편입과_두_제외_사유만() -> None:
    assert probe.group_of({"included": 1}) == "편입"
    assert probe.group_of({"included": 0, "exclude_reason": "관리종목"}) == "관리종목 제외"
    assert probe.group_of({"included": 0, "exclude_reason": "거래정지"}) == "거래정지 제외"
    assert probe.group_of({"included": 0, "exclude_reason": "시가총액 하한 미만"}) is None


def test_의심은_관리·정지·정리매매_값만() -> None:
    보통 = {"iscd_stat_cls_code": "55", "mang_issu_cls_code": "N", "sltr_yn": "N", "temp_stop_yn": "N"}
    assert not probe.suspect(보통)
    assert probe.suspect(보통 | {"iscd_stat_cls_code": "51"})
    assert probe.suspect(보통 | {"mang_issu_cls_code": "Y"})
    assert probe.suspect(보통 | {"temp_stop_yn": "Y"}) and probe.suspect(보통 | {"sltr_yn": "Y"})
    assert not probe.suspect({})  # 칸이 없으면 모른다 — 의심하지 않는다


def test_워크플로는_결과를_비공개_이슈로만() -> None:
    text = (ROOT / ".github" / "workflows" / "probe-kis-status.yml").read_text(encoding="utf-8")
    assert "ops_tee.py" in text and "set -o pipefail" in text and "publish_output.py" in text
