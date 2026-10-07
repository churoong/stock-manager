"""공개 저장소 판정 — 정의는 `batch/core/visibility.py` 한 곳이다 (docs/infra.md 25.978·25.979).

`python scripts/…` 로 불려 `batch` 를 import 하지 못할 수 있어 저장소 뿌리를 경로에 넣는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batch.core.visibility import is_public  # noqa: E402

__all__ = ["is_public"]
