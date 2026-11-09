"""42회차 u3 생존편향 ① gap 정의 — 순수 계산만 (docs/factors.md 12.45 "u3 생존편향 사전 등록", ⓪ 결과 2026-11-09).

**무엇을 하나.** 한국거래소 일별매매정보(basDd 마다 시장마다 전 종목 행)를 받아, 달마다 시점 유니버스 (a) 와
생존 대조군 (b1)·(b2) 의 동일가중 월 수익 계열을 내고, 판정판 줄마다 gap = CAGR(b2) − CAGR(a) 를 그 줄의 월 관측으로
다시 낸다. 그 gap 으로 깎아 읽기(보정 CAGR·깎은 초과·'모름(생존편향)')를 한다.

**하지 않는 것.** 호출·DB·시각을 모른다(전부 인자). 어느 작업에도 잇지 않았다 — ① 실행은 12월 판정 실행 뒤(12.45 단계).
'오늘도 있는가'(b1)·오늘 스냅숏(b2)은 일부러 미래를 쓴 생존 표시라 편향 크기 대조로만 쓴다. 전략·점수 경로가 이 모듈을
import 하지 않는다(12.45 시점 문단).

정의(12.45 그대로 — 고친 곳 없음, 모호한 곳은 보수적으로 정하고 그 자리에 적었다)
  (a)   달 m 의 cutoff = 전달 마지막 거래일 응답. 그 응답에 행이 있고, 정적 제외가 아니고,
        MKTCAP ≥ `UniverseFilters.korea().min_market_cap`, `pit_universe.judge`(20일 거래대금·상장 1년) 통과.
        관리종목은 거르지 않는다
  정지 중 보유  전달 (a) 에 든 종목이 cutoff 에 정지(그날 행의 ACC_TRDVAL 0, 또는 그날 행 없음)이고 그 뒤 행이 있으면
        필터를 보지 않고 그대로 둔다(엔진 "거래정지는 팔 수 없다", backtest.md 1.2)
  (b1)  (a) ∩ 마지막 응답에 ISU_CD 가 있는 종목     (b2) (a) ∩ 스냅숏 종목
  분위  (a) 안 cutoff MKTCAP 오름차순 같은 수씩 다섯(q1 = 가장 작다), (b2_q) = (a_q) ∩ 스냅숏
  달 수익  그달 거래일 Π(1 + r_d) − 1, r_d = FLUC_RT/100. 사라진 계열의 마지막 관측 20거래일 안에서
        |FLUC_RT − 원 종가 비율| > 1%p 인 날은 원 종가 비율. 행 없는 날(사라진 뒤 포함) r = 0
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from batch.services import pit_universe as pit
from batch.services.universe import UniverseFilters, is_spac

#: 탐침 호출 시작일(12.45 "2021-09-01~", 금고 B 때문에 그 전은 부르지 않는다). 상장 1년 규칙의 '이력 시작' 이다
PROBE_START = "2021-09-01"
#: 사라지기 전 창 — 마지막 관측 20거래일(12.45)
DELIST_WINDOW = 20
#: FLUC_RT 대 원 종가 비율이 이 %p 를 **넘게** 갈리면 원 종가 비율(12.45 "> 1%p")
DIVERGE_PP = 1.0
#: 진단 — 사라지기 전 |FLUC_RT| 가 이 % 를 **넘는** 날(12.45 "> 30%", 정리매매 꼴)
BIG_MOVE_PCT = 30.0
QUINTILES = 5
#: 깎은 초과 ≤ 0 이거나 묶인 탐침이 없을 때 0→1 초과 칸에 적는 말(12.45 깎아 읽기 ②)
VERDICT_UNKNOWN = "모름(생존편향)"

GROUPS = ("a", "b1", "b2")


def _filters() -> UniverseFilters:
    return UniverseFilters.korea()


#: 문턱은 운영 정의처에서 빌린다(하드코딩하지 않음 — 시험이 같음을 묶는다)
MIN_MARKET_CAP = _filters().min_market_cap
MIN_AVG_TURNOVER_20D = _filters().min_avg_turnover_20d
MIN_LISTED_DAYS = _filters().min_listed_days
HISTORY_EDGE_DAYS = pit.HISTORY_EDGE_DAYS
TURNOVER_DAYS = pit.TURNOVER_DAYS


def is_preferred(code: str) -> bool:
    """우선주 — 단축코드 끝자리 ≠ 0 (12.45 ⓪ 결과로 고정 — ⓪ 탐침 `preferred_by_code` 와 같은 식)."""
    return len(code) == 6 and code[-1] != "0"


@dataclass(frozen=True)
class Row:
    """한 (날, 종목) 행 — 쓰는 칸만. 이름은 KRX 응답 키와 `KrxDailyRow` 필드를 따른다."""

    isu_cd: str
    isu_nm: str
    close: float | None  # TDD_CLSPRC
    change_pct: float | None  # FLUC_RT (%)
    value: float | None  # ACC_TRDVAL
    market_cap: float | None  # MKTCAP


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_row(raw: Any) -> Row:
    """`KrxDailyRow`·KRX 원 응답 dict(대문자 키)·`Row` 를 `Row` 로."""
    if isinstance(raw, Row):
        return raw
    if isinstance(raw, Mapping):
        return Row(str(raw.get("ISU_CD", "")), str(raw.get("ISU_NM", "")), _num(raw.get("TDD_CLSPRC")),
                   _num(raw.get("FLUC_RT")), _num(raw.get("ACC_TRDVAL")), _num(raw.get("MKTCAP")))  # fmt: skip
    return Row(raw.isu_cd, raw.isu_nm, _num(raw.close), _num(raw.change_pct), _num(raw.value), _num(raw.market_cap))


def iso(d: str) -> str:
    """basDd(YYYYMMDD) 또는 ISO → ISO."""
    d = str(d)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d[:10]


def _prev_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


@dataclass
class World:
    """계열 열쇠 = ISU_CD 하나(시장을 가리지 않는다 — 이전상장은 한 계열)."""

    dates: list[str]
    rows: dict[str, dict[str, Row]]  # code → {날: 행}
    dup_rows: int = 0  # 같은 날 두 시장에 같은 ISU_CD — 거래대금이 큰 쪽(같으면 먼저 본 쪽)을 남겼다

    def series_dates(self, code: str) -> list[str]:
        return sorted(self.rows[code])


def build_world(responses: Mapping[str, Mapping[str, Iterable[Any]]]) -> World:
    """{basDd: {시장: [행]}} → World."""
    rows: dict[str, dict[str, Row]] = defaultdict(dict)
    dup = 0
    days: set[str] = set()
    for bas, by_market in responses.items():
        d = iso(bas)
        days.add(d)
        for market in sorted(by_market):
            for raw in by_market[market]:
                r = to_row(raw)
                if not r.isu_cd:
                    continue
                old = rows[r.isu_cd].get(d)
                if old is not None:
                    dup += 1
                    if (r.value or 0) <= (old.value or 0):
                        continue
                rows[r.isu_cd][d] = r
    return World(sorted(days), dict(rows), dup)


def static_excluded(world: World, code: str) -> bool:
    """정적 제외 — (a)·(b1)·(b2) 에 대칭. 스팩은 관측한 어느 날 이름이든 '스팩' 이면(운영 `is_spac` 규칙).

    '어느 날이든' 으로 고른 까닭: 스팩은 합병하면 이름이 바뀌는데 날마다 다르게 빼면 같은 계열이 달마다 들고 나서
    대칭이 깨진다. 계열 하나에 한 번 정해 세 무리 모두에서 같이 뺀다(오늘 스냅숏도 운영 규칙으로 스팩을 이미 뺐다).
    """
    return is_preferred(code) or any(is_spac(r.isu_nm) for r in world.rows[code].values())


def month_cutoffs(dates: Iterable[str]) -> dict[str, str]:
    """거래일 → {달: cutoff(전달 마지막 거래일)}. 첫 달은 cutoff 가 없어 빠진다."""
    last: dict[str, str] = {}
    for d in sorted(iso(x) for x in dates):
        last[d[:7]] = d
    return {m: last[_prev_month(m)] for m in sorted(last) if _prev_month(m) in last}


@dataclass
class ProbeResult:
    #: 계열 이름(a·b1·b2·a_q1..5·b2_q1..5) → {달: 수익 | None(빈 무리)}
    series: dict[str, dict[str, float | None]]
    #: 달 → 무리 이름 → 종목
    members: dict[str, dict[str, frozenset[str]]]
    #: 달 → 그달 마지막 거래일(부분 끝 달 판정에 쓴다)
    month_last_day: dict[str, str]
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _daily_returns(world: World, last_day: str) -> tuple[dict[str, dict[str, float]], int, dict[str, bool]]:
    """종목 → {날: r}. 사라진 계열의 마지막 20 관측 안에서 갈린 날은 원 종가 비율. 갈린 (날, 종목) 수·큰 움직임도."""
    out: dict[str, dict[str, float]] = {}
    diverged = 0
    big: dict[str, bool] = {}
    for code, by_day in world.rows.items():
        ds = sorted(by_day)
        gone = ds[-1] < last_day
        창_시작 = len(ds) - DELIST_WINDOW
        rs: dict[str, float] = {}
        큰 = False
        for i, d in enumerate(ds):
            r = by_day[d]
            prev = by_day[ds[i - 1]] if i > 0 else None
            raw = (r.close / prev.close - 1) if prev and prev.close and r.close is not None and prev.close > 0 else None
            fluc = r.change_pct / 100 if r.change_pct is not None else None
            if gone and i >= 창_시작:
                if r.change_pct is not None and abs(r.change_pct) > BIG_MOVE_PCT:
                    큰 = True
                if raw is not None and (fluc is None or abs(r.change_pct - raw * 100) > DIVERGE_PP):
                    diverged += 1
                    rs[d] = raw
                    continue
            # FLUC_RT 가 없는 날은 0 으로 둔다(⓪ 결과에 그런 행 꼴은 없었다 — 진단으로 센다)
            rs[d] = fluc if fluc is not None else 0.0
        out[code] = rs
        if gone:
            big[code] = 큰
    return out, diverged, big


def _quintiles(codes: Iterable[str], caps: Mapping[str, float | None]) -> dict[int, frozenset[str]]:
    """cutoff MKTCAP 오름차순(같으면 ISU_CD) 다섯 — 순위 i → floor(i·5/n)+1. 시총 모름은 0(가장 작은 쪽)."""
    order = sorted(codes, key=lambda c: (caps.get(c) or 0.0, c))
    n = len(order)
    qs: dict[int, set[str]] = {q: set() for q in range(1, QUINTILES + 1)}
    for i, c in enumerate(order):
        qs[i * QUINTILES // n + 1].add(c)
    return {q: frozenset(s) for q, s in qs.items()}


def compute(
    responses: Mapping[str, Mapping[str, Iterable[Any]]],
    snapshot: Iterable[str],
    cutoffs: Mapping[str, str] | None = None,
    *,
    history_start: str = PROBE_START,
) -> ProbeResult:
    """월 계열 (a)·(b1)·(b2)·(a_q1..5)·(b2_q1..5) 와 진단.

    Args:
        responses: {basDd: {시장: [행]}} — 행은 `KrxDailyRow`·원 응답 dict·`Row`
        snapshot: (b2) 스냅숏의 ISU_CD
        cutoffs: {달 YYYY-MM: cutoff}. 없으면 `month_cutoffs`. 주면 '전달 마지막 거래일' 인지 확인, 아니면 ValueError
    """
    world = build_world(responses)
    if not world.dates:
        return ProbeResult({}, {}, {}, {})
    snap = frozenset(snapshot)
    expected = month_cutoffs(world.dates)
    if cutoffs is None:
        cutoffs = expected
    for m, c in cutoffs.items():
        if expected.get(m) != iso(c):
            raise ValueError(f"{m} 의 cutoff {c} 가 전달 마지막 거래일 응답({expected.get(m)})이 아니다")
    last_day = world.dates[-1]
    days_by_month: dict[str, list[str]] = defaultdict(list)
    for d in world.dates:
        days_by_month[d[:7]].append(d)
    month_last_day = {m: ds[-1] for m, ds in days_by_month.items()}

    excluded = {c for c in world.rows if static_excluded(world, c)}
    live = [c for c in sorted(world.rows) if c not in excluded]
    alive_at_end = frozenset(c for c in live if last_day in world.rows[c])
    last_obs = {c: max(world.rows[c]) for c in live}
    daily, diverged, big = _daily_returns(World(world.dates, {c: world.rows[c] for c in live}), last_day)

    f = _filters()
    pf = pit.Filters(min_market_cap=f.min_market_cap, min_avg_turnover_20d=f.min_avg_turnover_20d,
                     min_listed_days=f.min_listed_days, history_starts=(iso(history_start),))  # fmt: skip
    pseries = {
        c: pit.Series(
            dates=world.series_dates(c),
            closes={d: r.close for d, r in world.rows[c].items() if r.close is not None},
            turnover={d: r.value for d, r in world.rows[c].items() if r.value is not None},
        )
        for c in live
    }

    names = [*GROUPS, *(f"a_q{q}" for q in range(1, 6)), *(f"b2_q{q}" for q in range(1, 6))]
    series: dict[str, dict[str, float | None]] = {n: {} for n in names}
    members: dict[str, dict[str, frozenset[str]]] = {}
    carried_n: dict[str, int] = {}
    prev_a: frozenset[str] = frozenset()

    for m in sorted(cutoffs):
        c = iso(cutoffs[m])
        시장창 = [d for d in world.dates if d <= c][-TURNOVER_DAYS:]
        a: set[str] = set()
        caps: dict[str, float | None] = {}
        carried = 0
        for code in live:
            row = world.rows[code].get(c)
            # 정지 중 보유 — 전달 무리이고, cutoff 에 정지(거래대금 0 또는 행 없음)이고, 아직 사라지지 않았다
            if code in prev_a and last_obs[code] > c and (row is None or (row.value or 0) == 0):
                a.add(code)
                carried += 1
                past = [world.rows[code][d] for d in pseries[code].dates if d <= c]
                caps[code] = row.market_cap if row else (past[-1].market_cap if past else None)
                continue
            if row is None:
                continue
            if row.market_cap is None or row.market_cap < f.min_market_cap:
                continue
            dec = pit.judge(pit.Stock(stock_id=0), pseries[code], c, pf, market_window=시장창)
            if dec.included:
                a.add(code)
                caps[code] = row.market_cap
        carried_n[m] = carried
        qa = _quintiles(a, caps) if a else {q: frozenset() for q in range(1, 6)}
        groups = {"a": frozenset(a), "b1": frozenset(a) & alive_at_end, "b2": frozenset(a) & snap}
        for q in range(1, 6):
            groups[f"a_q{q}"] = qa[q]
            groups[f"b2_q{q}"] = qa[q] & snap
        members[m] = groups
        rets = {code: _month_return(daily[code], days_by_month[m]) for code in a}
        for n in names:
            g = groups[n]
            series[n][m] = sum(rets[x] for x in g) / len(g) if g else None
        prev_a = frozenset(a)

    # 진단 — 정적 제외 뒤 계열만. 데이터 끝(last_day)까지 있는 계열은 사라진 것이 아니다
    gone = [c for c in live if last_obs[c] < last_day]
    by_month: dict[str, int] = defaultdict(int)
    big_by_month: dict[str, int] = defaultdict(int)
    for code in gone:
        by_month[last_obs[code][:7]] += 1
        if big.get(code):
            big_by_month[last_obs[code][:7]] += 1
    diag = {
        "disappeared_by_month": dict(sorted(by_month.items())),
        "big_move_before_disappear_by_month": dict(sorted(big_by_month.items())),
        "disappeared_rows": sum(len(world.rows[c]) for c in gone),
        "total_rows": sum(len(world.rows[c]) for c in live),
        "diverged_pairs": diverged,
        "carried_halted_by_month": carried_n,
        "static_excluded": len(excluded),
        "duplicate_rows": world.dup_rows,
        "fluc_missing_rows": sum(1 for c in live for r in world.rows[c].values() if r.change_pct is None),
    }
    return ProbeResult(series, members, month_last_day, diag)


def _month_return(daily: Mapping[str, float], days: Iterable[str]) -> float:
    """그달 거래일 Π(1 + r) − 1. 행 없는 날(정지 '행 없음' 꼴·사라진 뒤)은 0 — 마지막 종가로 정산된 채 그대로."""
    p = 1.0
    for d in days:
        p *= 1 + daily.get(d, 0.0)
    return p - 1


def cagr_monthly(rets: Iterable[float]) -> float | None:
    """월 수익 n 개 → (Π(1+r))^(12/n) − 1 (metrics.md CAGR = (기말/기초)^(1/연수) − 1, 연수 = n/12)."""
    xs = list(rets)
    if not xs:
        return None
    p = math.prod(1 + r for r in xs)
    if p <= 0:
        return None
    return p ** (12 / len(xs)) - 1


@dataclass(frozen=True)
class Gap:
    months: tuple[str, ...]
    gap: float | None  # CAGR(b2) − CAGR(a)
    gap_q: dict[int, float | None]  # CAGR(b2_q) − CAGR(a_q)
    gap_b1: float | None  # CAGR(b1) − CAGR(a) — ③ 실측 대조에만


def _cagr_of(series: Mapping[str, Mapping[str, float | None]], name: str, months: tuple[str, ...]) -> float | None:
    s = series.get(name, {})
    xs = [s.get(m) for m in months]
    if any(x is None for x in xs):
        return None
    return cagr_monthly(x for x in xs if x is not None)


def gap(
    series_months: Mapping[str, Mapping[str, float | None]],
    row_months: Iterable[str],
    *,
    month_last_day: Mapping[str, str] | None = None,
    row_last_date: str | None = None,
) -> Gap:
    """판정판 한 줄의 월 관측으로 gap 을 다시 낸다(gap 하나를 모든 줄에 쓰지 않는다 — 12.45).

    `row_months` 는 그 줄의 월 관측(`luck.monthly_returns` 의 달). `row_last_date` 가 그 끝 달의 마지막 거래일보다
    앞이면 부분 끝 달이라 뺀다. 끝 달의 마지막 거래일을 모르면(탐침에 그 달 없음) 빼지 않아 gap 이 '모름' 이 된다.
    줄의 어느 달이든 계열 값이 없으면(빈 무리·탐침 밖) 그 gap 은 None(모름).
    """
    ms = sorted(set(row_months))
    if ms and row_last_date is not None and month_last_day is not None:
        끝 = month_last_day.get(ms[-1])
        if 끝 is not None and iso(row_last_date) < 끝:
            ms = ms[:-1]
    months = tuple(ms)
    if not months:
        return Gap(months, None, {q: None for q in range(1, 6)}, None)

    def diff(x: str, y: str) -> float | None:
        cx, cy = _cagr_of(series_months, x, months), _cagr_of(series_months, y, months)
        return None if cx is None or cy is None else cx - cy

    return Gap(months, diff("b2", "a"), {q: diff(f"b2_q{q}", f"a_q{q}") for q in range(1, 6)}, diff("b1", "a"))


def adjusted_cagr(cagr: float | None, g: float | None) -> float | None:
    """생존편향 보정 어림 = CAGR − max(0, gap). gap 이 음수면 깎지 않는다."""
    if cagr is None or g is None:
        return None
    return cagr - max(0.0, g)


def trimmed_excess(excess: float | None, g: float | None, gap_q: Mapping[int, float | None]) -> float | None:
    """깎은 초과 = 초과 − max(0, max_q gap_q − gap). 분위 gap 하나라도 모르면 None."""
    if excess is None or g is None or not gap_q or any(v is None for v in gap_q.values()):
        return None
    worst = max(v for v in gap_q.values() if v is not None)
    return excess - max(0.0, worst - g)


def worst_quintile(gap_q: Mapping[int, float | None]) -> int | None:
    """'가장 나쁜 분위' = gap_q 가 가장 큰 분위(같으면 작은 번호)."""
    known = {q: v for q, v in gap_q.items() if v is not None}
    if not known:
        return None
    return min(known, key=lambda q: (-known[q], q))


def excess_verdict(verdict: str, trimmed: float | None, *, tied_probe: bool) -> str:
    """0→1 초과 칸. 묶인 탐침이 없거나 깎은 초과가 모름·≤ 0 이면 '모름(생존편향)', 아니면 원래 판정 그대로
    (문턱은 그대로, 올림을 더 엄하게만 — 25.2083 꼴 막이)."""
    if not tied_probe or trimmed is None or trimmed <= 0:
        return VERDICT_UNKNOWN
    return verdict
