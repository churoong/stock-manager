"""미국 수정주가 재수집 (docs/adjust.md 8장). 대기열에 적힌 종목만 5년치를 다시 받는다. 주 1회.

실행
  python -m batch.jobs.refresh_us_adjusted
  python -m batch.jobs.refresh_us_adjusted --max 50
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.daily import _store_us_bars
from batch.sources import yfinance_src

log = logging.getLogger("refresh_us_adjusted")

JOB_NAME = "refresh_us_adjusted"
LOOKBACK_DAYS = 1300  # 백필과 같은 5년. 조정 기준이 한 계열 안에서 맞으려면 전 구간을 한 번에 받아야 한다
ROWS_PER_STOCK = 1_300  # 5년 ≈ 1,260 거래일. 예산 어림용
BUDGET_RESERVE = 1_000_000  # 일일 배치 몫으로 남겨 두는 쓰기 행 수. 한 달 일일 배치가 약 120만 행이다 [확인필요]
DEFAULT_MAX = 100  # 한 번에 받을 종목 상한. 100종목 ≈ 13만 행, 야후 조각 1개


#: 대기열이 이보다 오래 밀리면 **말한다** (docs/infra.md 25.165).
#: 재수집은 주 1회(토)라, 15일이면 예정된 실행을 **두 번** 지나친 것이다. 한 번 거른 것은
#: 예산 사정으로 흔한 일이라 그때마다 말하면 아무도 안 읽는다
MAX_PENDING_DAYS = 15


def pending_note(client: TursoClient, today: date) -> str | None:
    """재수집이 오래 밀려 있으면 한 줄. 아니면 `None` (docs/infra.md 25.165).

    **대기 중인 종목의 옛 `adj_close` 는 지금 기준과 어긋나 있다** — 그것을 알면서
    모멘텀·성과지표·백테스트가 그 값을 읽는다. 감지한 사실이 `log.info` 에만 남아
    있어서 아무도 몰랐다.
    """
    수, 가장오래 = pending_summary(client)
    if not 수 or 가장오래 is None:
        return None
    try:
        나이 = (today - date.fromisoformat(가장오래[:10])).days
    except ValueError:
        return None
    if 나이 <= MAX_PENDING_DAYS:
        return None
    return (
        f"미국 수정주가 재수집이 {수}종목 밀려 있습니다 (가장 오래된 감지 {가장오래[:10]}, {나이}일 전)."
        " 그 종목의 옛 수정종가는 지금 기준과 어긋나 있고 모멘텀·성과지표가 그 값을 씁니다"
        " — Actions 의 '미국 수정주가 재수집' 을 돌리세요"
    )


def pending_summary(client: TursoClient) -> tuple[int, str | None]:
    """(밀린 종목 수, 가장 오래된 감지 시각). 표가 없으면 (0, None)."""
    try:
        rs = client.execute(
            "SELECT COUNT(*), MIN(detected_at) FROM adjust_refresh_queue WHERE done_at IS NULL"
        )
    except Exception as e:  # noqa: BLE001 — 표가 없는 DB 는 정상 (25.164)
        if db.표가_없나(e):
            return 0, None
        raise
    row = rs.rows[0] if rs.rows else (0, None)
    return int(row[0] or 0), (str(row[1]) if row[1] else None)


def pending(client: TursoClient) -> list[tuple[int, str]]:
    """대기 종목. **분할이 먼저다** (docs/infra.md 25.503, 교차검증) — 분할은 50~90% 절벽이고 배당은 2% 안팎이다.
    배당락마다 쌓이는 종목 뒤에서 분할이 몇 주를 기다렸다. 분할 감지는 두 비율을 같게 적는다(`adjust_drift.detect`)."""
    rs = client.execute(
        "SELECT q.stock_id, s.yahoo_symbol FROM adjust_refresh_queue q JOIN stocks s ON s.id = q.stock_id"
        " WHERE q.done_at IS NULL AND s.yahoo_symbol IS NOT NULL"
        " ORDER BY CASE WHEN q.ratio_before = q.ratio_after THEN 0 ELSE 1 END, q.detected_at, q.stock_id",
    )
    return [(int(r[0]), str(r[1])) for r in rs.rows]


def lookback_for(client: TursoClient, stock_ids: list[int], today: date) -> int:
    """받을 달력일 수. **저장된 가장 오래된 행까지** 덮는다 (docs/infra.md 25.503, 교차검증).

    1,300일로 고정하면 백필 뒤 흐른 날만큼의 옛 구간이 분할 전 값으로 남아, 그 경계에 새 절벽이 생겼다
    (`prices` 는 지우지 않는다). 못 읽으면 기본값.
    """
    days = LOOKBACK_DAYS
    for stock_id in stock_ids:  # 종목당 한 번 — 최대 DEFAULT_MAX 번, (stock_id, date) 색인
        rs = client.execute("SELECT MIN(date) FROM prices WHERE stock_id = ?", [stock_id])
        oldest = rs.rows[0][0] if rs.rows else None
        if oldest:
            days = max(days, (today - date.fromisoformat(str(oldest)[:10])).days + 5)
    return days


def _넣을_시작(today: date, lookback_days: int) -> str:
    """넣을 첫 날. 기본 구간이면 그 시작, 저장 행에 맞춰 늘린 구간이면 여유 5일을 뺀 그 가장 오래된 날."""
    days = lookback_days if lookback_days <= LOOKBACK_DAYS else lookback_days - 5
    return (today - timedelta(days=days)).isoformat()


def rows_per_stock(lookback_days: int) -> int:
    """예산 어림: 달력일 → 거래일(약 252/365)."""
    return max(ROWS_PER_STOCK, lookback_days * 252 // 365 + 10)


def affordable(remaining: int, count: int, per_stock: int = ROWS_PER_STOCK) -> int:
    """예산 안에서 받을 수 있는 종목 수. 한도를 넘기면 쓰기가 통째로 막힌다 (docs/infra.md 23절)."""
    room = remaining - BUDGET_RESERVE
    return max(0, min(count, room // per_stock))


def run(max_stocks: int = DEFAULT_MAX) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        trade_date = cal.previous_session("US").isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market="US", trade_date=trade_date)
        queue = pending(client)
        remaining = db.remaining_write_budget(client)
        # 받을 구간은 대기 종목의 가장 오래된 저장 행까지 (25.503). 예산도 그 길이로 어림한다.
        # **종목마다 따로 잰다** (25.506, 교차검증) — 한 조각은 가장 긴 길이로 받되, 넣는 것은 그 종목 자기 구간뿐이다.
        # 모두에게 최댓값을 쓰면 원래 없던 옛 행이 새로 쓰였고, 그 이력이 다음 재수집 길이를 다시 늘렸다
        오늘 = date.fromisoformat(trade_date)
        자기_구간 = {sid: lookback_for(client, [sid], 오늘) for sid, _ in queue[:max_stocks]}
        lookback = max(자기_구간.values(), default=LOOKBACK_DAYS)
        take = affordable(remaining, min(len(queue), max_stocks), rows_per_stock(lookback))
        step: dict[str, Any] = {"queued": len(queue), "budget_remaining": remaining, "take": take,
                                "lookback_days": lookback}
        if not queue:
            db.finish_batch_run(client, run_id, status="success", step_log=step)
            print("재수집할 종목이 없습니다")
            return 0
        if take == 0:
            db.finish_batch_run(client, run_id, status="skipped", step_log={**step, "reason": "쓰기 예산 부족"})
            print(f"쓰기 예산이 모자라 미룹니다 (남은 {remaining:,}행, 대기 {len(queue)}종목)")
            return 0

        chosen = queue[:take]
        ids = {symbol: stock_id for stock_id, symbol in chosen}
        stored = 0
        done: list[int] = []
        notes: list[str] = []
        빈조각 = 0
        symbols = list(ids)
        for start in range(0, len(symbols), yfinance_src.CHUNK_SYMBOLS):
            group = symbols[start : start + yfinance_src.CHUNK_SYMBOLS]
            result = yfinance_src.fetch_daily_bars(group, lookback_days=lookback, chunk_size=len(group))
            db.record_api_call(client, "yfinance", count=1)
            if not result.ok:
                notes.append(result.error or "실패")
                # 조각마다 따로 부르므로 잇달아 빈 조각은 여기서 센다 (25.603, 교차검증)
                빈조각 = 빈조각 + 1 if not result.data else 0
                if result.limit_state == "blocked" or 빈조각 >= 2:
                    break
                continue
            빈조각 = 0
            # 받기는 5일 여유를 두지만 넣기는 **저장된 가장 오래된 날부터**다 (25.516, 교차검증) — 여유분까지 넣으면
            # 재수집마다 이력이 5일씩 앞으로 늘었다
            받은 = [
                bar for bar in result.data
                if bar.ticker in ids and bar.date >= _넣을_시작(오늘, 자기_구간[ids[bar.ticker]])
            ]
            rows, _seen, _ = _store_us_bars(client, 받은, trade_date, ids, result.source, detect_drift=False)
            stored += rows
            got = {bar.ticker for bar in result.data}
            done.extend(ids[sym] for sym in group if sym in got)
            # 받은 것은 담고, 막혔으면 멈춘다 (25.609, 교차검증 — 25.606 부터 일부 막힘은 ok=True·blocked 로 온다)
            if result.limit_state == "blocked":
                notes.append(result.error or "야후 호출 제한")
                break
        if done:
            now = db.now_iso()
            client.batch([
                ("UPDATE adjust_refresh_queue SET done_at = ? WHERE stock_id = ?", [now, sid]) for sid in done
            ])
        step.update({"stored_rows": stored, "done": len(done), "notes": notes[:5]})
        status = "success" if len(done) == len(chosen) else ("partial" if done else "failed")
        db.finish_batch_run(client, run_id, status=status, step_log=step)
        print(f"재수집 {len(done)}/{len(chosen)}종목, {stored:,}행 (대기 {len(queue)}종목 중)")
        for n in notes[:5]:
            print(f"  주의: {n}")
        return 0 if done else 1
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="미국 수정주가 재수집")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help="한 번에 받을 종목 상한")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.max)


if __name__ == "__main__":
    sys.exit(guard(main))
