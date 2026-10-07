"""D1 따라잡기 단계 앞의 **읽기 예산 문** (docs/infra.md 25.844).

쓰기는 `--budget-reserve` 로 지켰지만 읽기는 보지 않아, 2026-10-01 따라잡기가 D1 하루 읽기 한도(500만 행)를
124% 넘겼다 — UTC 자정까지 웹의 모든 읽기가 실패했고 로그인까지 막혔다(25.843). 단계마다 예상 읽기량을 두고,
**웹 몫(`WEB_READ_RESERVE`)을 남긴 채** 들어가지 않으면 그 단계를 다음 날로 미룬다.

    python scripts/d1_read_gate.py "6. 점수" 1200000   # 들어가도 되면 0, 미루면 DEFER_EXIT(78)

못 재면(카운터를 읽지 못함) 들어간다 — 재는 일이 따라잡기를 멈추게 하면 안 된다(예전 동작과 같다).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import db  # noqa: E402
from batch.core.client import TursoClient  # noqa: E402

#: "미룸" 종료 코드 — 워크플로가 이 값만 미룸으로 읽는다(다른 실패는 예전처럼 들어간다)
DEFER_EXIT = 78

KST = timezone(timedelta(hours=9))

#: 따라잡기가 끝난 뒤 웹(화면·크론·로그인)이 UTC 자정까지 쓸 읽기 몫.
#: 2026-10-01 시작 시점에 웹이 이미 41만 행을 썼다 — 그 두 배 남짓
WEB_READ_RESERVE = 1_000_000


#: 같은 UTC 날 **따라잡기 뒤에** 도는 예약 작업과 그 읽기 어림 (25.847, 교차검증 — 25.844 는 웹 몫만 남겨
#: 그날 밤 국내 일일 배치가 굶을 수 있었다). D1 하루는 UTC 자정에 풀리고 따라잡기는 00:05 UTC 에 돈다 —
#: 08:27 KST(23:27 UTC) 국내 일일 배치는 **같은 UTC 날의 끝**이다.
#: (이름, 요일[월=0]·날짜(그달 며칠)·달 — 없으면 None, 시, 분, 읽기 어림). 국내 일일 배치는 점수+신호를 다시 낸다
#: — 따라잡기 실측
#: (점수 108만 + 신호 102만)에 리포트·시세 읽기를 더해 약 230만 `[확인필요: 일일 배치 실측]`. 감성 수집은 기사
#: 채점·집계뿐이라 10만
#: `[확인필요]`. 미국은 D1 에서 쉰다(catchup_report "미국은 Turso 로 돌아갈 때까지 쉽니다").
#: **주·월 작업도 넣는다** (25.852, 교차검증 — `SKIP_ON_D1` 이 없어 D1 에서도 도는데 빠져 있었다). 몫은 모두 어림
#: `[확인필요: 작업별 실측]`:
#: 필수 백업(일 16:13)은 사람이 넣은 표·뉴스·재무 스냅샷이라 30만, 전체 백업(매월 1일 16:47)은 시세까지 모든 표라 250만,
#: 실적 일정(월 15:53) 5만, 밸류 밴드(월 17:53)와 신호 성과(월 21:07)는 시세를 훑어 각 40만
LATER_TODAY = (
    ("실적 일정", (0,), None, None, 15, 53, 50_000),
    ("필수 백업", (6,), None, None, 16, 13, 300_000),
    ("전체 백업", None, 1, None, 16, 47, 2_500_000),
    ("전체 백업(예비)", None, 8, None, 16, 47, 2_500_000),  # 25.872
    # 매월 작업 (25.854, 교차검증 — 25.852 가 빠뜨렸다). 몫은 어림 `[확인필요: 작업별 실측]`
    ("ETF", None, 2, None, 17, 19, 200_000),
    ("업종", None, 3, None, 18, 43, 100_000),
    ("적립 후보", None, 6, None, 19, 17, 300_000),
    ("배당", None, 5, (4, 5), 18, 41, 100_000),
    ("밸류 밴드", (0,), None, None, 17, 53, 400_000),
    ("신호 성과", (0,), None, None, 21, 7, 400_000),
    ("국내 감성 수집", (6, 0, 1, 2, 3), None, None, 22, 40, 100_000),
    ("국내 일일 배치", (6, 0, 1, 2, 3), None, None, 23, 27, 2_300_000),
)


def later_today(now: datetime) -> list[tuple[str, int]]:
    """`now` 뒤 같은 UTC 날에 남은 예약 작업 (이름, 읽기 어림)."""
    now = now.astimezone(UTC)
    return [(이름, 몫) for 이름, 요일, 날짜, 월, 시, 분, 몫 in LATER_TODAY
            if (요일 is None or now.weekday() in 요일) and (날짜 is None or now.day == 날짜)
            and (월 is None or now.month in 월) and (now.hour, now.minute) < (시, 분)]  # fmt: skip


#: 국내 일일 배치 **예비 실행**(00:35 UTC, 25.870)의 몫 — 그날 08:27 실행이 성공하지 못했을 때만 남긴다 (25.871, 감사).
#: 따라잡기와 같은 UTC 날이라 예전 계산에 빠져 있었다. 어림은 23:27 실행과 같다 `[확인필요: 25.865 뒤 실측]`
LATE_DAILY = ("국내 일일 배치 예비(늦은 리포트)", (0, 1, 2, 3, 4), 0, 35, 2_300_000)


def late_daily_pending(now: datetime, morning_succeeded: bool | None) -> list[tuple[str, int]]:
    """예비 실행이 아직 남았고 그날 아침 실행이 성공하지 못했으면 그 몫. 모르면(None) 남긴다 — 리포트가 먼저다."""
    이름, 요일, 시, 분, 몫 = LATE_DAILY
    now = now.astimezone(UTC)
    if now.weekday() not in 요일 or (now.hour, now.minute) >= (시, 분) or morning_succeeded:
        return []
    return [(이름, 몫)]


def morning_succeeded(client) -> bool | None:
    """오늘(KST) 국내 일일 배치가 성공했나. 못 읽으면 None."""
    try:
        kst_midnight = datetime.now(UTC).astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
        rs = client.execute(
            "SELECT COUNT(*) FROM batch_runs WHERE job_name = 'daily_kr' AND status IN ('success', 'partial')"
            " AND started_at >= ?",
            [kst_midnight.astimezone(UTC).isoformat()],
        )
        return int(rs.scalar() or 0) > 0
    except Exception:  # noqa: BLE001 — 모르면 남긴다
        return None


def decide(
    remaining: int | None, estimate: int, reserve: int = WEB_READ_RESERVE, later: list[tuple[str, int]] | None = None
) -> tuple[bool, str]:
    """(들어가도 되나, 사람이 읽을 문장). 모르면 들어간다.

    남길 몫 = 웹 + 오늘 뒤에 도는 예약 작업(`later_today`). 따라잡기는 **기다릴 수 있고** 일일 배치는 못 기다린다.
    """
    if remaining is None:
        return True, f"남은 읽기를 재지 못해 들어갑니다 (예상 {estimate:,}행)"
    later = later or []
    몫 = reserve + sum(n for _, n in later)
    몫글 = f"웹 {reserve:,}" + "".join(f" + {이름} {n:,}" for 이름, n in later)
    if remaining - 몫 >= estimate:
        return True, f"남은 읽기 {remaining:,}행 − 남길 몫({몫글}) ≥ 예상 {estimate:,}행 — 들어갑니다"
    return False, (f"남은 읽기 {remaining:,}행 − 남길 몫({몫글}) < 예상 {estimate:,}행 — 이 단계는 내일로 미룹니다"
                   " (넘기면 UTC 자정까지 웹 읽기·로그인과 그날 일일 배치가 막힌다, infra 25.843·25.847)")  # fmt: skip


def mark_deferred() -> None:
    """미룬 단계를 워크플로 출력(`deferred=true`)에 적는다 — 알림·복귀 점검이 "돌았다" 와 가른다 (25.847)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("deferred=true\n")
    except OSError as error:
        print(f"[읽기 문] 미룸을 출력에 적지 못했다: {error}")


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "단계"
    estimate = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    remaining: int | None
    try:
        client = TursoClient()
        try:
            used = db.d1_reads_today(client)
            remaining = None if used is None else max(0, db.D1_DAILY_READ_LIMIT - used)
            아침 = morning_succeeded(client)
        finally:
            client.close()
    except Exception as error:  # noqa: BLE001 — 재는 일이 따라잡기를 멈추게 하면 안 된다
        print(f"[읽기 문] {label}: 재지 못했다 ({error}) — 들어갑니다")
        return 0
    now = datetime.now(UTC)
    ok, text = decide(remaining, estimate, later=later_today(now) + late_daily_pending(now, 아침))
    print(f"[읽기 문] {label}: {text}")
    if not ok:
        mark_deferred()
    return 0 if ok else DEFER_EXIT


if __name__ == "__main__":
    sys.exit(main())
