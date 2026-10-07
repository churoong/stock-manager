"""`batch.config` 에 없는 이름을 읽지 않는다 (docs/infra.md 25.1000).

공시 반응 첫 운영 실행이 `config.DART_API_KEY`(없는 속성 — 키는 `config.get("DART_API_KEY")` 로 읽는다)에서
AttributeError 로 죽었다. 그 함수를 부르는 테스트가 없어 게이트를 지나갔다 — 대문자 속성 읽기를 모두 훑어 본다.
"""

from __future__ import annotations

import re
from pathlib import Path

from batch import config

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"\bconfig\.([A-Z][A-Z0-9_]*)\b")


def test_config_대문자_속성은_모두_있다() -> None:
    없음 = []
    for path in [*ROOT.joinpath("batch").rglob("*.py"), *ROOT.joinpath("scripts").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        if "from batch import config" not in text and "import batch.config" not in text:
            continue
        for m in PATTERN.finditer(text):
            if not hasattr(config, m.group(1)):
                없음.append(f"{path.relative_to(ROOT)}: config.{m.group(1)}")
    assert not 없음, "batch.config 에 없는 이름:\n" + "\n".join(없음)
