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


def candidate(name: str, text: str, now: datetime) -> datetime | None:
    """실행 기록을 물어볼 예약 시각 — 예비가 아니고, 지연 여유를 넘겼고, LOOKBACK 안이면. 아니면 None."""
    if name in SKIP:
        return None
    s = due(text, now - timedelta(hours=GRACE_HOURS))
    if s is None or now - s > timedelta(days=LOOKBACK_DAYS):
        return None
    return s


def run(repo: str, token: str, call, now: datetime, root: Path) -> str:  # noqa: ANN001 — call 은 keepalive._call
    깨움, 못깨움, 실패, 미룸 = [], [], [], []
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
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
                       token, {"ref": "main"})  # fmt: skip
        (깨움 if code == 204 else 실패).append(path.name if code == 204 else f"{path.name}({code})")
    return (f"빠진 예약 따라잡기: 깨움 {len(깨움)}개" + (f" ({', '.join(깨움)})" if 깨움 else "")
            + (f" · 다음 회로 미룸 {len(미룸)}개(한 번에 {MAX_PER_RUN}개까지)" if 미룸 else "")
            + (f" · 수동 실행 없음 {', '.join(못깨움)}" if 못깨움 else "")
            + (f" · 실패 {', '.join(실패)}" if 실패 else ""))  # fmt: skip
