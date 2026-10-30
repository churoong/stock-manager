"""핵심 H 다년 백테스트 실행 — 조각 1d (docs/backtest.md 10장, docs/infra.md 25.1534). 국내만.

운영 가상모의투자 H·G·B·R1·R2 의 매매 규칙(조각 1c `core_strategy`)을 수년 그 시점 자료로 **날마다** 돌려
`backtest_runs` 에 다섯 행(같은 `group_id`, 리밸런스 열 "daily")으로 싣는다.

- 자료는 월 백테스트(`jobs/backtest.run`)와 **같은 읽기·같은 워밍업**이다 — 유니버스·가격(시세 사본 길 포함)·
  재무 스냅샷·배당·지수, 시점 유니버스(`pit.members_at`), 거래비용(설정), 무위험수익률(설정)
- 하루 판단 재료(`CoreMarket.at`)는 **한 번만** 내고 변형 다섯이 나눠 읽는다(`SharedDays`) — 변형의 장부와 무관하다
- 팩터 점수의 리스크 지표는 판단기 안에서 주마다 잰다(`risk_every="week"` — 예전 주기와 같은 값, 메모리가
  종목 수로 묶인다). 시간 한도가 모자라면 `--risk-every month`(달마다 한 번, 편향은 10장 조각 1d)로 줄일 수 있다
- 결과 행: 변형마다 CAGR·MDD·샤프(엔진 `summarize`, 벤치마크는 월 백테스트와 같은 유니버스 동일가중 — 저장하지 않는다)와
  **반 나눈 기간**(앞·뒤 반의 CAGR·MDD, `metrics_json.split_half`). DSR·결정 단위 분위는 조각 1e 다

실행
  python -m batch.jobs.core_backtest --market KR
  python -m batch.jobs.core_backtest --market KR --years 3 --risk-every week
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs import backtest as jb
from batch.jobs import metrics as metrics_job
from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import core_evidence as ce
from batch.services import core_strategy as cs
from batch.services import exit_pair as xp
from batch.services import holding_eq as heq
from batch.services import loss_gate as lg
from batch.services import luck, report_picks, trend
from batch.services import metrics as m
from batch.services import pit_universe as pit
from batch.services import universe as uni

log = logging.getLogger("core_backtest")

#: `batch_runs.job_name` — 월 백테스트(`backtest`)와 가른다. 기법 발굴 루프의 판정은 `backtest` 의 기본 실행만 센다
#: (docs/factors.md 12장 머리) — 핵심 H 실행이 그 판정 칸을 밀어내지 않게
JOB_NAME = "backtest_core"
#: 전략 깔때기 0단계 탐색 실행(docs/funnel.md 4장 ①, 25.1906) — 핵심 H 실행(웹 `coreBacktest.ts` 가 `backtest_core` 만
#: 읽는다)·월 백테스트 판정과 가른다. 변형은 `core_strategy.EXPLORE_VARIANTS`(코호트 2 — 코드에 넣은 커밋이 등록)
EXPLORE_JOB_NAME = "backtest_explore"
#: 회차 지정가 체결 실행(`--limit-fills`) — 기본 실행(`JOB_NAME`)과 가른다(25.1918, 25.1914 교차검증 [권고]).
#: 같은 이름이면 웹 핵심 H 카드가 마지막 `backtest_core` 로 이 실행을 읽어 사전 등록 판정처럼 보였다. 결정 원장
#: (`DayLog.buys`)이 안 찬 회차까지 낸 비중이라 짝 판정도 내지 않는다
LIMIT_JOB_NAME = "backtest_limit"
#: 탐색 실행 결과에 붙는 경고 — 거르기만 하고 확정하지 않는다(funnel.md 1장, 생존편향)
WARN_EXPLORE = "탐색 실행(전략 깔때기 0단계) — 걸러내기용, 판정·확정 아님"
#: 회차 지정가 체결로 돈 실행에 붙는 경고 — 25.1914 (전략 깔때기 ④ 분할 지정가 체결). 기본(t 종가 전량) 실행과 체결
#: 잣대가 달라 섞어 견주지 않는다(`--limit-fills`, 기본 꺼짐)
WARN_LIMIT_FILLS = (
    "회차 지정가 체결(--limit-fills) — 회차 변형의 매수는 t 종가 ≤ 지정가 인 회차만 지정가에 찼다. t 종가 전량 실행과"
    " 섞어 견주지 않는다. 결정 단위 판정·청산 짝은 낸 매수(안 찬 회차 포함)를 센다(docs/backtest.md 10장)"
)


#: S·T 계좌형 리플레이(깔때기 ⑦-6, 25.2044) — 탐색 실행(`--explore --st`)에서만. 이름은 실행 행의 `strategy`
ST_VARIANTS: tuple[str, ...] = ("st_s", "st_t")
WARN_ST = ("S·T 계좌형 리플레이(st_s·st_t) — 운영 S·T 규칙을 주식 수·현금 장부로 과거에 돌린 값(docs/funnel.md 4장 ⑦, "
           "swing_day_replay 머리의 이름 붙은 차이 ①~⑥). 시가가 빈 날은 T 가 사지 않고(진입 막기) "
           "S 는 종가로 사되 넘음 판정에서 그날을 세지 않아 매도가 늦거나 없다 — "
           "실행 기록(DB) st.open_coverage 를 함께 본다")  # fmt: skip
#: 시가 덮개가 이보다 낮으면 S·T 줄에 따로 경고(25.2045) — 시가 빈 날이 많으면 T 는 거의 거래하지 않고 S 는 갇힌다
ST_MIN_OPEN_COVERAGE = 0.9


def explore_trial_floor(st: bool = False) -> int:
    """탐색 실행의 DSR 시도 수 하한 — 등록부 누적(funnel.md 2.1): 코호트 1(`REGISTERED_TRIALS`) + 탐색 변형 수."""
    return jb.REGISTERED_TRIALS + len(cs.EXPLORE_VARIANTS) + (len(ST_VARIANTS) if st else 0)
#: `backtest_runs.rebalance` — 장부형 전략은 날마다 판단한다(월 백테스트는 "monthly")
REBALANCE = "daily"
#: 기본 연수 — 월 백테스트와 같다(자료 약 5년, docs/backtest.md 10장 자료 깊이)
DEFAULT_YEARS = jb.DEFAULT_YEARS
#: 팩터 점수 리스크 지표 주기. "week" 는 예전 주기(주간 점수)와 같은 값이다(시험이 대조). 실측(25.1534, 합성 880종목):
#: 하루 재료 week 1.89초/일 · month 1.35초/일 — 5년 약 40분 대 28분, 엔진·전략 다섯 약 19분을 더해도 week 가
#: 120분 안이라 편향 없는 쪽을 기본으로 둔다. month 는 실행 기록 `timing` 이 한도에 다가가면 쓰는 손잡이다
DEFAULT_RISK_EVERY = "week"
#: 결과 행마다 붙는 경고 — 운영 H 와 다른 판이라는 것과 어디를 읽으면 되는지
WARN_CORE = (
    "핵심 H — 운영 가상모의투자 H 와 다른 판이다(재현 못 하는 부서 비움·t 종가 체결·팩터 점수 주 1회 등)."
    " 어긋남은 docs/backtest.md 10장 — CAGR 절대값을 믿지 말고 짝(H−G·G−B)과 반 나눈 기간을 본다"
)
#: 리스크 지표를 달마다 잰 실행에만 붙는 경고(`--risk-every month`)
WARN_RISK_MONTHLY = (
    "리스크 팩터 지표를 달마다 한 번 쟀다 — 최대 약 4주 묵는다(docs/backtest.md 10장 조각 1d)."
    " 주마다 잰 실행과 섞어 견주지 않는다"
)
#: 벤치마크를 저장하지 않는다는 표시(초과 CAGR·이긴 달의 기준) — `metrics_json.benchmark`
BENCHMARK_NOTE = "유니버스 동일가중 월 리밸런스(월 백테스트 benchmark 와 같은 판단 함수, 저장 안 함)"


def split_half(curve: list[tuple[str, float]]) -> dict[str, Any]:
    """곡선을 날 수로 반 나눠 앞·뒤 반의 CAGR·MDD (docs/backtest.md 10장 "기간을 반으로 나눈 결과를 함께 싣는다").

    가운데 날은 두 반에 모두 든다(앞 반의 끝 = 뒤 반의 시작 — 뒤 반 수익이 그날 자본에서 시작한다).
    식은 docs/metrics.md 그대로(`metrics.cagr`·`max_drawdown`). 곡선이 셋보다 짧으면 비운다"""
    if len(curve) < 3:
        return {}
    가운데 = len(curve) // 2
    out: dict[str, Any] = {}
    for 이름, 조각 in (("first", curve[: 가운데 + 1]), ("second", curve[가운데:])):
        점 = [m.PricePoint(date=date.fromisoformat(d), close=v) for d, v in 조각]
        out[이름] = {"start": 조각[0][0], "end": 조각[-1][0], "days": len(조각),
                    "cagr": m.cagr(점), "mdd": m.max_drawdown(점).mdd}  # fmt: skip
    return out


def pit_eligible(
    universe: list[dict[str, Any]], prices: dict[int, dict[str, float]], turnover: dict[int, dict[str, float]],
    market_of: dict[int, str],
) -> tuple[jb.EligibleAt, dict[str, set[int]], dict[str, int]]:
    """국내 시점 유니버스 판정 — 월 백테스트 `run` 과 같은 하한·같은 20일 창·같은 이력 가장자리 (docs/backtest.md 1.3).

    (판정 함수, cutoff → 종목 캐시, 제외 사유 수). `run` 은 바꾸지 않으려고 같은 조립을 여기 둔다"""
    base = uni.UniverseFilters.korea()
    stocks = jb.pit_stocks(universe, observed=False)  # 국내 listed_date 는 거래소가 준 실제 상장일
    series = jb.pit_series(prices, turnover)  # 국내 수정주가는 분할만 조정 — 가격 수준과 같다(25.275)
    filters = pit.Filters(
        min_market_cap=base.min_market_cap, min_avg_turnover_20d=base.min_avg_turnover_20d,
        min_listed_days=base.min_listed_days, history_starts=jb.history_starts(stocks, series),
    )  # fmt: skip
    시장일 = jb.pit_market_dates(series, market_of)
    cache: dict[str, set[int]] = {}
    reasons: dict[str, int] = {}

    def eligible_at(cutoff: str) -> set[int]:
        hit = cache.get(cutoff)
        if hit is None:
            hit, why = pit.members_at(stocks, series, cutoff, filters, 시장일)
            cache[cutoff] = hit
            for k, n in why.items():
                reasons[k] = reasons.get(k, 0) + n
        return hit

    return eligible_at, cache, reasons


def run_core(
    market: str = "KR",
    years: int = DEFAULT_YEARS,
    *,
    risk_every: str = DEFAULT_RISK_EVERY,
    force: bool = False,
    today: date | None = None,
    variants: tuple[str, ...] | None = None,
    explore: bool = False,
    limit_fills: bool = False,
    st: bool = False,
) -> int:
    """핵심 H 변형 다섯을 날마다 돌려 저장한다. 0 = 성공. `today` 는 시험이 날짜를 고정할 때만 준다.

    `limit_fills` — 25.1914 (전략 깔때기 ④ 분할 지정가 체결): 회차 변형의 매수를 운영처럼 회차 지정가로(기본 꺼짐 — 끄면
    예전과 같다). 켜면 경고(`WARN_LIMIT_FILLS`)를 붙이고 실행 기록 params 에 적는다

    `explore` 면 탐색 변형(`EXPLORE_VARIANTS`)을 `EXPLORE_JOB_NAME` 으로 돌린다 — 두 목록을 섞지 않는다(시도 수와
    판정이 갈린다). 탐색 실행은 결정 단위 짝 판정(H−G)·H 진단을 내지 않는다"""
    market = market.upper()
    목록 = cs.EXPLORE_VARIANTS if explore else cs.VARIANTS
    variants = tuple(목록) if variants is None else variants
    if not variants or any(v not in 목록 for v in variants):
        raise ValueError(f"{'탐색' if explore else '핵심'} 실행 변형은 {sorted(목록)} 가운데서 — {variants}")
    if st and not explore:
        # S·T 리플레이는 거르기용 탐색 코호트에만 — 핵심 판정(코호트 1)의 시도 수를 바꾸지 않는다(funnel.md 2.1).
        # 또 엔진은 마지막 날을 평가만 해 핵심 변형이 cutoff dates[-2] 를 묻지 않는데, S·T 는 그날을 처음 불러
        # H 메모리 원장에 하루를 더 쌓는다 — 탐색 실행은 그 원장을 읽는 H 진단을 내지 않아 닿지 않는다(25.2045)
        raise ValueError("S·T 리플레이(st)는 탐색 실행(explore)에서만")
    job_name = EXPLORE_JOB_NAME if explore else LIMIT_JOB_NAME if limit_fills else JOB_NAME
    if market != "KR":
        # 미국은 지수·통화·분할 대기 규칙을 아직 안 맞췄다(`CoreMarket` 이 막는다) — 시작 전에 크게 멈춘다.
        # 명령줄은 KR 만 받고 워크플로도 KR 만 넘기므로 예약 실행이 여기서 조용히 끝날 일은 없다
        raise NotImplementedError("핵심 H 백테스트는 아직 국내(KR)만 돈다 — docs/backtest.md 10장")
    if risk_every not in cb.RISK_EVERY:
        raise ValueError(f"risk_every 는 {cb.RISK_EVERY} 가운데 하나 — {risk_every}")
    country = "KR"
    client = TursoClient()
    try:
        db.apply_migrations(client)
        now = db.now_iso()
        today = today or datetime.now(UTC).date()
        since = jb.start_of_backtest(today, years)
        batch_id = db.start_batch_run(client, job_name=job_name, market=market, trade_date=today.isoformat())

        universe, warnings = jb.load_universe(client, country)
        if not universe:
            db.finish_batch_run(client, batch_id, status="failed", error_text="유니버스가 비어 있습니다")
            print("유니버스가 비어 있습니다")
            return 1

        # 읽기 예산 — 월 백테스트와 같은 어림(종목 × (기간 + 있는 워밍업))
        가장_이른 = client.execute(
            "SELECT MIN(d) FROM (SELECT (SELECT MIN(p.date) FROM prices p WHERE p.stock_id = s.id) AS d"
            " FROM stocks s WHERE s.country = ?)",
            [country],
        ).scalar()
        워밍업_일 = 0
        if 가장_이른:
            앞선_일 = (date.fromisoformat(since) - date.fromisoformat(str(가장_이른)[:10])).days
            워밍업_일 = max(0, min(jb.WARMUP_CALENDAR_DAYS, 앞선_일))
        ok, text = db.check_read_budget(
            client, len(universe) * (years * jb.TRADING_DAYS_PER_YEAR + 워밍업_일 * 252 // 365), "핵심 H 백테스트"
        )
        print(text)
        if not ok and not force:
            db.finish_batch_run(client, batch_id, status="skipped", error_text=text)
            return 1

        # 월 백테스트와 같은 워밍업(3년 창 + 30일) — 12-1 모멘텀·리스크·밴드(750행)가 첫날부터 찬다(25.532)
        워밍업 = (date.fromisoformat(since) - timedelta(days=jb.WARMUP_CALENDAR_DAYS)).isoformat()
        ids = [int(r["stock_id"]) for r in universe]
        t0 = time.perf_counter()
        # S·T 리플레이는 시가를 종가와 같은 수정 계열로 함께 읽는다(25.2029) — 안 하면 질의에 `open` 이 없다
        opens: dict[int, dict[str, float]] | None = {} if st else None
        prices, turnover = jb.load_prices_and_turnover(client, ids, 워밍업, opens=opens)
        snapshots = jb.load_snapshots(client, country)
        pit_divs = jb.load_pit_dividends(client, country)
        # 국면(200일선)이 워밍업 첫날부터 서게 지수는 더 앞(400일)부터
        지수 = jb.load_index_series(client, country, (date.fromisoformat(워밍업) - timedelta(days=400)).isoformat())
        indexes = {code: dict(rows) for code, rows in 지수.items()}
        delisted = jb.has_delisted(client, country, ids)
        jb._RISK_FREE = metrics_job.risk_free_for(client, country)
        jb._위험_캐시.clear()  # 핵심 H 는 쓰지 않지만 앞 실행이 남긴 것을 들고 가지 않는다
        읽기_초 = time.perf_counter() - t0

        시장_맵 = {int(r["stock_id"]): str(r["market"]) for r in universe if r.get("market")}
        dates = bt.market_dates(prices, since, 시장_맵)
        if len(dates) < 40:
            db.finish_batch_run(client, batch_id, status="failed", error_text="거래일이 40일 미만입니다")
            print("가격이 너무 적어 백테스트를 돌릴 수 없습니다. 백필을 먼저 돌리세요")
            return 1
        # 판 뒤 쉼을 세는 거래일 — 워밍업 구간의 시장일 + 엔진 날짜 축
        sessions = [d for d in bt.market_dates(prices, 워밍업, 시장_맵) if d < dates[0]] + dates
        수수료설정 = db.get_setting(client, "fees", {})
        costs = bt.Costs.for_country(country, 수수료설정, db.get_setting(client, "taxes", {}))
        # 로트 원가의 매수 수수료는 운영 가상모의투자와 같은 쪽 값(25.1625) — 엔진의 회전 비용은 편도 평균 그대로
        from batch.jobs import paper as jp

        매수수수료 = jp._수수료(country, 수수료설정)[0]
        eligible_at, pit_cache, pit_reasons = pit_eligible(universe, prices, turnover, 시장_맵)
        warnings.append(jb.WARN_PIT_UNIVERSE)
        warnings.append(WARN_CORE)
        if explore:
            warnings.append(WARN_EXPLORE)
        if limit_fills:
            warnings.append(WARN_LIMIT_FILLS)
        if st:
            warnings.append(WARN_ST)
        if risk_every == "month":
            warnings.append(WARN_RISK_MONTHLY)
        if years != DEFAULT_YEARS:
            warnings.append(f"{jb.WARN_NOT_DEFAULT_PARAMS}: 기간 {years}년")
        trend_settings = trend.load_settings(client)
        group_id = uuid.uuid4().hex[:12]

        market_ = cb.CoreMarket(
            universe, snapshots, indexes, country=country, eligible_at=eligible_at, dividends_by_stock=pit_divs,
            trend_settings=trend_settings, risk_every=risk_every,
        )  # fmt: skip
        shared = cb.SharedDays(market_)
        results: dict[str, bt.BacktestResult] = {}
        기록: dict[str, list[cs.DayLog]] = {}  # 변형 → 하루 기록(결정 단위 판정, 조각 1e)
        시간: dict[str, float] = {}
        종목수: dict[str, int] = {}
        for i, variant in enumerate(variants):
            _진행(client, batch_id, i, len(variants) + 1 + (len(ST_VARIANTS) if st else 0), variant)
            t1 = time.perf_counter()
            strategy = cs.make_core_strategy(variant, shared.reader(), sessions=sessions, costs=costs,
                                             buy_fee_pct=매수수수료, limit_fills=limit_fills)  # fmt: skip
            results[variant] = bt.simulate(prices, dates, dates, strategy, costs, has_delisted=delisted,
                                           turnover=turnover, dividends_included=False)  # fmt: skip
            기록[variant] = strategy.log
            종목수[variant] = strategy.v.top_n  # 위원회 변형은 헌장 top_n(전략이 덮은 값)
            시간[variant] = round(time.perf_counter() - t1, 1)
            print(f"  {variant:8s} 최종 {results[variant].curve[-1][1]:.3f}  ({시간[variant]:.0f}초)", flush=True)

        st_log: dict[str, Any] = {}
        if st:
            # 깨져도 이미 낸 탐색 변형은 싣는다(25.2045 — H 진단과 같은 "곁다리가 실행을 죽이지 않는다"). 기록은 인자로
            # 넘겨 채우므로 S 뒤 T 에서 깨져도 덮개·S 합이 남는다(25.2046)
            try:
                _run_st(shared, prices, opens or {}, dates, turnover, country, 수수료설정,
                        db.get_setting(client, "taxes", {}), costs, results, 종목수, 시간,
                        progress=lambda 이름, k: _진행(client, batch_id, len(variants) + k, len(variants) + 3, 이름),
                        log=st_log)  # fmt: skip
            except Exception as e:  # noqa: BLE001
                log.exception("S·T 리플레이 실패")
                st_log["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                warnings.append(f"S·T 리플레이 실패: {type(e).__name__}: {str(e)[:120]}")
            cov = st_log.get("open_coverage")
            if cov is not None and cov < ST_MIN_OPEN_COVERAGE:
                warnings.append(f"S·T 시가 덮개 {cov * 100:.1f}% < {ST_MIN_OPEN_COVERAGE * 100:g}% — "
                                "st_t 는 시가 빈 날 거래하지 않고 st_s 는 매도가 늦거나 없다. "
                                "이 두 줄을 성적으로 읽지 않는다")  # fmt: skip

        # 초과 CAGR·이긴 달의 기준 — 월 백테스트와 같은 유니버스 동일가중(월 리밸런스). 다섯 행만 싣는다
        벤치 = bt.simulate(
            prices, dates, bt.month_starts(dates),
            jb.make_strategy("benchmark", universe, snapshots, cs.variant_spec(variants[0]).top_n, {}, [], eligible_at),
            costs, has_delisted=delisted, turnover=turnover,
        )  # fmt: skip
        무위험 = jb.risk_free_with_warning(client, country, warnings)
        # 조각 1e (25.1542·25.1544) — 운 판정(DSR): 월 백테스트와 같은 `luck.judge`, 시도 수 하한은 핵심 계열을
        # 더한 등록 수.
        # 무작위 대조(R1·R2)도 같은 실행의 곡선이라 V 와 "이 실행" 수에 들어간다(대개 V 를 키워 보수적) — 등록 시도 수
        # 하한에는 세지 않는다. 결정 단위 판정은 실행 기록 진단이라 깨져도 다섯 행 저장을 막지 않는다(25.1544 [권고] 7)
        t2 = time.perf_counter()
        운: dict[str, dict] = {}
        증거: dict[str, dict[str, Any]] = {}
        짝: dict[str, dict[str, Any]] = {}
        판정 = "판정 불가(계산 실패)"
        벤치월: dict[str, float] | None = None  # 월 초과 계열(25.1947)의 기준도 이것 — 깨지면 None(25.1956)
        try:
            벤치월 = luck.monthly_returns(벤치.curve)
            운 = luck.judge({v: luck.excess_series(luck.monthly_returns(r.curve), 벤치월) for v, r in results.items()},
                           n_floor=explore_trial_floor(st) if explore else jb.REGISTERED_TRIALS)  # fmt: skip
            # 결정 단위 — 무작위 대조 분위(가상모의투자 가상 대조와 같은 함수)·관문과 같은 잣대·판 뒤(읽기 0)
            종가 = ce.Closes(prices)
            코스피 = indexes.get(cb.BENCH_INDEX) or {}
            for v in 기록:  # S·T 리플레이(25.2044)는 결정 원장 꼴(DayLog)이 아니라 결정 단위 증거를 내지 않는다
                증거[v] = ce.evidence(기록[v], 종가, eligible_at, 코스피) | {
                    "gate": ce.gate_evidence(기록[v], 종가), "after_sell": ce.after_sell(기록[v], 종가, 코스피)}
            if explore:
                판정 = "탐색 실행 — 짝 판정 없음(funnel.md 1장 0단계는 거르기만)"
            elif limit_fills:
                # 결정 단위 증거는 낸 매수(안 찬 회차 포함)를 센다 — 판정·짝에 쓰지 않는다(25.1918)
                판정 = "회차 지정가 실행 — 짝 판정 없음(결정 원장이 안 찬 회차까지 센다, 25.1914)"
            else:
                짝 = ce.pairs(증거)
                판정 = ce.verdict(증거, 짝.get("core_h-core_g"))
        except Exception as e:  # noqa: BLE001
            log.exception("핵심 H 운 판정·결정 단위 판정 실패")
            warnings.append(f"운 판정·결정 단위 판정 실패: {type(e).__name__}: {str(e)[:120]}")
        증거_초 = round(time.perf_counter() - t2, 1)
        반: dict[str, dict[str, Any]] = {}
        실은_행: list[dict[str, Any]] = []  # 판정판(25.1940) 재료 — `store_run` 이 실은 행 그대로
        for variant, result in results.items():
            summary = bt.summarize(result, 벤치, risk_free_annual=무위험)
            반[variant] = split_half(result.curve)
            e = 증거.get(variant) or {}
            extra = {"split_half": 반[variant], "benchmark": BENCHMARK_NOTE, "risk_every": risk_every,
                     "account": ({"st_s": "S", "st_t": "T"}[variant] if variant in ST_VARIANTS
                                 else cs.variant_spec(variant).account),
                     "evidence": {k: e.get(k) for k in ("buys", "scored", "no_control", "days", "n_blocks", "mean",
                                                        "se", "lower", "gate", "after_sell")},
                     # 월 초과 계열(25.1947) — 판정판 걷는 창의 재료. 운 판정과 같은 기준(동일가중 월 리밸런스).
                     # 깨지면 칸만 빠진다(25.1952)
                     **jb.monthly_excess_extra(result.curve, 벤치월),
                     # 수용량(25.1954) — 리밸런스마다 단위 금액의 가장 빠듯한 종목 비율. 깨지면 칸만 빠진다
                     **jb.capacity_extra(result, turnover, market),
                     # 비용 길 빈도(25.1969) — 대체 길(현금 < 비용)이 운영과 다른 길이라 몇 번 탔나
                     "cost_path": bt.cost_paths(result)}  # fmt: skip
            jb.store_run(client, market, variant, result, summary, 종목수[variant], group_id, warnings,
                         now, 운.get(variant), rebalance=REBALANCE, extra=extra, kept=실은_행)  # fmt: skip
            mt = summary.metrics
            앞, 뒤 = 반[variant].get("first", {}), 반[variant].get("second", {})
            print(f"  요약 {variant:8s} CAGR {jb._pct(mt.cagr)}  MDD {jb._pct(mt.mdd)}  샤프 {jb._fmt(mt.sharpe)}"
                  f"  초과 {jb._pct(summary.excess_cagr)}  앞 반 {jb._pct(앞.get('cagr'))}/{jb._pct(앞.get('mdd'))}"
                  f"  뒤 반 {jb._pct(뒤.get('cagr'))}/{jb._pct(뒤.get('mdd'))}"
                  f"  분위 하한 {jb._fmt(e.get('lower'))}(묶음 {e.get('n_blocks')})"
                  f"  {luck.line(운.get(variant))}")  # fmt: skip

        # H 가중 대 동일가중 대조(25.1538, 31회차 P1) — 나눠 읽은 하루 재료와 메모리 원장으로, 읽기 0. 진단이 깨져도
        # 변형 결과는 이미 저장됐으니 실행을 죽이지 않고 기록에 남긴다
        holding_eq: dict[str, Any] = {"skipped": "탐색 실행 — H 진단 없음"}
        if not explore:
            try:
                eq_days = [(dv.day, *heq.day_sets(dv, market_.top_n)) for dv in shared.views()]
                성과 = {(r.decided_on, r.stock_id): r.ret_20d - r.bench_20d for r in market_.ledger.rows
                        if r.ret_20d is not None and r.bench_20d is not None}  # fmt: skip
                holding_eq = heq.summarize(eq_days, 성과)
            except Exception as e:  # noqa: BLE001
                log.exception("H 가중 대조 진단 실패")
                holding_eq = {"error": str(e)[:200]}
        print(f"  H 가중 대 동일가중: {holding_eq}")
        # 같은 로트 청산 대조·하루 손실 관문 빈도(25.1552, 32회차 P1·P4) — core_h 하루 기록·종가·지수로, 읽기 0.
        # 깨져도 실행을 죽이지 않는다
        exit_pair: dict[str, Any] = {}
        loss_gate: dict[str, Any] = {}
        if "core_h" in 기록:
            try:
                exit_pair = xp.summarize(기록["core_h"], ce.Closes(prices), indexes.get(cb.BENCH_INDEX) or {})
            except Exception as e:  # noqa: BLE001
                log.exception("같은 로트 청산 대조 진단 실패")
                exit_pair = {"error": str(e)[:200]}
            try:
                loss_gate = lg.frequency(기록["core_h"], indexes.get(cb.BENCH_INDEX) or {})
            except Exception as e:  # noqa: BLE001
                log.exception("하루 손실 관문 빈도 진단 실패")
                loss_gate = {"error": str(e)[:200]}
        print(f"  같은 로트 청산 대조: {exit_pair}")
        print(f"  하루 손실 관문 빈도: {loss_gate}")

        실제_리밸런스 = sum(1 for d in dates if d != dates[-1] or len(dates) == 1)
        step_log = {
                "group_id": group_id, "strategies": len(results), "rebalance": REBALANCE, "rebalances": 실제_리밸런스,
                "split_half": 반, "stocks": len(ids), "dates": len(dates), "warnings": warnings,
                "params": {"years": years, "risk_every": risk_every, "variants": list(variants), "explore": explore,
                           "limit_fills": limit_fills,  # 25.1914 (전략 깔때기 ④ 분할 지정가 체결)
                           "trial_floor": explore_trial_floor(st) if explore else jb.REGISTERED_TRIALS, "st": st,
                           "trigger": os.environ.get("BACKTEST_TRIGGER") or None,
                           "sha": (os.environ.get("GITHUB_SHA") or "")[:12] or None},
                # 실측 — 시간 한도 120분 안인지 다음 실행 전에 본다(25.1534)
                "timing": {"load_s": round(읽기_초, 1), "variants_s": 시간, "evidence_s": 증거_초,
                           "days_computed": shared.computed,
                           "days_replayed": shared.replayed, "risk_computed": market_.risk_computed},
                "pit_universe": {"sizes": jb._pit_sizes(pit_cache), "excluded": pit_reasons},
                "holding_eq": holding_eq, "exit_pair": exit_pair, "loss_gate": loss_gate,
                # 결정 단위 판정(조각 1e) — 변형마다 묶음 값까지, 짝(H−G·G−B) 차이
                "evidence": 증거, "pairs": 짝, "verdict": 판정,
                **({"st": st_log} if st else {}),
            }  # fmt: skip
        # 판정판(docs/funnel.md 2.2, 25.1940) — 이 실행의 줄을 실행 기록에(웹 /backtest "판정판").
        # 읽기 0, 깨져도 성공으로 닫는다
        step_log.update(jb.scoreboard_log(job_name, now, step_log, 실은_행))
        db.finish_batch_run(client, batch_id, status="success", step_log=step_log)
        print(f"저장 {len(results)}개 변형, 리밸런스 {실제_리밸런스}회(날마다), 그룹 {group_id}")
        for w in sorted(set(warnings)):
            print(f"  경고: {w}")
        return 0
    finally:
        # 도중에 깨지면 월 백테스트와 같이 `guard` 가 받는다(DB 한도는 건너뜀) — 기록은 `running` 으로 남아 정리된다
        jb._위험_캐시.clear()
        client.close()


def _진행(client: Any, batch_id: Any, done: int, total: int, current: str) -> None:
    try:
        db.note_progress(client, batch_id, {"done": done, "total": total, "current": current})
    except Exception:  # noqa: BLE001 — 진행 표시가 계산을 죽이면 안 된다
        log.warning("진행 상태를 적지 못했습니다")


def _run_st(
    shared: cb.SharedDays, prices: dict[int, dict[str, float]], opens: dict[int, dict[str, float]], dates: list[str],
    turnover: dict[int, dict[str, float]], country: str, fees: dict, taxes: dict, costs: bt.Costs,
    results: dict[str, bt.BacktestResult], 종목수: dict[str, int], 시간: dict[str, float],
    progress: Any = None,
    log: dict[str, Any] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """S·T 계좌형 리플레이 둘(깔때기 ⑦-6, 25.2044) — 핵심 변형과 같은 하루 재료(`SharedDays`)의 단기 신호 행으로
    `swing_day_replay.simulate_account` 를 돌려 `results` 에 판정판 꼴(`BacktestResult`)로 더한다. 기록: 시가 덮개
    (유니버스·기간의 종가 행 가운데 시가가 있는 몫 — 시가가 빈 날은 T 가 사지 않고 S 는 넘음 판정이 그날을 세지 않아
    매도가 늦거나 없다)·체결 수·
    날마다 기록의 합(만료 까닭·관문 코드·매도 까닭)."""
    from batch.jobs import paper as jp
    from batch.services import swing_day_replay as sdr

    종가행 = sum(1 for by in prices.values() for d in by if d >= dates[0])
    시가행 = sum(1 for sid, by in opens.items() for d in by if d >= dates[0] and d in prices.get(sid, {}))
    캐시 = {
        int(r["stock_id"]): {"id": int(r["stock_id"]), "ticker": str(r["ticker"]),
                             "name": str(r.get("name") or r["ticker"]),
                             "sector": r.get("sector") or None, "country": country,
                             "currency": r.get("currency") or "KRW", "asset_type": r.get("asset_type") or "stock"}
        for r in shared.market.universe
    }  # fmt: skip
    설정 = {"max_weight_per_stock": float(shared.market.max_weight_per_stock),
            "max_weight_per_sector": float(report_picks.DEFAULT_MAX_SECTOR_PCT), "min_order_amount": 0.0}  # fmt: skip
    out: dict[str, Any] = log if log is not None else {}
    out.update({"open_coverage": round(시가행 / 종가행, 4) if 종가행 else None, "accounts": {}})
    for k, (계좌, 이름) in enumerate((("S", "st_s"), ("T", "st_t"))):
        if progress:
            progress(이름, k)
        t1 = time.perf_counter()
        읽개 = shared.reader()
        r = sdr.simulate_account(
            계좌, rows_at=lambda view, d, rd=읽개: rd.at(view, d).rows, prices=prices, opens=opens, dates=dates,
            costs_at=lambda d: jp.costs_for(country, fees, taxes, d), stock_cache=캐시, settings=설정,
            turnover=turnover, market=country,
        )  # fmt: skip
        # 날마다 보유 비중을 리밸런스 꼴로 — 판정판의 노출(투자한 달·평균 보유)·수용량이 실제 값이 되게(25.2045,
        # 예전 `rebalances=[]` 는 "투자한 달 0%" 거짓 경고와 0 칸을 실었다). 비중은 max(하루 끝 평가, 그날 산 금액)
        # (T 는 장중 보유), 회전은 그날 체결 금액 기준(값 움직임 빼고 T 왕복 넣음, 25.2046).
        # 비용은 체결에 이미 들어 있어 0, 비용 길은 없다(None — 장부형 엔진의 현금·대체 길이 아니다)
        회전 = dict(r.turnover)
        리밸런스 = [bt.Rebalance(날, dict(w), 회전.get(날, 0.0), 0.0) for 날, w in r.weights]
        results[이름] = bt.BacktestResult(curve=r.curve, rebalances=리밸런스, costs=costs,
                                          warnings=[bt.WARN_NO_DIVIDENDS])  # fmt: skip
        종목수[이름] = sdr.TOP_N
        시간[이름] = round(time.perf_counter() - t1, 1)
        합: dict[str, Any] = {"fills": len(r.fills), "buys": 0, "sells": 0, "deferred": 0}
        for g in r.log:
            for k in ("buys", "sells", "deferred"):
                합[k] += g.get(k, 0)
            for k in ("expired", "blocked_by", "sold"):
                for 까닭, n in (g.get(k) or {}).items():
                    합.setdefault(k, {})[까닭] = 합.get(k, {}).get(까닭, 0) + n
        out["accounts"][계좌] = 합
        print(f"  {이름:8s} 최종 {r.curve[-1][1]:.3f}  ({시간[이름]:.0f}초, 체결 {len(r.fills)})", flush=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="핵심 H 다년 백테스트 (docs/backtest.md 10장)")
    parser.add_argument("--market", default="KR", choices=["KR", "kr"])
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    parser.add_argument("--risk-every", dest="risk_every", choices=list(cb.RISK_EVERY), default=DEFAULT_RISK_EVERY,
                        help="팩터 점수 리스크 지표 주기 — week(기본, 예전 주기와 같은 값) · month(한도가 모자랄 때)")
    parser.add_argument("--force", action="store_true", help="읽기 예산이 모자라도 강행")
    parser.add_argument("--explore", action="store_true",
                        help="탐색 변형(EXPLORE_VARIANTS)을 backtest_explore 로 — 전략 깔때기 0단계(docs/funnel.md)")
    parser.add_argument("--limit-fills", dest="limit_fills", action="store_true",
                        help="회차 변형의 매수를 운영처럼 3회 분할 지정가로 체결(종가 ≤ 지정가) — 25.1914, 기본 꺼짐")
    parser.add_argument("--st", action="store_true",
                        help="탐색 실행에 S·T 계좌형 리플레이(st_s·st_t)를 더한다 — 깔때기 ⑦-6(25.2044), --explore 와")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run_core(args.market, args.years, risk_every=args.risk_every, force=args.force, explore=args.explore,
                    limit_fills=args.limit_fills, st=args.st)


if __name__ == "__main__":
    sys.exit(guard(main))
