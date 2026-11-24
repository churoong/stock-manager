"""깔때기 작동특성 표 — 합성 전략을 실제 판정 함수에 넣은 통과율 (docs/funnel.md 3.2, infra 25.2276).

DB·네트워크를 쓰지 않는다(합성 자료만). 로컬에서 돌린다 — Actions 에 두지 않는다.

    python scripts/funnel_oc.py [--sims 2000] [--seed 1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.services import funnel_oc  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sims", type=int, default=2000)
    p.add_argument("--seed", type=int, default=1)
    a = p.parse_args()
    for 줄 in funnel_oc.table(sims=a.sims, seed=a.seed):
        print(줄)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
