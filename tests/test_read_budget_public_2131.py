"""읽기 예산 문의 남은 양을 공개 Actions 로그에 찍지 않는다 (docs/infra.md 25.2131, 25.2106 [참고] ②).

`db.check_read_budget` 의 글("이번 달 남은 읽기 N행")은 운영 출력이다. 백테스트·핵심 백테스트·국내 수정주가는 그 글을
`print` 로 공개 로그에 찍었다(워크플로 단계에 `ops_tee` 없음 — 25.1307 은 진도 문만 가렸다).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch.core import db

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("ok", [True, False])
def test_공개_한_줄에는_숫자가_없다(ok: bool) -> None:
    글 = db.read_budget_public("백테스트", ok)
    assert ("통과" if ok else "부족") in 글
    assert not re.search(r"\d{3}", 글.replace("25.2131", "")), 글


def test_예산_문을_쓰는_작업은_그_글을_찍지_않는다() -> None:
    """`check_read_budget` 을 부르는 작업 파일 모두 — 돌려받은 글(`text`)을 `print` 하지 않는다"""
    부르는곳 = [p for p in (ROOT / "batch").rglob("*.py") if "check_read_budget(" in p.read_text(encoding="utf-8")
             and p.name != "db.py"]  # fmt: skip
    assert {p.name for p in 부르는곳} >= {"backtest.py", "core_backtest.py", "adjust_kr.py"}
    for p in 부르는곳:
        글 = p.read_text(encoding="utf-8")
        for m in re.finditer(r"(\w+), (\w+) = db\.check_read_budget\(", 글):
            이름 = m.group(2)
            assert not re.search(rf"print\({이름}\)", 글), f"{p.name}: print({이름})"
