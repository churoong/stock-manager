"""이 Actions 실행이 **공개 저장소**에서 도는가 (docs/infra.md 25.978·25.979, docs/public-repo.md).

공개 저장소에서는 Actions 로그와 이슈를 누구나 본다. 운영 출력(리포트 본문·보유·금액·시세·DB 조회 결과)은
그런 곳에 남기지 않는다 — `scripts/ops_tee.py` 는 로그에 찍지 않고 파일에만 쓰고, `scripts/publish_output.py` 는
비공개 운영 저장소(`OPS_REPO`)로만 보낸다.

판정: 환경변수 `REPO_PUBLIC`(true/false)가 있으면 그것, 없으면 Actions 이벤트 파일(`GITHUB_EVENT_PATH`)의
`repository.visibility == "public"` (또는 `repository.private == false`). **모르면 공개로 본다** — 잘못 판정했을 때
덜 위험한 쪽(로그를 덜 찍는 쪽)이다. Actions 밖(로컬·테스트)에서는 이벤트 파일이 없으므로 비공개로 본다.
"""

from __future__ import annotations

import json
import os


def is_public(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    forced = str(env.get("REPO_PUBLIC", "")).strip().lower()
    if forced in ("true", "1", "yes"):
        return True
    if forced in ("false", "0", "no"):
        return False
    path = env.get("GITHUB_EVENT_PATH")
    if not env.get("GITHUB_ACTIONS") or not path:
        return False  # Actions 밖
    try:
        with open(path, encoding="utf-8") as f:
            repo = (json.load(f) or {}).get("repository") or {}
    except (OSError, ValueError):
        return True  # Actions 안인데 모르면 공개로 — 덜 찍는 쪽
    if "visibility" in repo:
        return str(repo["visibility"]).lower() == "public"
    if "private" in repo:
        return repo["private"] is False
    return True
