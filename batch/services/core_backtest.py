"""핵심 H 다년 백테스트의 하루 판단 재료 — 조각 1b (docs/backtest.md 10장, docs/infra.md 25.1529). 순수 함수.

`CoreMarket.at(view)` 가 운영 가상모의투자 H(지주사, docs/company.md 10장)의 **결정 입력**을 그 시점(PIT)으로
다시 낸다 — 신호 줄(`signals.evaluate` 그대로) → 조사 자료(Dossier, 재현할 수 없는 칸은 비움) →
A·C·D·E 위원회(`company.meet`)·B 점수 상위 5 표 → 메모리 결정 원장(`HoldingLedger`)으로
회사 가중치(`holding.weights`) → 합의 순위(`holding.rank`).
매도·크기·변형 다섯(1c)과 실행 연결·저장(1d `jobs/core_backtest`)은 여기 없다. 변형 다섯이 하루 재료를 나눠 읽는
`SharedDays`(1d, 25.1534)와 리스크 지표 주기(`risk_every`)는 여기 있다.

**미래를 볼 것이 없다** (CLAUDE.md 절대 규칙, docs/backtest.md 1.1). 가격은 `PriceView`(cutoff 이하 사본)만 본다.
부르는 쪽이 통째로 넘기는 재무 스냅샷·배당·지수는 쓰는 자리마다 cutoff 로 자른다 — 재무는 접수일(정정 포함)로,
지수는 날짜로. 결정 원장 성과는 20번째 뒤 종가가 cutoff 이하이고 결정일부터 28일이 지난 행만 보인다
(운영 `due_horizons`).

판단식을 **다시 적지 않는다** — `signals`·`company`·`holding`·`report_picks` 와 `jobs/backtest`(PIT 팩터 입력·리스크)·
`jobs/signals`(장기 후보)·`jobs/company`(안건)의 함수를 그대로 부른다. `jobs` 쪽은 함수 안에서 들여온다(서비스가
모듈을 읽을 때 배치 작업 전체를 끌고 오지 않게).
"""

from __future__ import annotations

import bisect
import json
import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from batch.services import backtest as bt
from batch.services import company as co
from batch.services import holding as hold
from batch.services import paper as pp
from batch.services import report_picks, trend
from batch.services import scoring as sc
from batch.services import signals as sg

#: H 에 표를 내는 위원회 회사 — 운영 H 의 `charter_json.voters` 와 같은 넷 (docs/company.md 10장). B 는 점수 상위 표
VOTERS: tuple[str, ...] = ("A", "C", "D", "E")
#: 종합 점수 가중 — 백테스트 `run` 과 같은 5팩터 균등. 운영은 설정값 + 감성 10% (docs/backtest.md 10장 어긋남)
EQUAL_WEIGHTS: dict[str, float] = dict.fromkeys(sc.FACTORS, 20.0)
#: 결정 원장 성과의 시장 지수 — 운영 `jobs/paper.BENCH_INDEX["KR"]` (결정 시장이 국내면 늘 KOSPI)
BENCH_INDEX = "KOSPI"
#: 팩터 점수 리스크 지표를 다시 재는 주기(`CoreMarket(risk_every=)`, 25.1534). 실행(`jobs/core_backtest`) 기본은 "week"
RISK_EVERY: tuple[str, ...] = ("week", "month")


# ── 재무 시점 고르기 ──────────────────────────────────────────────────────────


def _known_by_year(snapshots: list[dict[str, Any]], cutoff: str) -> dict[int, dict[str, Any]]:
    """cutoff 까지 **접수된** 연간 스냅샷을 사업연도마다 하나 — 정정은 늦게 접수된 것(접수일, 같으면 접수번호).

    기준(연결·별도)은 `jobs/backtest.pit_basis_rows` 가 cutoff 에 알 수 있던 가장 늦은 해로 고른다(운영 25.856 과 같다).
    **접수일이 cutoff 뒤인 행은 없는 것이다** — 이 거르기가 성장률·현재 PBR 의 유일한 시점 문이다(시험이 미끼로 지킨다)
    """
    from batch.jobs import backtest as jb

    known: dict[int, dict[str, Any]] = {}
    for row in jb.pit_basis_rows(snapshots, cutoff):
        if str(row["as_of_date"]) > cutoff:
            continue
        해 = int(row["fiscal_year"])
        if 해 not in known or jb._공시_순(row) > jb._공시_순(known[해]):
            known[해] = row
    return known


def _flat(row: dict[str, Any]) -> dict[str, Any]:
    """스냅샷 한 행을 운영 `financials` 행 모양으로 — 값 + 접수일·연결 여부."""
    return {**row["values"], "report_date": str(row["as_of_date"]), "consolidated": row.get("consolidated", True)}


def pit_growth(snapshots: list[dict[str, Any]], cutoff: str) -> dict[str, Any] | None:
    """운영 `jobs/signals.load_growth` 한 종목 몫을 스냅샷에서 — 규칙은 `signals.annual_growth` 하나다."""
    known = _known_by_year(snapshots, cutoff)
    if not known:
        return None
    latest = max(known)
    전년 = known.get(latest - 1)
    return sg.annual_growth(_flat(known[latest]), _flat(전년) if 전년 else None, cutoff, latest)


def pit_latest_equity(snapshots: list[dict[str, Any]], cutoff: str, currency: str | None) -> float | None:
    """운영 `jobs/signals._latest_equity` — 알 수 있던 **가장 늦은 사업연도** 행의 자본.

    그 행에 자본이 없거나 종목 통화가 아니면 모름(옛 해로 내려가지 않는다, 25.1172).
    통화를 모르는 행은 막지 않는다(`scoring.same_currency` 와 같다)"""
    known = _known_by_year(snapshots, cutoff)
    if not known:
        return None
    값 = known[max(known)]["values"]
    통화 = 값.get("currency")
    if currency and 통화 and 통화 != currency:
        return None
    자본 = 값.get("total_equity")
    if 자본 is None:
        return None
    v = float(자본)
    return v if math.isfinite(v) else None


# ── 메모리 결정 원장 ──────────────────────────────────────────────────────────


@dataclass
class LedgerRow:
    """H 결정 원장 한 줄 (운영 `paper_decisions` 의 H 행 가운데 `detail.holding.votes` 가 붙은 것)."""

    decided_on: str
    stock_id: int
    kind: str  # pick(고름 — 1c 가 buy·skip·blocked 로 가른다) · skip(추천받았으나 고르지 않음)
    reason: str
    votes: dict[str, float]
    ref_close: float | None
    ref_date: str | None
    ret_20d: float | None = None  # % (운영과 같은 단위)
    bench_20d: float | None = None
    resolved: bool = False


class HoldingLedger:
    """H 의 결정 원장을 메모리에 (docs/backtest.md 10장 조각 1b). 회사 가중치는 이 원장만으로 순차 계산한다.

    성과(`ret_20d`·`bench_20d`, %)는 **두 문이 다 열려야** 보인다 — 운영 `fill_outcomes` 와 같다.
    ① 기준일 뒤 `pp.ATTRIBUTION_HORIZON` 번째 그 종목 종가가 **cutoff 앞**(cutoff 날 종가는 아니다 — 25.1529 반박 검증:
    운영은 결정일 t 의 회의가 t−1 실행 **끝에** 채운 성과만 읽고, t−1 실행이 가진 종가는 t−2(= cutoff 앞날)까지다.
    휴장이 28일 창에 끼면 20번째 종가가 28일째에 걸려 하루 일찍 보였다) ② cutoff − 결정일 ≥
    `pp.HORIZON_CALENDAR_DAYS[20]` 달력일(운영 `due_horizons` — 그 전에는 읽지도 않는다). 지수는 늘 KOSPI(`pp.outcomes`)
    """

    def __init__(self) -> None:
        self.rows: list[LedgerRow] = []

    def add(
        self, decided_on: str, stock_id: int, kind: str, reason: str, votes: dict[str, float],
        ref_close: float | None, ref_date: str | None,
    ) -> None:
        self.rows.append(LedgerRow(decided_on, int(stock_id), kind, reason, dict(votes), ref_close, ref_date))

    def discard(self, decided_on: str, stock_id: int) -> int:
        """그날 그 종목의 줄을 뺀다 — 운영에서 표가 붙지 않는 결정(사면 바로 매도 skip 등, 1c)을 지울 때. 뺀 줄 수"""
        전 = len(self.rows)
        self.rows = [r for r in self.rows if not (r.decided_on == decided_on and r.stock_id == int(stock_id))]
        return 전 - len(self.rows)

    def _resolve(self, row: LedgerRow, view: bt.PriceView, bench: list[tuple[str, float]]) -> None:
        if row.resolved or row.ref_close is None or not row.ref_date:
            return
        n = pp.ATTRIBUTION_HORIZON
        지난 = (date.fromisoformat(view.cutoff[:10]) - date.fromisoformat(row.decided_on[:10])).days
        if 지난 < pp.HORIZON_CALENDAR_DAYS[n]:
            return
        계열 = [x for x in view.closes(row.stock_id) if x[0] < view.cutoff]  # cutoff 날 종가는 아직 안 채워졌다
        뒤 = 계열[bisect.bisect_right(계열, (row.ref_date, math.inf)):][:n]
        if len(뒤) < n:
            return

        def 지수(날: str) -> float | None:
            k = bisect.bisect_right(bench, (날, math.inf))
            return bench[k - 1][1] if k else None

        o = pp.outcomes(row.ref_close, 뒤, 지수(row.ref_date), {뒤[n - 1][0]: 지수(뒤[n - 1][0])})
        row.ret_20d, row.bench_20d, row.resolved = o.get(f"ret_{n}d"), o.get(f"bench_{n}d"), True

    def records(self, view: bt.PriceView, bench: list[tuple[str, float]], day: str) -> list[dict[str, Any]]:
        """`holding.weights` 가 읽는 행 — 결정일이 `day` 앞 `HOLDING_WINDOW_DAYS` 안이고 성과가 보이는 것만.

        `bench` = cutoff 까지 자른 (날짜, KOSPI) 오름차순. 한 번 찬 성과는 고정한다(운영도 다시 쓰지 않는다)
        """
        부터 = (date.fromisoformat(day[:10]) - timedelta(days=hold.HOLDING_WINDOW_DAYS)).isoformat()
        out: list[dict[str, Any]] = []
        for r in self.rows:
            if r.decided_on < 부터:
                continue
            self._resolve(r, view, bench)
            if r.ret_20d is None:
                continue
            표 = json.dumps({"holding": {"votes": r.votes}})
            out.append({"stock_id": r.stock_id, "decided_on": r.decided_on, "ret_20d": r.ret_20d,
                        "bench_20d": r.bench_20d, "detail_json": 표})  # fmt: skip
        return out


# ── 하루 판단 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DayView:
    """한 cutoff 의 H 결정 입력 — `CoreMarket.at` 의 결과. 1c 가 이것으로 매도·크기를 정한다."""

    cutoff: str
    day: str  # 결정일 t (운영 trade_date)
    score_cutoff: str  # 팩터 점수를 낸 cutoff — 그 ISO 주의 첫 cutoff
    factor_scores: dict[int, dict[str, float | None]]
    total_scores: dict[int, float | None]
    inputs: dict[int, sg.SignalInput]  # cutoff 종가가 있는 후보의 신호 입력
    signals: dict[int, list[sg.Signal]]  # 근거표가 있는 신호
    rows: list[report_picks.SignalRow]  # 운영 `load_signal_rows` 모양 (종목 id·기간 순)
    regimes: dict[str, trend.Regime]
    regime_factors: dict[str, float]  # 지수 → 추세 필터 배수
    population: list[co.Dossier]
    agenda: list[co.Dossier]
    approved: dict[str, list[co.Item]]  # 위원회 회사 → 가결(표 순 top_n)
    votes: dict[str, dict[int, float]]  # 회사(B 포함) → {종목: 세기}
    company_weights: dict[str, float]
    company_stats: dict[str, dict[str, Any]]
    ranked: list[dict[str, Any]]  # `holding.rank` 그대로
    picks: list[int]  # H 가 고른 종목 (점수 순, 상위 top_n)
    pick_rows: list[report_picks.SignalRow]
    b_top: list[int]
    holding: dict[int, dict[str, Any]] = field(default_factory=dict)  # 종목 → {votes, score, weights}


def _signal_row(s: sg.Signal, inp: sg.SignalInput, total: float | None, cutoff: str, close: float | None,
                risk: dict[str, float | None]) -> report_picks.SignalRow:  # fmt: skip
    """운영 `jobs/daily.load_signal_rows` 한 행 — 저장·읽기를 거치지 않고 같은 칸을 채운다."""
    return report_picks.SignalRow(
        stock_id=s.stock_id, ticker=s.ticker, name=s.name, market=inp.market, horizon=s.horizon,
        signal_type=s.signal_type, currency=s.currency, buy_zone_low=s.buy_zone_low, buy_zone_high=s.buy_zone_high,
        suggested_weight_pct=s.weight_pct, suggested_amount=s.suggested_amount, size_reduction=s.size_reduction,
        tranche_plan=[{"step": t.step, "ratio": t.ratio, "price": t.price, "amount": t.amount} for t in s.tranches],
        rationale_text=s.rationale_text, as_of_date=cutoff, total_score=total,
        factor_scores=dict(inp.factor_scores), close=close, close_date=cutoff,
        criteria=list((s.rationale_data or {}).get("criteria") or []), perf_window=None, perf_as_of=cutoff,
        cagr=risk.get("cagr"), mdd=risk.get("mdd"), sharpe=risk.get("sharpe"), sector=inp.sector,
    )  # fmt: skip


class CoreMarket:
    """한 시장(지금은 국내만)의 핵심 H 판단기. cutoff 를 **줄지 않는 순서로** 부른다 — 원장이 순차로 쌓인다.

    - `universe` = 종목 행(stock_id·ticker·name·market·sector·currency·listed_shares).
      `eligible_at(cutoff)` 를 주면 그날 시점 유니버스로 자른다(`jobs/backtest.EligibleAt`)
    - `snapshots_by_stock` = `jobs/backtest.load_snapshots` 모양(연결·별도·정정 모두, 미래 포함 — 여기서 자른다)
    - `indexes` = {지수 코드: {날짜: 종가}} — KOSPI·KOSDAQ(국면), KOSPI(벤치마크·원장 성과). 미래 포함 — 여기서 자른다
    - 팩터 점수는 **ISO 주마다 첫 cutoff 에 한 번**(`build_pit_inputs`→`score_factors`) — 모멘텀이 최대 4 거래일 묵는다
    - 무위험수익률은 `jobs/backtest._RISK_FREE`(실행이 설정에서 읽어 둔 값)를 팩터와 신호 사이징이 함께 쓴다
    """

    def __init__(
        self,
        universe: list[dict[str, Any]],
        snapshots_by_stock: dict[int, list[dict[str, Any]]],
        indexes: Mapping[str, Mapping[str, float]],
        *,
        country: str = "KR",
        eligible_at: Callable[[str], set[int]] | None = None,
        dividends_by_stock: dict[int, list[tuple[str, int, float]]] | None = None,
        factor_weights: dict[str, float] | None = None,
        trend_settings: dict[str, Any] | None = None,
        charters: dict[str, dict[str, Any]] | None = None,
        voters: tuple[str, ...] = VOTERS,
        b_top_n: int = report_picks.TOP_STOCKS,
        top_n: int = report_picks.TOP_STOCKS,
        max_weight_per_stock: float = 10.0,
        targets: dict | None = None,
        risk_every: str | None = None,
    ) -> None:
        if risk_every is not None and risk_every not in RISK_EVERY:
            raise ValueError(f"risk_every 는 {RISK_EVERY} 가운데 하나(또는 None) — {risk_every}")
        if country != "KR":
            # 미국은 broker·flows·governance 가 원래 "자료 없음" 이라 Dossier 는 같지만,
            # 지수(SP500)·통화(USD 사이징·환율)·분할 재수집 대기 규칙을 아직 맞추지 않았다 —
            # 맞추기 전에 돌려 "국내와 같은 판" 으로 읽히지 않게 막는다
            raise NotImplementedError("핵심 H 백테스트는 아직 국내(KR)만 — 미국은 docs/backtest.md 10장 다음 조각")
        self.country = country
        self.universe = sorted((dict(r) for r in universe), key=lambda r: int(r["stock_id"]))
        self.snapshots = snapshots_by_stock
        self.dividends = dividends_by_stock or {}
        self.eligible_at = eligible_at
        self.factor_weights = dict(factor_weights or EQUAL_WEIGHTS)
        self.trend_settings = dict(trend_settings or {"enabled": trend.DEFAULT_ENABLED,
                                                      "bear_factor": trend.DEFAULT_BEAR_FACTOR})  # fmt: skip
        self.charters = {c: (charters or co.CHARTERS)[c] for c in voters}
        for c, ch in self.charters.items():
            틀림 = co.charter_errors(ch)
            if 틀림:
                # 운영은 헌장이 틀리면 그 회사 위원회를 열지 않고 H 는 사지 않는다 — 여기서는 시작부터 막는다
                raise ValueError(f"투표 회사 {c} 헌장 오류: {틀림}")
        self.b_top_n, self.top_n = int(b_top_n), int(top_n)
        self.max_weight_per_stock = float(max_weight_per_stock)
        self.targets = targets
        self.indexes = {code: sorted((str(d), float(c)) for d, c in by.items()) for code, by in indexes.items()}
        self.ledger = HoldingLedger()
        self._week: tuple[int, int] | None = None
        self._scores: dict[int, dict[str, float | None]] = {}
        self._score_cutoff = ""
        self._last: DayView | None = None
        #: 주간 팩터 점수의 리스크 지표를 다시 재는 주기 — None 이면 예전 길(`jobs/backtest._위험_캐시`, 주마다).
        #: "week" 는 같은 값을 이 판단기 안에서 주마다, "month" 는 달마다 한 번(25.1534 — 아래 `_risk_fn`)
        self.risk_every = risk_every
        self._risk_period: tuple[int, int] | None = None
        self._risk_memo: dict[int, dict[str, float | None]] = {}
        self.risk_computed = 0  # 실제로 잰 종목 수(실측·시험)

    # 지수를 cutoff 로 자른다 — 국면·벤치마크·원장 성과가 모두 이 사본만 본다
    def _index_upto(self, code: str, cutoff: str) -> list[tuple[str, float]]:
        rows = self.indexes.get(code, [])
        return rows[: bisect.bisect_right(rows, (cutoff, math.inf))]

    def pool(self, cutoff: str) -> list[dict[str, Any]]:
        """그 cutoff 의 시점 유니버스 행 — `eligible_at` 이 없으면 전체(무작위 변형도 이것을 쓴다, 25.1532)"""
        if self.eligible_at is None:
            return self.universe
        allowed = self.eligible_at(cutoff)
        return [r for r in self.universe if int(r["stock_id"]) in allowed]

    def _weekly_scores(self, view: bt.PriceView) -> None:
        """그 ISO 주의 첫 cutoff 에 한 번 — 운영은 날마다 낸다(편향은 docs/backtest.md 10장)."""
        from batch.jobs import backtest as jb
        from batch.jobs import metrics as metrics_job

        주 = tuple(date.fromisoformat(view.cutoff[:10]).isocalendar()[:2])
        if 주 == self._week:
            return
        벤치 = dict(self._index_upto(metrics_job.BENCHMARK[self.country], view.cutoff))
        inputs = jb.build_pit_inputs(self.pool(view.cutoff), self.snapshots, view, self.dividends, 벤치,
                                     risk_for=self._risk_fn(view, 벤치) if self.risk_every else None)  # fmt: skip
        점수: dict[int, dict[str, float | None]] = {}
        for r in sc.score_factors(inputs) if inputs else []:
            점수.setdefault(r.stock_id, {})[r.factor] = r.score
        self._week, self._scores, self._score_cutoff = 주, 점수, view.cutoff  # type: ignore[assignment]

    def _risk_fn(
        self, view: bt.PriceView, bench: dict[str, float],
    ) -> Callable[[int, list[tuple[str, float]]], dict[str, float | None]]:  # fmt: skip
        """팩터 점수의 리스크 지표(`build_pit_inputs` 의 `risk_for`) — 주기(`risk_every`)마다 종목당 한 번 잰다.

        식·입력은 예전 길과 같다: `risk_from_prices`(창 3Y→1Y, docs/metrics.md) · 지수는 cutoff 까지 · 창 끝은
        `view.broad_last()`(25.723). 주기가 "week" 면 주간 점수와 같은 주기라 예전 길과 값이 같다(시험이 대조).
        "month" 면 그 달 첫 점수 cutoff 에 잰 값을 그 달 나머지 주에도 쓴다 — 3년 창이라 몇 주 사이 값이 거의
        같다. 그 달 처음 보이는 종목은 처음 본 cutoff 에 잰다. **편향**: 리스크 팩터(MDD·변동성·샤프·베타 …)가
        최대 약 4주 묵는다(docs/backtest.md 10장 조각 1d)"""
        from batch.jobs import backtest as jb
        from batch.services import metrics as m

        d = date.fromisoformat(view.cutoff[:10])
        주기 = (d.isocalendar()[0], d.isocalendar()[1]) if self.risk_every == "week" else (d.year, d.month)
        if 주기 != self._risk_period:
            self._risk_period, self._risk_memo = 주기, {}  # 지난 주기의 값은 버린다(메모리가 종목 수로 묶인다)
        점 = [m.PricePoint(date=date.fromisoformat(k), close=c) for k, c in sorted(bench.items()) if k <= view.cutoff]
        끝 = date.fromisoformat(str(view.broad_last() or view.cutoff)[:10])

        def risk_for(sid: int, closes: list[tuple[str, float]]) -> dict[str, float | None]:
            hit = self._risk_memo.get(sid)
            if hit is None:
                hit = jb.risk_from_prices(closes, risk_free_annual=jb._RISK_FREE, benchmark_points=점, until=끝)
                self._risk_memo[sid] = hit
                self.risk_computed += 1
            return hit

        return risk_for

    def _risk(self, view: bt.PriceView, sid: int, bench: list[tuple[str, float]]) -> dict[str, float | None]:
        """신호 사이징·리스크관리부가 보는 성과 지표.

        운영 `performance_metrics`(창 3Y→1Y) 대신 같은 식을 cutoff 창에서.
        `risk_from_prices` 그대로(창 끝은 대부분의 종목이 들어온 날, 25.723)."""
        from batch.jobs import backtest as jb
        from batch.services import metrics as m

        점 = [m.PricePoint(date=date.fromisoformat(d), close=c) for d, c in bench]
        r = jb.risk_from_prices(view.closes(sid), risk_free_annual=jb._RISK_FREE, benchmark_points=점,
                                until=date.fromisoformat(str(view.broad_last() or view.cutoff)[:10]))  # fmt: skip
        mdd = r.get("mdd_abs")
        return {"mdd": None if mdd is None else -mdd, "volatility_ann": r.get("volatility_ann"),
                "cagr": r.get("cagr"), "sharpe": r.get("sharpe")}  # fmt: skip

    def at(self, view: bt.PriceView, day: str | None = None) -> DayView:
        """cutoff(= `view.cutoff`) 의 H 결정 입력. `day` = 결정일(없으면 cutoff).

        같은 cutoff 를 다시 부르면 같은 결과(원장을 두 번 쌓지 않는다)."""
        cutoff = view.cutoff
        day = day or cutoff
        if day < cutoff:
            raise ValueError(f"결정일 {day} 이 cutoff {cutoff} 보다 앞 — 결정은 cutoff 종가 뒤에 한다")
        if self._last is not None:
            if cutoff == self._last.cutoff:
                if day != self._last.day:
                    raise ValueError(
                        f"같은 cutoff {cutoff} 를 다른 결정일({self._last.day} → {day})로 — 원장이 두 번 쌓인다"
                    )
                return self._last
            if cutoff < self._last.cutoff:
                raise ValueError(f"cutoff 가 거꾸로 간다 ({self._last.cutoff} → {cutoff}) — 원장은 순차로만 쌓는다")
            # 결정일도 앞으로만 — 같은 결정일에 cutoff 만 바꿔 부르면 그날 줄이 두 번 쌓였다
            # (운영은 거래일마다 한 벌, 25.1529 반박 검증)
            if day <= self._last.day:
                raise ValueError(f"결정일이 앞으로 가지 않는다 ({self._last.day} → {day}) — 원장 줄이 두 번 쌓인다")
        self._last = self._decide(view, day)
        return self._last

    def _decide(self, view: bt.PriceView, day: str) -> DayView:  # noqa: C901 — 운영 세 단계(신호·위원회·지주)를 한 줄로
        from batch.jobs import company as cjob
        from batch.jobs import signals as signals_job

        cutoff = view.cutoff
        self._weekly_scores(view)
        pool = self.pool(cutoff)
        코스피 = self._index_upto(BENCH_INDEX, cutoff)

        # 1 국면 — 운영 `trend.regimes_for_country` 와 같은 지수, 같은 판정
        regimes = {code: trend.regime_from_closes(code, self._index_upto(code, cutoff), cutoff)
                   for code in trend.COUNTRY_INDEXES[self.country]}  # fmt: skip
        배수 = {code: trend.factor_for(g, self.trend_settings) for code, g in regimes.items()}

        # 2 신호 입력 — 운영 `jobs/signals.build_inputs`. 점수는 이번 주 캐시, 총점은 5팩터(감성 없음)
        점수 = {int(r["stock_id"]): dict(self._scores.get(int(r["stock_id"]), {})) for r in pool}
        총점 = {sid: sc.total_score(fs, self.factor_weights).total if fs else None for sid, fs in 점수.items()}
        장기후보 = sorted((sid for sid, fs in 점수.items() if signals_job.needs_band(fs)),
                      key=lambda sid: (-(총점[sid] or 0), sid))  # fmt: skip
        밴드풀 = set(장기후보[: signals_job.MAX_BAND_STOCKS])
        inputs: dict[int, sg.SignalInput] = {}
        for row in pool:
            sid = int(row["stock_id"])
            if view.last_date(sid) != cutoff:
                continue  # 기준일 종가가 없는 종목은 신호를 내지 않는다 (운영 25.517)
            최근 = view.closes(sid)[-signals_job.RECENT_DAYS:]
            대금 = view.values(sid)[-signals_job.RECENT_DAYS:]
            snaps = self.snapshots.get(sid, [])
            성장 = pit_growth(snaps, cutoff) or {}
            주식수 = int(row["listed_shares"]) if row.get("listed_shares") else None
            통화 = str(row.get("currency") or "KRW")
            band = pbr_now = band_close = None
            if sid in 밴드풀 and 주식수 and 주식수 > 0:
                # 운영 `load_band` — 분할만 반영한 가격 750행 + 기준일까지 접수된 종목 통화 자본
                from batch.jobs import backtest as jb

                수준 = view.level_closes(sid)[-sg.BAND_DAYS:]
                자본들 = jb.pit_equities(snaps, cutoff, currency=통화)
                if 수준 and 자본들:
                    band = sg.build_band(sg.pbr_series(수준, 자본들, 주식수))
                if band is not None:
                    band_close = 수준[-1][1]
                    자본 = pit_latest_equity(snaps, cutoff, 통화)
                    pbr_now = band_close / (자본 / 주식수) if 자본 and 자본 > 0 else None
            inputs[sid] = sg.SignalInput(
                stock_id=sid, ticker=str(row["ticker"]), name=str(row.get("name") or row["ticker"]),
                market=str(row["market"]), currency=통화, sector=row.get("sector") or None,
                closes=[c for _d, c in 최근],
                turnovers=[float(v) for v in 대금 if v is not None and math.isfinite(float(v))],
                factor_scores=점수[sid], revenue_growth=성장.get("revenue_growth"),
                operating_income_growth=성장.get("operating_income_growth"), fiscal_year=성장.get("fiscal_year"),
                consolidated=성장.get("consolidated", True), annual_stale=성장.get("stale_reason"),
                pbr_now=pbr_now, band_close=band_close, band=band,
                band_capped=sid not in 밴드풀 and signals_job.needs_band(점수[sid]),
                listed_shares=주식수, price_date=cutoff, score_date=self._score_cutoff,
            )  # fmt: skip

        # 3 신호 — `signals.evaluate` 그대로. 판정은 성과 지표를 보지 않으니(사이징만) 먼저 판정하고
        # 걸린 종목만 지표를 낸다
        signals: dict[int, list[sg.Signal]] = {}
        위험: dict[int, dict[str, float | None]] = {}
        rows: list[report_picks.SignalRow] = []
        for sid, inp in inputs.items():
            if not sg.evaluate(inp):
                continue
            위험[sid] = self._risk(view, sid, 코스피)
            inp.volatility_ann, inp.mdd = 위험[sid]["volatility_ann"], 위험[sid]["mdd"]
            inp.cagr, inp.sharpe, inp.metrics_date = 위험[sid]["cagr"], 위험[sid]["sharpe"], cutoff
            지수 = trend.index_for_market(inp.market) or ""
            f, data = 배수.get(지수, trend.factor_for(None, self.trend_settings))
            나온 = [s for s in sg.evaluate(inp, max_weight_per_stock=self.max_weight_per_stock, targets=self.targets,
                                         regime_factor=f, regime_data=data) if sg.verifiable(s)]  # fmt: skip
            if 나온:
                signals[sid] = 나온
                rows += [_signal_row(s, inp, 총점.get(sid), cutoff, view.last_close(sid), 위험[sid]) for s in 나온]

        # 4 조사 자료 — 운영 `jobs/company.population_dossiers`·`load_dossiers`. 재현할 수 없는 칸은 비운다
        한종목: dict[int, report_picks.SignalRow] = {}
        for r in report_picks.with_criteria(rows):
            한종목.setdefault(r.stock_id, r)

        def _factors(r: report_picks.SignalRow) -> dict[str, float | None]:
            return {k: r.factor_scores.get(k) for k in ("value", "quality", "growth", "momentum")}

        population = [co.Dossier(
            stock_id=sid, ticker=r.ticker, as_of=cutoff, total_score=r.total_score, domestic=True, broker_upsides=[],
            factors=_factors(r), mdd=위험[sid]["mdd"], volatility=위험[sid]["volatility_ann"], perf_as_of=cutoff,
        ) for sid, r in sorted(한종목.items())]  # fmt: skip
        크기 = max(int(c["agenda"]) for c in self.charters.values())
        agenda: list[co.Dossier] = []
        for r in cjob.agenda_rows(rows, 크기):
            idx = trend.index_for_market(r.market)
            g = regimes.get(idx or "")
            agenda.append(co.Dossier(
                stock_id=r.stock_id, ticker=r.ticker, as_of=cutoff, total_score=r.total_score, factors=_factors(r),
                # 증권사 의견·수급·감성·공시 사건은 무료 과거 자료가 없다 — "자료 없음".
                # 공시는 **덮지 않음**(gov_covered False)이어야 "사건 없음 — 중립" 이 아니라
                # "자료 없음" 이 된다 (docs/backtest.md 10장)
                broker_upsides=[], flow_sum=None, turnover_20d=None, flow_days=0, flow_date=None, gov_events=[],
                cb_pct=None, domestic=True, sentiment=None, sentiment_error=None, gov_covered=False,
                mdd=위험[r.stock_id]["mdd"], volatility=위험[r.stock_id]["volatility_ann"], perf_window=None,
                perf_as_of=cutoff, regime=g.state if g else None, regime_index=idx, regime_date=g.date if g else None,
            ))  # fmt: skip

        # 5 표 — 운영 `jobs/paper.holding_step` 과 같은 순서·같은 함수
        approved: dict[str, list[co.Item]] = {}
        표: dict[str, dict[int, float]] = {}
        for c in sorted(self.charters):
            ch = self.charters[c]
            _items, 가결 = co.meet(agenda[: int(ch["agenda"])], ch, population)
            approved[c] = 가결
            표[c] = {i.stock_id: hold.strength(i.vote, float(ch["threshold"])) for i in 가결}
        행들: dict[int, list[report_picks.SignalRow]] = {}
        for r in report_picks.with_criteria(rows):
            행들.setdefault(r.stock_id, []).append(r)
        b_rows = report_picks.select_top(report_picks.with_criteria(rows), self.b_top_n)
        표["B"] = {r.stock_id: 1.0 for r in b_rows}
        b_top = list(dict.fromkeys(r.stock_id for r in b_rows))

        # 6 회사 가중치(원장 — cutoff 이하로 찬 성과만) → 합의 순위
        가중, 성적 = hold.weights(self.ledger.records(view, 코스피, day), sorted(표))
        점수순 = {sid: float(rs[0].total_score or 0) for sid, rs in 행들.items()}
        순위 = hold.rank(표, 가중, 점수순)
        고른 = [x["stock_id"] for x in 순위 if x["eligible"] and x["stock_id"] in 행들][: self.top_n]
        기록 = {x["stock_id"]: {"votes": {c: 표[c][x["stock_id"]] for c in x["voters"]}, "score": x["score"],
                             "weights": 가중} for x in 순위}  # fmt: skip
        순서 = report_picks._기간_순
        고른행 = [r for sid in 고른 for r in sorted(행들.get(sid, []), key=lambda r: 순서.get(r.horizon, 9))]

        # 7 원장에 오늘 줄 — 고른 것(pick)과 추천받았으나 고르지 않은 것(skip), 둘 다 표를 단다(운영 1143~1157·1463)
        for x in 순위:
            sid = x["stock_id"]
            if sid not in 행들:
                continue
            r0 = 행들[sid][0]
            if sid in 고른:
                종류, 까닭 = "pick", "지주사 합의"
            else:
                종류 = "skip"
                까닭 = "지주사: 추천 회사 1곳" if not x["eligible"] else f"지주사: 점수 순위 밖(상위 {self.top_n})"
            self.ledger.add(day, sid, 종류, 까닭, 기록[sid]["votes"], r0.close, r0.close_date)

        return DayView(
            cutoff=cutoff, day=day, score_cutoff=self._score_cutoff, factor_scores=점수, total_scores=총점,
            inputs=inputs, signals=signals, rows=rows, regimes=regimes,
            regime_factors={k: v[0] for k, v in 배수.items()},
            population=population, agenda=agenda, approved=approved, votes=표, company_weights=가중,
            company_stats=성적, ranked=순위, picks=고른, pick_rows=고른행, b_top=b_top,
            holding={sid: 기록[sid] for sid in 고른},
        )  # fmt: skip


# ── 변형 다섯이 하루 재료를 나눠 읽기 (조각 1d, 25.1534) ─────────────────────────────


#: 나눠 줄 기록에서 비우는 칸 — 유니버스 크기(종목마다 신호 입력·팩터 점수)라 1,250일을 들면 메모리가 넘친다.
#: 장부형 전략(`core_strategy`)은 `rows`·`pick_rows` 만 읽는다. 나머지 칸은 그대로 싣는다
SHARED_DROPPED: tuple[str, ...] = ("inputs", "factor_scores", "total_scores")


class SharedDays:
    """`CoreMarket` 한 벌의 하루 재료(`DayView`)를 cutoff 마다 **한 번만** 내고 변형들이 나눠 읽는다
    (docs/backtest.md 10장 조각 1d, 25.1534).

    왜 같아도 되나: `DayView` 는 (cutoff, 결정일)과 H 메모리 원장에만 달린다. `CoreMarket.at` 은 창의 가격만 읽고
    엔진 상태(`view.weights`·`view.holding`)를 읽지 않으며, 원장에는 그날 추천받은 종목이 변형의 매매와 무관하게
    들어간다(조각 1c — 표 없는 운영 결정이 없어 `discard` 를 부를 자리가 없다). 그래서 변형마다 제 `CoreMarket` 을
    돌리면 같은 계산을 다섯 번 한다(실측 약 350분 > 시간 한도 120분).

    - 처음 그 cutoff 를 묻는 읽개가 진짜 `at` 을 부르고 적는다(따로 미리 도는 판이 없다). 다음 읽개는 적힌 것을 받는다
    - **읽기 전용**: 기록은 피클 바이트로 들고 읽개마다 새로 푼다 — 한 변형이 받은 행을 고쳐도 다른 변형·다음 날이
      보지 못한다(시험이 일부러 고쳐 본다). 바이트라 1,250일을 들어도 작다
    - 기록에서는 유니버스 크기 칸(`SHARED_DROPPED`)을 비운다 — 모든 변형(처음 적는 변형 포함)이 같은 꼴을 받는다
    - 순서 규칙은 `CoreMarket` 과 같다(읽개마다 cutoff·결정일이 앞으로만). 적히지 않은 앞 cutoff 를 물으면
      `CoreMarket.at` 이 거꾸로 감을 막는다(`ValueError`)
    """

    def __init__(self, market: CoreMarket) -> None:
        import pickle

        self.market = market
        self._pickle = pickle
        self._days: dict[str, tuple[str, bytes]] = {}  # cutoff → (결정일, 피클)
        self.computed = 0  # 진짜 `at` 을 부른 날 수
        self.replayed = 0  # 적힌 것을 준 날 수

    def reader(self) -> SharedMarket:
        """변형 하나가 `CoreMarket` 대신 받는 읽개 — `make_core_strategy(variant, shared.reader(), …)`."""
        return SharedMarket(self)

    def _fetch(self, view: bt.PriceView, day: str) -> DayView:
        적힌 = self._days.get(view.cutoff)
        if 적힌 is None:
            dv = self.market.at(view, day)
            적힌 = (day, self._pickle.dumps(_slim(dv), protocol=self._pickle.HIGHEST_PROTOCOL))
            self._days[view.cutoff] = 적힌
            self.computed += 1
        else:
            if 적힌[0] != day:
                raise ValueError(f"cutoff {view.cutoff} 를 다른 결정일({적힌[0]} → {day})로 — 변형의 날짜 축이 다르다")
            self.replayed += 1
        return self._pickle.loads(적힌[1])


    def views(self) -> Iterator[DayView]:
        """적힌 하루 재료(cutoff 순)를 **하나씩** 푼다 — 실행이 끝난 뒤 진단(`holding_eq`, 25.1538)이 읽는다.
        한꺼번에 풀면 피클의 약 5배라 5년이면 1GB 남짓 더 든다(25.1541 반박 검증) — 생성기로 하루치만 든다"""
        for _c, (_d, b) in sorted(self._days.items()):
            yield self._pickle.loads(b)


def _slim(dv: DayView) -> DayView:
    from dataclasses import replace

    return replace(dv, **{k: type(getattr(dv, k))() for k in SHARED_DROPPED})


class SharedMarket:
    """`SharedDays` 의 읽개 — 장부형 전략이 `CoreMarket` 에서 쓰는 것만 같은 이름으로 준다."""

    def __init__(self, shared: SharedDays) -> None:
        self.shared = shared
        m = shared.market
        self.country, self.universe = m.country, m.universe
        self.max_weight_per_stock, self.targets = m.max_weight_per_stock, m.targets
        self._last: tuple[str, str] | None = None  # (cutoff, 결정일)
        self._dv: DayView | None = None

    def pool(self, cutoff: str) -> list[dict[str, Any]]:
        return self.shared.market.pool(cutoff)

    def at(self, view: bt.PriceView, day: str | None = None) -> DayView:
        """`CoreMarket.at` 과 같은 약속 — 같은 cutoff·결정일이면 같은 결과, 거꾸로 가면 `ValueError`."""
        cutoff, day = view.cutoff, day or view.cutoff
        if self._last is not None:
            if cutoff == self._last[0]:
                if day != self._last[1]:
                    raise ValueError(f"같은 cutoff {cutoff} 를 다른 결정일({self._last[1]} → {day})로")
                assert self._dv is not None
                return self._dv
            if cutoff < self._last[0] or day <= self._last[1]:
                raise ValueError(f"cutoff·결정일이 앞으로 가지 않는다 ({self._last} → {(cutoff, day)})")
        self._dv = self.shared._fetch(view, day)
        self._last = (cutoff, day)
        return self._dv
