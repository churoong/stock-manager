"""백테스트 실행과 적재.

방법은 docs/backtest.md, 엔진은 batch/services/backtest.py 에 있다.
이 파일은 데이터를 읽어 엔진에 넘기고 결과를 저장하는 일만 한다.

**시점 재무를 고르는 함수(`pit_financials`)가 이 파일에서 가장 중요하다.**
발표일(`as_of_date`)이 기준일 이후인 스냅샷을 하나라도 쓰면 미래를 보는
것이다. 순수 함수로 떼어 테스트로 고정했다.

같은 엔진으로 일곱 전략을 돌린다.
  composite                        종합 점수 상위 N
  value quality growth momentum risk   팩터 하나로 상위 N  (성과요인분석)
  benchmark                        유니버스 동일가중
그리고 composite 를 비용 0 으로 한 번 더 돌려 나란히 둔다.

장기 신호 문턱 비교 (docs/backtest.md 7장, 2026-09-17 사용자 결정 "백테스트로 정하기")
  long_q60 long_q55 long_q50       퀄리티·밸류 문턱만 바꾼 장기 신호. 들어가면 1년 보유

실행
  python -m batch.jobs.backtest --market KR
  python -m batch.jobs.backtest --market KR --years 3 --top-n 20
  python -m batch.jobs.backtest --market KR --only-long --long-thresholds 60,55,50
"""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import math
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs import metrics as metrics_job
from batch.services import backtest as bt
from batch.services import buyback as bb
from batch.services import dilution as dil
from batch.services import div_omission as dvo
from batch.services import earnings_quality as eq
from batch.services import factor_ab as fab
from batch.services import factor_ic as fic
from batch.services import flow_surge as fs_
from batch.services import gross_profitability as gp
from batch.services import luck, sectors, trend
from batch.services import metrics as m
from batch.services import pit_universe as pit
from batch.services import quarterly_earnings as qe
from batch.services import scoring as sc
from batch.services import sector_momentum as secmom
from batch.services import share_issuance as si
from batch.services import short_reversal as sr
from batch.services import signals as sg
from batch.services import universe as uni
from batch.services.pit import assert_no_lookahead
from batch.sources import dart, sec_facts

log = logging.getLogger("backtest")

JOB_NAME = "backtest"

#: 정의처는 `batch/sources/dart.ANNUAL_REPORT_CODE` 하나다 (docs/infra.md 25.143)
ANNUAL_REPORT_CODE = dart.ANNUAL_REPORT_CODE
DEFAULT_YEARS = 5
#: 기간·상위 N 이 기본이 아닌 실행의 표시 (docs/infra.md 25.928). 웹 `/backtest` 는 이 글로 비교용 묶음을 가린다 —
#: 시점 유니버스 끔·추세 필터 켬과 달리 이것만 결과 행에 흔적이 없어, 기간 2년·상위 50 실행이 대표 결과가 됐다
WARN_NOT_DEFAULT_PARAMS = "기본 파라미터 아님 (비교용, 규칙 판단에 쓰지 않는다)"

# 리스크 팩터를 그 자리에서 계산할 창. docs/backtest.md 2.3 절. 3Y 창의 표본 하한이다.
RISK_LOOKBACK_DAYS = 750

STRATEGIES = ("composite", "value", "quality", "growth", "momentum", "risk", "benchmark", "momentum_sector")
#: 발굴 루프로 넣은 **후보** 전략 (docs/factors.md 12장). 실제 추천에는 켜는 기준을 넘기 전까지 쓰지 않는다
CANDIDATE_STRATEGIES = ("momentum_sector",)

# 장기 신호 백테스트 (docs/backtest.md 7장)
LONG_HOLD_DAYS = 365  # 장기 = 1년 이상 (CLAUDE.md 투자 기간). 들어간 뒤 1년은 신호가 꺼져도 들고 있다
LONG_BAND_DAYS = sg.BAND_DAYS  # 정의처는 services/signals (docs/infra.md 25.108)
DEFAULT_LONG_THRESHOLDS = (60.0, 55.0, 50.0)

WARN_NO_HISTORICAL_UNIVERSE = "과거 유니버스 스냅샷 없음. 현재 유니버스를 전 기간에 썼다"
# 시점 유니버스를 쓰면 위 경고 대신 이것이 붙는다. 고친 것과 못 고친 것을 함께 적는다
WARN_PIT_UNIVERSE = (
    "시점 유니버스: 리밸런스마다 그때의 상장 경과일·거래대금·시총(현재 주식수×그날 종가)으로 다시 걸렀다."
    " 주식수는 현재 값 하나뿐이라 증자·분할이 있던 종목의 과거 시총은 근사다."
    " 후보는 오늘 유니버스에 든 종목뿐이라, 그 사이 문턱 아래로 떨어진 종목은 과거에도 고르지 않았다(실제보다 낙관적)."
    " 관리종목·거래정지는 시점별로 보지 않아, 그때 관리종목이었다가 회복한 종목도 편입됐다(낙관적)"
)
WARN_NO_RISK_FREE = "무위험수익률 설정이 없어 샤프·소르티노를 내지 않았다 (설정 화면 → 무위험수익률)"
# 문구 정정 (docs/infra.md 25.786, 백테스트 감사 #1): 예전엔 "4팩터로 재정규화됐다" 였는데, 리스크와 함께 다른
# 팩터(성장 등)도 비면
# `scoring.total_score` 가 점수를 내지 않아(둘 이상 결측) 종합 전략은 그동안 **현금으로 쉬었다**. 첫 투자일·투자한
# 달은 결과 표에 있다
WARN_RISK_GAP = (
    "초기 구간은 표본 부족으로 리스크 팩터가 빈 종목이 있었다 — 리스크만 빈 종목은 4팩터로 재정규화하고,"
    " 팩터가 둘 이상 빈 종목은 점수를 내지 않아 그동안 전략이 현금으로 쉴 수 있다(결과 표의 첫 투자일·투자한 달)"
)
#: docs/backtest.md 7장 판단 기준 2 — 투자한 달이 절반 미만이거나 평균 보유가 3종목 미만이면 수익을 믿지 않는다
MIN_INVESTED_SHARE = 0.5
MIN_AVG_HOLDINGS = 3.0

#: 기준일(cutoff)을 받아 그 시점 유니버스에 든 종목 집합을 돌려주는 함수
EligibleAt = Callable[[str], set[int]]


# ----------------------------------------------------------------------
# 시점 재무 — 미래를 보지 않는다
# ----------------------------------------------------------------------


def _guard(cutoff: str, *as_of_dates: str | None) -> None:
    """돌려주기 직전에 한 번 더 본다 (`services/pit.assert_no_lookahead`).

    아래 세 함수는 각자 `as_of_date > cutoff` 를 걸러 낸다 — **같은 규칙이 세 벌**이다.
    한 벌이 언젠가 깨지면 백테스트는 멈추지 않고 **더 좋은 성적을 낸다.** 그게 가장
    나쁜 고장이다. 틀린 성적표는 틀린 줄을 모른다.

    그래서 거르고 나서, 실제로 돌려줄 행을 다시 본다. 고정 데이터로 만든 테스트가
    보지 못한 모양의 데이터에도 걸린다. 조용히 틀린 결과를 내는 것보다 멈추는 편이 낫다.
    """
    for as_of in as_of_dates:
        if as_of is not None:
            assert_no_lookahead(cutoff, as_of)


def pit_basis_rows(snapshots: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    """`cutoff` 에 알 수 있던 행으로 **연결·별도 기준을 고르고** 그 기준의 행만 돌려준다 (docs/infra.md 25.860, 7회차
    제안 1).

    예전에는 `load_snapshots` 가 **오늘 DB** 의 가장 늦은 사업연도로 종목 전체의 기준을 한 번 골랐다 — 2025 에 처음
    연결을
    낸 회사는 2022~2025-03 기준일에 그때 알 수 있던 별도 재무를 모두 잃어 후보에서 빠졌고(미래가 과거 표본을 골랐다),
    연결을 끊은 회사는 그때 운영이 쓰던 연결 대신 별도로 과거를 쟀다. 운영(`db.FINANCIAL_BASIS_F_AS_OF`, 25.856)과
    같은 규칙:
    기준일까지 접수된 행 중 가장 늦은 사업연도에 연결이 있으면 연결, 없으면 별도. 분기 SUE 의 3단계 규칙(25.461)은
    쓰지 않는다.

    돌려주는 행에는 기준일 뒤 행도 남는다 — 시점 거르기는 각 함수가 그대로 한다. `consolidated` 가 없는 행(예전
    모양)은 그대로 둔다.
    """
    if not snapshots or any("consolidated" not in r for r in snapshots):
        return snapshots
    known = [r for r in snapshots if str(r["as_of_date"]) <= cutoff]
    if not known:
        return []
    늦은해 = max(int(r["fiscal_year"]) for r in known)
    연결 = any(r["consolidated"] for r in known if int(r["fiscal_year"]) == 늦은해)
    return [r for r in snapshots if bool(r["consolidated"]) == 연결]


def pit_financials(
    snapshots: list[dict[str, Any]], cutoff: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """cutoff 에 알 수 있던 (최근 연도, 전년도, 3년 전) 재무.

    snapshots 는 한 종목의 연간 스냅샷 목록이다(연결·별도 — 기준은 `pit_basis_rows` 가 고른다). 각 행은
    as_of_date(발표일), fiscal_year, values(dict) 를 가진다.

    **as_of_date > cutoff 인 행은 존재하지 않는 것으로 본다.** 2026-03-10 에
    접수된 2025 사업보고서는 2026-03-09 에 알 수 없었다. 같은 회계연도가
    여러 번 접수됐으면(정정) cutoff 까지 나온 것 중 가장 최근 것을 쓴다.
    """
    snapshots = pit_basis_rows(snapshots, cutoff)
    known: dict[int, dict[str, Any]] = {}
    for row in snapshots:
        if str(row["as_of_date"]) > cutoff:
            continue
        year = int(row["fiscal_year"])
        kept = known.get(year)
        if kept is None or str(row["as_of_date"]) > str(kept["as_of_date"]):
            known[year] = row

    if not known:
        return None, None, None
    latest = max(known)
    # 운영 점수와 같게 — 최신 사업보고서가 묵었으면 연간 재무를 쓰지 않는다 (docs/infra.md 25.804,
    # `jobs/scores.drop_stale_annual`)
    if sc.annual_is_stale(str(known[latest]["as_of_date"]), cutoff, latest):
        return None, None, None
    # 중간 해가 비면 3년 전 재무를 쓰지 않는다 — 3년 CAGR 은 4개 연도가 다 있어야 한다 (docs/infra.md 25.308)
    three_ago = known.get(latest - 3) if sc.has_cagr_years(known, latest) else None
    picked = (known[latest], known.get(latest - 1), three_ago)
    _guard(cutoff, *(None if row is None else str(row["as_of_date"]) for row in picked))
    return picked


def stability_from(
    snapshots: list[dict[str, Any]], cutoff: str, latest_year: int
) -> tuple[int | None, int | None]:
    """최근 5개 사업연도 중 영업흑자 연수. jobs/scores.py 와 같은 규칙."""
    snapshots = pit_basis_rows(snapshots, cutoff)
    known: dict[int, dict[str, Any]] = {}
    for row in snapshots:
        if str(row["as_of_date"]) > cutoff:
            continue
        year = int(row["fiscal_year"])
        kept = known.get(year)
        if kept is None or str(row["as_of_date"]) > str(kept["as_of_date"]):
            known[year] = row

    window = [
        known[y]
        for y in range(latest_year - 4, latest_year + 1)
        if y in known and known[y]["values"].get("operating_income") is not None
    ]
    if not window:
        return None, None
    _guard(cutoff, *(str(r["as_of_date"]) for r in window))
    profitable = sum(1 for r in window if float(r["values"]["operating_income"]) > 0)
    return profitable, len(window)


# ----------------------------------------------------------------------
# 판단 함수 — t-1 창만 보고 점수를 낸다
# ----------------------------------------------------------------------


def make_strategy(
    strategy: str,
    universe: list[dict[str, Any]],
    snapshots_by_stock: dict[int, list[dict[str, Any]]],
    top_n: int,
    weights: dict[str, float],
    warnings: list[str],
    eligible_at: EligibleAt | None = None,
    dividends_by_stock: dict[int, list[tuple[str, int, float]]] | None = None,
    benchmark_closes: dict[str, float] | None = None,
) -> bt.WeightsAt:
    """전략 이름에 맞는 판단 함수를 만든다.

    eligible_at 을 주면 리밸런스마다 **그때의 유니버스**로 후보를 먼저 자른다
    (docs/backtest.md 1.3). 주지 않으면 오늘 유니버스를 전 구간에 쓴다 — 옛 방식이다.
    """

    def candidates(t: str, view: bt.PriceView) -> list[dict[str, Any]]:
        if eligible_at is None:
            return universe
        allowed = eligible_at(view.cutoff)
        return [row for row in universe if row["stock_id"] in allowed]

    if strategy == "benchmark":

        def benchmark(t: str, view: bt.PriceView) -> dict[int, float]:
            alive = [row["stock_id"] for row in candidates(t, view) if view.last_close(row["stock_id"])]
            if not alive:
                return {}
            return {sid: 1.0 / len(alive) for sid in alive}

        return benchmark

    risk_gap_noted = False
    sector_noted = False

    def decide(t: str, view: bt.PriceView) -> dict[int, float]:
        nonlocal risk_gap_noted, sector_noted
        inputs = build_pit_inputs(
            candidates(t, view), snapshots_by_stock, view, dividends_by_stock, benchmark_closes
        )
        if not inputs:
            return {}

        if not risk_gap_noted and any(i.metrics.get("mdd_abs") is None for i in inputs):
            warnings.append(WARN_RISK_GAP)
            risk_gap_noted = True

        results = sc.score_factors(inputs)
        by_stock: dict[int, dict[str, float | None]] = {}
        for r in results:
            by_stock.setdefault(r.stock_id, {})[r.factor] = r.score

        if strategy == "composite":
            scores = {
                sid: sc.total_score(fs, weights).total for sid, fs in by_stock.items()
            }
        elif strategy == "momentum_sector":
            # 업종 모멘텀 상위 절반 업종 안에서 모멘텀 상위 N (docs/factors.md 12.2, 기법 루프 D, 25.439)
            means = secmom.sector_means(inputs)
            top = secmom.top_half_sectors(means)
            # **업종 평균이 잡힌 종목이 60% 미만이면 비교는 판정 불가다** (25.443, 교차검증). 업종 채움이 낮으면
            # 거의 전부 통과해 `momentum` 과 같은 종목을 고르고, 두 전략의 차이는 잡음이 된다
            비율 = secmom.coverage(inputs, means)
            if not sector_noted and 비율 < fic.MIN_COVERAGE:
                warnings.append(secmom.WARN_LOW_COVERAGE.format(pct=비율 * 100, t=t))
                sector_noted = True
            통과 = {i.stock_id for i in inputs if secmom.passes(i, means, top)}
            scores = {sid: fs.get("momentum") for sid, fs in by_stock.items() if sid in 통과}
        else:
            scores = {sid: fs.get(strategy) for sid, fs in by_stock.items()}

        return bt.top_n_equal_weight(scores, top_n)

    return decide


# ----------------------------------------------------------------------
# 시장 추세 오버레이 (docs/backtest.md 2.4, docs/signals.md 3.5)
# ----------------------------------------------------------------------

WARN_TREND_ON = "추세 필터 켬: 지수 < 200일선이면 비중 × 배수 (docs/signals.md 3.5). 끈 그룹과 비교할 것"
WARN_TREND_NO_INDEX = (
    "추세 필터: 지수가 200일치 미만이거나 없어 배수 1.0 으로 둔 리밸런스가 있습니다"
    " (python -m batch.jobs.index_prices --lookback 2000)"
)


def load_index_series(client: TursoClient, country: str, since: str) -> dict[str, list[tuple[str, float]]]:
    """그 나라 지수들의 (날짜, 종가). since 는 백테스트 시작보다 200 거래일 앞이어야 한다."""
    out: dict[str, list[tuple[str, float]]] = {}
    for code in trend.COUNTRY_INDEXES.get(country, ()):
        rs = client.execute(
            "SELECT date, close FROM index_prices WHERE index_code = ? AND date >= ? ORDER BY date",
            [code, since],
        )
        out[code] = [(str(r[0]), float(r[1])) for r in rs.rows]
    return out


def with_trend_filter(
    decide: bt.WeightsAt,
    index_series: dict[str, list[tuple[str, float]]],
    market_of: dict[int, str],
    bear_factor: float,
    warnings: list[str],
) -> bt.WeightsAt:
    """판단 함수의 비중에 그 종목 시장 지수의 국면 배수를 곱한다. 지수도 t-1(view.cutoff)까지만 본다.

    국면을 모르면(지수 부족) 배수 1.0 — 적재 경로(services/trend.factor_for)와 같은 규칙이다.
    """
    settings = {"enabled": True, "bear_factor": bear_factor}
    noted = False

    def wrapped(t: str, view: bt.PriceView) -> dict[int, float]:
        nonlocal noted
        proposed = decide(t, view)
        if not proposed:
            return proposed
        cutoff = view.cutoff
        factor_of: dict[str, float] = {}
        out: dict[int, float] = {}
        for sid, weight in proposed.items():
            code = trend.index_for_market(market_of.get(sid)) or ""
            if code not in factor_of:
                regime = trend.regime_from_closes(code, index_series.get(code, []), cutoff)
                if regime.state == "unknown" and not noted:
                    warnings.append(WARN_TREND_NO_INDEX)
                    noted = True
                factor_of[code] = trend.factor_for(regime, settings)[0]
            out[sid] = weight * factor_of[code]
        return out

    return wrapped


def year_values(snapshots: list[dict[str, Any]], cutoff: str, picked: dict[str, Any] | None) -> dict[str, Any]:
    """`pit_financials` 가 고른 행의 값에, **같은 회계연도의 앞 공시**(기준일 이전)가 가진 값을 빈 칸에만 메운다
    (docs/infra.md 25.448, 교차검증). 손익·재무상태만 다시 낸 10-K/A 가 원래 10-K 의 영업현금흐름을 가려
    발생액이 None 이 됐다. 둘 다 기준일 전에 나온 공시라 시점 규칙에 맞는다
    — `sec_facts.latest_by_year` 와 같은 생각이다."""
    if picked is None:
        return {}
    snapshots = pit_basis_rows(snapshots, cutoff)  # 다른 기준의 같은 해 행으로 빈 칸을 메우지 않는다 (25.860)
    merged = dict(picked.get("values") or {})
    year = picked.get("fiscal_year")
    earlier = sorted(
        (r for r in snapshots if r.get("fiscal_year") == year and str(r["as_of_date"]) <= cutoff and r is not picked),
        key=lambda r: str(r["as_of_date"]), reverse=True,
    )  # fmt: skip
    끝 = merged.get("period_end")
    for r in earlier:
        # **결산일이 다른 공시로는 메우지 않는다** (25.743, 교차검증) — 결산월을 바꾼 회사의 두 10-K 가 같은 회계연도
        # 번호를 받아
        # 다른 기간 값이 섞였다. `sec_facts.latest_by_year`(25.730)와 같은 규칙. 결산일을 모르는 행(국내 payload)은
        # 예전처럼 메운다
        앞끝 = (r.get("values") or {}).get("period_end")
        if 끝 and 앞끝 and 앞끝 != 끝:
            continue
        if not sc.same_currency(merged, r.get("values")):  # 다른 통화의 앞 공시로는 메우지 않는다 (25.919)
            continue
        # 당기·전기 주식수는 짝으로만 메운다 — 공시가 섞이면 분할이 발행으로 보인다 (25.453, 교차검증)
        merged = sec_facts.fill_blanks(merged, r.get("values") or {})
    return merged


#: IC 를 잴 이름들 (docs/backtest.md 2.5). 팩터 다섯과 종합
IC_NAMES = (
    "composite", "value", "quality", "growth", "momentum", "risk", "sector_mom", "accrual", "net_issuance", "sue",
    "reversal_1m", "div_omission", "buyback", "dilution", "flow_surge",
    "gross_profitability", "flow_surge_x_rev",
)
#: 발굴 루프로 넣은 이름(팩터 다섯·종합 제외) — 다중검정 기록의 묶음이다. 크기를 고정한다 (6회차 2, 25.810)
LOOP_IC_NAMES = tuple(n for n in IC_NAMES if n not in ("composite", "value", "quality", "growth", "momentum", "risk"))


def factor_ics(
    universe: list[dict[str, Any]],
    snapshots_by_stock: dict[int, list[dict[str, Any]]],
    prices: dict[int, dict[str, float]],
    dates: list[str],
    rebalance_dates: list[str],
    weights: dict[str, float],
    eligible_at: EligibleAt | None = None,
    dividends_by_stock: dict[int, list[tuple[str, int, float]]] | None = None,
    benchmark_closes: dict[str, float] | None = None,
    turnover: dict[int, dict[str, float]] | None = None,
    levels: dict[int, dict[str, float]] | None = None,
    quarters_by_stock: dict[int, list[dict[str, Any]]] | None = None,
    buybacks: tuple[dict[int, list[str]], dict[int, list[tuple[str, str]]]] | None = None,
    dilutions: dict[int, list[str]] | None = None,
    ab: fab.AbAccumulator | None = None,
) -> dict[str, fic.IcSummary]:
    """팩터별 IC — 리밸런스 t 의 점수(t-1 까지의 값) 순위와 t → 다음 리밸런스 수익률 순위의 상관
    (docs/backtest.md 2.5, docs/infra.md 25.435).

    **점수는 전략과 같은 함수로 낸다**(`build_pit_inputs` → `score_factors`) — 전략이 쓰는 점수와 IC 가 재는 점수가
    달라지면 IC 는 아무것도 말하지 않는다. 수익률은 평가에만 쓴다(판단에 들어가지 않는다). 체결가와 같이
    **그날 종가**로 들어가 다음 리밸런스 날 종가로 나온다. 둘 중 하나라도 없으면 그 종목은 그 달에서 빠진다.

    `ab` 를 주면 지표 교체 A/B(docs/factors.md 12.12)를 같은 달·같은 수익률로 함께 쌓는다 — 기록 전용.
    """
    series: dict[str, list[float | None]] = {name: [] for name in IC_NAMES}
    비율: dict[str, list[float]] = {name: [] for name in IC_NAMES}
    # 시장을 섞은 예전 IC(참고용)와 시장별 계열 (6회차 1, 25.810)
    섞음: dict[str, list[float | None]] = {name: [] for name in IC_NAMES}
    시장별: dict[str, dict[str, list[float | None]]] = {name: {} for name in IC_NAMES}
    걸림: list[float] = []  # 배당 중단이 걸린 비율, 달마다 (25.479)

    def carry(sid: int, d: str) -> float | None:
        """d 의 종가, 없으면 그 전 마지막 종가 — `simulate` 와 같은 규칙이다(거래정지는 묶인 값으로, 25.442)."""
        by_date = prices.get(sid, {})
        if d in by_date:
            return by_date[d]
        earlier = [k for k in by_date if k < d]
        return by_date[max(earlier)] if earlier else None

    for i, t in enumerate(rebalance_dates[:-1]):
        cutoff = bt.decision_cutoff(dates, prices, t)  # 전략과 같은 규칙 — 첫 달도 워밍업으로 (25.627)
        if not cutoff:
            continue
        view = bt.PriceView(prices, cutoff, turnover, levels)
        후보 = universe if eligible_at is None else [r for r in universe if r["stock_id"] in eligible_at(cutoff)]
        inputs = build_pit_inputs(후보, snapshots_by_stock, view, dividends_by_stock, benchmark_closes)
        if not inputs:
            continue
        by_stock: dict[int, dict[str, float | None]] = {}
        for r in sc.score_factors(inputs):
            by_stock.setdefault(r.stock_id, {})[r.factor] = r.score
        nxt = rebalance_dates[i + 1]
        수익: dict[int, float | None] = {}
        for sid in by_stock:
            # 들어가는 날은 체결가라 그날 가격이 있어야 한다(없으면 살 수 없다 — simulate 도 뺀다).
            # 나오는 날은 마지막 종가로 묶는다 — 예전에는 빠져서 전략 성적과 다른 세계를 쟀다 (25.442, 교차검증)
            들 = prices.get(sid, {}).get(t)
            날 = carry(sid, nxt)
            수익[sid] = 날 / 들 - 1 if 들 and 날 else None
        업종값 = secmom.stock_values(inputs)
        발생액점수: dict[int, float | None] = {}
        발행점수: dict[int, float | None] = {}
        총이익점수: dict[int, float | None] = {}
        # 분기 실적 서프라이즈 — 기준일까지 접수된 분기 공시로 (docs/factors.md 12.2, 25.456).
        # 시가총액은 약분돼 사라지므로(11.4) 1 을 넘긴다
        # 단기 반전 — 진단용, 팩터에 넣지 않는다 (docs/factors.md 12.2, 25.475)
        반전점수 = {sid: sr.reversal_1m([c for _, c in view.closes(sid)]) for sid in by_stock}
        시장 = {i.stock_id: i.market for i in inputs}
        # 배당 중단 — 이진값, 걸린 비율도 남긴다 (docs/factors.md 12.2, 25.479)
        # **국내만** (2회차 검증 조건). 미국은 주당배당만 있는 해도 지급액 0 으로 실려 거짓 "중단" 이 난다 (25.481)
        국내 = {i.stock_id for i in inputs if i.market in ("KOSPI", "KOSDAQ")}
        중단 = {sid: (dvo.omitted((dividends_by_stock or {}).get(sid, []), cutoff) if sid in 국내 else None)
                for sid in by_stock}  # fmt: skip
        중단점수 = {sid: (None if v is None else (-1.0 if v else 0.0)) for sid, v in 중단.items()}
        # 자사주 취득 결정 공시 — 국내만 (docs/factors.md 12.2, 25.482). 수집이 창을 덮지 못하면 NULL
        사건들, 수집구간 = buybacks or ({}, {})
        매입점수 = {sid: (float(f) if (f := bb.flag(사건들.get(sid, []), cutoff, 수집구간.get(sid, []))) is not None
                          else None) if sid in 국내 else None for sid in by_stock}  # fmt: skip
        # 희석 사건 공시 — 국내만, 방향 − (3회차 C, 25.738). 수집 구간은 자사주와 같다(같은 공시 수집)
        희석점수 = {sid: (None if (f := bb.flag((dilutions or {}).get(sid, []), cutoff, 수집구간.get(sid, []))) is None
                          else -float(f)) if sid in 국내 else None for sid in by_stock}  # fmt: skip
        # 거래대금 급증 — 진단용, 수급 유입 조건의 20/60 비 (3회차 B, 25.738)
        급증점수 = {sid: fs_.flow_surge(view.values(sid)) for sid in by_stock}
        아는것 = [v for v in 중단.values() if v is not None]
        if 아는것:
            걸림.append(sum(아는것) / len(아는것))
        sue점수: dict[int, float | None] = {}
        for sid in by_stock:
            계열 = qe.quarter_series((quarters_by_stock or {}).get(sid, []), cutoff)
            sue점수[sid] = None if 계열 is None else sc.sue(계열, 1.0)
        # 금융업은 발생액에서 뺀다 — 영업현금흐름을 대출·트레이딩 자산 변동이 좌우해 이익의 질을 뜻하지 않는다
        # (docs/factors.md 12.2, 25.452). 업종 코드가 없으면(모름) 뺄 근거가 없어 남긴다
        금융 = {r["stock_id"] for r in 후보 if sectors.is_financial(r.get("sector_code"))}
        for sid in by_stock:
            snaps = snapshots_by_stock.get(sid, [])
            최근, 전년, _ = pit_financials(snaps, cutoff)
            v = year_values(snaps, cutoff, 최근)
            pv = year_values(snaps, cutoff, 전년)
            a = eq.accrual_ratio(v.get("net_income"), v.get("operating_cash_flow"), v.get("total_assets"),
                                 pv.get("total_assets"))  # fmt: skip
            발생액점수[sid] = None if a is None or sid in 금융 else -a
            # 같은 10-K 의 당기·전기 가중평균 주식수 — 분할이 소급 조정돼 있다 (25.450, 교차검증).
            # 해마다 다른 표지 주식수(dei·us-gaap 섞임)를 비교하던 것이 가짜 발행·소각을 만들었다
            n = si.net_issuance(v.get("shares_basic"), v.get("shares_basic_prev"), split_adjusted=True)
            발행점수[sid] = None if n is None else -n
            # 매출총이익/총자산 — 미국만, 금융업 제외 (3회차 E, 25.740)
            총이익점수[sid] = (None if sid in 국내 or sid in 금융
                               else gp.gross_profitability(v.get("gross_profit"), v.get("total_assets")))  # fmt: skip
        if ab is not None:
            ab.add_period(inputs, by_stock, 수익, 시장, {"sue": sue점수})
        for name in IC_NAMES:
            if name == "composite":
                점수 = {sid: sc.total_score(fs, weights).total for sid, fs in by_stock.items()}
            elif name == "sector_mom":
                점수 = {sid: 업종값.get(sid) for sid in by_stock}  # 업종 모멘텀 (25.439)
            elif name == "accrual":
                점수 = 발생액점수  # 이익의 질 — 부호를 뒤집어 "클수록 좋다" 로 맞춘다 (25.445)
            elif name == "net_issuance":
                점수 = 발행점수  # 순주식발행 — 부호를 뒤집는다 (25.446)
            elif name == "buyback":
                점수 = 매입점수
            elif name == "dilution":
                점수 = 희석점수
            elif name == "flow_surge":
                점수 = 급증점수
            elif name == "gross_profitability":
                점수 = 총이익점수
            elif name == "div_omission":
                점수 = 중단점수
            elif name == "reversal_1m":
                점수 = 반전점수
            elif name == "sue":
                점수 = sue점수  # 분기 실적 서프라이즈 — 클수록 좋다 (25.456)
            else:
                점수 = {sid: fs.get(name) for sid, fs in by_stock.items()}
            # **판정 계열은 시장 안에서 잰 IC 의 가중 평균**이다 (6회차 1, docs/backtest.md 2.5, 25.810).
            # 섞은 IC 는 참고로만 남긴다
            if name == "flow_surge_x_rev":
                # 거래대금 급증의 **증분 IC** — 단기 반전을 통제한다 (3회차 검증 2 조건, 25.742).
                # 거래량이 튀는 날은 가격도 튀어 반전과 겹친다. 통제해도 남아야 수급 유입 조건의 근거가 된다
                ic, 쓴수, 갈래 = fic.period_ic_by_market(급증점수, 수익, 시장, control=반전점수)
                섞음[name].append(fic.partial_period_ic_n(급증점수, 반전점수, 수익)[0])
            else:
                ic, 쓴수, 갈래 = fic.period_ic_by_market(점수, 수익, 시장)
                섞음[name].append(fic.period_ic_n(점수, 수익)[0])
            for 시장이름, (mic, _) in 갈래.items():
                시장별[name].setdefault(시장이름, []).append(mic)
            series[name].append(ic)
            # 후보 대비 — 켜는 기준의 "비지 않은 종목 60%" (25.442). 발생액은 **금융업을 뺀 후보** 대비다 —
            # 일부러 뺀 종목을 "값이 비었다" 로 세면 문턱이 금융 비중만큼 까다로워진다 (25.452)
            # 매출총이익도 금융업을 뺀 후보 대비다 (25.740)
            분모 = len(inputs) - (
                sum(1 for x in inputs if x.stock_id in 금융) if name in ("accrual", "gross_profitability") else 0
            )
            비율[name].append(쓴수 / 분모 if 분모 > 0 else 0.0)
    out = {
        name: replace(
            fic.summarize(v, 비율[name]),
            mixed=fic.summarize(섞음[name]),
            by_market=tuple(
                (시장이름, fic.summarize(xs), sum(1 for x in xs if x is None))
                for 시장이름, xs in sorted(시장별[name].items())
            ),
        )
        for name, v in series.items()
    }
    out["div_omission"] = replace(out["div_omission"], hit_rate=sum(걸림) / len(걸림) if 걸림 else None)
    return out


def ic_log(summaries: dict[str, fic.IcSummary]) -> dict[str, dict[str, Any]]:
    """실행 기록(step_log)에 남길 모양. 소수 넷째 자리까지"""
    def r(x: float | None) -> float | None:
        return None if x is None else round(x, 4)

    # 다중검정 기록 — 발굴 루프 이름만, 묶음 크기 고정. **판정에 쓰지 않는다** (6회차 2, 25.810)
    bh = fic.bh_record(summaries, LOOP_IC_NAMES)
    out: dict[str, dict[str, Any]] = {}
    for name, s in summaries.items():
        row: dict[str, Any] = {
            "months": s.months, "mean": r(s.mean), "t": r(s.t_stat), "first_half": r(s.first_half_mean),
            "second_half": r(s.second_half_mean), "coverage": r(s.coverage), "autocorr": r(s.autocorr),
            "verdict": s.verdict(), "passes": s.passes(), "hit_rate": r(s.hit_rate),
            # 시장을 섞은 예전 IC — 참고용 (25.810)
            "mixed_mean": r(s.mixed.mean) if s.mixed else None, "mixed_t": r(s.mixed.t_stat) if s.mixed else None,
            "by_market": {시장이름: {"months": ms.months, "mean": r(ms.mean), "t": r(ms.t_stat), "dropped": 뺌}
                          for 시장이름, ms, 뺌 in s.by_market},
        }  # fmt: skip
        if name in bh:
            row["fdr"] = {"p": r(float(bh[name]["p"])), "q": r(float(bh[name]["q"])), "bh_pass": bh[name]["bh_pass"],
                          "note": "기록용 — 켜는 기준에 쓰지 않음"}  # fmt: skip
        out[name] = row
    return out


#: 판단용 과거를 시작일보다 앞서 읽는 달력일. 가장 긴 창이 리스크 팩터의 600거래일(약 870 달력일)이다 (25.532)
WARMUP_CALENDAR_DAYS = 900


def long_entry(
    quality: float | None,
    value: float | None,
    closes: list[tuple[str, float]],
    equities: list[tuple[str, float]],
    listed_shares: int | None,
    threshold: float,
) -> bool:
    """장기 신호가 켜지는가. services/signals.long_term 과 같은 식에서 퀄리티·밸류 문턱만 바꾼다.

    closes 와 equities(발표일, 자본총계)는 t-1 까지만 들어와야 한다. 밴드도 그 창에서 만든다.
    """
    if quality is None or value is None or quality < threshold or value < threshold:
        return False
    series = sg.pbr_series(closes[-LONG_BAND_DAYS:], equities, listed_shares)
    band = sg.build_band(series)
    # **진입 분위를 글자로 적지 않는다.** 여기가 `band.p30` 이고 생산이 상수를 보면,
    # 문턱을 바꾼 날 백테스트는 **예전 전략을** 검증한다 (docs/infra.md 25.108)
    return band is not None and series[-1] <= band.at(sg.BAND_ENTRY_PERCENTILE)


def pit_equities(snapshots: list[dict[str, Any]], cutoff: str) -> list[tuple[str, float, int]]:
    """발표일이 cutoff 이하인 연간 자본총계 (발표일, 자본, 사업연도).

    같은 연도를 여러 번 내면 늦은 것까지 모두(시점 순).

    사업연도를 함께 넘겨 `known_equity_at` 이 옛 연도 정정 대신 가장 늦은 연도를 고르게 한다 (25.549)."""
    snapshots = pit_basis_rows(snapshots, cutoff)
    rows = [
        (str(r["as_of_date"]), float(r["values"]["total_equity"]), int(r["fiscal_year"]))
        for r in snapshots
        if str(r["as_of_date"]) <= cutoff and r["values"].get("total_equity") is not None
    ]
    _guard(cutoff, *(row[0] for row in rows))
    return sorted(rows)


def make_long_strategy(
    threshold: float,
    universe: list[dict[str, Any]],
    snapshots_by_stock: dict[int, list[dict[str, Any]]],
    max_holdings: int,
    eligible_at: EligibleAt | None = None,
    dividends_by_stock: dict[int, list[tuple[str, int, float]]] | None = None,
    benchmark_closes: dict[str, float] | None = None,
) -> bt.WeightsAt:
    """장기 신호로 사고 1년 들고 있는 전략. 동시에 max_holdings 까지, 새 진입은 밸류 점수 높은 순.

    매달 판단하되 이미 든 종목은 1년이 지나기 전에는 팔지 않는다. 비면 현금(수익 0)이다.
    """
    held: dict[int, str] = {}
    shares_of = {int(r["stock_id"]): r.get("listed_shares") for r in universe}

    def decide(t: str, view: bt.PriceView) -> dict[int, float]:
        today = date.fromisoformat(t)
        for sid, entered in list(held.items()):
            if (today - date.fromisoformat(entered)).days >= LONG_HOLD_DAYS:
                del held[sid]

        if len(held) < max_holdings:
            # 새로 살 때만 그 시점 유니버스로 자른다. 이미 든 종목은 1년 보유가 규칙이라
            # 도중에 유니버스에서 빠져도 팔지 않는다 (docs/backtest.md 7장)
            pool = universe
            if eligible_at is not None:
                allowed = eligible_at(view.cutoff)
                pool = [row for row in universe if row["stock_id"] in allowed]
            inputs = build_pit_inputs(
                pool, snapshots_by_stock, view, dividends_by_stock, benchmark_closes
            )
            by_stock: dict[int, dict[str, float | None]] = {}
            for r in sc.score_factors(inputs) if inputs else []:
                by_stock.setdefault(r.stock_id, {})[r.factor] = r.score
            ranked = sorted(
                (sid for sid in by_stock if sid not in held),
                key=lambda sid: (-(by_stock[sid].get("value") or 0), sid),
            )
            for sid in ranked:
                if len(held) >= max_holdings:
                    break
                fs = by_stock[sid]
                if long_entry(
                    fs.get("quality"), fs.get("value"), view.level_closes(sid),  # PBR 밴드는 가격 수준 (25.275)
                    pit_equities(snapshots_by_stock.get(sid, []), view.cutoff), shares_of.get(sid), threshold,
                ):
                    held[sid] = t

        return {sid: 1.0 / len(held) for sid in held} if held else {}

    return decide


def exposure(result: bt.BacktestResult) -> tuple[float, float]:
    """(평균 보유 종목 수, 한 종목이라도 든 달의 비율). 신호가 드물면 현금으로 쉬는 달이 많다."""
    if not result.rebalances:
        return 0.0, 0.0
    # 비중이 0 보다 큰 종목만 센다 (25.789, 교차검증) — 추세 필터 배수 0 이면 엔진이 `{종목: 0.0}` 을 남겨 전액
    # 현금인데 "투자" 로 셌다
    counts = [sum(1 for w in r.weights.values() if w > 0) for r in result.rebalances]
    return sum(counts) / len(counts), sum(1 for c in counts if c) / len(counts)


def exposure_fields(result: bt.BacktestResult) -> dict[str, Any]:
    """결과 행 `metrics_json` 에 함께 싣는 노출 (docs/infra.md 25.786, 백테스트 감사 #1).

    예전에는 평균 보유·투자한 달을 **로그에만** 찍어, 7장 판단 기준 2(투자한 달 50%·평균 보유 3종목)와
    기법 발굴 루프의 "표본 하한" 을 화면으로도 DB 로도 확인할 수 없었다. 첫 리밸런스부터 사는 벤치마크와
    13개월만 투자한 전략의 초과 CAGR 이 같은 칸에 나란히 섰다.
    """
    평균, 투자 = exposure(result)
    첫 = next((r.date for r in result.rebalances if any(w > 0 for w in r.weights.values())), None)
    return {"avg_holdings": round(평균, 2), "invested_share": round(투자, 4), "first_invested": 첫}


def exposure_warning(strategy: str, fields: dict[str, Any]) -> str | None:
    """7장 판단 기준 2 아래면 그 전략 행에 붙이는 말. 벤치마크는 기준이라 보지 않는다."""
    if strategy == "benchmark":
        return None
    투자, 평균 = fields["invested_share"], fields["avg_holdings"]
    if 투자 >= MIN_INVESTED_SHARE and 평균 >= MIN_AVG_HOLDINGS:
        return None
    첫 = fields.get("first_invested") or "없음"
    # 어느 전략의 말인지 적는다 — 화면은 모든 전략의 경고를 한 패널로 합친다. 기준 바로 아래(2.96)가 "3.0" 으로 보이지
    # 않게 내림 (25.789)
    # 부동소수 오차(0.57×100 = 56.99…)로 한 단위 더 내려가지 않게 먼저 반올림한 뒤 내린다 (25.790, 교차검증). 웹
    # `exposureText` 와 같은 규칙
    평균글 = f"{math.floor(round(평균 * 100, 6)) / 100:.2f}"
    return (
        f"{strategy}: 투자한 달 {math.floor(round(투자 * 100, 6))}%·평균 보유 {평균글}종목(첫 투자 {첫})"
        " — 판단 기준(투자한 달 50%·평균 보유 3종목) 아래라 이 수익은 표본이 적어 믿지 않는다 (docs/backtest.md 7장)"
    )


def load_benchmark_closes(client: TursoClient, country: str) -> dict[str, float]:
    """그 나라 벤치마크 지수의 {날짜: 종가} (docs/factors.md 11.3).

    **지수 코드를 여기서 다시 적지 않는다** — `jobs/metrics.BENCHMARK` 를 가져다 쓴다.
    25.65 가 코드를 손으로 잘못 적어 두었던 항목이다. 적재 경로
    (`jobs/scores.load_benchmark_closes`)와 같은 지수를 본다.

    **시점 자르기는 부르는 쪽이 한다** — `build_pit_inputs` 가 cutoff 로 자른다.
    여기서 전 구간을 들고 있는 것은 리밸런스마다 다시 읽지 않기 위해서다.
    """
    from batch.jobs.metrics import BENCHMARK

    코드 = BENCHMARK.get(country)
    if not 코드:
        return {}
    try:
        rs = client.execute(
            "SELECT date, close FROM index_prices WHERE index_code = ? ORDER BY date", [코드]
        )
    except Exception as exc:  # noqa: BLE001 — 지수가 없어도 나머지 팩터는 낸다
        log.warning("지수를 읽지 못해 백테스트의 잔차 변동성이 빕니다: %s", exc)
        return {}
    return {str(r[0]): float(r[1]) for r in rs.rows}


def load_pit_dividends(
    client: TursoClient, country: str
) -> dict[int, list[tuple[str, int, float]]]:
    """종목별 배당 이력 **(접수일, 사업연도, 현금배당총액)**, 접수일 순 (docs/factors.md 11.1).

    **접수일을 함께 읽는 것이 요지다.** `as_of_date` 는 "이 날부터 알 수 있었다" 이므로
    시점 판정은 그때까지 접수된 행만 본다(migrations/0013). 접수일을 모르는 행은
    **아예 싣지 않는다** — 언제부터 알 수 있었는지 모르는 값을 과거에 놓으면 look-ahead 다.

    적재 경로(`jobs/scores.load_dividends`)와 **같은 규칙**이되 그쪽은 한 시점만,
    이쪽은 되감을 수 있게 이력을 통째로 들고 있는다(재무 스냅샷과 같은 모양).
    """
    나온것: dict[int, list[tuple[str, int, float]]] = {}
    try:
        rows = client.execute(
            "SELECT d.stock_id, d.as_of_date, d.fiscal_year, d.report_year, d.cash_dividend_total"
            " FROM stock_dividends d JOIN stocks s ON s.id = d.stock_id"
            " WHERE s.country = ? AND d.as_of_date IS NOT NULL"
            " ORDER BY d.stock_id, d.as_of_date, d.report_year",
            [country],
        ).dicts()
    except Exception as exc:  # noqa: BLE001 — 배당이 없어도 나머지 팩터는 낸다
        log.warning("배당 이력을 읽지 못해 백테스트의 배당수익률이 빕니다: %s", exc)
        return {}
    for r in rows:
        나온것.setdefault(int(r["stock_id"]), []).append(
            (str(r["as_of_date"]), int(r["fiscal_year"]), float(r["cash_dividend_total"] or 0))
        )
    return 나온것


def pit_dividend(history: list[tuple[str, int, float]], cutoff: str) -> float | None:
    """`cutoff` 까지 접수된 것 중 **가장 최근 사업연도**의 현금배당총액.

    같은 사업연도를 여러 보고서가 실으면 뒤에 접수된 것이 이긴다 —
    `load_pit_dividends` 가 접수일 순으로 담아 두므로 나중 것이 앞을 덮는다.

    아무것도 못 봤으면 None(모름)이다. **0(무배당)과 다르다**(docs/infra.md 25.74).
    """
    행 = pit_dividend_row(history, cutoff)
    return 행[1] if 행 else None


def pit_dividend_row(history: list[tuple[str, int, float]], cutoff: str) -> tuple[int, float] | None:
    """`pit_dividend` 가 고른 (사업연도, 현금배당총액). 미국 무배당 판정이 연도를 본다 (25.561)."""
    최신해: int | None = None
    값: float | None = None
    for as_of, fiscal_year, total in history:
        if as_of > cutoff:
            continue
        if 최신해 is None or fiscal_year >= 최신해:
            최신해, 값 = fiscal_year, total
    return None if 최신해 is None or 값 is None else (최신해, 값)


def latest_10k_year(snapshots: list[dict[str, Any]], cutoff: str) -> int | None:
    """`cutoff` 에 배당과 함께 보이는 가장 최근 10-K 사업연도 (25.561·25.565).

    미국 스냅샷의 `as_of_date` 는 제출 **다음 거래일**이라 배당 행과 같은 날부터 보인다 — 그래서 `<=` 다.
    """
    연도 = [int(r["fiscal_year"]) for r in pit_basis_rows(snapshots, cutoff) if str(r["as_of_date"]) <= cutoff]
    return max(연도) if 연도 else None


#: 국내 시장. 나머지는 미국이다 — 미국만 10-K 기준 무배당 채움을 한다 (25.561)
KR_MARKETS = frozenset({"KOSPI", "KOSDAQ", "KONEX"})


def build_pit_inputs(
    universe: list[dict[str, Any]],
    snapshots_by_stock: dict[int, list[dict[str, Any]]],
    view: bt.PriceView,
    dividends_by_stock: dict[int, list[tuple[str, int, float]]] | None = None,
    benchmark_closes: dict[str, float] | None = None,
) -> list[sc.StockInput]:
    """t-1 창에서 팩터 입력을 만든다. jobs/scores.py 의 build_inputs 와 같은 식이되
    재무는 스냅샷에서, 리스크는 가격에서 그 자리에서 낸다."""
    cutoff = view.cutoff
    dividends_by_stock = dividends_by_stock or {}
    benchmark_closes = benchmark_closes or {}
    inputs: list[sc.StockInput] = []
    # **지수는 한 번만 자르고 한 번만 읽는다** (docs/infra.md 25.625, 교차검증). 25.622 가 종목마다 지수 전체를
    # 다시 걸러 날짜를 파싱·정렬해, 한 번에 1ms → 4.5ms 로 늘었다(종목 800 × 리밸런스 61 × 호출 경로 8 이면 20분 넘게)
    지수_잘림 = {d: c for d, c in benchmark_closes.items() if d <= cutoff}
    지수_점 = [m.PricePoint(date=date.fromisoformat(d), close=c) for d, c in sorted(지수_잘림.items())]

    for row in universe:
        sid = int(row["stock_id"])
        closes = view.closes(sid)
        if not closes:
            continue

        current, previous, three_ago = pit_financials(snapshots_by_stock.get(sid, []), cutoff)
        cur_v = current["values"] if current else {}
        prev_v = previous["values"] if previous else {}
        old_v = three_ago["values"] if three_ago else {}
        # **표시통화가 다르면** 해끼리 견주지 않고 시총과 나누지 않는다 — 운영(`scores.build_inputs`)과 같은 규칙
        # (25.915·25.919)
        if previous is not None and not sc.same_currency(cur_v, prev_v):
            previous, prev_v = None, {}
        if three_ago is not None and not sc.same_currency(cur_v, old_v):
            three_ago, old_v = None, {}
        통화다름 = bool(cur_v.get("currency") and row.get("currency") and cur_v["currency"] != row["currency"])
        밸류_v = {} if 통화다름 else cur_v

        # 시총은 **가격 수준**으로 (docs/infra.md 25.275). 미국 수정종가로 재면 미래 배당만큼 작다
        수준 = view.level_closes(sid)
        last_close = 수준[-1][1] if 수준 else closes[-1][1]
        shares = row.get("listed_shares")
        market_cap = last_close * shares if shares else None

        merged: dict[str, float | None] = {}
        merged.update(sc.value_metrics(
            _num(밸류_v.get("net_income")), _num(밸류_v.get("total_equity")),
            _num(밸류_v.get("revenue")), market_cap,
        ))
        # 배당수익률 (docs/factors.md 11.1). **적재 경로와 같은 지표를 같은 시점 규칙으로** 낸다 —
        # 그러지 않으면 백테스트가 운영과 다른 밸류를 재고, 11.0 이 약속한
        # "넣기 전·후 비교" 가 구조적으로 불가능해진다 (docs/infra.md 25.92)
        배당행 = pit_dividend_row(dividends_by_stock.get(sid, []), cutoff)
        배당 = 배당행[1] if 배당행 else None
        # 미국은 적재와 같은 규칙으로 무배당·배당 중단을 0 으로 (25.561 — 25.557 이 적재에만 넣어 둘이 갈렸다).
        # 배당 이력을 하나도 못 읽었으면 채우지 않는다(모름). 스냅샷 `as_of_date` 는 **제출 다음 거래일**이라 배당 행과
        # 같은 날 보인다 — `<=` 다. `<` 로 두어 운영보다 하루 늦게 0 이 됐다 (25.565, 교차검증)
        if dividends_by_stock and str(row["market"]) not in KR_MARKETS:
            배당 = sc.us_dividend_or_zero(
                배당, 배당행[0] if 배당행 else None, latest_10k_year(snapshots_by_stock.get(sid, []), cutoff)
            )
        merged["dividend_yield"] = sc.dividend_yield(배당, market_cap)

        profitable, observed = (
            stability_from(snapshots_by_stock.get(sid, []), cutoff, int(current["fiscal_year"]))
            if current else (None, None)
        )
        merged.update(sc.quality_metrics(
            _num(cur_v.get("net_income")), _num(cur_v.get("total_assets")),
            _num(cur_v.get("total_equity")), _num(cur_v.get("operating_income")),
            _num(cur_v.get("revenue")), total_liabilities=_num(cur_v.get("total_liabilities")),
            profitable_years=profitable, observed_years=observed,
        ))
        merged.update(sc.growth_metrics(
            _num(cur_v.get("revenue")), _num(prev_v.get("revenue")),
            _num(cur_v.get("operating_income")), _num(prev_v.get("operating_income")),
            _num(old_v.get("revenue")), _num(prev_v.get("total_assets")),
        ))
        # 재무제표 지표 (docs/factors.md 10.2). 전년 스냅샷이 없으면 cur_v/prev_v 가 비어 None 이 된다
        merged.update(sc.statement_metrics(cur_v if current else None, prev_v if previous else None))
        close_values = [c for _d, c in closes]
        merged.update(sc.momentum_metrics(close_values))
        # 2026-09-17 추가 지표. 적재 경로(jobs/scores)와 같은 함수다
        # **날짜도 넘긴다** (2026-09-22, docs/infra.md 25.103). 구멍을 사이에 둔 쌍을
        # 하루치 수익률로 세지 않는다 — `closes` 가 (날짜, 종가) 쌍이라 그대로 있다
        merged.update(
            sc.price_series_metrics(close_values, view.values(sid), [d for d, _c in closes])
        )

        # 잔차 변동성 (docs/factors.md 11.3). **적재 경로와 같은 함수로 날짜를 맞춘다** —
        # `PriceView` 는 cutoff 뒤를 아예 안 들고 있고(services/backtest), 지수도 여기서
        # cutoff 까지만 잘라 넘기므로 미래를 볼 것이 없다
        merged["idio_volatility"] = sc.idio_volatility(*sc.aligned_returns(dict(closes), 지수_잘림))
        # 운영과 같은 지표로 — 지수(cutoff 까지)와 무위험수익률을 넘겨 샤프·소르티노·베타도 낸다 (docs/infra.md 25.622)
        # 같은 종목·같은 기준일의 리스크 지표는 전략이 달라도 같다 — 한 번만 낸다 (25.625). 전략 6개 + IC + 비용 0 이
        # 같은 리밸런스에서 입력을 다시 만들어 8번 계산했다
        # 가격 계열의 모양(길이·첫 날·마지막 값)까지 열쇠에 — 다른 가격으로 같은 종목·날짜를 부르면(테스트) 섞이지 않게
        열쇠 = (sid, cutoff, _RISK_FREE, len(closes), closes[0][0], closes[-1][1], len(지수_점))
        if 열쇠 not in _위험_캐시:
            _위험_캐시[열쇠] = risk_from_prices(
                closes, risk_free_annual=_RISK_FREE, benchmark_points=지수_점,
                # 창 끝은 cutoff 가 아니라 **대부분의 종목이 들어온 날** (25.723, 교차검증 — 한 종목만 있는 날이
                # cutoff 면 나머지가 None)
                until=date.fromisoformat(str(view.broad_last() or cutoff)[:10]),
            )
        merged.update(_위험_캐시[열쇠])

        inputs.append(sc.StockInput(
            stock_id=sid, market=str(row["market"]), sector=row.get("sector") or None, metrics=merged,
        ))
    return inputs


#: 이번 실행의 무위험수익률(연, 소수). `run` 이 전략을 돌리기 전에 설정에서 읽어 둔다 — 리스크 팩터의 샤프·소르티노가
#: 운영(`jobs/metrics`)과 같은 값을 쓰게 (25.622). 설정 하나뿐이라 과거 시점의 금리가 아니다(알고 둔다)
_RISK_FREE: float | None = None

#: (종목, 기준일, 무위험수익률) → 리스크 지표. `run` 이 시작할 때 비운다 (25.625)
_위험_캐시: dict[tuple, dict[str, float | None]] = {}


def risk_from_prices(
    closes: list[tuple[str, float]],
    benchmark: dict[str, float] | None = None,
    risk_free_annual: float | None = None,
    *,
    benchmark_points: list[m.PricePoint] | None = None,
    until: date | None = None,
) -> dict[str, float | None]:
    """리스크 팩터를 t-1 까지의 가격으로 그 자리에서 낸다. docs/metrics.md 식 그대로.

    performance_metrics 에는 과거 시점의 값이 없어서다(docs/backtest.md 2.3).
    **운영과 같은 지표를 낸다** (docs/infra.md 25.622, 감사). 예전에는 무위험수익률과 지수를 넘기지 않아
    샤프·소르티노·베타가 늘 비었고, 리스크 팩터(지표 10개 중 5개 이상)가 운영과 **다른 점수**를 검증하고 있었다.
    `benchmark` 는 부르는 쪽이 cutoff 까지 잘라 넘긴다(미래를 볼 것이 없다).
    """
    recent = closes[-RISK_LOOKBACK_DAYS:]
    모든점 = [m.PricePoint(date=date.fromisoformat(d), close=c) for d, c in recent]
    # 미리 읽어 둔 지수 점이 있으면 그것을 이분 탐색으로 자른다(25.625). 없으면 dict 를 그 자리에서 읽는다
    전체 = benchmark_points if benchmark_points is not None else [
        m.PricePoint(date=date.fromisoformat(d), close=c) for d, c in sorted((benchmark or {}).items())
    ]
    # **운영과 같은 창 고르기** (docs/infra.md 25.710, 교차검증 재현). 운영은 창 시작·끝을 채웠는지 보고(25.703) 3Y 가
    # 비면
    # 1Y 로 내려간다(`pick_window`). 백테스트는 마지막 750행을 늘 "3Y" 로 계산해, 상장 2.3~3년 종목에서 운영은 1Y
    # 값·백테스트는
    # 3Y 값으로 갈라졌다. 끝은 cutoff 까지 자른 계열의 마지막 날이다
    points = 모든점
    computed = m.Metrics(window="3Y", data_points=0)
    for 창 in m.RISK_WINDOWS:
        if not 모든점:
            break
        # 창 끝은 **그 시점(cutoff)** 이다 (25.717, 교차검증) — 종목의 마지막 시세로 잡으면 끝 검사가 걸리지 않아,
        # 시세가 두 달 전에
        # 끊긴(정지) 종목이 운영에서는 값이 없고 백테스트에서만 리스크 점수를 받았다. `until` 이 없으면(옛 호출)
        # 마지막 시세
        끝 = until or 모든점[-1].date
        시작일 = 끝 - timedelta(days=metrics_job.WINDOW_DAYS[창])
        points = [p for p in 모든점 if p.date >= 시작일]
        market = 전체[bisect.bisect_left(전체, points[0].date, key=lambda p: p.date):] if points else []
        computed = m.compute(
            창, points,
            market=market if len(market) >= metrics_job.MIN_BENCHMARK_POINTS else None,
            benchmark=None, risk_free_annual=risk_free_annual, span=(시작일, 끝),
        )  # fmt: skip
        if computed.cagr is not None or computed.mdd is not None:
            break
    return sc.risk_metrics(
        mdd=computed.mdd, volatility_ann=computed.volatility_ann,
        sharpe=computed.sharpe, sortino=computed.sortino, beta=computed.beta,
        mdd_recovery_days=computed.mdd_recovery_days, cagr_value=computed.cagr,
        unrecovered_rows=sc.elapsed_rows_since(
            [p.date.isoformat() for p in points],
            computed.mdd_trough_date.isoformat() if computed.mdd_trough_date else None,
        ),
    )


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


# ----------------------------------------------------------------------
# 입력 읽기
# ----------------------------------------------------------------------


def load_universe(client: TursoClient, country: str) -> tuple[list[dict[str, Any]], list[str]]:
    """오늘 유니버스에 든 종목과, 시점 판정에 필요한 정적 정보.

    `listed_date` 는 국내는 거래소가 준 실제 상장일이고 미국은 us_shares 가 채운
    관측 첫 거래일이다. 둘 다 **과거의 사실**이라 시점 판정에 써도 미래를 보지 않는다.
    """
    rs = client.execute(
        "SELECT s.id AS stock_id, s.market, s.sector, s.sector_code, s.listed_shares, s.listed_date, u.snapshot_date,"
        " s.currency"
        " FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
        " WHERE u.included = 1 AND s.country = ?"
        f"   AND u.snapshot_date = {db.latest_snapshot_sql()}",
        [country, country],
    )
    return rs.dicts(), []


def pit_stocks(universe: list[dict[str, Any]], observed: bool = False) -> list[pit.Stock]:
    """유니버스 행을 시점 판정 입력으로. 정적 결격은 이미 오늘 유니버스에서 걸러졌다.

    시점 필터는 이 목록에서 **빼기만** 한다. 과거에는 문턱을 넘었지만 오늘은 못 넘는 종목(줄어든 회사)은
    처음부터 목록에 없다 — 재무도 오늘 유니버스 종목만 받는다(`financials.py`).
    docs/backtest.md 1.3 남은 한계, docs/infra.md 25.283.
    """
    return [
        pit.Stock(
            stock_id=int(row["stock_id"]),
            listed_date=str(row["listed_date"]) if row.get("listed_date") else None,
            listed_shares=int(row["listed_shares"]) if row.get("listed_shares") else None,
            listed_date_observed=observed,
        )
        for row in universe
    ]


def history_starts(stocks: list[pit.Stock], series_by_stock: dict[int, pit.Series]) -> tuple[str, ...]:
    """이력이 시작한 날들 — **판정이 실제로 비교하는 값**(관측 상장일)이 몰린 곳 (docs/infra.md 25.403·25.418·25.422).

    관측값만 모은다: 미국 `listed_date`(DB 전체의 첫 가격일), 상장일이 빈 종목의 불러온 첫 거래일.
    25.418 은 불러온 가격의 첫날로 셌는데, 백테스트는 `since` 부터만 읽고 미국 `listed_date` 는 DB 전체의 최솟값이라
    `--years` 가 백필 깊이보다 짧으면 둘이 어긋나 첫 몇 달이 다시 "상장 1년 미만" 으로 잘렸다(교차검증 반박).
    실제 상장일(국내)은 섞지 않는다 — 1970년대 날짜가 가장자리를 끌고 간다.
    """
    # 두 무리를 **따로** 구해 합친다 (docs/infra.md 25.431, 교차검증 부분 반박). 한 목록에 섞으면 상장일이 빈 종목이
    # 10개 미만일 때 그 첫날(불러온 창의 시작)이 무리도, 가장 이른 날도 못 되어 다시 "상장 1년 미만" 으로 잘렸다.
    # 따로 구하면 두 번째 무리의 가장 이른 날(= 창의 시작)이 늘 들어간다
    관측: list[str] = []
    빈것: list[str] = []
    for st in stocks:
        if st.listed_date_observed and st.listed_date:
            관측.append(st.listed_date)
        elif not st.listed_date and (d := series_by_stock.get(st.stock_id, pit.Series()).first_date()):
            빈것.append(d)
    # 빈 무리의 "가장 이른 날" 은 **불러온 창의 시작**이어야 한다 (25.436, 교차검증). 빈 무리가 전부 신규 상장이면
    # 그 무리의 가장 이른 날이 곧 실제 상장일이라, 25.431 은 신규 상장 종목을 이력 가장자리로 잘못 봐 통과시켰다
    창시작 = min((d for se in series_by_stock.values() if (d := se.first_date())), default=None)
    빈_무리 = pit.history_starts_of(빈것 + [창시작]) if 빈것 and 창시작 else ()
    return tuple(sorted(set(pit.history_starts_of(관측)) | set(빈_무리)))


def pit_series(
    prices: dict[int, dict[str, float]], turnover: dict[int, dict[str, float]]
) -> dict[int, pit.Series]:
    """종목별 시계열을 시점 판정이 쓰는 모양으로 한 번만 만든다(리밸런스마다 다시 만들지 않는다)."""
    out: dict[int, pit.Series] = {}
    for stock_id, by_date in prices.items():
        dates = sorted(by_date)
        out[stock_id] = pit.Series(dates=dates, closes=dict(by_date), turnover=turnover.get(stock_id, {}))
    return out


def load_prices(
    client: TursoClient, stock_ids: list[int], since: str, chunk: int = 100
) -> dict[int, dict[str, float]]:
    """종목별 {날짜: 종가}. 종목을 묶어 요청 수를 줄인다."""
    prices, _ = load_prices_and_turnover(client, stock_ids, since, chunk, with_turnover=False)
    return prices


def load_prices_and_turnover(
    client: TursoClient,
    stock_ids: list[int],
    since: str,
    chunk: int = 100,
    with_turnover: bool = True,
    levels: dict[int, dict[str, float]] | None = None,
) -> tuple[dict[int, dict[str, float]], dict[int, dict[str, float]]]:
    """가격과 거래대금을 **한 번에** 읽는다.

    같은 행에서 둘 다 나오므로 따로 읽으면 같은 5년치를 두 번 가져오게 된다.
    수익률은 수정주가(adj_close)를 쓴다.

    거래대금은 **저장된 `p.value` 를 먼저 쓰고, 없으면 원 종가×거래량**이다 —
    `jobs/scores.load_series` 와 **같은 규칙**이다(2026-09-21, docs/infra.md 25.99).
    조정하지 않은 값을 쓰는 것은 그대로다: 거래대금은 "그날 얼마가 오갔나" 라서
    수정주가로 환산하면 뜻이 달라진다.

    예전에는 **늘** 종가×거래량이었다. 그래서 백테스트가 운영과 **다른 값**으로
    Amihud 비유동성을 내고 **다른 종목을 유니버스에 넣었다.**

    - 국내: `p.value` 는 거래소가 준 실제 거래대금이다. 종가×거래량은 장중 가격을
      무시한 근사라 종목마다 날마다 다르게 어긋난다
    - 미국: `p.value` 에 이미 `estimate_turnover(close, volume)` 가 들어 있다
      (`jobs/daily._store_us_bars`) — **바뀌는 것이 없다**
    """
    prices: dict[int, dict[str, float]] = {}
    turnover: dict[int, dict[str, float]] = {}
    # 기준일 하나 몫을 남긴다. D1 은 질의당 파라미터 100개라 100종목 + 1 이 막힌다 (infra 25.5)
    chunk = min(chunk, db.in_chunk(reserve=1))
    for start in range(0, len(stock_ids), chunk):
        ids = stock_ids[start : start + chunk]
        placeholders = ", ".join(["?"] * len(ids))
        rs = client.execute(
            "SELECT stock_id, date, COALESCE(adj_close, close) AS px, close, volume, value FROM prices"
            f" WHERE stock_id IN ({placeholders}) AND date >= ? AND close IS NOT NULL",
            [*ids, since],
        )
        for row in rs.dicts():
            stock_id = int(row["stock_id"])
            day = str(row["date"])
            prices.setdefault(stock_id, {})[day] = float(row["px"])
            if levels is not None and row["close"] is not None:
                # 가격 수준(분할만 조정된 원 종가). 시총·PBR 이 쓴다 (docs/infra.md 25.275)
                levels.setdefault(stock_id, {})[day] = float(row["close"])
            if with_turnover:
                거래대금 = row["value"]
                if 거래대금 is None and row["volume"] is not None and row["close"] is not None:
                    거래대금 = float(row["close"]) * float(row["volume"])
                if 거래대금 is not None:
                    turnover.setdefault(stock_id, {})[day] = float(거래대금)
    return prices, turnover


def load_buyback_events(
    client: TursoClient, country: str
) -> tuple[dict[int, list[str]], dict[int, list[tuple[str, str]]]]:
    """(종목 → 자기주식취득결정 공시 접수일들, 종목 → 그 종목을 다 받은 전 종목 수집 구간들)
    (docs/factors.md 12.2, 25.482·25.484·25.487).

    **구간은 종목마다다** (25.487, 교차검증). `disclosures_kr --all` 실행이 step_log 에 남긴 `covered_ids`
    (호출이 성공했고 쪽 상한에 안 잘린 종목)에게만 [실행일 − days, 실행일] 을 준다. 예전(25.484)에는 DART 고유번호가
    있는 종목 전부에 주어, 수집 당시 유니버스 밖이었던 종목이 수집된 적 없이 0 이 됐다.
    `covered_ids` 가 없는 옛 기록은 쓰지 않는다."""
    rows = client.execute(BUYBACK_EVENTS_SQL, [country, "%자기주식%"]).dicts()
    events: dict[int, list[str]] = {}
    for r in rows:
        if bb.is_buyback(str(r["title"])):
            events.setdefault(int(r["stock_id"]), []).append(str(r["disclosed_at"]))
    windows: dict[int, list[tuple[str, str]]] = {}
    for r in client.execute(
        "SELECT trade_date, step_log FROM batch_runs WHERE job_name = 'disclosures_kr'"
        " AND status IN ('success', 'partial')"
    ).dicts():
        try:
            log_ = json.loads(r["step_log"] or "{}")
            if not (log_.get("all") and r["trade_date"] and log_.get("covered_ids")):
                continue
            끝 = date.fromisoformat(str(r["trade_date"])[:10])
            구간 = ((끝 - timedelta(days=int(log_["days"]))).isoformat(), 끝.isoformat())
            for sid in log_["covered_ids"]:
                windows.setdefault(int(sid), []).append(구간)
        except (TypeError, ValueError, KeyError):
            continue
    # **시장 전체 B 공시를 다 받은 날은 국내 전 종목을 덮는다** (25.1014, 14회차 교차검증). 자사주·희석 공시는
    # 주요사항보고(B)로 온다(10-08 실측: 1년 동안 자사주 결정 B 43~130건/달, B 밖 0~10건).
    # 덮은 날은 `disclosure_reaction.collect` 가 하루마다 남긴다
    if country == "KR":
        시장구간 = coverage_ranges([str(r[0]) for r in client.execute(COVERAGE_B_SQL).rows])
        if 시장구간:
            for r in client.execute("SELECT id FROM stocks WHERE country = 'KR'").rows:
                windows.setdefault(int(r[0]), []).extend(시장구간)
    return events, windows


#: 종목에 아직 잇지 못한 공시(stock_id 없음)도 DART 고유번호로 이어 읽는다 (25.1014). 시장 전체 수집은 잇지 못한 행을
#: 버리지 않는다 — 신규 상장이 `stocks` 에 들어오기 전 공시가 빠지면 덮은 날인데 0 이 된다
BUYBACK_EVENTS_SQL = (
    "SELECT s.id AS stock_id, d.title, d.disclosed_at FROM disclosures d JOIN stocks s"
    " ON s.id = d.stock_id OR (d.stock_id IS NULL AND s.dart_corp_code = d.corp_code)"
    " WHERE s.country = ? AND d.title LIKE ?"
)
DILUTION_EVENTS_SQL = (
    "SELECT s.id AS stock_id, d.title, d.disclosed_at FROM disclosures d JOIN stocks s"
    " ON s.id = d.stock_id OR (d.stock_id IS NULL AND s.dart_corp_code = d.corp_code)"
    " WHERE s.country = ? AND (d.title LIKE '%증자%' OR d.title LIKE '%사채%')"
)
COVERAGE_B_SQL = "SELECT day FROM disclosure_coverage WHERE kind = 'B' ORDER BY day"


def coverage_ranges(days: list[str]) -> list[tuple[str, str]]:
    """덮은 날들 → 하루씩 이어지는 (시작, 끝) 구간들. 하루라도 빠지면 끊는다 — 그 날 공시를 모르기 때문이다."""
    out: list[list[str]] = []
    for d in sorted(set(days)):
        if out and d == (date.fromisoformat(out[-1][1]) + timedelta(days=1)).isoformat():
            out[-1][1] = d
        else:
            out.append([d, d])
    return [(a, b) for a, b in out]


def load_dilution_events(client: TursoClient, country: str) -> dict[int, list[str]]:
    """종목 → 희석 사건 공시 접수일들 (3회차 C, docs/factors.md 12.2, 25.738).

    수집 구간은 `load_buyback_events` 의 것을 쓴다 — 같은 공시 수집(`disclosures_kr --all`)이다."""
    rows = client.execute(DILUTION_EVENTS_SQL, [country]).dicts()
    events: dict[int, list[str]] = {}
    for r in rows:
        if dil.is_dilution(str(r["title"])):
            events.setdefault(int(r["stock_id"]), []).append(str(r["disclosed_at"]))
    return events


def load_quarter_snapshots(client: TursoClient, country: str) -> dict[int, list[dict[str, Any]]]:
    """SUE 의 입력 — 분기·반기·사업보고서 스냅샷 전부, **연결·별도 둘 다** (docs/factors.md 12.2, 25.456·25.461).
    시점 필터와 기준 고르기는 `quarterly_earnings.quarter_series` 가 기준일마다 한다 — 여기서 종목 전체로 고르면
    미래에 처음 낸 연결 공시가 과거 기준일의 별도 계열을 지운다."""
    rs = client.execute(
        "SELECT f.stock_id, f.as_of_date, f.fiscal_year, f.report_code, f.payload, f.receipt_no,"
        " f.consolidated AS consolidated_picked_at_cutoff,"  # = db.FINANCIAL_BASIS_AT_CUTOFF
        " fin.currency AS ledger_currency"  # payload 에 통화 키가 없다 — 원장에서 잇는다 (25.919·25.920)
        " FROM financial_snapshots f JOIN stocks s ON s.id = f.stock_id"
        " LEFT JOIN financials fin ON fin.stock_id = f.stock_id AND fin.fiscal_year = f.fiscal_year"
        "  AND fin.report_code = f.report_code AND fin.consolidated = f.consolidated"
        " WHERE s.country = ? AND f.report_code IN (?, ?, ?, ?)",
        [country, ANNUAL_REPORT_CODE, *qe.QUARTER_OF],
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rs.dicts():
        try:
            values = json.loads(row["payload"])
        except (TypeError, ValueError):
            values = {}
        if not values.get("currency") and row.get("ledger_currency"):
            values["currency"] = str(row["ledger_currency"])
        out.setdefault(int(row["stock_id"]), []).append({
            "as_of_date": str(row["as_of_date"]), "fiscal_year": int(row["fiscal_year"]),
            "report_code": str(row["report_code"]), "values": values, "receipt_no": str(row["receipt_no"]),
            "consolidated": bool(row["consolidated_picked_at_cutoff"]),
        })  # fmt: skip
    return out


def load_snapshots(client: TursoClient, country: str) -> dict[int, list[dict[str, Any]]]:
    """연간 스냅샷 전부, **연결·별도 둘 다**. 기준은 `pit_basis_rows` 가 기준일마다 고르고(25.860), 시점 필터는 각
    함수가 한다."""
    rs = client.execute(
        "SELECT f.stock_id, f.as_of_date, f.fiscal_year, f.payload,"
        " f.consolidated AS consolidated_picked_at_cutoff,"  # = db.FINANCIAL_BASIS_AT_CUTOFF
        # **통화를 원장에서 잇는다** (docs/infra.md 25.919, 11회차 검증 A·B). 스냅샷 payload 에는 통화 키가 없다(운영
        # 50,973행 전부,
        # 2026-10-03) — 같은 (종목, 사업연도, 보고서, 기준) 의 `financials.currency` 를 붙인다. 쓰기 없이 읽기만
        " fin.currency AS ledger_currency"
        " FROM financial_snapshots f JOIN stocks s ON s.id = f.stock_id"
        " LEFT JOIN financials fin ON fin.stock_id = f.stock_id AND fin.fiscal_year = f.fiscal_year"
        "  AND fin.report_code = f.report_code AND fin.consolidated = f.consolidated"
        " WHERE s.country = ? AND f.report_code = ?",
        [country, ANNUAL_REPORT_CODE],
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rs.dicts():
        try:
            values = json.loads(row["payload"])
        except (TypeError, ValueError):
            values = {}
        if not values.get("currency") and row.get("ledger_currency"):
            values["currency"] = str(row["ledger_currency"])
        out.setdefault(int(row["stock_id"]), []).append({
            "as_of_date": str(row["as_of_date"]), "fiscal_year": int(row["fiscal_year"]), "values": values,
            "consolidated": bool(row["consolidated_picked_at_cutoff"]),
        })  # fmt: skip
    return out


def risk_free_with_warning(client: TursoClient, country: str, warnings: list[str]) -> float | None:
    """백테스트 샤프·소르티노에 쓸 무위험수익률 (docs/infra.md 25.299). 없으면 경고를 남기고 None."""
    무위험 = metrics_job.risk_free_for(client, country)
    if 무위험 is None:
        warnings.append(WARN_NO_RISK_FREE)
    return 무위험


def has_delisted(client: TursoClient, country: str, candidate_ids: list[int]) -> bool:
    """**백테스트 후보에** 상장폐지 종목이 들어 있는가 (docs/infra.md 25.402).

    예전에는 `stocks` 표 **전체**에서 `delisted` 가 하나라도 있는지만 봤다. 그런데 후보는 최신 유니버스
    스냅샷의 `included = 1`(`load_universe`)이고, 스냅샷은 `status = 'active'` 만 넣는다 — 폐지 종목은
    후보에 절대 들어오지 않는다. 25.361 이후 폐지가 한 종목이라도 적히면 "생존편향" 경고가 **거짓으로 꺼졌다.**
    그래서 후보 안에서만 센다.
    """
    rs = client.execute("SELECT id FROM stocks WHERE country = ? AND status = 'delisted'", [country])
    폐지 = {int(r[0]) for r in rs.rows}
    return any(sid in 폐지 for sid in candidate_ids)


# ----------------------------------------------------------------------
# 적재
# ----------------------------------------------------------------------

_RUN_COLS = (
    "market, strategy, start_date, end_date, top_n, rebalance, costs_json, rebalances,"
    " final_equity, turnover_avg, excess_cagr, win_rate, metrics_json, warnings_json,"
    " group_id, calc_version, scoring_calc_version, created_at"
)


def store_run(
    client: TursoClient, market: str, strategy: str, result: bt.BacktestResult,
    summary: bt.Summary, top_n: int, group_id: str, warnings: list[str], now: str,
    운판정: dict | None = None,
) -> int:
    노출 = exposure_fields(result)
    노출말 = exposure_warning(strategy, 노출)
    row = (
        market, strategy, result.curve[0][0], result.curve[-1][0], top_n, "monthly",
        json.dumps(asdict(result.costs)), len(result.rebalances),
        summary.final_equity, summary.turnover_avg, summary.excess_cagr, summary.win_rate,
        json.dumps({**asdict(summary.metrics), **노출, **({"luck": 운판정} if 운판정 else {})}, default=str),
        json.dumps(sorted(set(result.warnings + warnings + ([노출말] if 노출말 else []))), ensure_ascii=False),
        # 판 **둘**을 남긴다 (2026-09-22, docs/infra.md 25.101).
        # `calc_version` 은 엔진 판(리밸런스·비용·곡선), `scoring_calc_version` 은
        # 점수 계산식이다. 백테스트 결과를 실제로 가르는 것은 **뒤쪽**이다 —
        # 어떤 종목을 고르느냐가 곧 수익률이기 때문이다
        group_id, bt.CALC_VERSION, sc.CALC_VERSION, now,
    )
    if len(row) != db.column_count(_RUN_COLS):
        raise ValueError("backtest_runs 값 묶음과 열 개수가 어긋납니다")

    placeholders = ", ".join(["?"] * len(row))
    rs = client.execute(f"INSERT INTO backtest_runs ({_RUN_COLS}) VALUES ({placeholders})", list(row))
    run_id = rs.last_insert_rowid
    if run_id is None:
        rs2 = client.execute("SELECT MAX(id) FROM backtest_runs")
        run_id = int(rs2.scalar())

    per = 300
    drawdowns = bt.drawdown_curve(result.curve)
    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(result.curve), per):
        chunk = result.curve[start : start + per]
        sql = (
            "INSERT INTO backtest_curves (run_id, date, equity, drawdown) VALUES "
            + ", ".join(["(?, ?, ?, ?)"] * len(chunk))
            + " ON CONFLICT (run_id, date) DO UPDATE SET equity = excluded.equity, drawdown = excluded.drawdown"
        )
        args: list[Any] = []
        for (d, v), dd in zip(chunk, drawdowns[start : start + per], strict=True):
            args.extend([run_id, d, v, dd])
        statements.append((sql, args))
    if statements:
        client.batch(statements)
    return int(run_id)


# 1년 거래일 어림. 예산 어림에만 쓴다 (docs/metrics.md 의 연환산 계수와 같은 값)
TRADING_DAYS_PER_YEAR = 252


def 실행_파라미터(
    years: int, top_n: int, long_thresholds: tuple[float, ...], only_long: bool, pit_universe: bool, trend_filter: bool
) -> dict[str, Any]:
    """실행 기록에 남길 파라미터와 **기본 파라미터 실행인가** (docs/factors.md 12장 머리, docs/infra.md 25.781).

    기법 발굴 루프의 판정은 기본 파라미터 실행의 IC 만 센다 — 파라미터를 바꿔 여러 번 돌려
    판정을 늘리거나 고르지 않게.
    예전 기록에는 시점 유니버스·추세 필터만 있고 연수·상위 N 이 없어, 기록만 보고는 어느 실행이
    판정에 세는지 가를 수 없었다.
    장기 문턱(`long_thresholds`)은 장기 신호 전략만 더하고 IC 를 바꾸지 않아 기본 여부에 넣지 않는다.
    """
    기본 = (
        years == DEFAULT_YEARS and top_n == bt.DEFAULT_TOP_N and not only_long and pit_universe and not trend_filter
    )
    return {
        "years": years, "top_n": top_n, "long_thresholds": list(long_thresholds), "only_long": only_long,
        "pit_universe": pit_universe, "trend_filter": trend_filter, "default": 기본,
    }  # fmt: skip


def run(
    market: str,
    years: int = DEFAULT_YEARS,
    top_n: int = bt.DEFAULT_TOP_N,
    long_thresholds: tuple[float, ...] = (),
    only_long: bool = False,
    pit_universe: bool = True,
    trend_filter: bool = False,
    force: bool = False,
) -> int:
    """백테스트를 돌려 저장한다.

    pit_universe=False 는 **비교용**이다. 옛 방식(오늘 유니버스를 전 구간에)으로 돌려
    시점 유니버스가 결과를 얼마나 바꾸는지 재는 데만 쓴다. 규칙을 판단할 때는 쓰지 않는다.
    trend_filter=True 는 벤치마크를 뺀 전략에 시장 추세 오버레이를 곱한다 (docs/backtest.md 2.4).
    """
    market = market.upper()
    country = "KR" if market == "KR" else "US"
    # 미국 수정종가는 배당 포함 총수익이다 (docs/infra.md 25.244). 결과의 경고 글이 그것을 말한다
    배당포함 = country == "US"
    client = TursoClient()

    try:
        db.apply_migrations(client)
        now = db.now_iso()
        today = datetime.now(UTC).date()
        since = (today - timedelta(days=365 * years + 30)).isoformat()
        batch_id = db.start_batch_run(
            client, job_name=JOB_NAME, market=market, trade_date=today.isoformat()
        )

        universe, warnings = load_universe(client, country)
        if not universe:
            db.finish_batch_run(client, batch_id, status="failed", error_text="유니버스가 비어 있습니다")
            print("유니버스가 비어 있습니다")
            return 1

        # 읽기 예산을 **시작 전에** 본다 (docs/infra.md 24절). 백테스트는 종목마다 몇 년치 가격을
        # 훑어 한 번에 수백만 행을 읽는다. 넘고 나서 아는 것과 넘기 전에 아는 것은 다르다.
        # 워밍업은 **있는 가격만** 읽는다 — 백필이 얕으면 워밍업 구간은 비어 있어 예산에 더하지 않는다
        # (25.537, 교차검증). 없는 행까지 종목당 621행을 더해 어림이 약 49% 부풀었고,
        # 읽기 예산 문턱에서 실행이 새로 거부될 수 있었다
        # 나라 전체 MIN 은 조인 때문에 MIN 최적화가 안 돼 그 나라 가격 행을 다 훑을 수 있었다 (25.543, 교차검증).
        # 표본 종목으로 보면 어림이 작아질 수만 있었고(25.547), 표 전체 MIN 은 다른 나라 백필이 깊으면 25.537 의
        # 부풂을 되살렸다(25.548). **그 나라 종목마다 (stock_id, date) 색인으로 MIN** 을 내고 그중 가장 이른 것을
        # 쓴다 — 종목당 색인 한 번이고, 어느 종목의 이력도 빠뜨리지 않아 작게 어림하지 않는다
        가장_이른 = client.execute(
            "SELECT MIN(d) FROM (SELECT (SELECT MIN(p.date) FROM prices p WHERE p.stock_id = s.id) AS d"
            " FROM stocks s WHERE s.country = ?)",
            [country],
        ).scalar()
        워밍업_일 = 0
        if 가장_이른:
            앞선_일 = (date.fromisoformat(since) - date.fromisoformat(str(가장_이른)[:10])).days
            워밍업_일 = max(0, min(WARMUP_CALENDAR_DAYS, 앞선_일))
        estimate = len(universe) * (years * TRADING_DAYS_PER_YEAR + 워밍업_일 * 252 // 365)
        ok, text = db.check_read_budget(client, estimate, "백테스트")
        print(text)
        if not ok and not force:
            db.finish_batch_run(client, batch_id, status="skipped", error_text=text)
            print("이번 달 읽기 예산이 모자랍니다. --force 로 넘길 수 있지만 계정이 막힐 수 있습니다")
            return 1

        ids = [int(r["stock_id"]) for r in universe]
        # 미국은 가격 수준을 따로 싣는다 — 수정종가는 미래 배당만큼 낮다 (docs/infra.md 25.275).
        # 국내 수정주가는 분할만 조정이라 같다
        levels: dict[int, dict[str, float]] | None = {} if country == "US" else None
        # **판단에 쓸 과거는 시작일보다 앞에서부터 읽는다** (docs/backtest.md 2.3, docs/infra.md 25.532, 감사 재현).
        # 시작 30일 전부터만 읽어, 12-1 모멘텀(273거래일)·리스크(600거래일)가 초반 내내 비고 종합 점수도 나오지 않아
        # 종합·모멘텀은 첫 약 12개월, 리스크는 약 28개월을 **현금으로** 보냈다(벤치마크·밸류는 첫 달부터 투자) —
        # 전략 비교가 통째로 기울었다. 곡선과 리밸런스는 그대로 `since` 부터다
        워밍업 = (date.fromisoformat(since) - timedelta(days=WARMUP_CALENDAR_DAYS)).isoformat()
        prices, turnover = load_prices_and_turnover(client, ids, 워밍업, levels=levels)
        snapshots = load_snapshots(client, country)
        # 배당 이력 (docs/factors.md 11.1). **접수일을 함께 들고** 시점마다 되감는다 —
        # 적재 경로와 같은 지표를 같은 시점 규칙으로 내야 백테스트가 운영을 잰다(infra 25.92)
        pit_divs = load_pit_dividends(client, country)
        # 잔차 변동성의 지수 계열 (docs/factors.md 11.3). 베타와 **같은 지수**를 본다
        bench_closes = load_benchmark_closes(client, country)
        delisted = has_delisted(client, country, ids)
        # 리스크 팩터의 샤프·소르티노가 쓸 무위험수익률 — 전략을 돌리기 전에 둔다 (25.622). 경고는 요약 단계가 남긴다
        global _RISK_FREE
        _RISK_FREE = metrics_job.risk_free_for(client, country)
        _위험_캐시.clear()  # 나라·가격이 바뀐다 — 지난 실행의 값을 쓰지 않는다

        dates = sorted({d for by_date in prices.values() for d in by_date if d >= since})
        if len(dates) < 40:
            db.finish_batch_run(client, batch_id, status="failed", error_text="거래일이 40일 미만입니다")
            print("가격이 너무 적어 백테스트를 돌릴 수 없습니다. 백필을 먼저 돌리세요")
            return 1
        rebalance_dates = bt.month_starts(dates)

        weights = {"value": 20.0, "quality": 20.0, "growth": 20.0, "momentum": 20.0, "risk": 20.0}
        # **나라마다 다르다** (docs/infra.md 25.126). 미국에는 매매마다 붙는 세금이 없는데
        # 국내 거래세를 그대로 쓰면 없는 세금으로 연 0.5% 를 깎는다. 국내는 체결일의 법정 세율이다(25.783).
        # 설정이 있으면 설정을 쓴다 — docs/backtest.md 가 그렇게 약속해 두고 안 지키고 있었다
        costs = bt.Costs.for_country(
            country, db.get_setting(client, "fees", {}), db.get_setting(client, "taxes", {})
        )
        log.info("거래비용: 편도 수수료 %.3f%% · 매도 거래세 %s · 슬리피지 %.3f%%", costs.commission_pct,
                 ", ".join(f"{d}~ {p:.2f}%" for d, p in costs.tax_schedule) or f"{costs.tax_sell_pct:.3f}%",
                 costs.slippage_pct)  # fmt: skip
        group_id = uuid.uuid4().hex[:12]

        # 시점 유니버스 (docs/backtest.md 1.3). 리밸런스 날짜마다 한 번만 판정하고 그 결과를 재사용한다.
        # 전략이 여덟이라 매번 다시 판정하면 같은 계산을 여덟 번 한다
        # 하한은 유니버스 배치와 같은 값을 쓴다. 백테스트용으로 따로 고르면 그것이 과최적화다
        base = uni.UniverseFilters.korea() if country == "KR" else uni.UniverseFilters.usa()
        # 미국 listed_date 는 관측 첫 거래일이다(us_shares) — 실제 상장일이 아니다 (docs/infra.md 25.403)
        pit_stock_list = pit_stocks(universe, observed=country == "US")
        series_by_stock = pit_series(levels or prices, turnover)  # 시총 하한은 가격 수준으로 (25.275)
        filters = pit.Filters(
            min_market_cap=base.min_market_cap,
            min_avg_turnover_20d=base.min_avg_turnover_20d,
            min_listed_days=base.min_listed_days,
            history_starts=history_starts(pit_stock_list, series_by_stock),
        )
        # 20일 평균 거래대금의 창은 **시장의** 최근 20 거래일이다 (docs/infra.md 25.271). 한 번만 낸다
        market_dates = pit.market_dates_of(series_by_stock)
        eligible_cache: dict[str, set[int]] = {}
        pit_reasons: dict[str, int] = {}

        def eligible_at(cutoff: str) -> set[int]:
            hit = eligible_cache.get(cutoff)
            if hit is None:
                hit, reasons = pit.members_at(pit_stock_list, series_by_stock, cutoff, filters, market_dates)
                eligible_cache[cutoff] = hit
                for key, count in reasons.items():
                    pit_reasons[key] = pit_reasons.get(key, 0) + count
            return hit

        eligible: EligibleAt | None = eligible_at if pit_universe else None
        warnings.append(WARN_PIT_UNIVERSE if pit_universe else WARN_NO_HISTORICAL_UNIVERSE)
        if years != DEFAULT_YEARS or top_n != bt.DEFAULT_TOP_N:
            warnings.append(f"{WARN_NOT_DEFAULT_PARAMS}: 기간 {years}년 · 상위 {top_n}개")

        # 추세 오버레이. 배수는 설정값(적재와 같은 값)을 쓴다.
        # 지수는 시작보다 1년 앞부터 읽어 첫 달부터 200일이 있게 한다
        trend_settings = trend.load_settings(client)
        index_series = (
            load_index_series(client, country, (today - timedelta(days=365 * years + 400)).isoformat())
            if trend_filter else {}
        )
        market_of = {int(r["stock_id"]): str(r.get("market") or "") for r in universe}
        if trend_filter:
            warnings.append(WARN_TREND_ON)

        def overlay(decide: bt.WeightsAt, strat_warnings: list[str]) -> bt.WeightsAt:
            if not trend_filter:
                return decide
            return with_trend_filter(decide, index_series, market_of, trend_settings["bear_factor"], strat_warnings)

        results: dict[str, bt.BacktestResult] = {}
        planned = [s for s in STRATEGIES if not (only_long and s != "benchmark")]
        # 전략 + 장기 문턱 + (비용 0 비교 + IC) — IC 단계도 센다 (25.451, 교차검증: 전에는 빠져 진행 칸이 멈춰 보였다)
        total_steps = len(planned) + len(long_thresholds) + (0 if only_long else 2)
        끝낸_단계 = 0  # 결과 표에 안 들어가는 단계(IC) 수

        def progress(done: int, current: str) -> None:
            # 화면이 "지금 어디까지 왔나" 를 보여 준다 (docs/backtest.md 8.4). 실패해도 계산은 계속한다
            try:
                db.note_progress(
                    client, batch_id, {"done": done + 끝낸_단계, "total": total_steps, "current": current}
                )
            except Exception:  # noqa: BLE001 — 진행 표시가 계산을 죽이면 안 된다. 아래에서 말한다
                log.warning("진행 상태를 적지 못했습니다")

        for strategy in planned:
            progress(len(results), strategy)
            strat_warnings: list[str] = []
            decide = make_strategy(
                strategy, universe, snapshots, top_n, weights, strat_warnings, eligible, pit_divs,
                bench_closes,
            )
            if strategy != "benchmark":
                decide = overlay(decide, strat_warnings)  # 벤치마크는 비교 기준이라 그대로 둔다
            results[strategy] = bt.simulate(
                prices, dates, rebalance_dates, decide, costs, has_delisted=delisted, turnover=turnover,
                dividends_included=배당포함, levels=levels,
            )
            results[strategy].warnings.extend(strat_warnings)
            print(f"  {strategy:10s} 최종 {results[strategy].curve[-1][1]:.3f}")

        # **팩터 IC** — 효용을 재는 두 번째 잣대 (docs/backtest.md 2.5, docs/factors.md 12장).
        # 상위 N 전략의 CAGR 만으로는 5년 표본에서 잡음이 크다. 기록에만 남기고 결과 표는 바꾸지 않는다
        ic_summary: dict[str, dict[str, Any]] = {}
        ab = fab.AbAccumulator()  # 지표 교체 A/B — 기록 전용 (docs/factors.md 12.12)
        if not only_long:
            progress(len(results), "IC")
            ic_summary = ic_log(factor_ics(
                universe, snapshots, prices, dates, rebalance_dates, weights, eligible, pit_divs, bench_closes,
                turnover, levels, load_quarter_snapshots(client, country),
                load_buyback_events(client, country) if country == "KR" else None,
                load_dilution_events(client, country) if country == "KR" else None, ab,
            ))  # fmt: skip
            for name, v in ic_summary.items():
                print(f"  IC {name:10s} {v['months']}개월 평균 {v['mean']} t {v['t']}"
                      f" 비율 {v['coverage']} → {v['verdict']}")
            끝낸_단계 = 1

        for threshold in long_thresholds:
            name = f"long_q{threshold:g}"
            progress(len(results), name)
            long_warnings: list[str] = []
            decide = overlay(
                make_long_strategy(threshold, universe, snapshots, top_n, eligible, pit_divs, bench_closes),
                long_warnings,
            )
            results[name] = bt.simulate(
                prices, dates, rebalance_dates, decide, costs, has_delisted=delisted, turnover=turnover,
                dividends_included=배당포함, levels=levels,
            )
            results[name].warnings.extend(long_warnings)
            avg_hold, invested = exposure(results[name])
            print(
                f"  {name:10s} 최종 {results[name].curve[-1][1]:.3f}"
                f"  평균 보유 {avg_hold:.1f}종목, 투자한 달 {invested:.0%}"
            )

        if not only_long:
            # composite 를 비용 0 으로 한 번 더. 비용을 내고도 남는지 나란히 본다
            progress(len(results), "composite_no_cost")
            free = bt.simulate(
                prices, dates, rebalance_dates,
                overlay(
                    make_strategy(
                        "composite", universe, snapshots, top_n, weights, [], eligible, pit_divs, bench_closes
                    ),
                    [],
                ),
                bt.Costs.zero(), has_delisted=delisted, turnover=turnover,
                dividends_included=배당포함, levels=levels,
            )
            results["composite_no_cost"] = free

        bench = results["benchmark"]
        # **설정의 무위험수익률을 쓴다** (docs/infra.md 25.299). 예전에는 넘기지 않아 샤프·소르티노가 늘 비었다 —
        # 설정 화면은 "샤프지수 계산에 씁니다" 라고 하는데 백테스트에는 아무 효과가 없었다.
        # 종목 성과와 같은 함수로 읽는다(0 은 0%, 없거나 범위 밖이면 모름)
        무위험 = risk_free_with_warning(client, country, warnings)
        # 운인가 실력인가 (docs/backtest.md 9장, 25.997) — 같은 실행에서 시험한 전략들을 한꺼번에 판정한다.
        # 비용 없는 사본(`*_no_cost`)은 같은 전략이라 시도 수에 넣지 않는다
        벤치월 = luck.monthly_returns(bench.curve)
        운 = luck.judge({
            name: luck.excess_series(luck.monthly_returns(r.curve), 벤치월)
            for name, r in results.items() if name != "benchmark" and not name.endswith("_no_cost")
        })  # fmt: skip
        stored = 0
        for strategy, result in results.items():
            summary = bt.summarize(result, None if strategy == "benchmark" else bench, risk_free_annual=무위험)
            # 벤치마크에는 추세 오버레이를 곱하지 않는다 — "추세 필터 켬" 을 붙이면 비교 기준을 오해한다 (25.471)
            그경고 = [w for w in warnings if not (strategy == "benchmark" and w == WARN_TREND_ON)]
            store_run(client, market, strategy, result, summary, top_n, group_id, 그경고, now, 운.get(strategy))
            stored += 1
            mt = summary.metrics
            print(
                f"  요약 {strategy:18s} CAGR {_pct(mt.cagr)}  MDD {_pct(mt.mdd)}  샤프 {_fmt(mt.sharpe)}"
                f"  초과 {_pct(summary.excess_cagr)}  이긴 달 {_pct(summary.win_rate)}"
                + (f"  {luck.line(운[strategy])}" if strategy in 운 else "")
            )

        실제_리밸런스 = sum(1 for d in rebalance_dates if d != dates[-1] or len(dates) == 1)
        db.finish_batch_run(
            client, batch_id, status="success",
            # 마지막 날은 평가만 한다(25.792) — 결과 행의 `rebalances` 와 같은 수를 적는다 (25.795, 교차검증)
            step_log={"group_id": group_id, "strategies": stored, "rebalances": 실제_리밸런스,
                      "factor_ic": ic_summary,
                      # 지표 교체 A/B (10회차, docs/factors.md 12.12) — 판정 키가 아니다. 기본 파라미터 실행만 센다
                      **({"factor_ab": ab.log()} if ab.ics else {}),
                      "params": 실행_파라미터(years, top_n, long_thresholds, only_long, pit_universe, trend_filter),
                      "stocks": len(ids), "dates": len(dates), "warnings": warnings,
                      "pit_universe": {"on": pit_universe, "sizes": _pit_sizes(eligible_cache),
                                       "excluded": pit_reasons},
                      "trend_filter": {"on": trend_filter, "bear_factor": trend_settings["bear_factor"],
                                       "index_days": {k: len(v) for k, v in index_series.items()}}},
        )
        print(f"저장 {stored}개 전략, 리밸런스 {실제_리밸런스}회, 그룹 {group_id}")
        for w in sorted(set(warnings + results.get('composite', bench).warnings)):
            print(f"  경고: {w}")
        return 0
    finally:
        client.close()


def _pit_sizes(cache: dict[str, set[int]]) -> dict[str, int]:
    """시점별 유니버스 크기. 처음·중간·마지막만 남긴다 — 전부 적으면 로그가 길다."""
    if not cache:
        return {}
    keys = sorted(cache)
    picked = [keys[0], keys[len(keys) // 2], keys[-1]]
    return {k: len(cache[k]) for k in dict.fromkeys(picked)}


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="백테스트")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    parser.add_argument("--top-n", dest="top_n", type=int, default=bt.DEFAULT_TOP_N)
    parser.add_argument(
        "--long-thresholds", default="", help="장기 신호 퀄리티·밸류 문턱 비교. 예: 60,55,50 (docs/backtest.md 7장)"
    )
    parser.add_argument("--only-long", action="store_true", help="팩터 전략을 빼고 장기 신호와 벤치마크만")
    parser.add_argument(
        "--no-pit-universe",
        action="store_true",
        help="비교용. 시점 유니버스를 끄고 오늘 유니버스를 전 구간에 쓴다 (docs/backtest.md 1.3)",
    )
    parser.add_argument(
        "--trend-filter",
        action="store_true",
        help="시장 추세 오버레이를 켠다. 지수 < 200일선이면 비중 × 설정 배수 (docs/backtest.md 2.4)",
    )
    parser.add_argument("--force", action="store_true", help="읽기 예산이 모자라도 강행")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    thresholds = tuple(float(x) for x in args.long_thresholds.split(",") if x.strip())
    return run(
        args.market, args.years, args.top_n, thresholds, args.only_long, not args.no_pit_universe,
        args.trend_filter, args.force,
    )


if __name__ == "__main__":
    sys.exit(guard(main))
