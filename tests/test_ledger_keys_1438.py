"""판정 대기 원장(docs/factors.md 12.32)의 실행 기록 키가 코드에 실제로 있다 (docs/infra.md 25.1438, 29회차 P2)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: 아직 구현 전인 29회차 키 — 구현하는 커밋에서 이 목록에서 뺀다
아직 = {"tilt_paper", "horizon_fit"}


def test_원장_키가_코드에_있다() -> None:
    doc = (ROOT / "docs" / "factors.md").read_text(encoding="utf-8")
    절 = doc[doc.index("### 12.32 판정 대기 원장"):doc.index("### 12.6 지난 회차 판정 상태")]
    keys = {k.split(".")[-1] for k in re.findall(r"\| `([a-z_0-9.]+)`(?:·`[a-z_]+`)? \|", 절)}
    코드 = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "batch").rglob("*.py"))
    없음 = sorted(k for k in keys - 아직 if f'"{k}"' not in 코드)
    assert len(keys) >= 20 and not 없음, 없음
