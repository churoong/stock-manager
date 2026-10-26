"""KIS모의투자 장중 손절·익절을 한 잡 안에서 10분마다 돌린다 (docs/infra.md 25.1796, docs/paper.md 15.11).

    python scripts/kis_guard_loop.py [--dry-run] 2>&1 | python scripts/ops_tee.py run.log

**왜 예약(cron) 10분 간격을 버렸나**: 2026-10-26(월) 첫 장중 날 `3-59/10` 예약이 09:03~09:33 KST 네 회차 모두
시작조차 안 했다. 이 저장소의 Actions 예약은 한두 시간~네다섯 시간 늦는 일이 흔하다(국내 일일 배치 예비 회차 1~5시간,
미국 4~7시간 — 25.1678). 손절선에 닿은 뒤 몇 시간 뒤에 파는 감시는 감시가 아니다. 공개 저장소라 Actions 분은
무료·무제한이고(CLAUDE.md 비용 규칙), "Actions 에 잦은 폴링을 두지 않는다" 의 까닭이 바로 예약 지연이라 잡 하나가
깨어 있는 꼴은 그 까닭에 걸리지 않는다.

한 회차는 예전처럼 **따로 된 프로세스**(`python -m batch.jobs.intraday_guard`)다 — 실행 기록 한 줄·시간·휴장일 재확인·
DB 연결이 회차마다 새로 시작한다(감시 코드는 그대로). 이 스크립트는 언제 부를지만 정한다:

- 회차 시각 = 그날 감시 창(`intraday_guard.window` — 개장 + 5분 ~ 마감 − 15분, 수능일 늦춤) 시작부터 10분 간격,
  창 끝 포함(끝 눈금은 30초 앞당겨 띄운다 — `END_MARGIN`)
- 한 회차가 길어 다음 시각을 넘기면 밀린 회차는 건너뛰고 다음 시각으로(몰아서 여러 번 돌지 않는다)
- GitHub 잡은 6시간이 상한이라 시작부터 `BUDGET` 이 지나면 새 회차를 열지 않고 `chain=true` 를 남긴다 — 워크플로의
  다른 잡이 같은 워크플로를 다시 깨워 이어 간다(같은 동시 실행 그룹이라 앞 실행이 끝난 뒤 시작한다)
- 휴장일이면 바로 끝난다(이어 가지 않는다)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 회차 간격 — 예전 예약(`3-59/10`)과 같다
INTERVAL = timedelta(minutes=10)
#: 새 회차를 여는 상한(스크립트 시작부터). 잡 상한 `timeout-minutes` 350 − 회차 하나(7분) − 여유
BUDGET = timedelta(minutes=330)
#: 회차 하나의 상한 — 예전 잡 상한(8분)에서 설치 시간을 뺀 몫
RUN_TIMEOUT = "7m"
#: 창 끝 눈금은 이만큼 앞당겨 띄운다 — 하위 프로세스가 뜨고 import 하는 몇 초 사이 `intraday_guard.due` 의 창 끝을 넘겨
#: "시간 밖" 으로 건너뛰었다(25.1796 교차검증 [권고] — 15:15 회차가 빠져 마지막 감시가 15:05 였다)
END_MARGIN = timedelta(seconds=30)


def next_slot(now: datetime, start: datetime, end: datetime, interval: timedelta = INTERVAL) -> datetime | None:
    """`now` 뒤(같으면 그 시각) 첫 회차 시각 — 창 시작부터 `interval` 간격, 창 끝을 넘으면 None"""
    if now <= start:
        return start
    지남 = now - start
    k = -(-지남 // interval)  # 올림
    slot = start + k * interval
    return slot if slot <= end else None


def plan(
    now: datetime, started: datetime, start: datetime, end: datetime, last: datetime | None = None
) -> tuple[str, datetime | None]:
    """다음에 할 일 — ("run", 시각) · ("chain", None) 잡 상한이라 이어 가기 · ("done", None) 창이 끝남.

    첫 회차(`last` 없음)는 창 안이면 **바로** — 이어 받은 실행·늦게 깬 실행이 다음 10분 눈금까지 비지 않게. 그 뒤로는
    앞 회차 시각보다 뒤의 눈금만(회차가 눈금에 딱 맞게 끝나도 같은 눈금을 두 번 돌지 않게)"""
    if last is None and start <= now <= end:
        slot: datetime | None = now
    else:
        slot = next_slot(now if last is None else max(now, last + timedelta(seconds=1)), start, end)
    if slot is None:
        return "done", None
    if slot - started > BUDGET:
        return "chain", None
    return "run", slot


def run_at(slot: datetime, end: datetime) -> datetime:
    """눈금 `slot` 의 회차를 실제로 띄울 시각 — 창 끝 눈금만 `END_MARGIN` 앞당긴다(눈금 자체는 그대로 세어 같은
    눈금을 두 번 돌지 않는다)"""
    return min(slot, end - END_MARGIN)


def _window(now: datetime) -> tuple[datetime, datetime] | None:
    """오늘 감시 창(UTC) — 휴장일이면 None. 달력이 시각을 모르면 고정 창(`WINDOW_KST`)"""
    from batch.core import calendar as cal
    from batch.jobs import intraday_guard as ig

    tz = cal.market_tz("KR")
    오늘 = now.astimezone(tz).date()
    if not cal.is_session("KR", 오늘):
        return None
    창 = ig.window("KR", 오늘)
    if 창 is not None:
        return 창
    시작, 끝 = ig.WINDOW_KST
    return (
        datetime.combine(오늘, 시작, tzinfo=tz).astimezone(UTC),
        datetime.combine(오늘, 끝, tzinfo=tz).astimezone(UTC),
    )


def _output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KIS모의투자 장중 손절·익절 — 한 잡 안에서 10분마다")
    parser.add_argument("--dry-run", action="store_true", help="회차마다 --dry-run 으로(주문·알림 없음)")
    args = parser.parse_args(argv)
    started = datetime.now(UTC)
    창 = _window(started)
    _output("chain", "false")
    if 창 is None:
        print("건너뜀: 휴장일", flush=True)
        return 0
    명령 = ["timeout", "--kill-after=30s", RUN_TIMEOUT, sys.executable, "-m", "batch.jobs.intraday_guard"]
    if args.dry_run:
        명령.append("--dry-run")
    실패 = 0
    회차 = 0
    마지막: datetime | None = None
    while True:
        할일, 시각 = plan(datetime.now(UTC), started, *창, 마지막)
        if 할일 == "done":
            break
        if 할일 == "chain":
            print(f"잡 상한 — 회차 {회차}번 뒤 다음 실행으로 이어 갑니다", flush=True)
            _output("chain", "true")
            break
        assert 시각 is not None
        time.sleep(max(0.0, (run_at(시각, 창[1]) - datetime.now(UTC)).total_seconds()))
        회차 += 1
        마지막 = 시각
        rc = subprocess.run(명령, check=False).returncode  # noqa: S603 — 고정된 명령
        if rc != 0:
            실패 += 1
            print(f"회차 {회차} 실패(종료코드 {rc}) — 다음 회차는 그대로", flush=True)
    print(f"끝: 회차 {회차} · 실패 {실패}", flush=True)
    return 1 if 실패 else 0


if __name__ == "__main__":
    raise SystemExit(main())
