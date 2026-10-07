"""예약 워크플로가 60일 무활동으로 꺼지지 않게 매일 다시 켠다 (docs/infra.md 25.981).

**왜 있나.** 공개 저장소에서는 "60일 동안 저장소 활동이 없으면 예약(schedule) 워크플로를 자동으로 끈다"
(GitHub 문서 "Disabling and enabling a workflow"). 2026-10-07 코드 저장소를 공개로 옮겼다(docs/public-repo.md).
결함 수정이 두 달 멈추면 월간 ETF 판정·백테스트·주간 요약·배당·업종이 조용히 멈추고, `daily-kr`·`daily-us` 도
schedule 이 붙어 있어 **워크플로째** 꺼진다 — 꺼진 워크플로는 cron-job.org 의 repository_dispatch 로도 돌지 않는다
`[확인필요: 꺼진 워크플로가 repository_dispatch 를 받는지 — 받지 않는다고 보고 막는다]`.

**무엇을 하나.** 일일 배치 끝에서 이 저장소의 워크플로 목록을 읽어
  - `disabled_inactivity`(무활동으로 꺼짐) → 다시 켠다
  - `active` → 켜기 요청을 한 번 더 보낸다(이미 켜져 있으면 바뀌는 것 없음). 무활동 시계를 되돌리는 효과가 있다고 보는
    keepalive 방식이다 `[확인필요: 켜기 요청이 60일 시계를 되돌리는지 — 되돌리지 않아도 꺼진 것을 다음 날 되살린다]`
  - `disabled_manually`(사람이 끈 것) → **건드리지 않는다**. 사람의 뜻이다

**절대 배치를 죽이지 않는다.** 실패해도 종료코드 0 이다. 이유만 찍는다. 찍는 것은 워크플로 파일 이름과 건수뿐이다
(공개 로그에 운영 출력이 아니다).

실행 (Actions 안에서, `permissions: actions: write`)
  python scripts/keepalive_workflows.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
#: 다시 켤 상태. `disabled_manually`·`disabled_fork` 는 사람이·GitHub 가 일부러 끈 것이라 넣지 않는다
REVIVE = ("disabled_inactivity",)
#: 켜기 요청을 다시 보내 시계를 되돌릴 상태
TOUCH = ("active",)


def plan(workflows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(되살릴 것, 다시 켜기만 할 것). 예약이 없는 워크플로도 넣는다 — 목록 API 가 schedule 유무를 주지 않는다."""
    revive = [w for w in workflows if w.get("state") in REVIVE]
    touch = [w for w in workflows if w.get("state") in TOUCH]
    return revive, touch


def _call(method: str, url: str, token: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def main() -> int:
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("워크플로 살려 두기: GITHUB_TOKEN·GITHUB_REPOSITORY 가 없어 건너뜀")
        return 0
    try:
        status, body = _call("GET", f"{API}/repos/{repo}/actions/workflows?per_page=100", token)
        if status != 200:
            print(f"워크플로 살려 두기: 목록 HTTP {status} — 건너뜀")
            return 0
        revive, touch = plan(json.loads(body).get("workflows") or [])
        실패 = []
        for w in revive + touch:
            code, _ = _call("PUT", f"{API}/repos/{repo}/actions/workflows/{w['id']}/enable", token)
            if code != 204:
                실패.append(f"{w.get('path', w['id']).rsplit('/', 1)[-1]}({code})")
        되살림 = ", ".join(w.get("path", "").rsplit("/", 1)[-1] for w in revive)
        print(
            f"워크플로 살려 두기: 켜 둠 {len(touch)}개"
            + (f" · 무활동으로 꺼져 있던 것 되살림 {len(revive)}개: {되살림}" if revive else "")
            + (f" · 실패 {len(실패)}개: {', '.join(실패)}" if 실패 else "")
        )
    except Exception as exc:  # noqa: BLE001 — 곁다리다. 배치를 죽이지 않는다
        print(f"워크플로 살려 두기 실패(배치는 계속): {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
