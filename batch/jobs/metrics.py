"""성과 지표 계산과 적재.

계산식은 docs/metrics.md, 구현은 batch/services/metrics.py 에 있다.
이 파일은 데이터를 읽어 넣고 결과를 저장하는 일만 한다.

**표본이 모자라면 값을 채우지 않는다.** 100일치로 계산한 값을
"1년 변동성" 이라고 부르면 안 된다. 개수만 남겨 왜 비었는지 알 수 있게 한다.

실행
  python -m batch.jobs.metrics
  python -m batch.jobs.metrics --window 1Y
  python -m batch.jobs.metrics --ticker 005930
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core import settings_range as sr
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import event_history, history, patterns
from batch.services import metrics as calc

log = logging.getLogger("metrics")

JOB_NAME = "metrics"

# 기간별로 거슬러 올라갈 달력 일수. 거래일이 아니라 달력 기준이다.
WINDOW_DAYS = {"1Y": 366, "3Y": 1096, "5Y": 1827}

# 나라별 벤치마크. 한국 종목의 베타를 S&P 500 으로 재면 뜻이 달라진다 (docs/metrics.md 6절).
#
# **값은 `index_prices.index_code` 그대로여야 한다.** 2026-09-21 까지 여기 "S&P500" 이라고
# 적혀 있었는데 실제로 저장되는 코드는 `SP500` 이다(`trend.INDEX_SYMBOLS`). 아무도 몰랐던 것은
# **이 값으로 지수를 읽은 적이 한 번도 없었기 때문**이다 — 베타를 계산하지 않고 있었다(25.65).
# 추세 필터와 같은 표를 보므로 그쪽 상수에서 가져온다. 둘이 갈라지면 또 조용히 틀린다.
BENCHMARK = {"KR": "KOSPI", "US": "SP500"}

#: 지수가 이만큼도 안 쌓였으면 베타를 내지 않는다. 몇 점으로 낸 베타는 베타가 아니다
MIN_BENCHMARK_POINTS = 60


def load_prices(
    client: TursoClient, stock_id: int, since: str, 구멍: list[int] | None = None
) -> list[calc.PricePoint]:
    """한 종목의 가격 계열. 수정주가가 있으면 그것을 쓴다.

    **수정주가 계열 사이에 낀 빈 행** (docs/infra.md 25.701·25.709). 종가 정정 재적재가 `adj_close` 를 비우던 때
    (25.705 전), 과거 빈 날을 새 행으로 넣을 때(국내 행은 수정주가가 늘 NULL) 1:10 분할 전 구간 한가운데
    원 종가 한 행이 끼어 MDD −90%·변동성 738% 가 났다. 25.701 은 그런 행을 **뺐는데**, 국내
    `adjust_kr` 는 누적 계수가 1 인 날(분할·병합이 이어져 되돌아온 구간)을 **정상적으로** NULL 로 둬 그것까지 뺐다.
    그래서 가른다: 원 종가와 직전 수정 계수로 옮긴 값 가운데 **직전 값에 더 가까운 쪽**을 쓴다(옮긴 값이면 `구멍` 에
    더한다, 25.715). 앞뒤 끝의 빈 행은 늘 원 종가.
    """
    rs = client.execute(
        "SELECT date, adj_close, close FROM prices WHERE stock_id = ? AND date >= ? ORDER BY date",
        [stock_id, since],
    )
    rows = rs.dicts()
    수정 = [i for i, r in enumerate(rows) if r["adj_close"] is not None]
    처음, 끝 = (수정[0], 수정[-1]) if 수정 else (len(rows), -1)
    out: list[calc.PricePoint] = []
    계수: float | None = None  # 직전 수정 행의 (수정 ÷ 원)
    # 각 행 뒤의 첫 수정 행 계수 (25.719). 앞뒤 계수가 같으면 그 사이 빈 행은 **반드시 구멍**이다 — `adjust_kr` 는
    # 누적 계수가
    # 정확히 1 인 날만 NULL 로 두므로, 계수가 같은(≠1) 두 수정 행 사이의 NULL 은 정정·삽입으로 비운 자리다
    다음계수: list[float | None] = [None] * len(rows)
    뒤: float | None = None
    for j in range(len(rows) - 1, -1, -1):
        다음계수[j] = 뒤
        r = rows[j]
        if r["adj_close"] is not None and r["close"] and float(r["close"]) > 0:
            뒤 = float(r["adj_close"]) / float(r["close"])
    for i, row in enumerate(rows):
        원 = None if row["close"] is None else float(row["close"])
        if row["adj_close"] is not None:
            px: float | None = float(row["adj_close"])
            if 원 and 원 > 0:
                계수 = px / 원
        elif 처음 < i < 끝 and 원 and out and 계수 and out[-1].close > 0:
            직전 = out[-1].close
            옮김 = 원 * 계수
            # **직전 값에 더 가까운 쪽** (25.715, 교차검증). 25.709 의 ±30% 문턱은 계수가 0.77 보다 큰
            # 구간(무상증자·배당 조정)의
            # 구멍을 놓쳐 +26%·−19% 로 튀었고, 가격제한이 없는 날의 큰 움직임엔 분할 구멍도 놓쳤다. 계수 1 구간은 원
            # 종가가
            # 이어지고(옮긴 값이 튄다), 정정 구멍은 옮긴 값이 이어진다 — 문턱 없이 가른다
            같은_계수 = 다음계수[i] is not None and math.isclose(계수, 다음계수[i], rel_tol=1e-6)  # type: ignore[arg-type]
            # 앞뒤 계수가 같으면 규칙 없이 옮긴다 — 로그 거리 규칙은 계수와 반대로 크게 움직인 날(0.8 구간의 −15% 날)을
            # 원 종가로 이었다 (25.719, 교차검증). 앞뒤 계수가 다를 때(그 사이에 분할·병합)만 가까운 쪽으로 가른다
            if not math.isclose(계수, 1.0, rel_tol=1e-9) and (
                같은_계수 or abs(math.log(옮김 / 직전)) < abs(math.log(원 / 직전))
            ):
                px = 옮김
                if 구멍 is not None:
                    구멍.append(stock_id)
            else:
                px = 원
        else:
            px = 원
        if px is not None:
            out.append(calc.PricePoint(date=date.fromisoformat(str(row["date"])), close=px))
    return out


def target_stocks(
    client: TursoClient, ticker: str | None = None, countries: tuple[str, ...] = ("KR", "US")
) -> list[tuple[int, str, str]]:
    """대상 종목. (stock_id, ticker, country).

    countries 로 한 나라만 고를 수 있다. 미국 백필 뒤 국내를 다시 계산하느라 Actions 분을 쓰지 않으려고 둔다.
    """
    if ticker:
        rs = client.execute(
            "SELECT id, ticker, country FROM stocks WHERE ticker = ?", [ticker]
        )
        return [(int(r[0]), str(r[1]), str(r[2])) for r in rs.rows]

    # 나라마다 최신 스냅샷이 다르다(db.latest_snapshot_sql 주석). 나라별로 따로 읽는다.
    out: list[tuple[int, str, str]] = []
    for country in countries:
        rs = client.execute(
            "SELECT s.id, s.ticker, s.country FROM stocks s"
            " JOIN universe_members u ON u.stock_id = s.id"
            " WHERE u.included = 1 AND s.country = ?"
            f"   AND u.snapshot_date = {db.latest_snapshot_sql()}",
            [country, country],
        )
        out.extend((int(r[0]), str(r[1]), str(r[2])) for r in rs.rows)
    return out


def load_benchmark(client: TursoClient, country: str, since: str) -> list[calc.PricePoint]:
    """그 나라 벤치마크 지수의 가격 계열 (docs/metrics.md 6절).

    종목마다 읽지 않는다. (나라, 기간)마다 한 번 읽어 돌려 쓴다 — 종목 수만큼 왕복하면
    D1 에서는 그것만으로 수백 번이다.
    """
    code = BENCHMARK.get(country)
    if not code:
        return []
    rs = client.execute(
        "SELECT date, close FROM index_prices WHERE index_code = ? AND date >= ? ORDER BY date",
        [code, since],
    )
    return [
        calc.PricePoint(date=date.fromisoformat(str(r[0])), close=float(r[1]))
        for r in rs.rows
        if r[1] is not None
    ]


def risk_free_for(client: TursoClient, country: str) -> float | None:
    """설정에 넣어 둔 무위험수익률. 없으면 None 이다.

    0 으로 두지 않는다. 0 으로 두면 무위험수익률이 0인 세상의 값이 나오는데
    그게 맞는 값처럼 보인다.
    """
    key = "kr_pct" if country == "KR" else "us_pct"
    # **범위 밖이면 모른다로 둔다** (docs/infra.md 25.171). 0 으로 되돌리면
    # "무위험수익률이 0 인 세상" 의 샤프가 나오는데 그게 맞는 값처럼 보인다
    값들, 경고 = sr.잎마다(db.get_setting(client, "risk_free_manual", {}), (key,))
    for 줄 in 경고:
        log.warning("%s", 줄)
    value = 값들[key]
    if value is None:
        return None
    return float(value) / 100.0


def 시세_끝날(client: TursoClient, country: str, 기준: str) -> tuple[dict[str, date], dict[int, str]]:
    """({시장: 대부분의 종목이 들어온 마지막 날}, {종목: 시장}) (25.710·25.715).

    25.710 은 나라 전체 `MAX(date)` 라 늦게 들어온 한 행이 끝날을 끌어올렸고, 한 시장(KOSDAQ)만 멈춘 경우를 못 봤다 —
    25.702 가 성적표에서 이미 고친 문제다. 같은 함수(`signal_outcomes.market_last_dates`)를 쓴다.
    """
    from batch.jobs.signal_outcomes import market_last_dates

    끝날 = {m: date.fromisoformat(d) for m, d in market_last_dates(client, country, 기준).items()}
    # 최근 40일에 행이 하나도 없는 시장은 거기 빠진다 — 그러면 창 끝이 기준일로 돌아가 **모든 값이 None** 이고 멈춤
    # 경고도 없었다
    # (25.719, 교차검증 — 25.710 을 되돌린 회귀). 그 시장은 마지막 시세 날로 물러난다
    # **필요할 때만, 종목별 인덱스로** (25.728, 교차검증). 25.719 는 늘 나라 가격 행 전부를 훑었고(VM 480만 단계 — D1
    # 하루 읽기
    # 한도 500만), 미국 작은 시장(IEX 등)을 제 시장의 MAX 로 물려 그 시장의 유일한 종목이 멈추면 끝 검사가 꺼졌다.
    # 미국은 나라
    # 하나로만 물러난다(부르는 쪽이 `끝날.get(country)`) — 나라 값이 통째로 없을 때만 찾는다. 국내는 빠진 시장만 찾는다
    시장들 = [str(r[0]) for r in client.execute(
        "SELECT DISTINCT market FROM stocks WHERE country = ?", [country]
    ).rows]  # fmt: skip
    찾을 = [m for m in 시장들 if m not in 끝날] if country == "KR" else ([] if country in 끝날 else [None])
    for 시장 in 찾을:
        rs = client.execute(
            "SELECT MAX(m) FROM (SELECT (SELECT MAX(p.date) FROM prices p WHERE p.stock_id = s.id AND p.date <= ?) AS m"
            " FROM stocks s WHERE s.country = ? AND (? IS NULL OR s.market = ?))",
            [기준, country, 시장, 시장],
        )
        마지막 = rs.rows[0][0] if rs.rows else None
        if 마지막:
            끝날[시장 or country] = date.fromisoformat(str(마지막))
    rs = client.execute("SELECT id, market FROM stocks WHERE country = ?", [country])
    return 끝날, {int(r[0]): str(r[1]) for r in rs.rows}


# 재무·수급 사건 이력 (docs/analysis.md 40~42장, 25.1063) — 나라마다 한 번
EVENT_FIN_SQL = (
    "SELECT f.stock_id, f.fiscal_year, f.report_code, f.consolidated, f.report_date, f.operating_income, f.net_income"
    " FROM financials f JOIN stocks s ON s.id = f.stock_id WHERE s.country = ? AND f.fiscal_year >= ?"
)
EVENT_DIV_SQL = (
    "SELECT d.stock_id, d.fiscal_year, d.cash_dividend_total FROM stock_dividends d JOIN stocks s ON s.id = d.stock_id"
    " WHERE s.country = ? AND d.fiscal_year >= ?"
)
EVENT_FLOWS_SQL = "SELECT stock_id, date, short_vol_pct FROM kr_flows WHERE date >= ? AND date <= ?"
EVENT_SHARES_SQL = "SELECT id, listed_shares FROM stocks WHERE country = ?"
#: 사건 이력을 읽는 햇수 — 5년 시세 창과 같고, 전년 대비를 내려고 한 해 더
EVENT_YEARS = 6


def load_event_inputs(client: TursoClient, country: str, as_of: str, warnings: list[str]) -> dict[str, dict]:
    """재무·배당·수급·상장주식수 이력을 종목별로. 못 읽은 것은 비우고 경고 — 그 칸만 빠진다."""
    from collections import defaultdict

    해 = date.fromisoformat(as_of).year - EVENT_YEARS
    out: dict[str, dict] = {"fin": defaultdict(list), "div": defaultdict(list), "flows": defaultdict(list),
                            "shares": {}}  # fmt: skip
    읽기 = (("fin", EVENT_FIN_SQL, [country, 해]), ("div", EVENT_DIV_SQL, [country, 해]),
            ("flows", EVENT_FLOWS_SQL, [f"{해 + EVENT_YEARS - 1}{as_of[4:]}", as_of]))  # fmt: skip
    for 키, sql, args in 읽기:
        try:
            for r in client.execute(sql, args).dicts():
                out[키][int(r["stock_id"])].append(r)
        except Exception as exc:  # noqa: BLE001
            if not db.표가_없나(exc):
                warnings.append(f"사건 이력({키})을 읽지 못했습니다: {exc}")
    try:
        out["shares"] = {int(r[0]): float(r[1]) for r in client.execute(EVENT_SHARES_SQL, [country]).rows if r[1]}
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"상장주식수를 읽지 못했습니다: {exc}")
    return out


def compute_all(
    client: TursoClient,
    stocks: list[tuple[int, str, str]],
    windows: list[str],
    as_of: str,
    as_of_by_country: dict[str, str] | None = None,
) -> tuple[int, dict[str, int], list[str]]:
    """전 종목의 지표를 계산해 저장한다. 나라마다 기준일이 다르면 `as_of_by_country` 가 이긴다 (25.558)."""
    warnings: list[str] = []
    counts: dict[str, int] = {w: 0 for w in windows}
    rows_data: list[tuple] = []
    now = db.now_iso()
    as_of_date = date.fromisoformat(as_of)

    rf_cache: dict[str, float | None] = {}
    skipped_for_samples = 0
    pattern_rows: list[tuple] = []

    # 종목마다 기간마다 읽으면 880종목 × 3기간 = 2,640 번 왕복한다. D1 은 왕복마다 HTTP 다.
    # **가장 긴 창을 한 번 읽어 나눠 쓴다** — 짧은 창은 긴 창의 부분집합이다 (2026-09-21, 25.65)
    longest = max(WINDOW_DAYS[w] for w in windows)
    # 나라마다 기준일이 다르면 **가장 이른 기준일**에서 잰다 — 늦은 쪽(max)으로 재면 이른 나라의 창 앞이 잘렸다 (25.561)
    가장_이른 = min([date.fromisoformat(d) for d in (as_of_by_country or {}).values()] + [as_of_date])
    since_longest = (가장_이른 - timedelta(days=longest)).isoformat()

    # 벤치마크도 (나라, 기간) 마다 한 번만 읽는다
    bench_cache: dict[str, list[calc.PricePoint]] = {}
    bench_missing: set[str] = set()
    시장국면: dict[str, dict[str, str]] = {}
    신고가_모음: dict[str, list[dict[str, list[float]]]] = {}
    사건이력: dict[str, dict] | None = None

    나라_끝: dict[str, tuple[dict[str, date], dict[int, str]]] = {}
    멈춤_경고: set[str] = set()
    수정_구멍: list[int] = []
    for stock_id, _ticker, country in stocks:
        if country not in rf_cache:
            rf_cache[country] = risk_free_for(client, country)
        risk_free = rf_cache[country]
        if country not in bench_cache:
            bench_cache[country] = load_benchmark(client, country, since_longest)
            if len(bench_cache[country]) < MIN_BENCHMARK_POINTS:
                bench_missing.add(country)

        all_points = load_prices(client, stock_id, since_longest, 수정_구멍)
        기준 = (as_of_by_country or {}).get(country, as_of)
        기준일 = date.fromisoformat(기준)
        # 비슷한 국면 (docs/analysis.md 13장, 25.1039) — 5년 시세를 읽은 김에. 기준일 뒤 시세는 넣지 않는다
        국면_점 = [q for q in all_points if q.date <= 기준일]
        # 시장 국면으로 한 번 더 나눈다 (29장, 25.1054) — 그날 국면은 그날까지의 지수만 보므로 나라마다 한 번 낸다
        if country not in 시장국면:
            시장국면[country] = patterns.index_regimes({q.date.isoformat(): q.close for q in bench_cache[country]})
        국면표 = patterns.table([q.date.isoformat() for q in 국면_점], [q.close for q in 국면_점],
                             시장국면[country] or None)  # fmt: skip
        # 하락장 성적 (28장, 25.1053) — 베타와 같은 기준 지수로
        지수 = {q.date.isoformat(): q.close for q in bench_cache[country] if q.date <= 기준일}
        하락장 = (patterns.stress([q.date.isoformat() for q in 국면_점], [q.close for q in 국면_점], 지수)
                if 지수 else None)  # fmt: skip
        # 최근 변동성 (10.5, 25.1055) — 예상 주가의 단기 범위가 지금 흔들림을 따르게
        변동 = patterns.ewma_vol([q.date.isoformat() for q in 국면_점[-patterns.EWMA_DAYS :]],
                               [q.close for q in 국면_점[-patterns.EWMA_DAYS :]])  # fmt: skip
        # 자기 시세 이력의 사실들 (31~34장, 25.1057~25.1060) — 같은 계열로, 추가 읽기 없이
        날짜들, 종가들 = [q.date.isoformat() for q in 국면_점], [q.close for q in 국면_점]
        신고가, 신고가_뒤 = history.breakout(날짜들, 종가들)
        신고가_모음.setdefault(country, []).append(신고가_뒤)
        이력 = {"drawdown": history.drawdowns(날짜들, 종가들), "tail": history.tail(날짜들, 종가들),
                "season": history.season(날짜들, 종가들), "breakout": 신고가}  # fmt: skip
        # 재무·수급 사건과 그 뒤 (40~42장, 25.1063) — 국내만. 나라마다 한 번 몰아 읽은 이력에서
        if country == "KR":
            if 사건이력 is None:
                사건이력 = load_event_inputs(client, country, 기준, warnings)
            재무, 배당, 수급, 주식수 = (사건이력[k] for k in ("fin", "div", "flows", "shares"))
            이력["earnings"] = event_history.earnings_reactions(재무.get(stock_id, []), 날짜들, 종가들, 지수)
            이력["sources"] = event_history.return_sources(재무.get(stock_id, []), 배당.get(stock_id, []), 날짜들,
                                                           종가들, 주식수.get(stock_id))  # fmt: skip
            이력["short"] = event_history.short_surges(수급.get(stock_id, []), 날짜들, 종가들)
        if 국면표 or 하락장 or 변동 or any(이력.values()):
            국면표 = {**(국면표 or {}), "stress": 하락장, "vol": 변동, **이력}
            pattern_rows.append((stock_id, 기준, json.dumps(국면표, separators=(",", ":")), now))
        # 창 끝 검사의 "끝" 은 달력 기준일이 아니라 **그 나라 시세가 실제로 있는 마지막 날**이다 (25.710, 교차검증) —
        # 나라 수집이
        # 11일 넘게 멈추면 모든 종목의 모든 창이 None 이 되고, 1Y 가 가장 짧은 창이라 리스크 축이 통째로 비었다
        if country not in 나라_끝:
            나라_끝[country] = 시세_끝날(client, country, 기준)
        끝날들, 시장_of = 나라_끝[country]
        시장 = 시장_of.get(stock_id, "")
        시장_끝 = 끝날들.get(시장) or 끝날들.get(country)
        검사_끝 = min(기준일, 시장_끝 or 기준일)
        # 미국은 나라 하나로 세므로 경고도 나라 하나 (25.719 — 시장 이름마다 되풀이됐다)
        경고_키 = 시장 if country == "KR" else country
        if 시장_끝 and (기준일 - 시장_끝).days > calc.MAX_SESSION_GAP_DAYS and 경고_키 not in 멈춤_경고:
            # 시장 수집이 멈췄다 — 값은 그 시장 마지막 날 기준으로 내지만, "기준일 현재" 로 읽히지 않게 말한다 (25.715)
            멈춤_경고.add(경고_키)
            warnings.append(
                f"{경고_키} 시세가 {시장_끝} 뒤로 없습니다({(기준일 - 시장_끝).days}일). 성과 지표는 그날 기준입니다"
            )

        for window in windows:
            cutoff = 기준일 - timedelta(days=WINDOW_DAYS[window])
            # **기준일 뒤 시세는 넣지 않는다** (25.561, 교차검증) — 저장 날짜가 직전 거래일이 되면서(25.558)
            # 그 뒤에 적재된 행이 과거 날짜 지표에 섞일 수 있었다(미국 장 마감 뒤 수동 실행).
            # `load_prices` 에는 위 한계가 없다
            points = [p for p in all_points if cutoff <= p.date <= 기준일]
            market = [p for p in bench_cache[country] if cutoff <= p.date <= 기준일]

            result = calc.compute(
                window,
                points,
                market=market if len(market) >= MIN_BENCHMARK_POINTS else None,
                benchmark=BENCHMARK.get(country),
                risk_free_annual=risk_free,
                span=(cutoff, 검사_끝),
            )

            if result.cagr is None and result.mdd is None:
                skipped_for_samples += 1
            else:
                counts[window] += 1

            rows_data.append(
                (
                    stock_id, 기준, window,
                    result.cagr, result.mdd,
                    result.mdd_peak_date.isoformat() if result.mdd_peak_date else None,
                    result.mdd_trough_date.isoformat() if result.mdd_trough_date else None,
                    result.mdd_recovery_days, result.volatility_ann,
                    result.sharpe, result.sortino, result.beta,
                    result.benchmark, result.risk_free_rate_used,
                    result.data_points, result.calc_version, now,
                )
            )

    if any(rf is None for rf in rf_cache.values()):
        warnings.append(
            "무위험수익률이 설정되지 않아 샤프와 소르티노를 계산하지 않았습니다. "
            "설정 화면에서 입력하세요"
        )
    if 수정_구멍:
        # 수정주가 사이의 빈 행을 뺐다 (25.701) — 국내 `adjust_kr` 를 돌리면 채워진다
        warnings.append(
            f"수정주가 계열 사이의 빈 행 {len(수정_구멍)}개를 직전 조정 계수로 옮겨 계산했습니다"
            f"({len(set(수정_구멍))}종목). 국내 수정주가 조정을 다시 돌리면 채워집니다"
        )
    if skipped_for_samples:
        warnings.append(
            f"표본 부족으로 값을 채우지 않은 조합 {skipped_for_samples}건. "
            "백필을 더 돌리면 줄어듭니다"
        )
    for country in sorted(bench_missing):
        # **조용히 비우지 않는다.** 베타는 2026-09-21 까지 아무 말 없이 늘 NULL 이었다 (25.65)
        warnings.append(
            f"{country} 벤치마크 지수({BENCHMARK.get(country)})가 {MIN_BENCHMARK_POINTS}일치에 못 미쳐"
            " 베타를 계산하지 않았습니다 (python -m batch.jobs.index_prices --lookback 2000)"
        )

    _bulk_upsert(client, rows_data)
    store_patterns(client, pattern_rows, warnings)
    # 시장 전체 신고가 뒤 (34장) — 나라마다 설정 한 행
    for 나라, 모음 in 신고가_모음.items():
        시장분포 = history.market_breakout(모음)
        if 시장분포:
            try:
                db.set_setting(client, history.market_key(나라), {"as_of": as_of, "h": 시장분포})
            except Exception as exc:  # noqa: BLE001 — 그 줄만 빠진다
                warnings.append(f"시장 전체 신고가 뒤 분포를 저장하지 못했습니다: {exc}")
    return len(rows_data), counts, warnings


PATTERN_UPSERT = (
    "INSERT INTO price_patterns (stock_id, as_of_date, stats_json, computed_at) VALUES (?, ?, ?, ?)"
    " ON CONFLICT (stock_id) DO UPDATE SET as_of_date = excluded.as_of_date, stats_json = excluded.stats_json,"
    " computed_at = excluded.computed_at"
)


def store_patterns(client: TursoClient, rows: list[tuple], warnings: list[str]) -> None:
    """국면표를 종목마다 한 행으로 덮는다. 실패해도 성과 지표는 이미 저장됐다 — 경고만 남긴다."""
    try:
        for i in range(0, len(rows), 200):
            client.batch([(PATTERN_UPSERT, list(r)) for r in rows[i : i + 200]])
    except Exception as exc:  # noqa: BLE001 — 곁 결과다
        if db.quota_reason(exc):
            raise
        warnings.append(f"비슷한 국면 표를 저장하지 못했습니다: {exc}")


_COLUMNS = (
    "stock_id, as_of_date, window, cagr, mdd, mdd_peak_date, mdd_trough_date,"
    " mdd_recovery_days, volatility_ann, sharpe, sortino, beta, benchmark,"
    " risk_free_rate_used, data_points, calc_version, created_at"
)


def _bulk_upsert(client: TursoClient, rows: list[tuple]) -> None:
    """여러 행을 한 문장에 넣는다. 열 개수는 목록에서 직접 센다."""
    if not rows:
        return

    param_count = len([c for c in _COLUMNS.split(",") if c.strip()])
    for row in rows:
        if len(row) != param_count:
            raise ValueError(
                f"값 {len(row)}개인데 열은 {param_count}개입니다. "
                "열을 더하거나 뺄 때 값 묶음도 함께 고쳐야 합니다"
            )

    placeholder = "(" + ", ".join(["?"] * param_count) + ")"
    per_statement = max(1, 20_000 // param_count)

    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        sql = (
            f"INSERT INTO performance_metrics ({_COLUMNS}) VALUES "
            + ", ".join([placeholder] * len(chunk))
            + " ON CONFLICT (stock_id, as_of_date, window, calc_version) DO UPDATE SET"
            "   cagr = excluded.cagr, mdd = excluded.mdd,"
            "   mdd_peak_date = excluded.mdd_peak_date,"
            "   mdd_trough_date = excluded.mdd_trough_date,"
            "   mdd_recovery_days = excluded.mdd_recovery_days,"
            "   volatility_ann = excluded.volatility_ann,"
            "   sharpe = excluded.sharpe, sortino = excluded.sortino,"
            # 기준 지수도 함께 덮는다 (25.684) — 빠져 있어, 지수 없이 한 번 돈 날 다시 돌면 베타만 채워지고 지수는 NULL
            "   beta = excluded.beta, benchmark = excluded.benchmark, data_points = excluded.data_points,"
            "   risk_free_rate_used = excluded.risk_free_rate_used"
        )
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append((sql, args))
    client.batch(statements)


def run(windows: list[str], ticker: str | None = None, countries: tuple[str, ...] = ("KR", "US")) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        # **나라마다 직전 거래일**로 저장한다 (docs/infra.md 25.558, 감사 재현). UTC 날짜로 저장하면 00:05 UTC 화요일에
        # 돈 D1 따라잡기가 화요일 날짜로 쓰고, 바로 이어 도는 점수(기준 월요일, `as_of_date <= 월`)가 그것을 못 봐
        # 리스크 입력이 늘 한 거래일 늦었다. 점수·신호·밴드는 25.234 에서 이미 `default_as_of` 로 옮겼다
        as_of_by = {c: cal.default_as_of(c) for c in countries}
        as_of = max(as_of_by.values())
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market=None, trade_date=as_of
        )

        stocks = target_stocks(client, ticker, countries)
        if not stocks:
            db.finish_batch_run(
                client, run_id, status="failed", error_text="대상 종목이 없습니다"
            )
            print("대상 종목이 없습니다. 유니버스를 먼저 만드세요")
            return 1

        print(f"대상 {len(stocks)}종목, 기간 {windows}")
        total, counts, warnings = compute_all(client, stocks, windows, as_of, as_of_by)

        db.finish_batch_run(
            client, run_id,
            status="partial" if warnings else "success",
            step_log={"rows": total, "computed": counts, "warnings": warnings},
        )

        print(f"저장 {total}행")
        for window in windows:
            print(f"  {window}: 값이 채워진 종목 {counts[window]}")
        if warnings:
            print("경고")
            for warning in warnings:
                print(f"  - {warning}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="성과 지표 계산")
    parser.add_argument("--window", choices=list(WINDOW_DAYS), help="특정 기간만")
    parser.add_argument("--ticker", help="특정 종목만")
    parser.add_argument("--market", choices=["KR", "US"], help="한 나라만 (비우면 둘 다)")
    args = parser.parse_args()

    logging.basicConfig(
        level=config.SETTINGS.log_level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    windows = [args.window] if args.window else list(WINDOW_DAYS)
    countries = (args.market,) if args.market else ("KR", "US")
    return run(windows, args.ticker, countries)


if __name__ == "__main__":
    sys.exit(guard(main))
