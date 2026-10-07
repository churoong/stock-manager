"""팩터 IC — 점수 순위와 다음 기간 수익률 순위의 상관 (docs/backtest.md 2.5, docs/factors.md 12장).

**왜 있나.** 종목선정 기법 발굴 루프(CLAUDE.md)는 새 기법을 실제 추천에 켜기 전에 **효용**을 재야 한다.
상위 20 종목 전략의 CAGR 차이만으로는 5년 표본에서 잡음이 크다(1회차 검증 2 지적). 그래서 리밸런스마다
"점수 순위가 다음 달 수익률 순위를 얼마나 맞혔나"(스피어만 순위 상관)를 재고, 그 평균의 t 값을 본다.

이 모듈은 **계산만** 한다. DB 도 시각도 모른다. 점수는 t-1 까지의 값으로 낸 것이어야 하고(부르는 쪽 책임),
수익률은 평가용으로만 쓴다 — 판단에 들어가지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: 한 리밸런스의 IC 를 내려면 점수·수익률이 모두 있는 종목이 이만큼은 있어야 한다.
#: 순위 상관은 표본이 작으면 크게 흔들린다. 업종 z-score 의 최소 집단(`scoring.MIN_PEER_SIZE` 30)과 같은 값을 쓴다
MIN_STOCKS = 30

#: 켜는 기준의 t 값 문턱과 최소 개월 수 (docs/factors.md 12장 "켜는 기준")
T_THRESHOLD = 2.0
MIN_MONTHS = 36
#: 그 지표가 비지 않은 종목이 후보의 이만큼은 돼야 판정한다 (켜는 기준 "유니버스의 60% 이상", 25.442)
MIN_COVERAGE = 0.6
#: t 값의 Newey-West 시차. 월 IC 는 점수가 느리게 변하는 팩터(밸류·퀄리티)에서 이어져 t 가 부푼다(25.442)
NW_LAGS = 3


def _ranks(values: list[float]) -> list[float]:
    """평균 순위(동점은 평균). 1 부터."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """스피어만 순위 상관. 한쪽이 전부 같은 값이면(분산 0) None — 순위가 아무것도 말하지 않는다."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def partial_period_ic_n(
    scores: dict[int, float | None],
    control: dict[int, float | None],
    forward_returns: dict[int, float | None],
) -> tuple[float | None, int]:
    """**증분 IC** — 통제 지표의 순위를 걷어 낸 뒤 남는 점수·수익률 순위의 편상관 (docs/backtest.md 2.5, 25.742).

    발굴 루프에서 "이미 있는 지표를 통제해도 남는가" 를 재야 하는 후보가 쌓였다
    (2회차 B SURGE·3회차 D EAR 는 SUE 통제, 3회차 B 는 단기 반전 통제).
    스피어만 편상관 `(r_xy − r_xz·r_yz) / √((1 − r_xz²)(1 − r_yz²))` — 교과서 식이라 새 문턱이 없다.
    셋 다 있는 종목만 쓰고, 표본 하한은 `MIN_STOCKS` 그대로. 점수와 통제가 완전히 같은 순위면(분모 0) None —
    통제하면 남는 것이 없다는 뜻이다.
    """
    rows = [
        (float(s), float(c), float(r))
        for sid, s in scores.items()
        if s is not None and (c := control.get(sid)) is not None and (r := forward_returns.get(sid)) is not None
    ]
    if len(rows) < MIN_STOCKS:
        return None, len(rows)
    xs, zs, ys = [a for a, _, _ in rows], [b for _, b, _ in rows], [c for _, _, c in rows]
    rxy, rxz, ryz = spearman(xs, ys), spearman(xs, zs), spearman(ys, zs)
    if rxy is None or rxz is None or ryz is None:
        return None, len(rows)
    밑 = (1 - rxz**2) * (1 - ryz**2)
    if 밑 <= 1e-12:
        return None, len(rows)
    return (rxy - rxz * ryz) / math.sqrt(밑), len(rows)


def period_ic(scores: dict[int, float | None], forward_returns: dict[int, float | None]) -> float | None:
    """한 리밸런스의 IC. 점수·수익률이 모두 있는 종목만 쓴다. `MIN_STOCKS` 미만이면 None."""
    return period_ic_n(scores, forward_returns)[0]


def period_ic_n(
    scores: dict[int, float | None], forward_returns: dict[int, float | None]
) -> tuple[float | None, int]:
    """(IC, 쓴 종목 수). 종목 수는 표본 비율(`MIN_COVERAGE`)을 재는 데 쓴다 (25.442)."""
    both = [
        (float(s), float(r))
        for sid, s in scores.items()
        if s is not None and (r := forward_returns.get(sid)) is not None
    ]
    if len(both) < MIN_STOCKS:
        return None, len(both)
    return spearman([s for s, _ in both], [r for _, r in both]), len(both)


def period_ic_by_market(
    scores: dict[int, float | None],
    forward_returns: dict[int, float | None],
    market_of: dict[int, str | None],
    control: dict[int, float | None] | None = None,
) -> tuple[float | None, int, dict[str, tuple[float | None, int]]]:
    """**시장 안에서** 잰 IC 를 쓴 종목 수로 가중 평균한다 (docs/backtest.md 2.5, factors.md 12.8 — 6회차 1, 25.810).

    원값 지표(단기 반전·거래대금 급증·업종 모멘텀 등)를 KOSPI·KOSDAQ 섞어 순위를 내면 IC 가 종목 선별이 아니라
    두 시장 사이의 타이밍을 일부 잰다. 시장마다 `period_ic_n`(control 이 있으면 증분 IC `partial_period_ic_n`)을
    내고, 표본이 `MIN_STOCKS` 미만인 시장은 그 달에서 뺀다.
    돌려주는 것: (합친 IC, 남은 시장들의 쓴 종목 수, {시장: (IC, 쓴 종목 수)}).
    시장을 모르는 종목은 "?" 한 묶음이다.
    """
    groups: dict[str, list[int]] = {}
    for sid in scores:
        groups.setdefault(market_of.get(sid) or "?", []).append(sid)
    per: dict[str, tuple[float | None, int]] = {}
    for market, ids in sorted(groups.items()):
        part = {sid: scores[sid] for sid in ids}
        if control is None:
            per[market] = period_ic_n(part, forward_returns)
        else:
            per[market] = partial_period_ic_n(part, control, forward_returns)
    kept = [(ic, n) for ic, n in per.values() if ic is not None]
    total = sum(n for _, n in kept)
    if not total:
        return None, 0, per
    return sum(ic * n for ic, n in kept) / total, total, per


@dataclass(frozen=True)
class IcSummary:
    months: int  # IC 를 낸 리밸런스 수
    mean: float | None
    std: float | None
    t_stat: float | None  # mean / NW 표준오차 (자기상관 보정, 25.442)
    first_half_mean: float | None
    second_half_mean: float | None
    #: 쓴 종목 수 / 후보 수 의 평균 — 켜는 기준의 "비지 않은 종목 60%" (25.442). 모르면 None
    coverage: float | None = None
    #: IC 1차 자기상관 — 크면 t 를 의심한다(기록용)
    autocorr: float | None = None
    #: 이진 지표가 1 인 종목 비율의 달 평균(기록용, 배당 중단 25.479). 거의 안 걸리면 IC 는 잡음이다
    hit_rate: float | None = None
    #: 시장을 섞어 낸 예전 IC 의 요약 — **참고용, 판정에 쓰지 않는다** (6회차 1, 25.810)
    mixed: IcSummary | None = None
    #: 시장별 요약과 그 시장이 표본 부족으로 빠진 달 수 — (시장, 요약, 뺀 달 수) (25.810)
    by_market: tuple[tuple[str, IcSummary, int], ...] = ()

    def verdict(self) -> str:
        """"통과" · "탈락" · "판정 불가(사유)". 판정 불가는 **기법이 떨어진 것과 다르다** (25.442, 교차검증).

        창이 짧거나(개월 수 부족) 표본이 성기면(비율 부족) 효용을 말할 수 없다 — 탈락으로 적으면
        "재 봤는데 없다" 로 읽힌다."""
        if self.months < MIN_MONTHS:
            return f"판정 불가(IC {self.months}개월 < {MIN_MONTHS})"
        if self.coverage is None or self.coverage < MIN_COVERAGE:
            return f"판정 불가(표본 비율 {self.coverage if self.coverage is not None else '모름'} < {MIN_COVERAGE:g})"
        return "통과" if self.passes() else "탈락"

    def passes(self) -> bool:
        """켜는 기준의 IC 부분 (docs/factors.md 12장): 36개월 이상, 표본 비율 60% 이상,
        t(NW) ≥ 2, 앞·뒤 반의 평균이 모두 양."""
        if self.months < MIN_MONTHS or self.t_stat is None or self.t_stat < T_THRESHOLD:
            return False
        if self.coverage is None or self.coverage < MIN_COVERAGE:
            return False
        if self.first_half_mean is None or self.second_half_mean is None:
            return False
        return self.first_half_mean > 0 and self.second_half_mean > 0


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def newey_west_se(xs: list[float], lags: int = NW_LAGS) -> float | None:
    """평균의 Newey-West 표준오차(Bartlett 가중). 시차 0 이면 보통의 표준오차 σ/√n 과 같은 식이다
    (분산은 n 으로 나눈다 — NW 의 관례). 표본이 2 미만이거나 분산이 0 이하이면 None."""
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    d = [x - m for x in xs]
    s = sum(v * v for v in d) / n
    for lag in range(1, min(lags, n - 1) + 1):
        w = 1 - lag / (lags + 1)
        s += 2 * w * sum(d[i] * d[i - lag] for i in range(lag, n)) / n
    return math.sqrt(s / n) if s > 0 else None


def _autocorr(xs: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    den = sum((x - m) ** 2 for x in xs)
    return sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, len(xs))) / den if den else None


def summarize(ics: list[float | None], coverages: list[float] | None = None) -> IcSummary:
    """리밸런스 순서대로의 IC 목록(None 은 그 달 표본 부족)을 요약한다.

    `coverages` 는 달마다 (쓴 종목 수 / 후보 수). 주면 평균을 `coverage` 로 남긴다 — 판정에 쓴다.
    """
    kept = [x for x in ics if x is not None]
    n = len(kept)
    mean = _mean(kept)
    std = math.sqrt(sum((x - mean) ** 2 for x in kept) / (n - 1)) if n >= 2 and mean is not None else None
    se = newey_west_se(kept)
    t_stat = mean / se if se and mean is not None else None
    half = n // 2
    return IcSummary(
        months=n,
        mean=mean,
        std=std,
        t_stat=t_stat,
        first_half_mean=_mean(kept[:half]) if half else None,
        second_half_mean=_mean(kept[half:]) if half else None,
        coverage=_mean(coverages) if coverages else None,
        autocorr=_autocorr(kept),
    )


#: 발굴 루프 이름의 다중검정 기록에서 쓰는 거짓 발견률 (6회차 2, 25.810) — **기록용, 켜는 기준에 넣지 않는다**
FDR_Q = 0.05


def one_sided_p(t_stat: float | None) -> float:
    """t(NW) 의 한쪽 p — 정규분포로 근사한다(factors.md 12.8 에 미리 적은 분포). 모르면 1."""
    if t_stat is None:
        return 1.0
    return 0.5 * math.erfc(t_stat / math.sqrt(2))


def bh_record(summaries: dict[str, IcSummary], names: tuple[str, ...]) -> dict[str, dict[str, float | bool]]:
    """Benjamini-Hochberg q 값 (6회차 2, 25.810).

    **묶음 크기는 `names` 의 수로 고정**한다 — 판정 불가인 이름은 p=1 로 넣는다(그 실행에서 판정 가능한 이름만
    묶으면 한 이름의 문턱이 다른 이름의 표본 사정에 따라 움직인다, 검증 1).
    실행 기록에만 남기고 `verdict`·`passes` 에는 쓰지 않는다(검증 2 — 경계선 IC 에서 검정력이 모자란다)."""
    m = len(names)
    def 판정됨(s: IcSummary | None) -> bool:
        return s is not None and not s.verdict().startswith("판정 불가")

    p = {n: one_sided_p(summaries[n].t_stat) if 판정됨(summaries.get(n)) else 1.0 for n in names}
    order = sorted(names, key=lambda n: p[n])
    q: dict[str, float] = {}
    running = 1.0
    for rank in range(m, 0, -1):
        n = order[rank - 1]
        running = min(running, p[n] * m / rank)
        q[n] = running
    return {n: {"p": p[n], "q": q[n], "bh_pass": q[n] <= FDR_Q} for n in names}
