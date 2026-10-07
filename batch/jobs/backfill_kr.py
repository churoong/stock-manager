"""국내 시세 백필.

한국거래소는 기간 조회를 지원하지 않는다. 기준일 하나에 전종목이 오므로
날짜를 하나씩 돌아야 한다. 하루 한도가 10,000회이고 하루치가 시장당 1회라
1년이 시장당 약 250회다. 10년도 한도 안에 든다.

20일 평균 거래대금을 구하려면 최소 20 거래일이 필요하다. 유니버스를
만들기 전에 이걸 먼저 돌린다.

실행
  python -m batch.jobs.backfill_kr --days 30
  python -m batch.jobs.backfill_kr --from 2026-01-01 --to 2026-09-15
  python -m batch.jobs.backfill_kr --from 2025-08-15            # --to 를 생략하면 오늘까지
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime
from datetime import time as 시각

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard

log = logging.getLogger("backfill")

JOB_NAME = "backfill_kr"

# 호출 사이 간격. 거래소가 초당 한도를 공개하지 않아 보수적으로 둔다.
CALL_INTERVAL_SECONDS = 0.5


def sessions_between(start: date, end: date) -> list[date]:
    """두 날짜 사이의 국내 거래일 목록."""
    import pandas as pd

    from batch.core import calendar as kcal

    cal = kcal.exchange_calendar("KR")  # 손으로 더한 휴장일 포함 (25.449)
    sessions = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return [s.date() for s in sessions]


def already_stored(client: TursoClient, market: str, start: date, end: date) -> set[str]:
    """이미 채워진 거래일. 다시 받지 않는다.

    **백필 구간으로 좁혀 묻는다 (2026-09-17).** 예전에는 그 시장의 전 기간을 훑었다.
    가격이 5년치로 쌓이자 코스닥 조회가 Turso 읽기 제한 60초를 넘겨 백필이 통째로
    죽었다(실행 35172252321, 코스피 241일 저장 직후). 필요한 것은 이번 구간뿐이다.
    """
    # **날짜 색인을 한 칸씩 건너뛰고 날마다 그 시장 거래소 행이 있는지만 본다** (docs/infra.md 25.866, 배치 D1 감사).
    # 예전 `DISTINCT` 는
    # 400일 구간의 시세를 전부 읽고 행마다 종목을 다시 찾아 질의 하나가 약 158만 행 — 따라잡기 4단계(183만)의 거의
    # 전부였다
    rs = client.execute(
        "WITH RECURSIVE d(x) AS ("
        " SELECT (SELECT MIN(date) FROM prices WHERE date BETWEEN ? AND ?)"
        " UNION ALL SELECT (SELECT MIN(date) FROM prices WHERE date > d.x AND date <= ?) FROM d WHERE d.x IS NOT NULL)"
        # 그 시장 종목 목록을 한 번 만들고 날마다 주키 `(stock_id, date)` 로 찾는다 (25.868, 교차검증) — `p.date =
        # d.x` 로 날짜 색인을
        # 걸으면 늦게 넣은 시장(KOSDAQ)은 날마다 앞 시장 행 약 950개를 지나쳤다(40만 행 남짓)
        " SELECT x FROM d WHERE x IS NOT NULL AND EXISTS (SELECT 1 FROM prices p WHERE p.date = d.x"
        "   AND p.source = 'krx_openapi'"
        "   AND p.stock_id IN (SELECT s.id FROM stocks s WHERE s.country = 'KR' AND s.market = ?))",
        [start.isoformat(), end.isoformat(), end.isoformat(), market],
    )
    return {str(row[0]) for row in rs.rows}


def front_gaps(
    sessions: list[date], first_seen: dict[int, str], listed: dict[int, str | None]
) -> dict[int, list[date]]:
    """종목마다 **앞쪽 구멍** — 상장일(또는 구간 시작) 뒤인데 그 종목의 첫 시세보다 앞선 거래일 (docs/infra.md 25.459).

    `already_stored` 는 날짜 단위다. 그날 **어느 종목이든** 한 행이 있으면 채워진 날로 친다. 그래서 유니버스에 새로
    든 종목(일일 배치가 최근 며칠만 넣었다)의 과거는 영영 채워지지 않았다(스케줄 감사 #8).

    다음 두 경우만 본다.
    - 첫 시세가 있는 종목: 시세가 하나도 없는 종목은 거래소 코드가 안 맞는 경우라, 매번 전 구간을 다시 부르게 된다.
    - 상장일을 아는 종목: 모르면 상장 전 날을 구멍으로 착각해 매번 다시 부른다.
    """
    out: dict[int, list[date]] = {}
    for sid, first in first_seen.items():
        상장 = listed.get(sid)
        if not 상장:
            continue
        gap = [d for d in sessions if 상장[:10] <= d.isoformat() < first[:10]]
        if gap:
            out[sid] = gap
    return out


def first_seen_and_listed(
    client: TursoClient, market: str, start: date, end: date
) -> tuple[dict[int, str], dict[int, str | None]]:
    """(종목 → 구간 안 첫 시세일, 종목 → 상장일). 출처를 가리지 않는다 — 일일 배치가 넣은 행도 "있다" 다."""
    # 종목마다 주키 `(stock_id, date)` 로 구간 안 첫 날을 찾는다 (25.866) — 예전에는 구간 시세 전부(약 158만 행)를
    # 묶었다
    rs = client.execute(
        "SELECT s.id, (SELECT MIN(p.date) FROM prices p WHERE p.stock_id = s.id AND p.date BETWEEN ? AND ?) AS first,"
        " s.listed_date FROM stocks s WHERE s.country = 'KR' AND s.market = ?",
        [start.isoformat(), end.isoformat(), market],
    )
    first: dict[int, str] = {}
    listed: dict[int, str | None] = {}
    for row in rs.rows:
        if row[1] is None:  # 구간 안 시세가 없는 종목은 예전처럼 빠진다
            continue
        first[int(row[0])] = str(row[1])
        listed[int(row[0])] = None if row[2] is None else str(row[2])
    return first, listed


def stock_id_map(client: TursoClient, market: str) -> dict[str, int]:
    rs = client.execute(
        "SELECT ticker, id FROM stocks WHERE country = 'KR' AND market = ?", [market]
    )
    return {str(row[0]): int(row[1]) for row in rs.rows}


#: 이만큼 잇따라 실패하면 멈춘다 (docs/infra.md 25.387). 배당 수집(25.319)과 같은 생각 — 한 날의 일시 오류가 아니다
MAX_CONSECUTIVE_FAILURES = 5


#: 한국거래소가 전 거래일 시세를 내놓는 시각(한국 시각). docs/infra.md 16.1 "08:00 전후" 에 여유를 둔다
KRX_PUBLISH_HOUR_KST = 9


def 아직_집계_전(day: date, now: datetime | None = None) -> bool:
    """그날 시세가 **아직 나오지 않았을 수 있는가** (25.574·25.575·25.576).

    그날의 **다음 거래일 09시(한국)** 전이면 아직으로 본다. 25.575 는 "한국 09시 전 직전 거래일" 로 봐, 토요일 낮·연휴
    중 낮의 백필이 금요일·연휴 전날을 오류로 셌다. 거래소가 **다음 영업일** 아침에 내는지 **다음 날짜** 아침에 내는지는
    평일 두 번 실측(16.1)으로는 정해지지 않았다 `[확인필요]` — 늦은 쪽(다음 영업일)으로 잡아 거짓 오류를 내지 않는다.
    이르게 나온 날을 한 번 건너뛰어도 다음 실행이 채운다
    """
    지금 = (now or datetime.now(UTC)).astimezone(cal.market_tz("KR"))
    공개 = datetime.combine(cal.next_session("KR", day), 시각(KRX_PUBLISH_HOUR_KST), tzinfo=cal.market_tz("KR"))
    return 지금 < 공개


def store_day(
    client: TursoClient, market: str, day: date, ids: dict[str, int]
) -> tuple[int, str | None]:
    """하루치 전종목을 저장한다. (저장 건수, 오류)."""
    from batch.sources import krx

    bas_dd = day.strftime("%Y%m%d")
    # **부르기 전에 본다** (docs/infra.md 25.387). 이미 100% 인 날에도 두 시장에 한 번씩 더 불렀다
    if db.usage_blocked_today(client, "krx_openapi"):
        return 0, "일일 호출 한도에 도달했습니다"
    result = krx.fetch_daily(market, bas_dd)
    usage = db.record_api_call(
        client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80
    )
    if usage["state"] == "warn":
        log.warning(
            "한국거래소 호출이 한도의 %d%% 를 넘었습니다 (%d/%d)",
            usage["warn_at_pct"],
            usage["call_count"],
            usage["limit_value"],
        )
    # 한도에 닿은 호출의 응답도 **저장하고 나서** 멈춘다 (25.387, 25.318 과 같은 모양). 예전에는 받은 하루치
    # 약 2,700행을 버리고 끝났다 — 호출 한 번은 이미 썼다
    한도_끝 = "일일 호출 한도에 도달했습니다" if usage["state"] == "blocked" else None

    if not result.ok:
        return 0, 한도_끝 or result.error

    rows = result.data
    if not rows:
        # **날짜는 달력이 준 거래일이다** — 비었으면 조용히 넘기지 않는다 (docs/infra.md 25.571, 감사). 예전에는 "휴장일
        # 이거나 집계 전, 오류 아님" 으로 성공 처리해, 달력과 거래소가 어긋난 것(CLAUDE.md 휴장일 규칙의 근거 기록)이
        # 남지 않았고, `--refresh` 로 등락률을 채우던 날이 분할일이면 계수가 1 로 남아 이전 이력이 −50~−90% 로 남았다.
        # 일일 배치는 같은 빈 응답을 이미 실패로 본다(`daily.collect_kr_prices`)
        # 단 **오늘(한국 현지)** 은 아직 집계 전이다 — 거래소는 그날 시세를 다음 날 08:00 무렵에 준다(16.1).
        # 끝날짜 기본값이 오늘이라, 이것까지 오류로 세면 D1 따라잡기(09:05)의 과거 시세 단계가
        # 평상시 거래일마다 실패로 끝났다 (25.574, 교차검증)
        if 아직_집계_전(day):
            return 0, 한도_끝
        return 0, 한도_끝 or "달력은 거래일인데 거래소 응답이 비었습니다 (집계 전이거나 달력의 휴장 반영 누락)"

    iso = day.isoformat()
    now = db.now_iso()
    rows_data: list[tuple] = []
    missing = 0

    for row in rows:
        stock_id = ids.get(row.isu_cd)
        if stock_id is None:
            # 마스터에 없는 종목. 마스터를 먼저 갱신해야 한다
            missing += 1
            continue
        if row.close is None:
            continue
        rows_data.append(
            (
                stock_id, iso, row.open, row.high, row.low, row.close,
                None,  # 한국거래소 원자료는 미조정이다 (adj_close 는 jobs/adjust_kr 이 채운다)
                row.volume, row.value, "KRW", result.source, now, row.change_pct,
            )
        )

    stored = db.bulk_upsert_prices(client, rows_data)

    if missing:
        log.info("%s %s 마스터에 없는 종목 %d개를 건너뛰었습니다", market, iso, missing)
    return stored, 한도_끝


def estimate_rows(
    client: TursoClient, sessions: list[date], refresh: bool, keep: set[int] | None = None
) -> int:
    """이 실행이 쓸 행 수 어림값. 거래일 × **실제로 받을** 종목 수다.

    정확할 필요는 없다. "한도를 넘을 만한가" 만 알면 된다. 그래도 **틀리는 방향이 중요하다** —
    이 값이 남은 예산보다 크면 실행이 통째로 멈춘다(hard stop). 그래서 부풀리면 안 된다.

    `keep` 은 `--only-universe` 가 고른 종목이다. 이것을 안 보면 한국거래소가 주는 전 종목
    (약 2,760)으로 세어 **실제(약 880)의 3배**가 나온다 (2026-09-21, docs/infra.md 25.63).
    """
    if keep is not None:
        per_day = len(keep)
    else:
        per_day = sum(len(stock_id_map(client, market)) for market in ("KOSPI", "KOSDAQ"))
    if not refresh:
        # 이미 저장된 날은 건너뛰므로 실제로는 더 적다. 어림값이라 그대로 둔다
        pass
    return per_day * len(sessions)


def watched_ids(client: TursoClient) -> set[int]:
    """점수·신호가 실제로 쓰는 국내 종목: 최신 유니버스 편입 + 보유 + 관심.

    과거 시세는 이 종목들만 있으면 된다. 한국거래소는 하루에 상장 전 종목(약 2,760)을 주는데
    유니버스는 약 880이다. D1 하루 쓰기 한도 안에서 **같은 예산으로 3배 긴 기간**을 채운다 (infra 25.8).
    """
    rs = client.execute(
        "SELECT u.stock_id FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
        f" WHERE u.included = 1 AND s.country = 'KR' AND u.snapshot_date = {db.latest_snapshot_sql()}"
        " UNION SELECT stock_id FROM positions"
        " UNION SELECT stock_id FROM watchlist",
        ["KR"],
    )
    return {int(r[0]) for r in rs.rows}


#: **논리 행 하나를 넣으면 D1 은 여러 행을 쓴 것으로 센다.** `meta.rows_written` 에 인덱스가
#: 함께 잡히기 때문이다. `prices` 한 행이면 표 1 + `UNIQUE (stock_id, date)` 자동 인덱스
#: + `idx_prices_date` 다.
#:
#: 2026-09-19·20 실측으로 배수는 **정확히 4.00** 이었다(거래일 75일 × 873종목 = 65,475 논리행을
#: 넣은 날 `d1_writes` 카운터가 262,045). 그때는 `idx_prices_stock_date` 가 하나 더 있었다.
#: 2026-09-20 에 그것을 지웠으므로(migrations/0039, `UNIQUE (stock_id, date)` 와 완전히 겹쳐
#: 실행계획이 바뀌지 않는 것을 실측으로 확인) **4 → 3** 이다.
#:
#: **인덱스를 더하거나 지우면 이 값도 함께 바꾼다.** 틀려도 한도를 넘지는 않는다 —
#: `budget_stop_reason` 이 도는 동안 실제 카운터를 다시 재기 때문이다. 이 상수는 몇 날을
#: 받아 볼지만 정한다.
PRICE_ROW_D1_COST = 3

#: 도는 동안 남은 예산을 다시 재는 주기(거래일). 시작할 때 센 것은 **예측**이고 이것은 **측정**이다.
#: 매일 재면 질의가 늘고, 너무 드물게 재면 그 사이에 넘긴다. 5일이면 최악이라도
#: 5 × 873종목 × 4 ≈ 1.7만 행을 더 쓰고 멈추는데, 기본 여유 3.2만 안이다.
#: **이 장치가 있으면 PRICE_ROW_D1_COST 가 틀려도 한도를 넘지 않는다** — 상수는 몇 날을 받을지만 정한다
BUDGET_RECHECK_EVERY = 5


def days_within_budget(remaining: int, reserve: int, rows_per_day: int) -> int:
    """남은 하루 예산 안에서 받을 수 있는 거래일 수. 여유(reserve)는 뒤 단계와 웹 크론 몫이다."""
    if rows_per_day <= 0:
        return 0
    return max(0, (remaining - reserve) // rows_per_day)


def days_within_daily_budget(remaining: int, reserve: int, stocks_per_day: int) -> int:
    """하루 예산 안에서 받을 거래일 수. **종목 수가 아니라 D1 이 세는 행 수로 나눈다.**

    2026-09-19 에 이것을 종목 수로 나눴다가 하루 한도 100,000 행짜리 D1 에 **220,085 행**을
    썼다. 예산 가드가 있는데도 2.2배를 넘긴 것이다. 넘긴 뒤에는 재무·업종·성과·점수·신호가
    전부 한도에 막혀 건너뛰어졌고, 그래서 추천이 한 건도 나오지 않았다 (docs/infra.md 25.20).
    """
    return days_within_budget(remaining, reserve, stocks_per_day * PRICE_ROW_D1_COST)


def budget_stop_reason(left: int, reserve: int, market: str, day: date) -> str | None:
    """여기서 멈춰야 하면 사람이 읽을 이유, 아니면 None.

    **시작할 때 센 예산은 예측이고 이것은 측정이다.** `PRICE_ROW_D1_COST` 는 인덱스 수에서
    나온 어림이라 표가 바뀌면 틀린다. 도는 동안 실제 카운터를 다시 봐서, 상수가 틀렸어도
    여유(reserve)를 파먹지 않고 멈춘다. 2026-09-19·20 에 예측만 믿다가 하루 한도의 2.2배를
    썼다 (docs/infra.md 25.20).

    **멈추는 것은 고장이 아니다.** 남은 거래일은 다음 날 이어진다 (25.6).
    """
    if left > reserve:
        return None
    return (
        f"{market} {day} 까지 받고 멈춥니다 — 오늘 남은 D1 쓰기 {left:,}행이"
        f" 여유 {reserve:,}행 아래입니다. 나머지는 내일 이어집니다"
    )


def limit_days(days: list[date], max_days: int | None) -> list[date]:
    """뒤에서부터 `max_days` 개. None 이면 전부, **0 이면 하나도 안 받는다.**

    `days[-0:]` 은 빈 목록이 아니라 **전부**다. 0 은 "오늘 하루 예산이 없다" 는 뜻이라
    정반대로 동작한다. 2026-09-21 까지 이 계산이 두 곳에 있었고 한 곳만 0 을 가렸다 —
    거를 곳과 어림할 곳이 서로 다른 날짜 수를 보고 있었다 (docs/infra.md 25.63).
    """
    if max_days is None:
        return list(days)
    if max_days <= 0:
        return []
    return days[-max_days:]


def pick_days(sessions: list[date], stored: set[str], max_days: int | None) -> list[date]:
    """받을 거래일. 저장된 날은 뺀다. max_days 가 있으면 **가장 최근의 빠진 날부터** 그만큼만.

    왜 최근부터인가 (docs/infra.md 25.8): D1 은 하루 쓰기 10만 행이라 며칠에 나눠 채운다.
    오래된 날부터 채우면 첫날 화면에 보이는 것이 몇 달 전 시세뿐이다. 최근부터 채우면 첫날부터
    신호·점수가 오늘에 가깝게 나오고, 날이 갈수록 과거가 늘어난다. 시장마다 같은 날을 고르므로
    코스피·코스닥의 구간이 어긋나지 않는다.
    """
    todo = [d for d in sessions if d.isoformat() not in stored]
    return limit_days(todo, max_days)


def run(
    start: date,
    end: date,
    refresh: bool = False,
    max_days: int | None = None,
    only_universe: bool = False,
    budget_reserve: int | None = None,
) -> int:
    """국내 시세를 날짜별로 받아 저장한다.

    refresh=True 면 **이미 저장된 날도 다시 받는다.** 2026-09-17 에 등락률(change_pct)
    열을 더했는데, 그 전에 저장된 행에는 값이 비어 있어 수정주가를 만들 수 없다.
    다시 받으면 같은 종가가 그대로 덮이고 등락률만 채워진다 (docs/adjust.md 4장).

    **계산해 둔 수정주가(`adj_close`)는 종가가 같으면 그대로 둔다** (docs/infra.md 25.250). 예전에는 NULL 로 지워져
    분할 구간을 다시 받으면 `adjust_kr` 를 다시 돌리기 전까지 원래 종가(−80%)가 보였다. 종가가 **바뀐** 행은
    옛 수정값이 틀리므로 비운다 — 그때는 여전히 `adjust_kr` 를 돌려야 한다.
    """
    client = TursoClient()
    try:
        db.apply_migrations(client)

        sessions = sessions_between(start, end)
        if not sessions:
            print(f"{start} ~ {end} 사이에 거래일이 없습니다")
            return 0

        run_id = db.start_batch_run(
            client,
            job_name=JOB_NAME,
            market="KR",
            trade_date=end.isoformat(),
        )

        # 유니버스만 받을 때는 종목 목록을 먼저 좁힌다 (infra 25.8)
        keep: set[int] | None = None
        if only_universe:
            keep = watched_ids(client)
            if not keep:
                message = "유니버스가 비어 있습니다. 유니버스를 먼저 돌리세요 (20거래일 시세가 있어야 판정된다)"
                db.finish_batch_run(client, run_id, status="failed", error_text=message)
                print(message)
                return 1
            print(f"유니버스·보유·관심 {len(keep):,}종목만 받습니다")

        # D1 하루 쓰기 한도에 맞춰 받을 날 수를 정한다. 여유는 뒤 단계(점수·신호)와 웹 크론 몫이다
        if budget_reserve is not None:
            per_day = len(keep) if keep is not None else sum(len(stock_id_map(client, m)) for m in ("KOSPI", "KOSDAQ"))
            remaining = db.remaining_d1_daily_writes(client)
            allowed = days_within_daily_budget(remaining, budget_reserve, per_day)
            max_days = allowed if max_days is None else min(max_days, allowed)
            print(
                f"오늘 남은 D1 쓰기 {remaining:,}행 − 여유 {budget_reserve:,}"
                f" → 하루 {per_day:,}종목 × 인덱스 {PRICE_ROW_D1_COST}배"
                f" = {per_day * PRICE_ROW_D1_COST:,}행씩 {max_days}거래일"
            )

        # 시작 전에 얼마나 쓸지 추정한다. 2026-09-17 에 이것을 하지 않아 Turso 월 쓰기
        # 한도를 태웠고 운영 DB 쓰기가 통째로 막혔다 (docs/infra.md 23절)
        # 거를 때와 어림할 때가 **같은 함수**를 쓴다. 따로 쓰면 또 어긋난다 (25.63)
        estimated = estimate_rows(client, limit_days(sessions, max_days), refresh, keep)
        remaining = db.remaining_write_budget(client)
        print(f"예상 쓰기 약 {estimated:,}행 (이번 달 남은 예산 {remaining:,}행)")
        if estimated > remaining:
            message = (
                f"예상 쓰기 {estimated:,}행이 이번 달 남은 예산 {remaining:,}행보다 많습니다."
                " 구간을 나눠 돌리세요 (예: --from/--to 로 1년씩)"
            )
            db.finish_batch_run(client, run_id, status="skipped", step_log={"reason": message})
            print(message)
            # **예산은 고장이 아니다** (docs/infra.md 25.6). DB 에는 이미 skipped 라고 적으면서
            # 셸에는 1 을 돌려주고 있었다 — 따라잡기에서 그 1 이 뒤 단계(성과·점수·신호·감시)를
            # 통째로 건너뛰게 만든다. 그것들은 이번에 시세를 못 받아도 할 일이 있다 (25.63)
            return 0

        total_rows = 0
        skipped = 0
        errors: list[str] = []
        budget_stop: str | None = None

        for market in ("KOSPI", "KOSDAQ"):
            if budget_stop:
                break
            ids = stock_id_map(client, market)
            if keep is not None:
                ids = {ticker: sid for ticker, sid in ids.items() if sid in keep}
            if not ids:
                errors.append(f"{market} 종목 마스터가 비어 있습니다. 유니버스를 먼저 돌리세요")
                continue

            stored = (
                set()
                if refresh
                else (already_stored(client, market, sessions[0], sessions[-1]) if sessions else set())
            )
            # 다시 받는 날(날짜로는 채워졌다)에는 **그날 구멍이 난 종목만** 쓴다 (25.465, 교차검증).
            # 거래정지로 행이 없던 기간은 다시 받아도 채워지지 않아 창이 밀릴 때마다 또 불린다 —
            # 받을 종목 전부를 다시 쓰면 D1 쓰기 예산까지 태운다
            구멍_종목: dict[str, set[int]] = {}
            if stored:
                # 날짜 단위로 "채워졌다" 해도, 받을 종목 중 앞쪽이 빈 종목이 있으면 그 날은 다시 받는다 (25.459)
                first, listed = first_seen_and_listed(client, market, sessions[0], sessions[-1])
                받을 = set(ids.values())
                구멍 = front_gaps(sessions, {k: v for k, v in first.items() if k in 받을}, listed)
                빈날 = {d.isoformat() for days in 구멍.values() for d in days}
                if 빈날 & stored:
                    print(f"  {market}: 앞쪽 시세가 빈 종목 {len(구멍)}개"
                          f" — 채워진 날 중 {len(빈날 & stored)}일을 다시 받는다")
                    for sid, days in 구멍.items():
                        for d in days:
                            if d.isoformat() in stored:
                                구멍_종목.setdefault(d.isoformat(), set()).add(sid)
                    stored -= 빈날
            todo = pick_days(sessions, stored, max_days)
            skipped += len(sessions) - len(todo)
            print(f"{market}: 거래일 {len(sessions)}일 중 {len(todo)}일 수집")

            연속실패 = 0
            for i, day in enumerate(todo, 1):
                좁힌 = 구멍_종목.get(day.isoformat())
                그날_ids = ids if 좁힌 is None else {t: sid for t, sid in ids.items() if sid in 좁힌}
                count, error = store_day(client, market, day, 그날_ids)
                total_rows += count
                if error:
                    errors.append(f"{market} {day}: {error}")
                    if "한도" in error:
                        break
                    # **연속 실패면 멈춘다** (docs/infra.md 25.387, 배당의 25.319 와 같은 규칙). KRX 401("인증키가
                    # 무효합니다")에는 "한도" 라는 말이 없어, 만료된 키로 `--days 365` 를 돌리면 약 490회를 끝까지
                    # 부르고 전부 카운터에 잡혔다. 한 날의 일시 오류가 아니라 키·점검 같은 전체 문제다
                    연속실패 += 1
                    if 연속실패 >= MAX_CONSECUTIVE_FAILURES:
                        errors.append(f"{market}: {연속실패}번 잇따라 실패해 멈췄습니다 (키·점검을 확인하세요)")
                        break
                else:
                    연속실패 = 0
                if i % 10 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}  누적 {total_rows:,}행")
                # 시작할 때 센 예산은 **예측**이다(PRICE_ROW_D1_COST 가 어림이다). 도는 동안
                # 실제 카운터를 다시 재서, 예측이 틀렸어도 여유를 파먹지 않고 멈춘다
                if budget_reserve is not None and i % BUDGET_RECHECK_EVERY == 0:
                    budget_stop = budget_stop_reason(
                        db.remaining_d1_daily_writes(client), budget_reserve, market, day
                    )
                    if budget_stop:
                        print(budget_stop)
                        break
                time.sleep(CALL_INTERVAL_SECONDS)

        # 예산으로 멈춘 것은 **고장이 아니다**(docs/infra.md 25.6). errors 에 넣으면 status 가
        # partial/failed 가 되어 진짜 실패와 섞인다. 기록에는 남기되 상태는 건드리지 않는다
        status = "failed" if errors and total_rows == 0 else ("partial" if errors else "success")
        db.finish_batch_run(
            client,
            run_id,
            status=status,
            step_log={
                "rows": total_rows, "skipped_days": skipped, "refresh": refresh,
                "errors": errors[:20], "budget_stop": budget_stop,
            },  # fmt: skip
        )

        print(f"저장 {total_rows:,}행, 건너뛴 거래일 {skipped}일")
        if errors:
            print(f"오류 {len(errors)}건")
            for error in errors[:5]:
                print(f"  - {error}")
        return 1 if status == "failed" else 0
    finally:
        client.close()


def resolve_range(
    today: date, days: int | None, start: str | None, end: str | None
) -> tuple[date, date] | None:
    """명령줄이 가리키는 날짜 구간. 정할 수 없으면 None.

    **`--to` 를 생략하면 오늘까지다.** 예전에는 `--from` 과 `--to` 가 둘 다 있어야만 했고,
    시작일만 주면 `parser.error` 로 **종료코드 2** 로 죽었다. `d1-catchup.yml` 과
    `turso-return.yml` 이 둘 다 시작일만 주고 부르고 있었다 — 그래서 D1 따라잡기는
    만들어진 뒤 단 한 번도 2단계(과거 시세)를 통과하지 못했고, 뒤의 재무·업종·점수·신호가
    전부 건너뛰어졌다. 워크플로 두 곳을 각각 고치는 대신 여기서 받아 준다. 세 번째 호출자가
    같은 함정에 빠지지 않는다 (2026-09-19, docs/infra.md 25.16).

    기준일은 UTC 의 오늘이다. `--days` 가 이미 그렇게 하고 있어 맞춘다. 따라잡기는
    00:05 UTC(09:05 KST)에 돌아 UTC 날짜와 KST 날짜가 같다.
    """
    if days:
        from datetime import timedelta

        return today - timedelta(days=days), today
    if start:
        return date.fromisoformat(start), date.fromisoformat(end) if end else today
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="국내 시세 백필")
    parser.add_argument("--days", type=int, help="오늘부터 며칠 전까지")
    parser.add_argument("--from", dest="start", help="시작일 YYYY-MM-DD")
    parser.add_argument("--to", dest="end", help="종료일 YYYY-MM-DD")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="이미 저장된 날도 다시 받는다 (등락률 채우기, docs/adjust.md 4장)",
    )
    parser.add_argument(
        "--only-universe", dest="only_universe", action="store_true",
        help="유니버스·보유·관심 종목만 저장한다 (같은 예산으로 3배 긴 기간, infra 25.8)",
    )  # fmt: skip
    parser.add_argument(
        "--budget-reserve", dest="budget_reserve", type=int,
        help="D1 오늘 남은 쓰기에서 이만큼 남기고 받을 날 수를 정한다",
    )  # fmt: skip
    parser.add_argument(
        "--max-days", dest="max_days", type=int,
        help="이번에 받을 최대 거래일 수. 가장 최근의 빠진 날부터 (D1 하루 쓰기 한도용, infra 25.8)",
    )  # fmt: skip
    args = parser.parse_args()

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    span = resolve_range(datetime.now(UTC).date(), args.days, args.start, args.end)
    if span is None:
        parser.error("--days 또는 --from 을 주세요 (--to 를 생략하면 오늘까지)")
    start, end = span

    print(f"{start} ~ {end}")
    return run(start, end, args.refresh, args.max_days, args.only_universe, args.budget_reserve)


if __name__ == "__main__":
    sys.exit(guard(main))
