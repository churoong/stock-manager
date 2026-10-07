"""증권사 적중률 성적표 (docs/brokers.md, docs/infra.md 25.995).

KIS 증권사 투자의견(`kr_opinions`, 25.988 — 1년에 100행까지, 발표일 기준)과 우리 시세로 증권사마다 셋을 잰다.

1. **제시 상승여력** — 목표가 ÷ 기준가 − 1 의 평균. 늘 크면 낙관 편향이다
2. **의견 뒤 60거래일 초과수익** — 기준가에서 60거래일 뒤 수익 − 같은 기간 지수(코스피·코스닥) 수익.
   상향 목표가(상승여력 > 0)만 "맞힘(초과수익 > 0)" 비율을 낸다
3. **목표가 터치율** — 120거래일 안에 고가가 목표가에 닿은 비율. 상향 목표가만, 120거래일이 다 지났거나
   이미 닿은 것만 센다

기준가는 발표일 **다음 거래일** 종가다 — 발표일 당일 종가를 쓰면 장 뒤 발표의 반응이 기준에 섞인다
`[확인필요: 발표 시각]`. 기간 안에 분할·병합(수정계수가 2% 넘게 바뀜)이 있으면 그 의견은 뺀다 — 목표가와
주가의 단위가 달라진다. 60일 뒤 값은 "그날 의견이 맞았나" 를 사후에 매기는 것이라 미래 정보 문제가 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 초과수익을 재는 거래일 수
FWD_DAYS = 60
#: 목표가 터치를 보는 거래일 수 — 증권사 목표가는 대개 12개월이지만 우리 이력이 1년이라 반년으로 본다 `[확인필요]`
TOUCH_DAYS = 120
#: 화면에 성적을 보이는 최소 표본(익은 의견 수). 이보다 적으면 "표본 부족" 으로만 적는다
MIN_N = 20
#: 기간 안 수정계수(adj_close ÷ close) 변화가 이보다 크면 기업행위로 보고 뺀다
ACTION_TOLERANCE = 0.02


@dataclass(frozen=True)
class Opinion:
    stock_id: int
    index_code: str  # KOSPI · KOSDAQ
    date: str
    broker: str
    target_price: float | None


@dataclass(frozen=True)
class Bar:
    date: str
    close: float
    high: float | None
    adj_ratio: float | None  # adj_close / close (없으면 None)


@dataclass
class BrokerStat:
    broker: str
    n_opinions: int = 0
    upsides: list[float] = field(default_factory=list)
    excess: list[float] = field(default_factory=list)  # 상향 목표가의 60거래일 초과수익
    touched: list[bool] = field(default_factory=list)
    skipped_action: int = 0

    def row(self) -> dict:
        avg = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
        return {
            "broker": self.broker,
            "n_opinions": self.n_opinions,
            "n_target": len(self.upsides),
            "avg_upside_pct": None if not self.upsides else round(avg(self.upsides) * 100, 2),
            "n_fwd": len(self.excess),
            "avg_excess_pct": None if not self.excess else round(avg(self.excess) * 100, 2),
            "hit_pct": None if not self.excess
            else round(sum(1 for x in self.excess if x > 0) / len(self.excess) * 100, 1),
            "n_touch": len(self.touched),
            "touch_pct": None if not self.touched else round(sum(self.touched) / len(self.touched) * 100, 1),
            "skipped_action": self.skipped_action,
        }


def _first_after(bars: list[Bar], day: str) -> int | None:
    """day **다음** 거래일의 인덱스 (발표일 당일은 건너뛴다). 없으면 None."""
    for i, b in enumerate(bars):
        if b.date > day:
            return i
    return None


def _action_between(bars: list[Bar], i: int, j: int) -> bool:
    ratios = [b.adj_ratio for b in bars[i : j + 1] if b.adj_ratio]
    return bool(ratios) and (max(ratios) / min(ratios) - 1) > ACTION_TOLERANCE


def compute(
    opinions: list[Opinion], bars: dict[int, list[Bar]], index_bars: dict[str, list[Bar]]
) -> list[dict]:
    """증권사별 성적. `bars`·`index_bars` 는 날짜 오름차순."""
    stats: dict[str, BrokerStat] = {}
    index_close = {code: {b.date: b.close for b in bs} for code, bs in index_bars.items()}
    for op in opinions:
        st = stats.setdefault(op.broker, BrokerStat(op.broker))
        st.n_opinions += 1
        series = bars.get(op.stock_id) or []
        i = _first_after(series, op.date)
        if i is None or not op.target_price or op.target_price <= 0:
            continue
        base = series[i].close
        if base <= 0:
            continue
        end = min(len(series) - 1, i + max(FWD_DAYS, TOUCH_DAYS))
        if _action_between(series, i, end):
            st.skipped_action += 1
            continue
        upside = op.target_price / base - 1
        st.upsides.append(upside)
        if upside <= 0:
            continue
        j = i + FWD_DAYS
        if j < len(series):
            ix = index_close.get(op.index_code, {})
            i0, i1 = ix.get(series[i].date), ix.get(series[j].date)
            if i0 and i1:
                st.excess.append((series[j].close / base - 1) - (i1 / i0 - 1))
        window = series[i : i + TOUCH_DAYS + 1]
        hit = any((b.high or b.close) >= op.target_price for b in window)
        if hit or len(window) == TOUCH_DAYS + 1:
            st.touched.append(hit)
    return sorted((s.row() for s in stats.values()), key=lambda r: (-(r["n_fwd"] or 0), r["broker"]))


def line(stat: dict | None) -> str:
    """종목 화면·리포트에 붙일 한 줄. 표본이 적으면 그렇다고만."""
    if not stat:
        return "성적 없음"
    if (stat.get("n_fwd") or 0) < MIN_N:
        return f"표본 부족(익은 의견 {stat.get('n_fwd') or 0}건 < {MIN_N})"
    parts = [f"60거래일 초과수익 평균 {stat['avg_excess_pct']:+.1f}%p · 맞힘 {stat['hit_pct']:.0f}%"
             f" ({stat['n_fwd']}건)"]
    if stat.get("touch_pct") is not None:
        parts.append(f"목표가 터치 {stat['touch_pct']:.0f}% ({stat['n_touch']}건)")
    if stat.get("avg_upside_pct") is not None:
        parts.append(f"제시 상승여력 평균 {stat['avg_upside_pct']:+.0f}%")
    return " · ".join(parts)
