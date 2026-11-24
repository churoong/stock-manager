"""절대 한도·가격 깃 그림자 계측 — 낸 KIS모의투자 주문이 걸렸을 건수 (docs/paper.md 15.12, infra 25.2284).

읽기 전용. `order-limits.yml`(수동 실행)이 `scripts/ops_tee.py` 를 거쳐 비공개 운영 이슈로 낸다(금액이 들어 있다).

    python scripts/order_limits.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.jobs import order_limits  # noqa: E402


def main() -> int:
    from batch.core import calendar as cal
    from batch.core.client import TursoClient

    with TursoClient() as client:
        for 줄 in order_limits.run(client, cal.local_today("KR").isoformat()):
            print(줄)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
