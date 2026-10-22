"""GitHub 예약이 빠진 워크플로를 대신 깨운다 (docs/infra.md 25.1015).

**왜 있나.** 2026-10-07 코드 저장소를 새 공개 저장소로 옮긴 뒤 GitHub 예약(schedule)이 크게 늦거나 빠졌다(10-08 실측).
국내 수급 수집 09:17 예약이 16:25 에(약 7시간), 미국 일일이 6시간 넘게 늦게 돌았고, 국내 감성 채점(22:40)·
국내 일일의 예비 예약(23:27·00:35)·D1 따라잡기(00:05)는 아예 돌지 않았다.
주간 백업·유니버스·재무처럼 **예약에만 기대는** 작업이 조용히 빠질 수 있다.
cron-job.org 가 확실히 부르는 일일 배치 곁의 keepalive 잡(`actions: write`)이 이 스크립트를 부른다.

**무엇을 하나.** 워크플로 파일마다 cron 을 읽어 **가장 최근 예약 시각 S** 를 낸다. S 가 지금보다 `GRACE_HOURS`(8시간 —
실측 지연 최대 7시간보다 길게) 넘게 지났는데 S 뒤에 그 워크플로 실행이 **하나도 없으면** `workflow_dispatch` 로 깨운다.
- `LOOKBACK_DAYS`(4일)보다 오래된 놓친 예약은 건드리지 않는다 — 그 사이 keepalive 도 돌지 않았다는 뜻이라 사람이 본다
- 예약이 **예비**인 것(`SKIP`)은 깨우지 않는다 — 일일 배치는 cron-job.org 가 부르고 스스로 "이미 돌았나" 를 본다
- `workflow_dispatch` 가 없는 워크플로는 깨울 수 없다 — 이름만 찍는다

**절대 배치를 죽이지 않는다.** 실패해도 이유만 찍는다. 찍는 것은 파일 이름과 건수뿐이다(공개 로그).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: 예약이 빠졌다고 보는 지연 — 10-08 실측 최대 지연(약 7시간)보다 길게. 짧으면 늦게 오는 예약과 겹쳐 두 번 돈다
GRACE_HOURS = 8
#: 이보다 오래된 놓친 예약은 따라잡지 않는다
LOOKBACK_DAYS = 4
#: 한 번에 깨우는 상한 (docs/infra.md 25.1029). 2026-10-08 02:47 UTC 새 저장소의 첫 따라잡기가 주간 작업 12개(전체
#: 백업·백업 복구
#: 리허설·성과 지표·밸류 밴드·유니버스·재무…)를 **한꺼번에** 깨웠다 — 새 저장소라 Actions 캐시(시세 사본)가 비어 각자
#: 처음부터
#: 읽었고, 같은 날 Turso 월 읽기 한도에 걸렸다. keepalive 는 하루 두 번(국내·미국 일일 배치) 돌고 놓친 예약은 4일까지
#: 따라잡으니
#: 셋씩이면 이틀 안에 다 깨운다. 앞서 깨운 작업이 사본을 캐시에 넣어 둔 뒤라 뒤의 작업은 바뀐 것만 읽는다
MAX_PER_RUN = 3
#: 예약이 예비인 워크플로 — 주 경로가 따로 있다
SKIP = {
    "daily-kr.yml": "cron-job.org 가 부른다. 예약은 예비이고 스스로 이미 돈 날을 건너뛴다",
    "daily-us.yml": "위와 같다",
    "d1-catchup.yml": "D1 한도 따라잡기 예비 — 필요할 때만 뜻이 있다",
    "turso-return.yml": "Turso 복귀 예비 — 웹 감시가 주 경로",
}

_CRON = re.compile(r"^\s*-\s*cron:\s*[\"']([^\"']+)[\"']")


def crons(text: str) -> list[str]:
    return [m.group(1).strip() for line in text.splitlines() if (m := _CRON.match(line))]


def has_dispatch(text: str) -> bool:
    return re.search(r"^\s*workflow_dispatch\s*:", text, re.M) is not None


def _field(spec: str, lo: int, hi: int) -> set[int]:
    """cron 한 칸 → 값 집합. `*`, 숫자, `a-b`, `a,b`, `*/n`, `a-b/n` 만 (우리 워크플로가 쓰는 것)."""
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return out


def last_due(cron: str, now: datetime) -> datetime | None:
    """now 이하의 가장 최근 예약 시각(UTC). LOOKBACK_DAYS + 1 일 안에 없으면 None.

    dom 과 dow 가 둘 다 `*` 가 아니면 cron 규칙대로 **둘 중 하나**만 맞아도 된다. dow 의 7 은 일요일(0)이다."""
    m, h, dom, mon, dow = cron.split()[:5]
    mins, hours, months = _field(m, 0, 59), _field(h, 0, 23), _field(mon, 1, 12)
    doms, dows = _field(dom, 1, 31), {d % 7 for d in _field(dow, 0, 7)}
    either = dom != "*" and dow != "*"
    day = now.date()
    for back in range(LOOKBACK_DAYS + 2):
        d = day - timedelta(days=back)
        cron_dow = (d.weekday() + 1) % 7  # 월=1 … 일=0
        ok_dom, ok_dow = d.day in doms, cron_dow in dows
        if d.month not in months or not ((ok_dom or ok_dow) if either else (ok_dom and ok_dow)):
            continue
        for hh in sorted(hours, reverse=True):
            for mm in sorted(mins, reverse=True):
                t = datetime(d.year, d.month, d.day, hh, mm, tzinfo=UTC)
                if t <= now:
                    return t
    return None


def due(text: str, now: datetime) -> datetime | None:
    """이 워크플로의 가장 최근 예약 시각 — 여러 cron 중 가장 늦은 것."""
    times = [t for c in crons(text) if (t := last_due(c, now)) is not None]
    return max(times) if times else None


def missed_cron(text: str, now: datetime) -> str | None:
    """가장 최근 예약 시각을 낸 cron 글자 — `due` 와 같은 고르기."""
    pairs = [(t, c) for c in crons(text) if (t := last_due(c, now)) is not None]
    return max(pairs)[1] if pairs else None


def dispatch_body(text: str, now: datetime) -> dict:
    """수동 실행 요청 본문 (docs/infra.md 25.1305, 운영 감사 재현).

    워크플로가 `missed_schedule` 입력을 받으면 빠진 예약의 cron 을 넘긴다 — 수동 실행에는 `github.event.schedule` 이
    없어, 예약마다 하는 일이 다른 워크플로(월 1일 전체 백업, 미국 월 백테스트, 재무 화요일 재시도)가 따라잡기로 깨면
    기본 갈래(필수 백업·국내)로 돌았다."""
    body: dict = {"ref": "main"}
    if re.search(r"^\s*missed_schedule\s*:", text, re.M) and (c := missed_cron(text, now)):
        body["inputs"] = {"missed_schedule": c}
    return body


def candidate(name: str, text: str, now: datetime) -> datetime | None:
    """실행 기록을 물어볼 예약 시각 — 예비가 아니고, 지연 여유를 넘겼고, LOOKBACK 안이면. 아니면 None."""
    if name in SKIP:
        return None
    s = due(text, now - timedelta(hours=GRACE_HOURS))
    if s is None or now - s > timedelta(days=LOOKBACK_DAYS):
        return None
    return s


def per_cron(text: str) -> bool:
    """cron 마다 따로 따라잡는 워크플로인가 — `run-name` 에 예약 cron 을 싣는 것만 (docs/infra.md 25.1437, 29회차 P1).

    GitHub 실행 목록에는 어느 cron 에서 왔는지 칸이 없다. 실행 이름(`display_title`)에 cron 글자가 있어야 가른다"""
    return re.search(r"^run-name:.*missed_schedule", text, re.M) is not None


def missed_crons(text: str, now: datetime) -> list[tuple[datetime, str]]:
    """지연 여유를 넘겼고 LOOKBACK 안인 (예약 시각, cron) — 이른 것부터"""
    out = []
    for c in crons(text):
        s = last_due(c, now - timedelta(hours=GRACE_HOURS))
        if s is not None and now - s <= timedelta(days=LOOKBACK_DAYS):
            out.append((s, c))
    return sorted(out)


#: 끝났지만 일을 못 한 실행 — "있음" 으로 보지 않는다(25.1660, 11-03 판정 실행 감사 [필수]).
#: 같은 그룹의 셋째 요청이 대기 실행을 밀어내 취소되거나, 시간초과·설치 실패면 그 예약은 빠진 것과 같다
BAD_CONCLUSIONS = frozenset({"cancelled", "failure", "timed_out", "startup_failure"})
#: 같은 예약을 다시 깨우는 상한 — 일을 못 한 실행이 이만큼 쌓였으면 더 깨우지 않고 "실패" 로 알린다(같은 까닭으로 되풀이
#: 실패하는 실행이 따라잡기마다 다시 깨어나지 않게)
MAX_BAD_RETRIES = 2


def _per_cron_dispatch(repo: str, token: str, call, now: datetime, name: str, text: str) -> tuple[str, str | None]:  # noqa: ANN001
    """cron 마다 그 예약 뒤 **그 cron 이름이 붙은** 실행이 있나 본다. 빠진 것 가운데 **가장 이른 하나만** 깨운다 —
    같은 동시 실행 그룹은 대기를 하나만 둬, 한 번에 둘을 깨우면 하나가 밀려난다(backtest.yml). 다음 회에 다음 것을.
    예전(`due`)은 가장 늦은 cron 하나만 봐서, 국내 백테스트(17:11) 예약이 빠지고 미국(19:11)이 돌면 국내를 깨우지
    않았고 둘 다 빠지면 미국만 깨웠다. 돌았지만 스스로 건너뛴 실행(읽기 진도 문)은 "있음" 이다 — keepalive 에는
    DB 비밀이 없어 가르지 않는다(그 달은 판정 불가, docs/factors.md 12장 머리).
    돌려준 값: ("깨움"|"없음"|"실패", 이름). **더 깨우지 않는 cron(일을 못 한 실행이 상한만큼)은 모아 두고
    다음 cron 을 본다** — 바로 돌려주면 국내가 두 번 실패한 달에 미국 예약이 빠져도 4일 내내 깨우지 않았다
    (25.1661, 교차검증 [필수])"""
    멈춤: list[str] = []
    for s, c in missed_crons(text, now):
        status, body = call("GET", f"https://api.github.com/repos/{repo}/actions/workflows/{name}/runs"
                                   f"?per_page=30&created=%3E%3D{s.strftime('%Y-%m-%dT%H:%M:%SZ')}", token)  # fmt: skip
        if status != 200:
            return "실패", f"{name}({status})"
        runs = [r for r in json.loads(body).get("workflow_runs") or [] if c in str(r.get("display_title") or "")]
        못함 = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") in BAD_CONCLUSIONS]
        if len(못함) < len(runs):
            continue  # 돌고 있거나 끝까지 돈 실행이 있다(스스로 건너뛴 것 포함 — 위 설명)
        if len(못함) >= MAX_BAD_RETRIES:
            멈춤.append(f"{name}[{c}] 일을 못 한 실행 {len(못함)}번")
            continue
        code, _ = call("POST", f"https://api.github.com/repos/{repo}/actions/workflows/{name}/dispatches",
                       token, {"ref": "main", "inputs": {"missed_schedule": c}})  # fmt: skip
        if code == 204:
            return "깨움", f"{name}[{c}]" + (f" · 멈춤 {'; '.join(멈춤)}" if 멈춤 else "")
        return "실패", f"{name}({code})"
    return ("실패", "; ".join(멈춤)) if 멈춤 else ("없음", None)


def run(repo: str, token: str, call, now: datetime, root: Path) -> str:  # noqa: ANN001 — call 은 keepalive._call
    깨움, 못깨움, 실패, 미룸 = [], [], [], []
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if path.name not in SKIP and per_cron(text) and has_dispatch(text):
            if len(깨움) >= MAX_PER_RUN:
                if missed_crons(text, now):
                    미룸.append(path.name)
                continue
            결과, 이름 = _per_cron_dispatch(repo, token, call, now, path.name, text)
            if 결과 == "깨움":
                깨움.append(이름)
            elif 결과 == "실패":
                실패.append(이름)
            continue
        s = candidate(path.name, text, now)
        if s is None:
            continue
        status, body = call("GET", f"https://api.github.com/repos/{repo}/actions/workflows/{path.name}/runs"
                                   f"?per_page=1&created=%3E%3D{s.strftime('%Y-%m-%dT%H:%M:%SZ')}", token)  # fmt: skip
        if status != 200:
            실패.append(f"{path.name}({status})")
            continue
        if json.loads(body).get("workflow_runs"):
            continue  # 예약 시각 뒤 실행이 있다(늦게라도 돌았거나 손으로 돌렸다)
        if not has_dispatch(text):
            못깨움.append(path.name)
            continue
        if len(깨움) >= MAX_PER_RUN:
            미룸.append(path.name)
            continue
        code, _ = call("POST", f"https://api.github.com/repos/{repo}/actions/workflows/{path.name}/dispatches",
                       token, dispatch_body(text, now))  # fmt: skip
        (깨움 if code == 204 else 실패).append(path.name if code == 204 else f"{path.name}({code})")
    return (f"빠진 예약 따라잡기: 깨움 {len(깨움)}개" + (f" ({', '.join(깨움)})" if 깨움 else "")
            + (f" · 다음 회로 미룸 {len(미룸)}개(한 번에 {MAX_PER_RUN}개까지)" if 미룸 else "")
            + (f" · 수동 실행 없음 {', '.join(못깨움)}" if 못깨움 else "")
            + (f" · 실패 {', '.join(실패)}" if 실패 else ""))  # fmt: skip
