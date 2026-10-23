"""돌연변이 점검 목록이 썩지 않게 (docs/infra.md 25.1743) — `scripts/mutation_check.py` 의 원문이 그 파일에 정확히 한 번,
시험 파일이 있는지. 코드를 고쳐 원문이 바뀌면 여기서 깨진다 — 목록의 원문·바꿈을 새 코드에 맞춘다(규칙을 지우지 않는다).

돌연변이 자체(시험이 잡는가)는 느려서 보통 시험에서 돌리지 않는다 — 로컬에서 `python scripts/mutation_check.py`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mutation_check", 뿌리 / "scripts" / "mutation_check.py")
assert _spec and _spec.loader
mc = importlib.util.module_from_spec(_spec)
sys.modules["mutation_check"] = mc  # dataclass 가 제 모듈을 sys.modules 에서 찾는다
_spec.loader.exec_module(mc)


@pytest.mark.parametrize("m", mc.MUTATIONS, ids=lambda m: m.name)
def test_원문이_한_번_시험이_있다(m) -> None:  # noqa: ANN001
    글 = (뿌리 / m.path).read_text(encoding="utf-8")
    assert 글.count(m.old) == 1, f"{m.name}: 원문이 {m.path} 에 {글.count(m.old)}번 — 목록을 새 코드에 맞춘다"
    assert m.old != m.new and m.tests and m.why
    for t in m.tests:
        assert (뿌리 / t).is_file(), f"{m.name}: 시험 파일 {t} 없음"


def test_이름이_겹치지_않는다() -> None:
    이름 = [m.name for m in mc.MUTATIONS]
    assert len(이름) == len(set(이름))
