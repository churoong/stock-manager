"""Actions 가 만든 글을 GitHub 이슈 코멘트로 내보낸다 (docs/infra.md 25.17).

**왜 있나.** 폰·클라우드 세션의 프록시가 `*.blob.core.windows.net` 을 막아 **Actions 로그 원문을
받을 수 없다.** 그런데 `db-status.yml`("DB 상태 확인")은 결과를 로그로만 냈다 — 그래서 정작
클라우드 세션에서는 무용지물이었다(2026-09-19 확인). 이슈 API(`api.github.com`)는 프록시가 막지
않으므로, 결과를 이슈 코멘트로 한 부 더 내보내면 세션이 읽을 수 있다.

**왜 이슈인가.** 무료다. 저장소가 프라이빗이라 이슈도 본인만 본다. 저장소에 커밋하는 방식은
Vercel 하루 배포 한도(infra 25.15)를 갉아먹어서 버렸다. 텔레그램은 사용자는 읽지만 세션은
못 읽는다 — 목적이 "세션이 읽는 것" 이므로 맞지 않는다.

**절대 배치를 죽이지 않는다.** 내보내기가 실패해도 종료코드 0 이다. 이건 곁다리 통로이지
작업의 목적이 아니다. 실패하면 이유만 찍는다.

실행
  python scripts/db_status.py | tee out.txt
  python scripts/publish_output.py --title "운영 출력" --file out.txt --note "DB 상태 확인"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API = "https://api.github.com"

#: GitHub 코멘트 본문 상한은 65,536자다. 코드펜스·머리말 자리를 넉넉히 빼고 자른다
MAX_BODY = 60_000
#: 잘릴 때 앞뒤로 남길 몫. 오류는 대개 끝에 있고, 무엇을 돌렸는지는 앞에 있다
HEAD_SHARE = 0.4


def truncate(text: str, limit: int = MAX_BODY) -> str:
    """가운데를 덜어 내고 앞뒤를 남긴다. 앞에는 무엇을 돌렸는지가, 끝에는 오류가 있다."""
    if len(text) <= limit:
        return text
    mark = "\n\n…… 가운데 {:,}자 덜어 냄 (전체 {:,}자). 전문은 Actions 로그에 있다 ……\n\n"
    head_len = int(limit * HEAD_SHARE)
    tail_len = limit - head_len - len(mark.format(0, 0)) - 16
    cut = len(text) - head_len - tail_len
    return text[:head_len] + mark.format(cut, len(text)) + text[-tail_len:]


# **이슈 코멘트로 올리기 전에 비밀값을 가린다** — 마지막 방어선 (docs/infra.md 25.625, 교차검증). Actions 는 콘솔만
# 시크릿을 가리고, tee 로 쓴 run.log 를 여기서 코멘트로 올리면 가려지지 않는다. `python scripts/…` 로 불려 `batch` 를
# import 할 수 없을 수 있어 경로를 한 번 넣는다 — 정의는 `batch/core/redact.py` 한 곳이다
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batch.core.redact import 가림  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_visibility import is_public  # noqa: E402


def target(environ: dict[str, str] | None = None) -> tuple[str, str, str | None]:
    """(올릴 저장소, 토큰, 건너뛸 까닭) — docs/infra.md 25.978.

    `OPS_REPO`·`OPS_TOKEN` 이 있으면 **비공개 운영 저장소**로 보낸다(공개 저장소로 옮긴 뒤의 길, docs/public-repo.md).
    없으면 예전처럼 이 저장소의 이슈로 — 단 **이 저장소가 공개면 올리지 않는다**(리포트 본문·보유·금액이 남이 보는
    이슈에 남는다)."""
    env = os.environ if environ is None else environ
    ops_repo, ops_token = env.get("OPS_REPO", "").strip(), env.get("OPS_TOKEN", "").strip()
    if ops_repo and ops_token:
        return ops_repo, ops_token, None
    if is_public(env):
        return "", "", "공개 저장소라 이슈에 올리지 않는다 — 비공개 운영 저장소(OPS_REPO·OPS_TOKEN 시크릿)가 없다"
    return env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", ""), None


def comment_body(text: str, note: str, run_url: str | None, pinned: str = "") -> str:
    """머리말(언제·무엇·어느 실행) + 코드펜스로 감싼 본문.

    `pinned` 는 **절대 잘리지 않는 앞부분**이다 (2026-09-21, docs/infra.md 25.55).
    따라잡기 로그는 6만 자를 넘길 수 있고, 그러면 `truncate()` 가 **가운데를 덜어 낸다**.
    하필 가운데에 있는 것이 "4. 과거 시세 끝" 같은 단계별 D1 사용량이다 — 하루 한 번뿐인
    실행에서 가장 알고 싶은 숫자가 바로 그것인데, 길이 때문에 사라지면 그날은 헛수고다.
    그래서 그 줄들은 따로 모아 맨 위에 붙이고, 잘림 계산에서 뺀다.
    """
    text, pinned = 가림(text), 가림(pinned)
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    head = f"**{note}** · {when}"
    if run_url:
        head += f" · [실행 기록]({run_url})"
    앞 = f"\n\n{pinned.strip()}\n" if pinned.strip() else ""
    # 앞에 붙인 만큼 본문 몫을 줄인다. 합쳐서 한도를 넘으면 코멘트가 거부된다
    남은몫 = max(1_000, MAX_BODY - len(앞) - len(head))
    # 본문에 ``` 가 들어 있어도 펜스가 깨지지 않게 물결표를 쓴다
    return f"{head}{앞}\n~~~\n{truncate(text, 남은몫)}\n~~~\n"


def _request(url: str, token: str, method: str = "GET", payload: dict | None = None) -> object:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 — 주소는 위 상수에서 온다
        return json.loads(response.read() or b"null")


def find_issue(repo: str, token: str, title: str) -> int | None:
    """제목이 정확히 같은 **열린** 이슈의 번호. 없으면 None.

    검색 API 는 색인이 늦을 수 있어 목록 API 로 훑는다. 이슈가 많지 않은 저장소다.
    """
    issues = _request(f"{API}/repos/{repo}/issues?state=open&per_page=100", token)
    if not isinstance(issues, list):
        return None
    for issue in issues:
        # 풀 리퀘스트도 이슈 목록에 섞여 온다. 걸러 낸다
        if "pull_request" not in issue and issue.get("title") == title:
            return int(issue["number"])
    return None


def publish(repo: str, token: str, title: str, body: str, issue_body: str) -> str:
    """코멘트를 남기고 그 주소를 돌려준다. 이슈가 없으면 만든다."""
    number = find_issue(repo, token, title)
    if number is None:
        created = _request(f"{API}/repos/{repo}/issues", token, "POST", {"title": title, "body": issue_body})
        number = int(created["number"])  # type: ignore[index,call-overload]
    comment = _request(f"{API}/repos/{repo}/issues/{number}/comments", token, "POST", {"body": body})
    return str(comment["html_url"])  # type: ignore[index,call-overload]


ISSUE_BODY = (
    "Actions 가 만든 글을 여기에 코멘트로 쌓는다. 폰·클라우드 세션은 Actions 로그 원문을 받지 못하는데"
    " (프록시가 `*.blob.core.windows.net` 을 막는다) 이슈 API 는 읽을 수 있기 때문이다."
    " 자세한 것은 `docs/infra.md` 25.17.\n\n"
    "**이 이슈를 닫지 마세요.** 닫으면 다음 실행이 새 이슈를 만들어 기록이 흩어진다."
    " 길어지면 오래된 코멘트를 지워도 된다 — 읽는 쪽은 늘 맨 마지막 코멘트만 본다.\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Actions 출력을 이슈 코멘트로 내보낸다")
    parser.add_argument("--title", required=True, help="쌓아 둘 이슈의 제목")
    parser.add_argument("--file", help="내보낼 파일 (없으면 표준입력)")
    parser.add_argument("--note", default="Actions 출력", help="코멘트 머리말")
    parser.add_argument(
        "--head-file",
        help="절대 잘리지 않고 맨 위에 붙일 파일 (단계별 D1 사용량 같은 것). 없어도 된다",
    )
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            # 워크플로가 한 줄도 못 남기고 멈출 수 있다(0번 단계에서 끝나는 등). 그건 고장이 아니다
            print(f"내보낼 것이 없다: {path} 가 없다")
            return 0
        text = path.read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("내보낼 것이 없다: 내용이 비었다")
        return 0
    pinned = ""
    if args.head_file:
        앞경로 = Path(args.head_file)
        # 없어도 그냥 넘어간다 — 한 단계도 못 돌고 멈춘 날에는 이 파일이 안 생긴다
        pinned = 앞경로.read_text(encoding="utf-8") if 앞경로.exists() else ""

    repo, token, 건너뜀 = target()
    if 건너뜀:
        print(f"내보내기 건너뜀: {건너뜀}")
        return 0
    if not token or not repo:
        print("내보내기 건너뜀: GITHUB_TOKEN 또는 GITHUB_REPOSITORY 가 없다")
        return 0

    run_url = None
    if os.environ.get("GITHUB_RUN_ID"):
        # 실행 기록 주소는 **실행한 저장소** 것이다 — 운영 저장소로 보낼 때도 (25.978)
        here = os.environ.get("GITHUB_REPOSITORY", repo)
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{here}/actions/runs/{os.environ['GITHUB_RUN_ID']}"

    try:
        url = publish(repo, token, args.title, comment_body(text, args.note, run_url, pinned), ISSUE_BODY)
        print(f"이슈 코멘트로 내보냈다: {url}")
    except urllib.error.HTTPError as error:
        # 403 이면 대개 워크플로 토큰에 issues: write 가 없다 (infra 25.17)
        print(f"내보내기 실패 (HTTP {error.code}): {error.reason}. 워크플로의 permissions 를 확인하세요")
    except Exception as error:  # noqa: BLE001 — 곁다리 통로가 배치를 죽이면 안 된다
        print(f"내보내기 실패: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
