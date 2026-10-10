"""유니버스 갱신. 주 1회 돈다.

하는 일
  1. 한국거래소에서 종목 마스터를 받아 stocks 를 갱신한다
  2. 나스닥 심볼 디렉터리로 미국 종목 마스터를 갱신한다
  3. 저장된 시세로 20일 평균 거래대금을 계산한다
  4. 제외 규칙을 적용해 universe_members 에 스냅샷을 남긴다

20일 평균 거래대금이 필요하므로 시세가 20 거래일 이상 쌓여 있어야 한다.
그 전에는 대부분 "데이터없음" 으로 제외된다. scripts 의 백필을 먼저 돌린다.

실행
  python -m batch.jobs.universe --market KR
  python -m batch.jobs.universe --market US
  python -m batch.jobs.universe --market KR --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import pit_universe as pit
from batch.services import universe as uni

log = logging.getLogger("universe")

JOB_NAME = "universe"

# 야후에 시총을 종목마다 물으면 주간 배치로 감당하기 어렵다(월 230~314분). 그래서 SEC 주식수를 쓴다.
# 미국 시가총액은 이 배치가 아니라 us_shares 배치(SEC 주식수 × 종가)가 채운다. 그 배치가 한 번도 안 돌았으면 알린다.
US_MARKET_CAP_MISSING = "미국 시가총액이 비어 있다. us-shares 워크플로를 먼저 돌려야 한다"



#: 시장의 거래일로 보는 문턱 — 그날 행이 있는 종목 수가 가장 많은 날의 이만큼 (25.1109). 0.5 는 "시장 대부분" 을
#: 가르는 값이다 — 수정주가 재수집이 넣는 행은 최대 100종목(미국 수천 종목의 몇 %)이고, 정상 거래일은 정지
#: 종목 몇이 빠져도 거의 전부다
MARKET_DAY_MIN_SHARE = 0.5
#: 종목마다 N 행보다 더 읽는 행 — 창에서 뺀 날(위)이 있어도 그 종목이 시장의 N 거래일을 덮게. 주 1회 실행이고
#: 한 주(5거래일)면 토요일 재수집 하루를 넉넉히 덮는다
MARKET_DAY_SLACK = 5

def us_market_cap_warnings(client: TursoClient) -> list[str]:
    rs = client.execute(
        "SELECT COUNT(*) FROM stocks WHERE country = 'US' AND status = 'active' AND market_cap IS NOT NULL"
    )
    return [] if rs.rows and int(rs.rows[0][0]) > 0 else [US_MARKET_CAP_MISSING]


def _avg_turnover_map(
    client: TursoClient, country: str, as_of: str, days: int = 20
) -> dict[int, int]:
    """종목별 최근 N 거래일 평균 거래대금.

    거래일이 N 일에 못 미치면 그 종목은 넣지 않는다. 적은 표본으로 계산한
    평균을 20일 평균이라고 부르면 안 된다.

    **창은 시장의 최근 N 거래일이다. 그 안에서 행이 없는 날은 거래대금 0 으로 센다** (docs/infra.md 25.211).
    예전에는 "그 종목의 최근 N 행" 의 평균이었다. 행이 빠진 날(거래가 없어 종가 0 이라 적재가 버린 날, 25.203)이
    있으면 창이 더 옛날로 늘어나 **거래가 없던 날이 평균에서 빠지고**, 오래 쉰 종목이 유동성 하한을 통과한다.
    읽는 행은 그대로다 — 종목마다 N 행을 받아 파이썬에서 시장의 N 번째 날짜를 찾는다.

    **행은 있는데 거래대금을 모르는 날(야후 폴백, 25.505)은 0 이 아니라 모르는 날이다** (docs/infra.md 25.518, 감사).
    예전에는 `value IS NOT NULL` 로 거른 뒤 그날을 "행 없음" 처럼 0 으로 셌다 — 한 시장만 폴백한 날이 끼면 그 시장
    종목의 평균이 1/20 낮아져 문턱 근처 종목이 '거래대금미달' 로 빠졌다. 그날은 분자·분모에서 모두 뺀다.
    """
    rs = client.execute(
        "SELECT p.stock_id, p.date, p.value FROM stocks s CROSS JOIN prices p"
        " WHERE s.country = ? AND p.stock_id = s.id AND p.date <= ?"
        "   AND p.date >= COALESCE((SELECT x.date FROM prices x WHERE x.stock_id = s.id AND x.date <= ?"
        "     ORDER BY x.date DESC LIMIT 1 OFFSET ? - 1), '')",
        # 몇 종목만 가진 날을 창에서 빼면(25.1109) 그 종목은 N 행으로 창을 다 덮지 못한다 — 여유 행을 더 읽는다
        [country, as_of, as_of, days + MARKET_DAY_SLACK],
    )

    by_stock: dict[int, list[tuple[str, float | None]]] = {}
    for row in rs.dicts():
        값 = row["value"]
        by_stock.setdefault(int(row["stock_id"]), []).append((str(row["date"]), None if 값 is None else float(값)))
    # 시장의 최근 N 거래일 = 받은 행의 날짜 가운데 위에서 N 번째. 대부분의 종목이 매일 거래하므로 빠짐이 없다.
    # **몇 종목만 가진 날은 시장의 거래일이 아니다** (docs/infra.md 25.1109, 유니버스 감사 재현). 토요일 수정주가
    # 재수집이 대기열 종목(최대 100개)에만 금요일 행을 넣고 일요일 유니버스가 돌면, 창이 금요일까지 밀려 금요일 행이
    # 없는 나머지 전 종목의 금요일이 0 으로 들어갔다 — 평균이 19/20 로 깎여 500만~526만 달러 종목이 매주
    # '거래대금미달' 로 빠졌다. 그날 행이 있는 종목이 가장 많은 날의 `MARKET_DAY_MIN_SHARE` 에 못 미치면 뺀다
    수: dict[str, int] = {}
    for rows in by_stock.values():
        for d, _v in rows:
            수[d] = 수.get(d, 0) + 1
    문턱 = MARKET_DAY_MIN_SHARE * max(수.values(), default=0)
    날짜들 = sorted((d for d, n in 수.items() if n >= 문턱), reverse=True)
    if len(날짜들) < days:
        return {}
    창 = set(날짜들[:days])

    out: dict[int, int] = {}
    for stock_id, rows in by_stock.items():
        # 이력이 N 거래일에 못 미치면 평균이라 부르지 않는다 — 창에서 뺀 날의 행은 세지 않는다
        if sum(1 for d, _v in rows if d in 창 or d < min(창)) < days:
            continue
        모름 = sum(1 for d, v in rows if d in 창 and v is None)
        아는_날 = days - 모름
        # 아는 날이 너무 적으면 평균이라 부르지 않는다 (25.525, 교차검증). 숫자(10)는 백테스트와 같지만
        # **효과는 반대다**(25.540 교차검증) — 백테스트는 모르면 통과, 운영은 '데이터없음' 으로 뺀다.
        # 운영은 돈이 걸려 덜 사는 쪽을 골랐다
        if 아는_날 < pit.MIN_TURNOVER_SAMPLES:
            continue
        out[stock_id] = int(sum(v for d, v in rows if d in 창 and v is not None) / 아는_날)
    return out


def refresh_kr_master(client: TursoClient, bas_dd: str) -> tuple[int, list[str]]:
    """한국거래소 종목기본정보로 stocks 를 갱신한다."""
    from batch.sources import krx

    warnings: list[str] = []
    total = 0
    받은: dict[str, set[str]] = {}

    for market in ("KOSPI", "KOSDAQ"):
        # **부르기 전에 본다** (docs/infra.md 25.606 — 25.387 과 같은 모양). 한도가 찬 날에도 시장마다 불렀다.
        # 멈추면 기존 행으로 판정한다(받은 시장이 없으면 폐지 판정도 건너뛴다)
        if db.usage_blocked_today(client, "krx_openapi"):
            warnings.append(f"{market} 마스터: 거래소 일일 호출 한도에 도달해 부르지 않았습니다")
            break
        result = krx.fetch_master(market, bas_dd)
        db.record_api_call(
            client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80
        )

        if not result.ok:
            warnings.append(f"{market} 마스터 실패: {result.error}")
            continue
        if not result.data:
            # **빈 응답도 말한다** (docs/infra.md 25.612, 감사 — 시총의 25.571 과 같은 모양). 예전에는 조용히 넘어가
            # 지난주 소속부(관리종목 지정·해제)로 판정하고 신규 상장을 빠뜨렸는데, 남는 말은 폐지 판정 건너뜀뿐이었다
            warnings.append(
                f"{market} 마스터: 거래소 응답이 비었습니다 — 소속부(관리종목)·신규 상장이 이번 주 갱신되지 않았습니다"
            )
            continue

        statements: list[tuple[str, list[Any]]] = []
        now = db.now_iso()
        받은[market] = {row.ticker for row in result.data if row.ticker}
        # 이전상장한 종목은 **새 행을 만들지 않고 옛 행의 시장을 바꾼다** (25.529) — 시세 이력이 그 행에 있다
        statements.extend(market_move_statements(
            _country_rows(client, "KR"), {row.ticker: (row.market or market) for row in result.data if row.ticker},
        ))  # fmt: skip
        for row in result.data:
            if not row.ticker:
                continue
            statements.append(
                (
                    "INSERT INTO stocks"
                    " (ticker, market, country, name_ko, name_en, currency, yahoo_symbol,"
                    "  status, source, fetched_at, listed_date, security_group,"
                    "  section_type, share_kind, isin, listed_shares)"
                    " VALUES (?, ?, 'KR', ?, ?, 'KRW', ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (ticker, market) DO UPDATE SET"
                    "   name_ko = excluded.name_ko, name_en = excluded.name_en,"
                    "   yahoo_symbol = excluded.yahoo_symbol, source = excluded.source,"
                    # 빈 상장일이 좋은 값을 NULL 로 덮어 "데이터없음" 으로 빠졌다 (25.654, 감사)
                    "   fetched_at = excluded.fetched_at,"
                    "   listed_date = COALESCE(excluded.listed_date, stocks.listed_date),"
                    "   security_group = excluded.security_group,"
                    "   section_type = excluded.section_type,"
                    "   share_kind = excluded.share_kind, isin = excluded.isin,"
                    "   listed_shares = excluded.listed_shares,"
                    # 목록에서 빠져 상장폐지로 적었는데 다시 나타나면 되돌린다 (25.361) — 되돌릴 수 있게 둔다
                    "   status = CASE WHEN stocks.status = 'delisted' THEN 'active' ELSE stocks.status END",
                    [
                        row.ticker,
                        row.market or market,
                        row.name,
                        row.name_en,
                        f"{row.ticker}.{'KS' if market == 'KOSPI' else 'KQ'}",
                        result.source,
                        now,
                        row.listed_date_iso,
                        row.security_group,
                        row.section_type,
                        row.share_kind,
                        row.isin,
                        row.listed_shares,
                    ],
                )
            )
            total += 1

        # 한 번에 다 보내면 요청이 너무 커진다. 나눠 보낸다.
        for chunk in _chunks(statements, 200):
            client.batch(chunk)

    # **목록에서 사라진 종목을 적는다** (docs/infra.md 25.361). 예전에는 넣고 고치기만 해서 3월에 폐지된 종목이
    # 계속 active 로 남아 매주 스냅샷에서 "거래대금미달"·"시총미달" 같은 **틀린 사유**로 빠졌고, 백테스트의
    # 생존편향 경고(`has_delisted`)도 계속 꺼져 있었다.
    if len(받은) == 2:
        active = client.execute(
            # 이은 ETF(25.896, market 'ETF')는 거래소 주식 목록으로 판정하지 않는다 (25.902)
            "SELECT id, ticker, market FROM stocks WHERE country = 'KR' AND status = 'active' AND asset_type = 'stock'"
        ).rows
        statements, 메모 = kr_missing_statements(
            [(int(r[0]), str(r[1]), str(r[2])) for r in active], 받은, db.now_iso()
        )
        warnings.extend(메모)
        for chunk in _chunks(statements, 200):
            client.batch(chunk)
    else:
        warnings.append("한 시장의 종목 목록을 못 받아 상장폐지 판정을 건너뛰었습니다")

    return total, warnings


def _country_rows(client: TursoClient, country: str) -> list[tuple[int, str, str, bool]]:
    """(id, ticker, market, active). **옮기는 것은 active 행만, 막는 것은 모든 행으로** 본다 (25.536·25.541, 교차검증).

    - 폐지·제외 행까지 옮기면 다른 시장에서 같은 티커로 새로 상장한 **다른 회사**가 옛 회사의 행을 물려받았다(25.536).
    - 그런데 막기(새 시장에 이미 행이 있나)까지 active 만 보자, 새 시장의 옛 폐지·제외 행을 못 봐 `UPDATE` 가
      UNIQUE(ticker, market) 에 걸려 **유니버스 배치 전체가 실패**했다(25.541, 재현)."""
    rs = client.execute("SELECT id, ticker, market, status FROM stocks WHERE country = ?", [country])
    return [(int(r[0]), str(r[1]), str(r[2]), str(r[3]) == "active") for r in rs.rows]


def market_move_statements(
    existing: list[tuple[int, str, str, bool]] | list[tuple[int, str, str]], listed: dict[str, str]
) -> list[tuple[str, list[Any]]]:
    """거래소를 옮긴 종목 → 옛 행의 `market` 을 바꾸는 문장 (docs/infra.md 25.529, 감사 재현).

    `(ticker, market)` 이 유일 키라 예전에는 옮긴 종목에 **새 행**이 생겼다. 시세 이력은 옛 행에 묶여
    새 행은 20거래일이 쌓일 때까지 '데이터없음'(약 4주)이었고, 미국은 첫 관측일이 상장일이 되어 그 뒤 1년
    '상장1년미만' 이었다(3년 된 회사가). 새 시장에 **이미 행이 있으면**(이 수정 전에 옮긴 종목) 건드리지 않는다 —
    두 행을 합치는 것은 되돌릴 수 없는 일이라 하지 않는다.
    """
    by_ticker: dict[str, list[tuple[int, str, bool]]] = {}
    for row in existing:
        stock_id, ticker, market = row[0], row[1], row[2]
        active = bool(row[3]) if len(row) > 3 else True
        by_ticker.setdefault(ticker, []).append((stock_id, market, active))
    out: list[tuple[str, list[Any]]] = []
    for ticker, new_market in listed.items():
        rows = by_ticker.get(ticker, [])
        # 새 시장에 **어떤 상태든** 행이 있으면 건드리지 않는다(UNIQUE). 옮길 수 있는 것은 active 행이 하나뿐일 때만
        if not rows or any(m == new_market for _i, m, _a in rows) or len(rows) != 1 or not rows[0][2]:
            continue
        out.append(("UPDATE stocks SET market = ? WHERE id = ?", [new_market, rows[0][0]]))
    return out


#: 받은 목록이 지금 active 수의 이만큼도 안 되면 응답이 잘린 것으로 보고 아무것도 적지 않는다 (25.361)
MIN_MASTER_COVERAGE = 0.8


def kr_missing_statements(
    active: list[tuple[int, str, str]], 받은: dict[str, set[str]], now: str
) -> tuple[list[tuple[str, list[Any]]], list[str]]:
    """거래소 종목 목록에서 사라진 active 종목 → 상태 갱신 문장과 메모 (docs/infra.md 25.361).

    - **두 시장 어디에도 없으면 `delisted`.** 백테스트가 이 값을 생존편향 판단에 쓴다
    - **다른 시장에 있으면 이전상장**이라 옛 행은 `excluded` 로 둔다. 25.529 부터 active 행은 마스터 갱신이 먼저 시장을
      바꿔 여기까지 오지 않는다 — 여기 오는 것은 이미 두 행으로 갈라진 옛 종목이다.
      `delisted` 로 세면 생존편향 경고가 거짓으로 켜진다 — `us_exclusion_statements` 와 같은 이유
    - 받은 목록이 지금 active 수의 `MIN_MASTER_COVERAGE` 에 못 미치면 **아무것도 적지 않는다.**
      응답이 잘렸을 때 멀쩡한 종목을 통째로 폐지로 적으면 안 된다
    지우지 않는다 — 쌓인 가격·매매가 이 행을 가리킨다. 다시 나타나면 마스터 저장이 active 로 되돌린다.
    """
    memos: list[str] = []
    for market, tickers in 받은.items():
        n_active = sum(1 for _i, _t, m in active if m == market)
        if n_active and len(tickers) < n_active * MIN_MASTER_COVERAGE:
            return [], [
                f"{market} 목록이 {len(tickers)}개로 active {n_active}개보다 너무 적어 상장폐지 판정을 건너뛰었습니다"
            ]
    전체 = set().union(*받은.values())
    out: list[tuple[str, list[Any]]] = []
    for stock_id, ticker, market in active:
        if market not in 받은 or ticker in 받은[market]:
            continue
        상태 = "excluded" if ticker in 전체 else "delisted"
        out.append(("UPDATE stocks SET status = ?, fetched_at = ? WHERE id = ?", [상태, now, stock_id]))
    if out:
        memos.append(f"거래소 목록에서 사라진 {len(out)}종목의 상태를 바꿨습니다(상장폐지·이전상장)")
    return out, memos


#: 시총 갱신의 빈 응답에 직전 거래일로 물러나는 횟수 (docs/infra.md 25.955). 연휴 전 마지막 거래일 치를 거래소가 아직
#: 안 낸 경우가 한 번이면 충분하다 — 더 물러나면 시총이 그만큼 더 묵는다
MARKET_CAP_FALLBACK_STEPS = 1


def refresh_kr_market_cap(client: TursoClient, bas_dd: str) -> list[str]:
    """일별매매정보의 시가총액을 stocks 에 반영한다.

    **빈 응답이면 직전 거래일로 한 번 물러난다** (docs/infra.md 25.955). 2026-10-04 실행은 기준일 10-02(연휴 전 마지막
    거래일)의 응답이 비어 두 시장 모두 "지난 시총으로 판정" 했고, 그 지난 시총은 Actions 정지 때문에 **09-16 치**였다 —
    874종목 가운데 303종목이 그 사이 10% 넘게 움직였다(13회차 진단). 10-01 치는 일일 배치가 받았으므로 그날 값은 있다.
    물러난 날짜를 `market_cap_date` 에 그대로 적는다 — 어느 날 값인지가 남는다.
    """
    from batch.sources import krx

    warnings: list[str] = []
    for market in ("KOSPI", "KOSDAQ"):
        if db.usage_blocked_today(client, "krx_openapi"):  # 부르기 전에 본다 (25.606)
            warnings.append(f"{market} 시총 갱신: 거래소 일일 호출 한도라 부르지 않았습니다 — 지난 시총으로 판정합니다")
            break
        쓸_날 = bas_dd
        result = krx.fetch_daily(market, 쓸_날)
        db.record_api_call(
            client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80
        )
        물러남 = 0
        while result.ok and not result.data and 물러남 < MARKET_CAP_FALLBACK_STEPS:
            # 빈 응답 — 직전 거래일로 물러난다 (25.955). 거래소가 그날 치를 아직 안 낸 것일 수 있다
            물러남 += 1
            앞날 = cal.previous_session("KR", before=date.fromisoformat(f"{쓸_날[:4]}-{쓸_날[4:6]}-{쓸_날[6:]}"))
            쓸_날 = 앞날.strftime("%Y%m%d")
            result = krx.fetch_daily(market, 쓸_날)
            db.record_api_call(
                client, "krx_openapi", count=result.attempts, limit_value=krx.DAILY_LIMIT, warn_at_pct=80
            )
        if not result.ok:
            warnings.append(f"{market} 시총 갱신 실패: {result.error}")
            continue
        if not result.data:
            # 빈 응답도 말한다 (25.571, 감사) — `ok` 라 경고 없이 지난주 시총으로 판정했고,
            # 묵은 값 경고는 2주 뒤에야 떴다
            warnings.append(
                f"{market} 시총 갱신: 거래소 응답이 비었습니다(직전 거래일 {쓸_날} 도) — 지난 시총으로 판정합니다"
            )
            continue
        if 물러남:
            warnings.append(f"{market} 시총 갱신: {bas_dd} 응답이 비어 직전 거래일 {쓸_날} 값으로 갱신했습니다")

        as_of = f"{쓸_날[:4]}-{쓸_날[4:6]}-{쓸_날[6:]}"
        statements = [
            (
                "UPDATE stocks SET market_cap = ?, market_cap_date = ?"
                " WHERE ticker = ? AND country = 'KR'",
                [row.market_cap, as_of, row.isu_cd],
            )
            for row in result.data
            if row.market_cap is not None
        ]
        for chunk in _chunks(statements, 200):
            client.batch(chunk)

    return warnings


def refresh_us_master(client: TursoClient) -> tuple[int, list[str]]:
    from batch.sources import nasdaq_symbols

    result = nasdaq_symbols.fetch_symbols()
    db.record_api_call(client, "nasdaqtrader_symdir")

    if not result.ok:
        return 0, [f"미국 마스터 실패: {result.error}"]

    common = [s for s in result.data if s.is_common_stock]
    now = db.now_iso()
    # 거래소를 옮긴 종목은 옛 행의 시장을 바꾼다 (25.529) — 새 행이면 시세 이력이 없어 4주 '데이터없음',
    # 첫 관측일이 상장일이 되어 1년 '상장1년미만' 이었다
    statements = market_move_statements(_country_rows(client, "US"), {s.symbol: s.exchange for s in common})
    statements += [
        (
            "INSERT INTO stocks"
            " (ticker, market, country, name_en, currency, yahoo_symbol,"
            "  status, source, fetched_at, share_kind)"
            " VALUES (?, ?, 'US', ?, 'USD', ?, 'active', ?, ?, '보통주')"
            " ON CONFLICT (ticker, market) DO UPDATE SET"
            "   name_en = excluded.name_en, yahoo_symbol = excluded.yahoo_symbol,"
            "   source = excluded.source, fetched_at = excluded.fetched_at,"
            # 목록에서 사라져 상장폐지로 적었다가 다시 나타나도 되돌린다 (25.519)
            "   status = CASE WHEN stocks.status IN ('excluded', 'delisted') THEN 'active' ELSE stocks.status END",
            [s.symbol, s.exchange, s.name, s.yahoo_symbol, result.source, now],
        )
        for s in common
    ]
    exclusions = us_exclusion_statements(result.data, now)
    for chunk in _chunks(statements + exclusions, 200):
        client.batch(chunk)

    # **목록에서 사라진 미국 종목도 적는다** (docs/infra.md 25.519, 감사 재현). 국내는 25.361 에서 고쳤는데 미국에는 그
    # 길이 없어, 인수로 폐지된 대형주가 20일 평균(남은 행 ÷ 20)이 하한 밑으로 내려갈 때까지 약 3~4주 동안 편입으로
    # 남았다. 심볼 목록 전체(보통주 아닌 것 포함)로 보므로 다른 거래소로 옮긴 종목은 이전상장(`excluded`)이다.
    # 심볼만 바뀐 경우도 옛 행은 `delisted` 가 된다 — 옛 심볼의 시세는 거기서 끝나므로 틀린 말이 아니다
    받은: dict[str, set[str]] = {}
    for s in result.data:
        받은.setdefault(s.exchange, set()).add(s.symbol)
    # **이은 ETF(25.896)는 빼고 본다** (docs/infra.md 25.902). 나스닥 심볼 목록은 ETF 도 담고 거래소 이름(NYSE
    # Arca·Cboe BZX)도
    # `etfs.exchange` 와 같아서, ETF 가 거래소를 옮기거나 목록에서 잠깐 빠지면 `excluded`·`delisted` 가 됐다. 그러면
    # 일일 시세
    # 수집(active 만)이 멈추는데, `etf_link` 는 이미 이은 ETF 를 다시 보지 않아 되살릴 길이 없었다. ETF 의 상장 여부는
    # `etfs` 가 본다
    active = client.execute(
        "SELECT id, ticker, market FROM stocks WHERE country = 'US' AND status = 'active' AND asset_type = 'stock'"
    ).rows
    사라진, 메모 = kr_missing_statements([(int(r[0]), str(r[1]), str(r[2])) for r in active], 받은, now)
    for chunk in _chunks(사라진, 200):
        client.batch(chunk)

    return len(common), [
        f"받은 종목 {len(result.data)}개 중 보통주 {len(common)}개만 저장했습니다",
        f"보통주가 아닌 {len(exclusions)}개는 이미 들어 있었다면 수집 대상에서 뺐습니다",
        *메모,
    ]


def us_exclusion_statements(symbols: list, now: str) -> list[tuple[str, list[Any]]]:
    """보통주가 아닌 심볼을 수집 대상에서 뺀다.

    **왜 필요한가 (2026-09-17)**: 위의 저장은 넣고 고치기만 한다. 보통주 판정을
    나중에 엄격하게 바꿔도 **이미 들어간 행은 그대로 active 로 남아** 매 거래일
    야후에 헛되이 물어봤다. 실제로 필터를 붙인 뒤에도 `AMH$G` 같은 우선주 25개가
    미국 배치 로그에 계속 나왔다.

    지우지 않고 `status = 'excluded'` 로 둔다. 이미 쌓인 prices 가 이 행을
    참조하고, 판정이 다시 바뀌면 위의 저장이 active 로 되돌린다.

    `delisted` 를 쓰지 않는 이유: 백테스트가 그 값을 상장폐지 종목 보유 여부로
    읽는다(`jobs/backtest.py`). 증권 종류로 뺀 것을 상장폐지로 세면 생존편향
    경고가 거짓으로 꺼진다.

    목록에서 아예 사라진 심볼은 `refresh_us_master` 가 `kr_missing_statements` 로 따로 적는다 (25.519).
    """
    return [
        (
            # 이은 ETF(25.896)는 빼지 않는다 — 나스닥 목록에서는 보통주가 아니지만 추천·보유 ETF 라 시세를 받아야 한다
            "UPDATE stocks SET status = 'excluded', fetched_at = ?"
            " WHERE country = 'US' AND ticker = ? AND status = 'active' AND asset_type = 'stock'",
            [now, s.symbol],
        )
        for s in symbols
        if not s.is_common_stock
    ]


#: 시총 기준일이 스냅샷보다 이만큼 넘게 앞서면 묵은 값이다 (docs/infra.md 25.362).
#: 국내 시총(유니버스 작업)과 미국 주식수(`us-shares.yml`)가 모두 주 1회라, 두 번 거르면 넘는다
MARKET_CAP_MAX_AGE_DAYS = 14


def stale_market_cap_warning(rows: list[dict], as_of: date) -> str | None:
    """묵은 시총으로 판정한 종목 수를 말한다 (docs/infra.md 25.362).

    `stocks.market_cap_date` 는 쓰기만 하고 아무도 읽지 않았다. 이번 주 시총 조회가 한 시장만 실패해도
    경고 한 줄만 남고 **지난주 값으로 편입·제외가 정해졌다**(950억→1,100억이 된 종목이 여전히 "시총미달").
    판정을 바꾸지는 않는다 — 묵었다고 빼면 수집이 멈춘 주에 유니버스가 통째로 빈다. 대신 실행 기록에 남긴다.
    """
    묵은: list[str] = []
    for row in rows:
        when = row.get("market_cap_date")
        if row.get("market_cap") is None or not when:
            continue
        try:
            age = (as_of - date.fromisoformat(str(when)[:10])).days
        except ValueError:
            continue
        if age > MARKET_CAP_MAX_AGE_DAYS:
            묵은.append(f"{row['ticker']}({when})")
    if not 묵은:
        return None
    return (
        f"시총 기준일이 {MARKET_CAP_MAX_AGE_DAYS}일 넘게 지난 {len(묵은)}종목을 그 값으로 판정했습니다"
        f" (예: {', '.join(묵은[:5])}) — 시총 수집이 멈췄는지 보세요"
    )


def build_snapshot(
    client: TursoClient, market: str, snapshot_date: str, *, dry_run: bool = False
) -> tuple[dict[str, int], list[str]]:
    """제외 규칙을 적용해 유니버스 스냅샷을 남긴다.

    `dry_run` 이면 **한 줄도 쓰지 않고** 판정 결과만 돌려준다.
    """
    country = "KR" if market.upper() == "KR" else "US"
    filters = (
        uni.UniverseFilters.korea() if country == "KR" else uni.UniverseFilters.usa()
    )
    warnings: list[str] = []

    rs = client.execute(
        "SELECT id, ticker, name_ko, name_en, market, security_group, section_type,"
        "       share_kind, listed_date, market_cap, market_cap_date, currency"
        # 주식만 판정한다 — 이은 ETF(25.896)를 보통주 잣대로 재면 "주권아님"·"데이터없음" 으로 빠져, 들고 있으면 날마다
        # "유니버스에서 빠졌습니다" 매도 플래그가 섰다. **25.896 은 이 주석만 적고 조건을 빠뜨렸다** (25.906, 감사)
        " FROM stocks WHERE country = ? AND status = 'active' AND asset_type = 'stock'",
        [country],
    )
    rows = rs.dicts()
    if not rows:
        return {"편입": 0}, [f"{country} 종목 마스터가 비어 있습니다"]

    # **못 본 검사를 말한다** (docs/infra.md 25.129). 미국 스냅샷에 "관리종목 0건" 이
    # 뜨는 것은 없어서가 아니라 판정할 열이 없어서다. 둘을 같은 모양으로 두지 않는다
    안본검사 = uni.not_checked_warning(country)
    if 안본검사:
        warnings.append(안본검사)

    turnover = _avg_turnover_map(client, country, snapshot_date)
    if not turnover:
        warnings.append(
            "20 거래일치 시세가 아직 없어 거래대금 판정이 불가능합니다. "
            "백필을 먼저 돌리세요"
        )

    as_of = date.fromisoformat(snapshot_date)
    verdicts: list[uni.Verdict] = []
    rows_data: list[tuple] = []
    now = db.now_iso()
    # 미국 상장일은 첫 관측일이다 — **일일 수집이 시작된 날** 근처면 실제 상장일은 그보다 이르다 (25.652, 감사).
    # 25.652 는 상장일 무리(7일 안 10종목)로 가장자리를 찾았는데, 운영은 활성 종목 전체를 모아 IPO 가 몰린 주도
    # 가장자리로 잡혔다(25.656, 교차검증). 날짜 하나 — 미국 일일 배치가 처음 성공한 거래일 — 만 가장자리로 본다.
    # 5년 백필 시작일은 이미 1년을 넘어 따로 볼 까닭이 없다. 기록이 없으면 가장자리도 없다(예전과 같다)
    수집시작: str | None = None
    if country == "US":
        try:
            from batch.jobs import daily as _daily

            수집시작 = client.execute(
                "SELECT MIN(trade_date) FROM batch_runs WHERE job_name = ? AND status IN ('success', 'partial')",
                [_daily.job_name("US")],
            ).scalar()
        except Exception as exc:  # noqa: BLE001 — 못 읽으면 가장자리 없이 판정한다(예전과 같다)
            warnings.append(f"미국 일일 수집 시작일을 읽지 못해 상장일 가장자리 예외를 쓰지 않았습니다 ({exc})")

    수집앞_여유 = 21

    def _가장자리인가(listed: object) -> bool:
        if not listed or not 수집시작:
            return False
        from batch.services import pit_universe as pit

        # 첫 일일 실행은 지난 약 10거래일치를 함께 받는다(`yfinance_src` period=10d) — 백필 밖 종목의 첫 관측일은
        # 수집 시작일보다 **앞선다.** 25.656 은 뒤쪽만 봐 그 종목들을 놓쳤다 (25.658, 교차검증). 앞으로 3주를 본다
        return -수집앞_여유 <= pit.days_between(str(수집시작)[:10], str(listed)[:10]) <= pit.HISTORY_EDGE_DAYS

    for row in rows:
        stock_id = int(row["id"])
        candidate = uni.Candidate(
            ticker=str(row["ticker"]),
            name=str(row["name_ko"] or row["name_en"] or row["ticker"]),
            market=str(row["market"]),
            security_group=str(row["security_group"] or ""),
            section_type=str(row["section_type"] or ""),
            share_kind=str(row["share_kind"] or ""),
            listed_date=row["listed_date"],
            market_cap=row["market_cap"],
            avg_turnover_20d=turnover.get(stock_id),
            listed_at_history_edge=_가장자리인가(row["listed_date"]),
        )
        verdict = uni.judge(candidate, filters, as_of)
        verdicts.append(verdict)

        rows_data.append(
            (
                snapshot_date,
                stock_id,
                1 if verdict.included else 0,
                verdict.reason,
                candidate.market_cap,
                candidate.avg_turnover_20d,
                verdict.listed_days,
                str(row["currency"]),
                now,
            )
        )

    빠진판정: list[uni.Verdict] = []  # 표시 경고(소속부 대조)와 섞지 않는다 — 소속부 목록과 줄이 어긋난다
    # **지난 스냅샷에 있던 종목이 마스터에서 빠졌으면 사유를 남긴다** (docs/infra.md 25.412).
    # 예전에는 active 만 판정해 폐지·제외 종목의 행이 다음 스냅샷에 **아예 없었다** — 왜 빠졌는지 기록이 없고,
    # 보유 중이면 매도 플래그의 "최신 유니버스에서 빠졌습니다" 도 뜨지 않았다. 같은 날짜로 다시 돌리면
    # 옛 `included = 1` 행이 그대로 남아 폐지 종목이 계속 점수·신호 대상이 됐다
    for r in client.execute(
        "SELECT DISTINCT s.id, s.status, s.currency FROM stocks s JOIN universe_members u ON u.stock_id = s.id"
        # 지난번에 편입이었거나 **지금 보유 중**인 것만 — 폐지 종목 전부를 매주 끝없이 이어 쓰지 않는다
        " WHERE s.country = ? AND s.status != 'active'"
        "   AND (u.included = 1 OR s.id IN (SELECT stock_id FROM positions WHERE quantity > 0))"
        "   AND u.snapshot_date = ("
        "   SELECT MAX(um.snapshot_date) FROM universe_members um JOIN stocks s2 ON s2.id = um.stock_id"
        "   WHERE s2.country = ? AND um.snapshot_date <= ?)",
        [country, country, snapshot_date],
    ).dicts():
        사유 = uni.STATUS_REASON.get(str(r["status"]), uni.REASON_MASTER_EXCLUDED)
        빠진판정.append(uni.Verdict(included=False, reason=사유, listed_days=None))
        rows_data.append((snapshot_date, int(r["id"]), 0, 사유, None, None, None, str(r["currency"]), now))

    묵은시총 = stale_market_cap_warning(rows, as_of)
    if 묵은시총:
        warnings.append(묵은시총)

    요약 = uni.summarize(verdicts + 빠진판정)
    # **판정이 정말 걸리는지 스스로 답하게 한다** (docs/infra.md 25.157).
    # 국내 관리종목·거래정지는 소속부 글자 하나에 매달려 있는데 그 값을 확인한 적이 없다
    표시경고 = uni.marker_warning(country, verdicts, [str(r["section_type"] or "") for r in rows])
    if 표시경고:
        warnings.append(표시경고)
    if dry_run:
        warnings.append("시험 실행이라 스냅샷을 쓰지 않았습니다")
        return 요약, warnings

    # **편입 0 인 판정으로 멀쩡한 스냅샷을 덮지 않는다** (2026-09-21, docs/infra.md 25.64).
    #
    # 전 종목이 "데이터없음" 으로 빠지는 일이 실제로 있었다(2026-09-18, D1). 시세를 넣기 전에
    # 유니버스를 돌리면 20일 평균 거래대금을 낼 수 없어 그렇게 된다. 그런데 뒤의 모든 것이
    # **최신 스냅샷**을 보므로, 그 0 이 곧바로 "유니버스가 비어 있다" 가 되어 백필이 멈추고
    # 점수·신호가 그날 치를 못 낸다. `should_skip` 이 "편입 0 은 돈 것으로 치지 않는다" 로
    # 다시 돌게는 해 두었지만, 그것은 **다시 시도하게 할 뿐 덮어쓰는 것을 막지 못한다.**
    #
    # 편입 0 은 지식이 아니라 **지식의 부재**다. 지난번의 답을 지울 이유가 되지 못한다.
    if 요약.get("편입", 0) == 0 and has_members(client, country):
        warnings.append(
            "편입 0 으로 판정되어 스냅샷을 쓰지 않았습니다. 지난 스냅샷을 그대로 둡니다"
            " — 20 거래일치 시세가 쌓인 뒤 다시 돌리세요"
        )
        return 요약, warnings

    _bulk_upsert_universe(client, rows_data)
    return 요약, warnings


# 종목마다 문장을 하나씩 보내면 8,000종목에 40번 넘게 왕복한다.
# 백필에서 같은 구조가 제한 시간에 걸렸다. 여러 행을 한 문장에 넣는다.
_UNIVERSE_COLUMNS = (
    "snapshot_date, stock_id, included, exclude_reason, market_cap,"
    " avg_turnover_20d, listed_days, currency, created_at"
)
_UNIVERSE_PLACEHOLDER = "(?, ?, ?, ?, ?, ?, ?, ?, ?)"
UNIVERSE_ROWS_PER_STATEMENT = 400


def _bulk_upsert_universe(client: TursoClient, rows: list[tuple]) -> int:
    if not rows:
        return 0

    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), UNIVERSE_ROWS_PER_STATEMENT):
        chunk = rows[start : start + UNIVERSE_ROWS_PER_STATEMENT]
        placeholders = ", ".join([_UNIVERSE_PLACEHOLDER] * len(chunk))
        sql = (
            f"INSERT INTO universe_members ({_UNIVERSE_COLUMNS}) VALUES {placeholders}"
            " ON CONFLICT (snapshot_date, stock_id) DO UPDATE SET"
            "   included = excluded.included,"
            "   exclude_reason = excluded.exclude_reason,"
            "   market_cap = excluded.market_cap,"
            "   avg_turnover_20d = excluded.avg_turnover_20d,"
            "   listed_days = excluded.listed_days"
        )
        args: list[Any] = []
        for r in chunk:
            args.extend(r)
        statements.append((sql, args))

    # **반쪽 스냅샷을 최신으로 남기지 않는다** (docs/infra.md 25.1110, 유니버스 감사 재현).
    # Turso 파이프라인은 트랜잭션이 아니라(문장마다 커밋, `turso.py`) 400행 문장 20여 개 중간에서 끊기면 앞 문장만
    # 쓰인다. 최신 스냅샷은 날짜의 MAX 라 그 반쪽(재현: 30종목 → 10종목)이 다음 실행까지 점수·신호·리포트의
    # 유니버스가 됐다. **새 날짜를 쓰다** 실패하면 이번에 쓴 날짜의 이 종목들 행을 지워 지난 스냅샷이 최신으로 남게
    # 한다. 같은 날짜를 다시 쓰던 중이면(이미 있다) 지우지 않는다 — 지우면 그날 스냅샷이 통째로 사라진다
    날짜 = str(rows[0][0])
    ids = sorted({int(r[1]) for r in rows})
    있던 = client.execute(
        "SELECT COUNT(*) FROM universe_members WHERE snapshot_date = ?"
        " AND stock_id IN (SELECT value FROM json_each(?))",
        [날짜, json.dumps(ids)],
    ).scalar()
    try:
        client.batch(statements)
    except Exception:
        if not 있던:
            try:
                client.execute(
                    "DELETE FROM universe_members WHERE snapshot_date = ?"
                    " AND stock_id IN (SELECT value FROM json_each(?))",
                    [날짜, json.dumps(ids)],
                )
            except Exception:  # noqa: BLE001 — 지우기도 실패하면 원래 오류를 올린다(실행이 실패로 남는다)
                log.exception("반쪽 유니버스 스냅샷(%s)을 지우지 못했습니다", 날짜)
        raise
    return len(rows)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def run(market: str, *, dry_run: bool = False) -> int:
    market = market.upper()
    client = TursoClient()
    try:
        db.apply_migrations(client)

        snapshot_date = cal.previous_session(market).isoformat()
        bas_dd = snapshot_date.replace("-", "")
        # **시험 실행은 한 줄도 쓰지 않는다** (2026-09-21, docs/infra.md 25.64).
        # 예전에는 마스터도 스냅샷도 그대로 쓰면서 실행 기록에만 "dryrun" 이라고 적었다 —
        # "확인만 해 보자" 로 부른 사람이 D1 하루 예산을 수만 행 쓰고 **유니버스까지 바꿨다**
        run_id = None if dry_run else db.start_batch_run(
            client,
            job_name=JOB_NAME,
            market=market,
            trade_date=snapshot_date,
        )

        warnings: list[str] = []
        try:
            if dry_run:
                # 마스터를 새로 받지 않는다(그 자체가 쓰기다). 지금 DB 에 있는 것으로 판정만 한다
                count = 0
                warnings.append("시험 실행이라 종목 마스터를 갱신하지 않았습니다")
            elif market == "KR":
                count, w = refresh_kr_master(client, bas_dd)
                warnings += w
                warnings += refresh_kr_market_cap(client, bas_dd)
            else:
                count, w = refresh_us_master(client)
                warnings += w
                warnings += us_market_cap_warnings(client)

            counts, w = build_snapshot(client, market, snapshot_date, dry_run=dry_run)
            warnings += w
        except Exception as exc:  # noqa: BLE001
            if run_id is not None:
                db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
            print(f"실패: {exc}")
            return 1

        step_log = {"master_rows": count, "universe": counts, "warnings": warnings}
        if run_id is not None:
            db.finish_batch_run(
                client,
                run_id,
                status="partial" if warnings else "success",
                step_log=step_log,
            )

        print(f"기준일 {snapshot_date}  마스터 {count}종목")
        print("유니버스 판정")
        for key, value in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {key:<14} {value:>6}")
        if warnings:
            print("경고")
            for warning in warnings:
                print(f"  - {warning}")
        return 0
    finally:
        client.close()


def has_members(client: TursoClient, market: str) -> bool:
    """그 나라의 최신 스냅샷에 편입 종목이 있는가."""
    country = market.upper()
    rs = client.execute(
        "SELECT COUNT(*) FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
        f" WHERE u.included = 1 AND s.country = ? AND u.snapshot_date = {db.latest_snapshot_sql()}",
        [country, country],
    )
    return int(rs.scalar() or 0) > 0


def should_skip(client: TursoClient, market: str, days: float) -> bool:
    """`--skip-if-ran-within` 판정. 최근에 돌았고 **편입 종목이 있어야** 건너뛴다.

    편입 0 인 스냅샷은 돈 것으로 치지 않는다 (2026-09-18 D1). 시세를 넣기 전에 유니버스를 먼저 돌려
    전 종목이 "데이터없음" 으로 빠졌고 그 실행이 partial 로 남았다. 실행 기록만 보고 건너뛰면 편입 0 이
    한 주 내내 이어지고, 유니버스만 받는 백필(backfill_kr --only-universe)이 "유니버스가 비어 있습니다"
    로 멈춰 따라잡기 전체가 서 버린다.
    """
    return db.ran_within(client, JOB_NAME, days, market.upper()) and has_members(client, market)


def main() -> int:
    parser = argparse.ArgumentParser(description="유니버스 갱신")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="한 줄도 쓰지 않고 판정만 본다 (마스터도 갱신하지 않는다)",
    )  # fmt: skip
    parser.add_argument(
        "--skip-if-ran-within", dest="skip_days", type=float,
        help="최근 이 일수 안에 성공했으면 건너뛴다 (D1 따라잡기, docs/infra.md 25.8)",
    )  # fmt: skip
    args = parser.parse_args()

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    if args.skip_days is not None:
        with TursoClient() as client:
            if should_skip(client, args.market, args.skip_days):
                print(f"최근 {args.skip_days:g}일 안에 돌았고 편입 종목이 있습니다. 건너뜁니다 (쓰기 예산을 아낀다)")
                return 0
    started = datetime.now(UTC)
    code = run(args.market, dry_run=args.dry_run)
    print(f"소요 {(datetime.now(UTC) - started).total_seconds():.1f}초")
    return code


if __name__ == "__main__":
    sys.exit(guard(main))
