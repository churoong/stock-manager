"""예약 워크플로가 60일 무활동으로 꺼지지 않게 매일 다시 켠다 (docs/infra.md 25.981).

**왜 있나.** 공개 저장소에서는 "60일 동안 저장소 활동이 없으면 예약(schedule) 워크플로를 자동으로 끈다"
(GitHub 문서 "Disabling and enabling a workflow"). 2026-10-07 코드 저장소를 공개로 옮겼다(docs/public-repo.md).
결함 수정이 두 달 멈추면 월간 ETF 판정·백테스트·주간 요약·배당·업종이 조용히 멈추고, `daily-kr`·`daily-us` 도
schedule 이 붙어 있어 **워크플로째** 꺼진다 — 꺼진 워크플로는 cron-job.org 의 repository_dispatch 로도 돌지 않는다
`[확인필요: 꺼진 워크플로가 repository_dispatch 를 받는지 — 받지 않는다고 보고 막는다]`.

**무엇을 하나.** 일일 배치(국내·미국) 곁의 작은 작업(`keepalive` 잡)이
  1. **마지막 커밋이 `COMMIT_AFTER_DAYS`(45일)를 넘었으면 빈 커밋을 하나 만든다** (25.986, 교차검증).
     커밋은 "저장소 활동" 이다. 예전(25.981)에는 켜기 요청만 했는데, 60일이 차면 `daily-kr`·`daily-us` 도 함께 꺼져
     **다시 켤 주체가 남지 않는다** —
     "꺼진 것을 다음 날 되살린다" 는 틀린 말이었다. 빈 커밋은 트리가 같아 코드가 바뀌지 않는다(Vercel 배포 한 번)
  2. 워크플로 목록을 읽어 `disabled_inactivity` 는 다시 켜고, `active` 는 켜기 요청을 한 번 더 보낸다(이미 켜져 있으면
     바뀌는 것 없음 `[확인필요: 이것만으로 60일 시계가 되돌아가는지 — 1 이 있어 기대지 않는다]`)
  3. `disabled_manually`(사람이 끈 것) → **건드리지 않는다**. 사람의 뜻이다

**절대 배치를 죽이지 않는다.** 실패해도 종료코드 0 이다. 이유만 찍는다. 찍는 것은 워크플로 파일 이름과 건수뿐이다
(공개 로그에 운영 출력이 아니다).

실행 (Actions 안에서, `permissions: actions: write, contents: write` — 배치 본체와 **다른 잡**에만 준다)
  python scripts/keepalive_workflows.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

API = "https://api.github.com"
#: 다시 켤 상태. `disabled_manually`·`disabled_fork` 는 사람이·GitHub 가 일부러 끈 것이라 넣지 않는다
REVIVE = ("disabled_inactivity",)
#: 켜기 요청을 다시 보내 시계를 되돌릴 상태
TOUCH = ("active",)
#: 마지막 커밋이 이만큼 지났으면 빈 커밋을 만든다 — 60일 문턱보다 넉넉히(주말·연휴에 일일 배치가 안 돌아도)
COMMIT_AFTER_DAYS = 45


def plan(workflows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(되살릴 것, 다시 켜기만 할 것). 예약이 없는 워크플로도 넣는다 — 목록 API 가 schedule 유무를 주지 않는다."""
    revive = [w for w in workflows if w.get("state") in REVIVE]
    touch = [w for w in workflows if w.get("state") in TOUCH]
    return revive, touch


def needs_commit(last_commit_iso: str, now: datetime) -> bool:
    last = datetime.fromisoformat(last_commit_iso.replace("Z", "+00:00"))
    return now - last > timedelta(days=COMMIT_AFTER_DAYS)


def _call(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
    })  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def touch_commit(repo: str, token: str, now: datetime) -> str:
    """마지막 커밋이 오래됐으면 같은 트리로 빈 커밋을 하나 얹는다. 무엇을 했는지 한 줄을 돌려준다."""
    status, body = _call("GET", f"{API}/repos/{repo}", token)
    if status != 200:
        return f"저장소 정보 HTTP {status}"
    branch = json.loads(body)["default_branch"]
    status, body = _call("GET", f"{API}/repos/{repo}/commits/{branch}", token)
    if status != 200:
        return f"마지막 커밋 HTTP {status}"
    head = json.loads(body)
    when = head["commit"]["committer"]["date"]
    if not needs_commit(when, now):
        return f"마지막 커밋 {when[:10]} — 빈 커밋 필요 없음"
    status, body = _call("POST", f"{API}/repos/{repo}/git/commits", token, {
        "message": f"저장소 활동 유지 — 마지막 커밋 {when[:10]} 뒤 {COMMIT_AFTER_DAYS}일 넘음 (공개 저장소 60일 무활동"
                   " 예약 꺼짐 대비, docs/infra.md 25.986)",
        "tree": head["commit"]["tree"]["sha"], "parents": [head["sha"]],
    })  # fmt: skip
    if status != 201:
        return f"빈 커밋 만들기 HTTP {status}"
    new = json.loads(body)["sha"]
    status, _ = _call("PATCH", f"{API}/repos/{repo}/git/refs/heads/{branch}", token, {"sha": new})
    return f"빈 커밋 {new[:7]} (마지막 커밋 {when[:10]})" if status == 200 else f"브랜치 옮기기 HTTP {status}"


def main() -> int:
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("워크플로 살려 두기: GITHUB_TOKEN·GITHUB_REPOSITORY 가 없어 건너뜀")
        return 0
    try:
        print(f"저장소 활동: {touch_commit(repo, token, datetime.now(UTC))}")
    except Exception as exc:  # noqa: BLE001 — 곁다리다
        print(f"저장소 활동 확인 실패(배치는 계속): {type(exc).__name__}")
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
    # GitHub 예약이 빠진 워크플로를 대신 깨운다 (25.1015)
    try:
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import schedule_catchup

        print(schedule_catchup.run(repo, token, _call, datetime.now(UTC), Path(__file__).resolve().parent.parent))
    except Exception as exc:  # noqa: BLE001 — 곁다리다
        print(f"빠진 예약 따라잡기 실패(배치는 계속): {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
