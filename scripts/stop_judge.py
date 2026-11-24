"""손절 매도 집행 판정 — 수만 세거나(문 전), 문을 처음 넘긴 실행에서 한 번 판정 (docs/factors.md 12.53, infra 25.2271).

읽기 전용. `stop-judge.yml`(수동 실행)이 `scripts/ops_tee.py` 를 거쳐 비공개 운영 이슈로 낸다.
매월 첫 발굴 회차 때 한 번.

    python scripts/stop_judge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.jobs import stop_judge  # noqa: E402


def main() -> int:
    from batch.core import calendar as cal
    from batch.core.client import TursoClient

    with TursoClient() as client:
        for 줄 in stop_judge.run(client, cal.local_today("KR").isoformat()):
            print(줄)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
