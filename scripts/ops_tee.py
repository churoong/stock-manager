"""`tee` 대신 — 운영 출력을 파일에 쓰고, **비공개 저장소에서만** 로그에도 찍는다 (docs/infra.md 25.978).

    python -m batch.jobs.daily 2>&1 | python scripts/ops_tee.py -a run.log
    python scripts/d1_usage.py 2>&1 | python scripts/ops_tee.py -a catchup.log d1-usage.log

공개 저장소(`ops_visibility.is_public`)면 로그에는 줄 수만 찍는다 — 리포트 본문·보유·금액·시세가 남이 보는 로그에
남지 않게. 파일은 그대로 써서 `publish_output.py` 가 비공개 운영 저장소로 보낸다. 종료코드는 늘 0 이다(앞 명령의
실패는 `set -o pipefail` 이 그대로 넘긴다).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_visibility import is_public  # noqa: E402


def main(argv: list[str]) -> int:
    append = False
    files: list[str] = []
    for a in argv:
        if a == "-a":
            append = True
        else:
            files.append(a)
    public = is_public()
    handles = [open(f, "a" if append else "w", encoding="utf-8") for f in files]  # noqa: SIM115
    lines = 0
    try:
        for line in sys.stdin:
            lines += 1
            for h in handles:
                h.write(line)
                h.flush()
            if not public:
                sys.stdout.write(line)
                sys.stdout.flush()
    finally:
        for h in handles:
            h.close()
    if public:
        print(f"(공개 저장소 — 출력 {lines}줄은 로그에 찍지 않고 {', '.join(files) or '파일 없음'}에만 남겼다)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
