"""마이그레이션만 적용하고 끝낸다.

배치가 돌 때도 자동으로 적용되지만, 스키마를 먼저 반영해야 할 때가 있다.
웹앱이 새 테이블을 쓰기 시작하는 경우가 그렇다.

실행
  python -m batch.jobs.migrate
"""

from __future__ import annotations

import logging
import sys

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard

log = logging.getLogger("migrate")


def main() -> int:
    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    available = [p.stem for p in db.migration_files()]
    print(f"파일 {len(available)}개: {', '.join(available)}")

    with TursoClient() as client:
        applied = db.apply_migrations(client)
        rs = client.execute(
            "SELECT version, substr(applied_at, 1, 19) FROM schema_migrations"
            " ORDER BY version"
        )

    if applied:
        print(f"새로 적용: {', '.join(applied)}")
    else:
        print("새로 적용할 것 없음")

    print("현재 적용 상태")
    for version, applied_at in rs.rows:
        print(f"  {version}  {applied_at}")
    return 0


if __name__ == "__main__":
    sys.exit(guard(main))
