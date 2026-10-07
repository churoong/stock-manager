"""주간 운영 요약 — 일요일 아침 텔레그램 한 통 (docs/health.md 7장, docs/infra.md 25.943).

지난 7일 동안 사람이(또는 세션이) `batch_runs` 를 훑어 손으로 확인하던 네 가지를 한 메시지로 보낸다.

  1. 실행 결과 — 작업마다 성공·부분·실패·건너뜀 수. 전부 성공이면 한 줄
  2. 되풀이된 경고·실패 사유 — 같은 머리말로 묶어 많은 순
  3. Turso 월 읽기 — 쓴 몫과 달의 진도(진도보다 앞서면 표시)
  4. 수정주가 — 국내 일일 배치가 "기업행위로 보이는 종목" 을 알린 뒤 `adjust_kr` 이 돌았는지
  5. 오래 안 돈 예약 작업 — 기대 주기를 넘긴 것

**읽기만 한다.** 쓰는 것은 자기 실행 기록(`batch_runs`) 한 줄뿐이다. 알림이지 판단이 아니다 —
점수·신호·매매를 바꾸지 않는다. 무응답 감시(docs/health.md 1~3장)는 "오늘 안 돈 것" 을 그날 알리고,
이것은 "지난주에 무슨 일이 있었나" 를 모아 말한다. 둘은 겹치지 않는다.

실행
  python -m batch.jobs.weekly_summary            # 보낸다
  python -m batch.jobs.weekly_summary --dry-run  # 찍기만
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.notify import telegram
from batch.services import column_rot

log = logging.getLogger("weekly_summary")

JOB_NAME = "weekly_summary"
#: 돌아보는 날수
WINDOW_DAYS = 7
#: 되풀이된 경고로 치는 최소 횟수. 한 번 난 것은 그날 실패 알림이 이미 말했다
REPEAT_MIN = 2
#: 경고를 묶는 머리말 길이 — 숫자·종목이 달라도 같은 종류면 한 줄로
PREFIX_LEN = 24
#: 한 절에 싣는 최대 줄 수. 텔레그램 한 조각(3,900자)에 들어가게
MAX_LINES = 8

#: **예약 작업의 기대 주기(일)** — 이보다 오래 성공이 없으면 "오래 안 돈 작업" 에 싣는다.
#: 값은 워크플로 cron 의 주기에 여유(휴장·한 번 건너뜀)를 더한 것이다. 주기가 바뀌면 여기도 바꾼다 —
#: `tests/test_weekly_summary_943.py` 가 예약 워크플로의 작업 이름이 여기 있는지 본다
EXPECTED_DAYS: dict[str, int] = {
    "daily_kr": 4, "daily_us": 4, "sentiment": 4, "monitor_targets": 4, "index_prices": 4,
    "portfolio": 4, "sell_flags": 4, "scores": 4, "signals": 4,  # 거래일마다. 연휴(최장 11일)는 오탐이 될 수 있다
    "kis_flows": 4,  # 거래일마다 (25.987). KIS 가 30일만 주므로 길게 멈추면 그 구간은 영영 빈다
    "metrics": 10, "universe": 10, "valuation_bands": 10, "signal_outcomes": 10, "earnings_calendar": 10,
    "backup": 10,  # 주 1회
    "dividends": 20,  # 월 2회
    "financials": 40, "us_financials": 40, "sectors": 40, "accumulation": 40, "etf": 40, "etf_satellite": 40,  # 월 1회
    "backtest": 40,  # 매달 3일 (25.942)
}  # fmt: skip

_기업행위 = re.compile(r"기업행위로 보이는 종목 \d+개: (.+?)\.")
_종목코드 = re.compile(r"\b(\d{6})\b")


@dataclass(frozen=True)
class Run:
    job_name: str
    market: str | None
    status: str
    started_at: str
    error_text: str | None
    warnings: tuple[str, ...]


def load_runs(client: TursoClient, since: str) -> list[Run]:
    rows = client.execute(
        "SELECT job_name, market, status, started_at, error_text, step_log FROM batch_runs"
        " WHERE started_at >= ? AND job_name <> ? ORDER BY started_at",
        [since, JOB_NAME],
    ).dicts()
    out: list[Run] = []
    for r in rows:
        경고: tuple[str, ...] = ()
        try:
            로그 = json.loads(r["step_log"]) if r["step_log"] else {}
            if isinstance(로그, dict) and isinstance(로그.get("warnings"), list):
                경고 = tuple(str(w) for w in 로그["warnings"])
        except (TypeError, ValueError):
            pass
        out.append(Run(str(r["job_name"]), r["market"], str(r["status"]), str(r["started_at"]), r["error_text"], 경고))
    return out


def load_last_success(client: TursoClient) -> dict[str, str]:
    """작업마다 마지막으로 **돈** 시각 — 성공과 부분 성공. 시장은 합친다(한쪽만 멈춘 것은 그날 무응답 감시가 말한다).

    `partial` 도 센다 (25.946 첫 실행에서 고침). 일일 배치는 포트폴리오 경고 하나로 거의 매일 partial 로 끝나,
    성공만 보면 "daily_kr 마지막 성공 18일 전" 처럼 **매일 돈 작업이 멈춘 것으로** 실렸다. 실패·건너뜀은 안 돈 것이다.
    """
    rows = client.execute(
        "SELECT job_name, MAX(finished_at) AS last FROM batch_runs WHERE status IN ('success', 'partial')"
        " GROUP BY job_name"
    ).rows
    return {str(j): str(t) for j, t in rows if t}


def status_counts(runs: list[Run]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in runs:
        칸 = out.setdefault(r.job_name, {"success": 0, "partial": 0, "failed": 0, "skipped": 0, "running": 0})
        칸[r.status if r.status in 칸 else "running"] += 1
    return out


def repeated_reasons(runs: list[Run]) -> list[tuple[str, int]]:
    """같은 머리말의 경고·실패 사유를 묶어 많은 순. `REPEAT_MIN` 미만은 뺀다 — 한 번 난 것은 그날 알림이 말했다."""
    묶음: dict[str, tuple[str, int]] = {}
    for r in runs:
        글들 = list(r.warnings) + ([r.error_text] if r.status == "failed" and r.error_text else [])
        for 글 in 글들:
            머리 = 글[:PREFIX_LEN]
            보기, n = 묶음.get(머리, (글, 0))
            묶음[머리] = (보기, n + 1)
    return sorted(((보기, n) for 보기, n in 묶음.values() if n >= REPEAT_MIN), key=lambda x: -x[1])


def adjust_needed(runs: list[Run]) -> list[str]:
    """국내 일일 배치가 기업행위를 알린 종목 가운데, 그 알림 **뒤에** `adjust_kr` 성공 실행이 없는 것.

    `adjust_kr` 은 종목 하나만 돌릴 수 있어(25.924) 뒤 실행이 그 종목을 덮었다고 단정할 수 없다 — 그래서
    뒤 실행이 있으면 종목을 빼되, 글에는 실행 수를 함께 적는다(`compose`). 뒤 실행이 하나라도 있으면 빼는 쪽을
    골랐다 — 전 종목 실행(325만 행)을 부추기지 않으려는 선택이다.
    """
    수정실행 = sorted(r.started_at for r in runs if r.job_name == "adjust_kr" and r.status == "success")
    남은: dict[str, None] = {}
    for r in runs:
        if r.job_name != "daily_kr":
            continue
        for w in r.warnings:
            m = _기업행위.search(w)
            if not m:
                continue
            if any(t > r.started_at for t in 수정실행):
                continue
            for code in _종목코드.findall(m.group(1)):
                남은[code] = None
    return list(남은)


def stale_jobs(last_success: dict[str, str], now: datetime) -> list[tuple[str, int | None]]:
    """기대 주기를 넘긴 작업. (이름, 며칠 전) — 한 번도 돈 적(성공·부분)이 없으면 None."""
    out: list[tuple[str, int | None]] = []
    for job, limit in EXPECTED_DAYS.items():
        last = last_success.get(job)
        if last is None:
            out.append((job, None))
            continue
        try:
            지난 = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
        except ValueError:
            continue
        if 지난 > limit:
            out.append((job, 지난))
    return out


def reads_line(used: int | None, limit: int, now: datetime) -> str:
    """Turso 월 읽기 — 쓴 몫과 달의 진도. 진도보다 앞서면 ⚠."""
    if used is None:
        return "Turso 월 읽기: 재지 못했습니다"
    일수 = monthrange(now.year, now.month)[1]
    진도 = now.day / 일수
    몫 = used / limit if limit else 0.0
    표시 = " ⚠ 진도보다 앞섭니다" if 몫 > 진도 + 0.05 else ""
    return f"Turso 월 읽기: {used / 1e6:,.1f}M / {limit / 1e6:,.0f}M ({몫:.0%}, 달 진도 {진도:.0%}){표시}"


def compose(
    runs: list[Run], last_success: dict[str, str], now: datetime, reads_used: int | None,
    read_limit: int = db.TURSO_MONTHLY_READ_LIMIT,
    rot_lines: list[str] | None = None,
) -> str:
    """메시지 본문. 순수 함수 — 테스트가 그대로 돌린다.

    `rot_lines` 는 데이터 열 점검 절(`services/column_rot.render`, docs/health.md 7장). None 이면 절을 싣지 않는다.
    """
    끝 = now.date()
    시작 = 끝 - timedelta(days=WINDOW_DAYS)
    lines = [f"[주간 운영 요약] {시작} ~ {끝}", ""]

    counts = status_counts(runs)
    문제 = {j: c for j, c in counts.items() if c["partial"] or c["failed"] or c["skipped"] or c["running"]}
    총 = sum(sum(c.values()) for c in counts.values())
    lines.append(f"실행 {총}건" + ("" if 문제 else " — 전부 성공"))
    심한순 = sorted(문제.items(), key=lambda x: -(x[1]["failed"] * 3 + x[1]["partial"] + x[1]["skipped"]))
    for j, c in 심한순[:MAX_LINES]:
        조각 = [f"성공 {c['success']}"]
        조각 += [f"{이름} {c[키]}" for 키, 이름 in (("partial", "부분"), ("failed", "실패"), ("skipped", "건너뜀"),
                                                      ("running", "미완"))
                 if c[키]]  # fmt: skip
        lines.append(f"  {j}: {' · '.join(조각)}")

    되풀이 = repeated_reasons(runs)
    if 되풀이:
        lines += ["", f"되풀이된 경고·실패 사유 ({REPEAT_MIN}회 이상)"]
        for 글, n in 되풀이[:MAX_LINES]:
            한줄 = 글.replace("\n", " ")
            lines.append(f"  ×{n} {한줄[:120]}{'…' if len(한줄) > 120 else ''}")

    lines += ["", reads_line(reads_used, read_limit, now)]

    남은 = adjust_needed(runs)
    수정실행 = sum(1 for r in runs if r.job_name == "adjust_kr" and r.status == "success")
    if 남은:
        lines += ["", f"국내 수정주가를 다시 내야 할 종목 {len(남은)}개: {', '.join(남은[:10])}"
                  + (" …" if len(남은) > 10 else "")
                  + ' — Actions → "국내 수정주가" (종목 하나씩 돌리면 읽기가 작습니다)']  # fmt: skip
    elif 수정실행:
        lines += ["", f"국내 수정주가: 이번 주 {수정실행}번 돌렸고 그 뒤 새 기업행위 알림은 없습니다"]

    묵은 = stale_jobs(last_success, now)
    if 묵은:
        lines += ["", "기대 주기를 넘긴 예약 작업"]
        for job, 지난 in 묵은[:MAX_LINES]:
            언제 = "돈 기록 없음" if 지난 is None else f"마지막 실행 {지난}일 전"
            lines.append(f"  {job}: {언제} (기대 {EXPECTED_DAYS[job]}일 안)")

    if rot_lines:
        lines += ["", *rot_lines]

    lines += ["", "자세한 것은 웹 /status 와 이슈 #1 의 운영 출력에 있습니다"]
    return "\n".join(lines)


def run(dry_run: bool = False) -> int:
    client = TursoClient()
    now = datetime.now(UTC)
    run_id = db.start_batch_run(client, job_name=JOB_NAME, market=None, trade_date=now.date().isoformat())
    try:
        since = (now - timedelta(days=WINDOW_DAYS)).isoformat()
        runs = load_runs(client, since)
        last = load_last_success(client)
        남음 = db.remaining_read_budget_or_none(client)
        used = None if 남음 is None else db.TURSO_MONTHLY_READ_LIMIT - 남음
        # 조용히 썩는 열 (docs/health.md 7장, 25.948) — 최근 7일을 그 전 90일과 견준다. 표 하나가 실패해도 요약은 나간다
        rot, checked, rot_errors = column_rot.scan(
            client,
            (now - timedelta(days=column_rot.RECENT_DAYS)).date().isoformat(),
            (now - timedelta(days=column_rot.RECENT_DAYS + column_rot.BASELINE_DAYS)).date().isoformat(),
        )
        rot_lines = column_rot.render(rot, checked, MAX_LINES) + [f"  (못 본 표: {e})" for e in rot_errors[:2]]
        text = compose(runs=runs, last_success=last, now=now, reads_used=used, rot_lines=rot_lines)
        print(text)
        sent = 0
        if not dry_run:
            sent = len(telegram.send(text))
        db.finish_batch_run(
            client, run_id, status="success",
            step_log={"runs": len(runs), "sent_chunks": sent, "stale": [j for j, _ in stale_jobs(last, now)],
                      "adjust_needed": adjust_needed(runs),
                      "rot": [f.text for f in rot], "rot_checked": checked, "rot_errors": rot_errors},
        )  # fmt: skip
        return 0
    except Exception as exc:
        db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
        raise
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="주간 운영 요약")
    parser.add_argument("--dry-run", action="store_true", help="보내지 않고 찍기만")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(guard(main))
