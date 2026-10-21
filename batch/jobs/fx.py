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
#: 직전에 받아들인 날에서 하루 이만큼(비율) 넘게 튀면 저장하지 않는다 — 야후가 하루 단위 오류(÷100 등)를 주면 그날 미국
#: 체결의 원화 현금에 영구히 남았다(가상모의투자 장부는 고치지 않는다, 25.1594·25.1597). 원·달러 하루 움직임은 보통 1%
#: 안쪽,
#: 큰 날도 3~5% — 10% 는 깨진 값만 거르는 문턱이다 `[확인필요: 실측 아님]`
FX_STEP_RATIO = 0.10
#: 튄 날이 이만큼 이어지고 서로 맞으면(그 안에서 하루 `FX_STEP_RATIO` 안) 진짜 국면 변화로 받아들인다 — 아니면 한 번
#: 크게
#: 움직인 뒤 저장이 영구히 멈췄다(25.1596 반박 [필수] 1). 단 직전 값의 `FX_REGIME_MAX` 배 넘게 다르면(÷100 같은 단위
#: 오류)
#: 이어져도 받아들이지 않는다
FX_REGIME_RUN = 3
FX_REGIME_MAX = 0.5
#: 기준점 — 창 첫날 앞 이 달력일 안에 저장된 날만. 그보다 옛 값(5년치를 다시 채울 때·오래 쉰 뒤)은 쓰지 않는다
#: (25.1596 반박 [필수] 2)
FX_ANCHOR_MAX_AGE_DAYS = 10

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
    """깨진 날을 뺀다 (25.1594·25.1597) — 날짜 순으로 걸으며 **직전에 받아들인 값**에서 하루 `FX_STEP_RATIO`
    넘게 튄 날을 뺀다.

    시작값은 `anchor`(창 앞 최근 저장 환율), 없으면 첫 11행의 아래·위 중앙값 — 둘이 10% 넘게 다르면(깨진 값이 절반쯤)
    판단을 보류하고 다 남기며 경고한다. 튄 날이 `FX_REGIME_RUN` 일 이어지고 서로 맞으며 직전 값의 50% 안이면 진짜
    국면 변화로 받아들인다(경고) — 기준점 하나와 견주면 진짜 큰 움직임 뒤 저장이 영구히 멈췄다(25.1596 반박 [필수])
    """
    차례 = sorted(rows, key=lambda r: str(r[1]))
    if not 차례:
        return rows, []
    경고: list[str] = []
    직전 = anchor
    if 직전 is None:
        if len(차례) < 3:
            return 차례, []
        아래, 위 = _중앙([float(r[2]) for r in 차례[:11]])
        if abs(위 / 아래 - 1) > FX_STEP_RATIO:
            return 차례, [f"환율 이상값 판단 보류: 받은 첫 날들이 둘로 갈림({아래:,.2f} · {위:,.2f}) — 모두 저장함"]
        직전 = 아래
    남김: list[tuple] = []
    뺌: list[tuple] = []
    튐: list[tuple] = []  # 직전에서 튄 채 이어지는 날들
    for r in 차례:
        v = float(r[2])
        if abs(v / 직전 - 1) <= FX_STEP_RATIO:
            남김.append(r)
            직전 = v
            뺌 += 튐
            튐 = []
            continue
        if 튐 and abs(v / float(튐[-1][2]) - 1) > FX_STEP_RATIO:
            뺌 += 튐
            튐 = []
        튐.append(r)
        if len(튐) >= FX_REGIME_RUN and abs(float(튐[0][2]) / 직전 - 1) <= FX_REGIME_MAX:
            경고.append(f"환율이 {직전:,.2f} 에서 {float(튐[0][2]):,.2f} 로 크게 움직였고 {len(튐)}일 이어져 받아들임"
                        f" ({튐[0][1]}부터) — 실제 움직임인지 확인")
            남김 += 튐
            직전 = float(튐[-1][2])
            튐 = []
    뺌 += 튐
    if 뺌:
        글 = ", ".join(f"{r[1]} {float(r[2]):,.2f}" for r in 뺌[:10])
        글 += f" 외 {len(뺌) - 10}일" if len(뺌) > 10 else ""
        경고.insert(0, f"환율 이상값 {len(뺌)}일을 저장하지 않음: {글}"
                       f" (직전 받아들인 값에서 하루 {FX_STEP_RATIO:.0%} 넘게 튐)")
    return sorted(남김, key=lambda r: str(r[1])), 경고


def collect(client: TursoClient, lookback_days: int = LOOKBACK_DAYS) -> tuple[int, list[str]]:
    """저장한 행 수와 경고. 실패해도 배치를 멈추지 않는다(금액만 비게 된다)."""
    result = yfinance_src.fetch_daily_bars([fx.YAHOO_SYMBOL], lookback_days=lookback_days, chunk_size=1)
    db.record_api_call(client, "yfinance", count=1)
    if not result.ok:
        return 0, [f"환율 수집 실패: {result.error or '알 수 없음'} (미국 권장 금액이 비게 됩니다)"]
    받음 = rows_from_bars(result.data, result.source, db.now_iso())
    rows, 경고 = drop_outliers(받음, _anchor(client, min((str(r[1]) for r in 받음), default=None)))
    if not rows:
        글 = "받은 행이 모두 이상값이라 저장하지 않았습니다" if 받음 else "받은 행이 없습니다"
        return 0, [f"환율 수집: {글} (미국 권장 금액이 비게 됩니다)", *경고]
    for start in range(0, len(rows), 300):
        _store(client, rows[start : start + 300])
    return len(rows), 경고


#: 기준점 — 창 앞의 이미 저장된 최근 다섯 날(읽기 5행). 그 값도 예전 수집이 저장한 것이라 깨졌을 수 있어 중앙값으로
ANCHOR_SQL = (
    "SELECT rate FROM fx_rates WHERE pair = ? AND date < ? AND date >= ? AND rate > 0 ORDER BY date DESC LIMIT 5"
)


def _anchor(client: TursoClient, first_day: str | None) -> float | None:
    if first_day is None:
        return None
    from datetime import date, timedelta

    부터 = (date.fromisoformat(first_day[:10]) - timedelta(days=FX_ANCHOR_MAX_AGE_DAYS)).isoformat()
    값 = [float(r[0]) for r in client.execute(ANCHOR_SQL, [fx.PAIR, first_day, 부터]).rows]
    if len(값) < 3:
        return None
    아래, 위 = _중앙(값)
    return 아래 if abs(위 / 아래 - 1) <= FX_STEP_RATIO else None  # 저장된 값부터 갈려 있으면 기준점으로 쓰지 않는다


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
