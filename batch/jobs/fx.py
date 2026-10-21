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
#: 받은 창의 중앙값에서 이만큼(비율) 넘게 벗어난 날은 저장하지 않는다 — 야후가 하루 단위 오류(÷100 등)를 주면 그날 미국
#: 체결의 원화 현금에 영구히 남았다(가상모의투자 장부는 고치지 않는다, 25.1594 — 25.1591 감사 [참고]). 원·달러 하루
#: 움직임은 보통 1% 안쪽, 2008·2020 같은 큰 날도 3~5% — 10% 는 깨진 값만 거르는 문턱이다 `[확인필요: 실측 아님]`
FX_OUTLIER_RATIO = 0.10
#: 이웃 창 반폭(거래일) — 앞뒤 5일. 하루짜리 깨진 값은 이웃 열하나 가운데 하나라 중앙값을 움직이지 못한다
FX_OUTLIER_NEIGHBORS = 5


def rows_from_bars(bars: list[yfinance_src.DailyBar], source: str, now: str) -> list[tuple]:
    return [
        (fx.PAIR, bar.date, float(bar.close), source, now)
        for bar in bars
        if bar.ticker == fx.YAHOO_SYMBOL and bar.close and bar.close > 0
    ]


def _중앙(xs: list[float]) -> tuple[float, float]:
    """(아래 중앙값, 위 중앙값) — 홀수면 같다."""
    ys = sorted(xs)
    return ys[(len(ys) - 1) // 2], ys[len(ys) // 2]


def drop_outliers(rows: list[tuple], anchor: float | None = None) -> tuple[list[tuple], list[str]]:
    """깨진 날을 뺀다 (25.1594·25.1596). `anchor` = DB 에 이미 있는 창 앞 최근 환율의 중앙값.

    기준점이 있으면 그것에서 `FX_OUTLIER_RATIO` 넘게 벗어난 날 — 깨진 값이 창의 절반을 넘어도 걸러진다.
    없으면(첫 수집·5년치 채우기) 날마다 앞뒤 `FX_OUTLIER_NEIGHBORS` 일(자기 포함)의 **아래·위 중앙값 둘 다**에서
    벗어난 날만 — 짝수 창에서 깨진 값이 절반이면 위 중앙값 하나로는 결과가 뒤집혔다(정상값을 빼고 깨진 값을 저장,
    25.1594 반박 [필수]).
    창 전체가 아니라 이웃이라 5년 사이 몇십 % 움직인 환율도 그대로. 셋 미만이고 기준점도 없으면 그대로
    """
    차례 = sorted(rows, key=lambda r: str(r[1]))
    벗어남 = (lambda v, m: abs(v / m - 1) > FX_OUTLIER_RATIO)  # noqa: E731
    if anchor:
        뺌 = [r for r in 차례 if 벗어남(float(r[2]), anchor)]
    elif len(차례) < 3:
        return rows, []
    else:
        뺌 = []
        for i, r in enumerate(차례):
            이웃 = 차례[max(0, i - FX_OUTLIER_NEIGHBORS): i + FX_OUTLIER_NEIGHBORS + 1]
            아래, 위 = _중앙([float(x[2]) for x in 이웃])
            if 벗어남(float(r[2]), 아래) and 벗어남(float(r[2]), 위):
                뺌.append(r)
    남김 = [r for r in 차례 if r not in 뺌]
    글 = ", ".join(f"{r[1]} {float(r[2]):,.2f}" for r in 뺌)
    기준 = f"직전 저장 환율 {anchor:,.2f}" if anchor else "앞뒤 이웃 중앙값"
    경고 = f"환율 이상값 {len(뺌)}일을 저장하지 않음: {글} ({기준}에서 {FX_OUTLIER_RATIO:.0%} 넘게 벗어남)"
    return 남김, [경고] if 뺌 else []


def collect(client: TursoClient, lookback_days: int = LOOKBACK_DAYS) -> tuple[int, list[str]]:
    """저장한 행 수와 경고. 실패해도 배치를 멈추지 않는다(금액만 비게 된다)."""
    result = yfinance_src.fetch_daily_bars([fx.YAHOO_SYMBOL], lookback_days=lookback_days, chunk_size=1)
    db.record_api_call(client, "yfinance", count=1)
    if not result.ok:
        return 0, [f"환율 수집 실패: {result.error or '알 수 없음'} (미국 권장 금액이 비게 됩니다)"]
    받음 = rows_from_bars(result.data, result.source, db.now_iso())
    rows, 경고 = drop_outliers(받음, _anchor(client, min((str(r[1]) for r in 받음), default=None)))
    if not rows:
        return 0, ["환율 수집: 받은 행이 없습니다 (미국 권장 금액이 비게 됩니다)", *경고]
    for start in range(0, len(rows), 300):
        _store(client, rows[start : start + 300])
    return len(rows), 경고


#: 기준점 — 창 앞의 이미 저장된 최근 다섯 날(읽기 5행). 그 값도 예전 수집이 저장한 것이라 깨졌을 수 있어 중앙값으로
ANCHOR_SQL = "SELECT rate FROM fx_rates WHERE pair = ? AND date < ? AND rate > 0 ORDER BY date DESC LIMIT 5"


def _anchor(client: TursoClient, first_day: str | None) -> float | None:
    if first_day is None:
        return None
    값 = [float(r[0]) for r in client.execute(ANCHOR_SQL, [fx.PAIR, first_day]).rows]
    return _중앙(값)[0] if len(값) >= 3 else None


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
