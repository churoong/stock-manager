"""Turso **월 읽기 예산의 진도 문** (docs/infra.md 25.886).

Turso 무료 플랜은 한 달 읽기 5억 행이다(`db.TURSO_MONTHLY_READ_LIMIT`). 2026-10-02 복귀 날 하루에 5,545만 행을 썼다 —
그 속도면 9일 만에 바닥난다. 바닥나면 **월말까지** 일일 리포트·웹이 함께 멈춘다(2026-09 에 실제로 그랬다, 24절).
D1 의 `d1_read_gate.py` 는 **하루** 예산을 보지만 Turso 는 **한 달** 예산이라, 하루치가 아니라 "달의 진도" 를 본다.

급하지 않은 무거운 작업(전체 백업·백테스트) 앞에 둔다. 이번 달 쓴 양 + 예상치가 허용선을 넘으면 미룬다:

    허용선 = min(진도선, 상한선)
    진도선 = 한도 × (달이 지난 몫) + 한도 × PACE_SLACK      — 앞서 쓰는 것을 조금은 봐준다(주·월 작업이 몰리는 날)
    상한선 = 한도 − CORE_DAILY_READS × (남은 날)             — 남은 날의 일일 배치·웹 몫은 반드시 남긴다

    python scripts/turso_read_gate.py backup 8000000   # 들어가도 되면 0, 미루면 DEFER_EXIT(78)

Turso 가 아니면(D1 임시 운영) 들어간다 — D1 은 제 문이 따로 있다.
못 재면 들어간다(재는 일이 작업을 멈추게 하면 안 된다).
미루면 `batch_runs` 에 `skipped` 기록을 남겨 상태 화면에 보이게 하고, 워크플로 출력 `deferred=true` 를 적는다.
"""

from __future__ import annotations

import calendar
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import client as backend  # noqa: E402
from batch.core import db  # noqa: E402

#: "미룸" 종료 코드 — `d1_read_gate.DEFER_EXIT` 와 같은 값. 워크플로가 이 값만 미룸으로 읽는다
DEFER_EXIT = 78

#: 진도보다 이만큼(한도의 몫)은 앞서도 된다. 매월 1·2일에 전체 백업·ETF 가 몰린다 — 5% = 2,500만 행
PACE_SLACK = 0.05

#: 남은 날마다 반드시 남길 읽기 — 국내·미국 일일 배치와 웹 크론·화면
#: `[확인필요: 25.885 실행별 읽기량으로 실측해 고친다]`. 어림: D1 따라잡기 실측(점수 108만 + 신호 102만, 25.847)에
#: 리포트·시세를 더한 국내 약 230만, 미국은 종목이 두 배라 약 300만, 웹 약 50만
CORE_DAILY_READS = 6_000_000


def month_progress(now: datetime) -> tuple[float, float]:
    """(달이 지난 몫 0~1, 남은 날 수). 하루 안의 시각까지 센다."""
    now = now.astimezone(UTC)
    days = calendar.monthrange(now.year, now.month)[1]
    지난날 = (now.day - 1) + (now.hour * 3600 + now.minute * 60 + now.second) / 86400
    return 지난날 / days, days - 지난날


def decide(
    used: int | None, estimate: int, now: datetime, limit: int = db.TURSO_MONTHLY_READ_LIMIT
) -> tuple[bool, str]:
    """(들어가도 되나, 사람이 읽을 문장). 이번 달 쓴 양을 모르면 들어간다."""
    if used is None:
        return True, f"이번 달 읽기를 재지 못해 들어갑니다 (예상 {estimate:,}행)"
    몫, 남은날 = month_progress(now)
    진도선 = int(limit * 몫 + limit * PACE_SLACK)
    상한선 = int(limit - CORE_DAILY_READS * 남은날)
    허용 = min(진도선, 상한선)
    글 = (f"이번 달 {used:,}행 + 예상 {estimate:,}행 = {used + estimate:,}행, 허용선 {허용:,}행"
          f" (진도 {몫:.0%}·여유 {PACE_SLACK:.0%} → {진도선:,}"
          f" / 남은 {남은날:.1f}일 몫을 뺀 상한 {상한선:,})")  # fmt: skip
    if used + estimate <= 허용:
        return True, f"{글} — 들어갑니다"
    return False, f"{글} — 미룹니다 (넘기면 월말 전에 한도가 바닥나 일일 리포트·웹이 함께 멈춘다, infra 24절·25.886)"


def mark_deferred() -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("deferred=true\n")
    except OSError as error:
        print(f"[Turso 읽기 문] 미룸을 출력에 적지 못했다: {error}")


def record_skip(client, job: str, text: str) -> None:
    """미룬 것을 실행 기록으로 남긴다 — 상태 화면이 "안 돌았다" 와 "미뤘다" 를 가른다. 못 남겨도 미룸은 그대로다."""
    try:
        번호 = db.start_batch_run(client, job_name=job, market=None, trade_date=None)
        db.finish_batch_run(
            client, 번호, status="skipped", step_log={"reason": "Turso 월 읽기 진도", "detail": text}, error_text=text
        )
    except Exception as error:  # noqa: BLE001
        print(f"[Turso 읽기 문] 미룸 기록을 남기지 못했다: {error}")


def main() -> int:
    job = sys.argv[1] if len(sys.argv) > 1 else "작업"
    estimate = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    try:
        if backend.resolved_backend() != backend.TURSO:
            print(f"[Turso 읽기 문] {job}: Turso 가 아니라 이 문은 보지 않습니다")
            return 0
        client = backend.TursoClient()
    except Exception as error:  # noqa: BLE001 — 재는 일이 작업을 멈추게 하면 안 된다
        print(f"[Turso 읽기 문] {job}: 재지 못했다 ({error}) — 들어갑니다")
        return 0
    try:
        남음 = db.remaining_read_budget_or_none(client)
        used = None if 남음 is None else db.TURSO_MONTHLY_READ_LIMIT - 남음
        ok, text = decide(used, estimate, datetime.now(UTC))
        print(f"[Turso 읽기 문] {job}: {text}")
        if not ok:
            mark_deferred()
            record_skip(client, job, text)
    finally:
        client.close()
    return 0 if ok else DEFER_EXIT


if __name__ == "__main__":
    sys.exit(main())
