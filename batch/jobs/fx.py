"""환율 수집 (USDKRW, 야후 KRW=X). 미국 일일 배치가 시세 뒤에 부른다.

실행
  python -m batch.jobs.fx
  python -m batch.jobs.fx --lookback 1900   # 과거 매매의 환율 자동 채움용으로 5년치 한 번 (docs/portfolio.md 3장)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import fx
from batch.sources import yfinance_src

log = logging.getLogger("fx")

# 며칠 빠져도 메워지도록 한 주 남짓 받는다. 호출은 1회로 같다.
LOOKBACK_DAYS = 10


def rows_from_bars(bars: list[yfinance_src.DailyBar], source: str, now: str) -> list[tuple]:
    return [
        (fx.PAIR, bar.date, float(bar.close), source, now)
        for bar in bars
        if bar.ticker == fx.YAHOO_SYMBOL and bar.close and bar.close > 0
    ]


def collect(client: TursoClient, lookback_days: int = LOOKBACK_DAYS) -> tuple[int, list[str]]:
    """저장한 행 수와 경고. 실패해도 배치를 멈추지 않는다(금액만 비게 된다)."""
    result = yfinance_src.fetch_daily_bars([fx.YAHOO_SYMBOL], lookback_days=lookback_days, chunk_size=1)
    db.record_api_call(client, "yfinance", count=1)
    if not result.ok:
        return 0, [f"환율 수집 실패: {result.error or '알 수 없음'} (미국 권장 금액이 비게 됩니다)"]
    rows = rows_from_bars(result.data, result.source, db.now_iso())
    if not rows:
        return 0, ["환율 수집: 받은 행이 없습니다 (미국 권장 금액이 비게 됩니다)"]
    for start in range(0, len(rows), 300):
        _store(client, rows[start : start + 300])
    return len(rows), []


def _store(client: TursoClient, rows: list[tuple]) -> None:
    client.batch(
        [
            (
                "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (pair, date) DO UPDATE SET rate = excluded.rate, source = excluded.source,"
                " fetched_at = excluded.fetched_at",
                list(row),
            )
            for row in rows
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="환율 수집")
    parser.add_argument("--lookback", type=int, default=LOOKBACK_DAYS, help="받을 달력일 수")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    client = TursoClient()
    try:
        db.apply_migrations(client)
        count, warnings = collect(client, args.lookback)
        latest = fx.latest_rate(client, datetime.now(UTC).date().isoformat())
        print(f"환율 {count}행 저장, 최신 {latest.describe() if latest else '-'}")
        for warning in warnings:
            print(f"  주의: {warning}")
        return 0 if count else 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(guard(main))
