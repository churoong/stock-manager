"""지수 일봉 수집 (코스피·코스닥·S&P 500, 야후). 일일 배치가 시세 뒤에 부른다.

실행
  python -m batch.jobs.index_prices
  python -m batch.jobs.index_prices --lookback 2000   # 백테스트 --trend-filter 용으로 5년치 한 번

세 심볼을 한 번에 부르므로 호출은 1회다. 쓰는 행은 하루 3행이라 Turso 예산에는 무시할 만하다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import trend
from batch.sources import yfinance_src

log = logging.getLogger("index_prices")

JOB_NAME = "index_prices"

# 며칠 빠져도 메워지도록 한 주 남짓 받는다 (jobs/fx 와 같은 이유)
LOOKBACK_DAYS = 10


#: 지수가 어느 나라 거래일을 따르나 — 확정 거래일을 나라마다 잡는다 (25.608)
INDEX_COUNTRY: dict[str, str] = {"KOSPI": "KR", "KOSDAQ": "KR", "SP500": "US"}


#: 마감 뒤 이만큼 지나야 그날 봉을 종가로 본다 [확인필요: 야후 일봉 확정 지연 실측 아님]
SETTLE_MARGIN = timedelta(minutes=30)


def settled_days(now: datetime | None = None) -> dict[str, str]:
    """나라별 확정 거래일. **오늘 장이 마감(+여유)했으면 오늘**, 아니면 직전 거래일.

    25.608 은 `cal.default_as_of`(늘 직전 거래일)를 썼다. 국내 아침(08:27 KST = 뉴욕 전날 저녁)에는 이미 마감한
    미국 종가까지 버려 S&P 500 이 한 거래일 늦었다 (25.611, 교차검증).
    """
    지금 = now or datetime.now(UTC)
    out: dict[str, str] = {}
    for country in set(INDEX_COUNTRY.values()):
        오늘 = 지금.astimezone(cal.market_tz(country)).date()
        마감 = cal.session_close_utc(country, 오늘)
        if 마감 is not None and 지금 >= 마감 + SETTLE_MARGIN:
            out[country] = 오늘.isoformat()
        else:
            out[country] = cal.previous_session(country, 오늘).isoformat()
    return out


def rows_from_bars(
    bars: list[yfinance_src.DailyBar], source: str, now: str, settled: dict[str, str] | None = None
) -> list[tuple]:
    """(index_code, date, close, source, fetched_at). 모르는 심볼·0 이하 종가는 버린다.

    `settled` 를 주면 **나라의 확정 거래일 뒤 봉은 버린다** (docs/infra.md 25.608, 감사). 야후 `period=Nd` 는 장중이면
    오늘 막대(진행 중 값)를 준다 — 09:20 KST 복귀 워크플로·늦게 도는 미국 cron 이 장중 지수를 오늘 종가로 넣었다.
    미국 시세의 `unsettled` 거르기(`daily._store_us_bars`)와 같은 규칙이다. 다음 날 10일 재수집이 확정값을 넣는다.
    """
    rows: list[tuple] = []
    for bar in bars:
        code = trend.SYMBOL_INDEX.get(bar.ticker)
        if code is None or not bar.close or bar.close <= 0:
            continue
        if settled is not None and bar.date > settled.get(INDEX_COUNTRY.get(code, ""), "9999-12-31"):
            continue
        rows.append((code, bar.date, float(bar.close), f"{source} {bar.ticker}", now))
    return rows


def collect(client: TursoClient, lookback_days: int = LOOKBACK_DAYS) -> tuple[int, list[str]]:
    """저장한 행 수와 경고. 실패해도 배치를 멈추지 않는다 (추세 필터가 '미판정' 으로 남을 뿐)."""
    symbols = list(trend.INDEX_SYMBOLS.values())
    result = yfinance_src.fetch_daily_bars(symbols, lookback_days=lookback_days, chunk_size=len(symbols))
    db.record_api_call(client, "yfinance", count=1)
    if not result.ok:
        return 0, [f"지수 수집 실패: {result.error or '알 수 없음'} (추세 필터가 국면을 내지 못합니다)"]
    rows = rows_from_bars(result.data, result.source, db.now_iso(), settled_days())
    if not rows:
        return 0, ["지수 수집: 받은 행이 없습니다 (추세 필터가 국면을 내지 못합니다)"]
    for start in range(0, len(rows), 300):
        _store(client, rows[start : start + 300])
    warnings = [f"지수 수집 일부 실패: {result.error}"] if result.error else []
    # **지수 하나가 통째로 빠져도 말한다** (docs/infra.md 25.610). 세 심볼 중 하나만 빠지면 행이 있어 조용히 넘어갔다
    빠진 = sorted(set(trend.INDEX_SYMBOLS) - {row[0] for row in rows})
    if 빠진:
        warnings.append(f"지수 {', '.join(빠진)} 를 받지 못했습니다 (그 나라 추세 필터가 국면을 내지 못합니다)")
    return len(rows), warnings


def _store(client: TursoClient, rows: list[tuple]) -> None:
    client.batch(
        [
            (
                "INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (index_code, date) DO UPDATE SET close = excluded.close,"
                " source = excluded.source, fetched_at = excluded.fetched_at",
                list(row),
            )
            for row in rows
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="지수 일봉 수집")
    parser.add_argument("--lookback", type=int, default=LOOKBACK_DAYS, help="받을 달력일 수")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    client = TursoClient()
    try:
        db.apply_migrations(client)
        # **실행 기록을 남긴다** (docs/infra.md 25.610, 감사). 수동·복귀 실행이 batch_runs 에 없어 /status 가 몰랐다.
        # 일일 배치 안의 수집은 이 main 을 거치지 않으므로(리포트 경고로 올라간다) 여기만 연다
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=None, trade_date=None)
        try:
            count, warnings = collect(client, args.lookback)
        except Exception as exc:
            db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
            raise
        status = "failed" if not count else ("partial" if warnings else "success")
        db.finish_batch_run(client, run_id, status=status, step_log={"rows": count, "lookback": args.lookback,
                            "warnings": warnings}, error_text="; ".join(warnings) or None)  # fmt: skip
        today = datetime.now(UTC).date().isoformat()
        print(f"지수 {count}행 저장")
        for country in ("KR", "US"):
            for regime in trend.regimes_for_country(client, country, today).values():
                print(f"  {regime.describe()}")
        for warning in warnings:
            print(f"  주의: {warning}")
        return 0 if count else 1  # 일부 빠짐은 기록(partial)·경고로 말하고 워크플로는 실패로 만들지 않는다
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(guard(main))
