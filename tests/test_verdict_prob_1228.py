"""판정 글의 확률은 반올림으로 0%·100% 가 되지 않는다 (docs/infra.md 25.1228, 웹 25.1227 과 같은 규칙)."""

from __future__ import annotations

import re
from pathlib import Path

from batch.services import verdict as vd


def test_끝값_근처() -> None:
    assert vd._확률(0.996) == "99% 넘게"
    assert vd._확률(0.003) == "1% 미만"
    assert vd._확률(1.0) == "100%"
    assert vd._확률(0.0) == "0%"
    assert vd._확률(0.374) == "37%"


def test_확률을_직접_반올림하는_자리가_없다() -> None:
    글 = (Path(__file__).resolve().parents[1] / "batch/services/verdict.py").read_text(encoding="utf-8")
    assert not re.search(r"\['(?:up|p|p_up)'\] \* 100:\.0f\}%", 글)
