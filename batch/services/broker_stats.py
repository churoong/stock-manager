"""증권사 적중률 성적표 (docs/brokers.md, docs/infra.md 25.995).

KIS 증권사 투자의견(`kr_opinions`, 25.988 — 1년에 100행까지, 발표일 기준)과 우리 시세로 증권사마다 셋을 잰다.

1. **제시 상승여력** — 목표가 ÷ 기준가 − 1 의 평균. 늘 크면 낙관 편향이다
2. **의견 뒤 60거래일 초과수익** — 기준가에서 60거래일 뒤 수익 − 같은 기간 지수(코스피·코스닥) 수익.
   상향 목표가(상승여력 > 0)만 "맞힘(초과수익 > 0)" 비율을 낸다
3. **목표가 터치율** — 120거래일 안에 고가가 목표가에 닿은 비율. 상향 목표가만, 120거래일이 다 지난 것만
   센다(25.1148)

**시세가 끊긴 종목도 센다** (25.1155, 감사 재현 — 생존편향). 의견 뒤 상장폐지·장기 정지로 시세가 끊기면 창이
영영 안 차 조용히 빠졌다 — 나쁜 결과일수록 빠져 증권사가 더 잘 맞힌 것처럼 보였다. 신호 성적표(`outcomes`,
25.696·25.697)와 같은 규칙: 그 종목 마지막 날이 **시세 전체의 마지막 날**보다 `CUTOFF_GAP_DAYS` 넘게 앞이면 끊긴
것으로 보고, 시장 거래일(지수 날짜)로 창의 끝날이 지났으면 **마지막 종가를 청산가**로 창을 채운다.

기준가는 발표일 **다음 거래일** 종가다 — 발표일 당일 종가를 쓰면 장 뒤 발표의 반응이 기준에 섞인다
`[확인필요: 발표 시각]`. 기간 안에 분할·병합(수정계수가 2% 넘게 바뀜)이 있으면 그 의견은 뺀다 — 목표가와
주가의 단위가 달라진다. 60일 뒤 값은 "그날 의견이 맞았나" 를 사후에 매기는 것이라 미래 정보 문제가 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from batch.services.outcomes import CUTOFF_GAP_DAYS

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
    #: 익은 의견의 (종목, 기준 거래일 자리) — 같은 종목 반복 의견의 창 겹침을 세는 데만 쓴다 (25.1361, 기록만)
    fwd_at: list[tuple[int, int]] = field(default_factory=list)
    touch_at: list[tuple[int, int]] = field(default_factory=list)

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

    def clusters(self) -> dict:
        """창이 겹치지 않는 묶음 수 — 기록만 (docs/brokers.md, docs/infra.md 25.1361, 23회차 P5).

        같은 종목에 60거래일 안에 다시 낸 의견은 앞 의견과 창이 겹쳐 독립 표본이 아니다(Hansen·Hodrick 1980).
        종목마다 앞에서부터, 직전에 센 의견에서 창 길이(초과수익 `FWD_DAYS`, 터치 `TOUCH_DAYS`) 이상 떨어진 의견만 센다.
        표본 문(`MIN_N`·`verdict.SKILLED_MIN_N`)은 그대로 — 이 수가 문을 밑돌게 할 증권사가 있는지 먼저 본다"""
        return {"n_fwd_clusters": _clusters(self.fwd_at, FWD_DAYS),
                "n_touch_clusters": _clusters(self.touch_at, TOUCH_DAYS)}  # fmt: skip


def _clusters(at: list[tuple[int, int]], gap: int) -> int:
    n = 0
    앞: dict[int, int] = {}
    for sid, i in sorted(at):
        if sid not in 앞 or i - 앞[sid] >= gap:
            앞[sid] = i
            n += 1
    return n


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
    opinions: list[Opinion], bars: dict[int, list[Bar]], index_bars: dict[str, list[Bar]],
    stats: dict[str, BrokerStat] | None = None,
) -> list[dict]:
    """증권사별 성적. `bars`·`index_bars` 는 날짜 오름차순. `stats` 를 주면 거기에 채운다(묶음 진단, 25.1361)."""
    stats = {} if stats is None else stats
    index_close = {code: {b.date: b.close for b in bs} for code, bs in index_bars.items()}
    # 끊김 판정의 기준 — 지수가 아니라 시세 전체의 마지막 날 (outcomes 25.697 과 같다)
    market_last = max((bs[-1].date for bs in bars.values() if bs), default=None)
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
        ix = index_close.get(op.index_code, {})
        끊김 = market_last is not None and (
            date.fromisoformat(market_last) - date.fromisoformat(series[-1].date)
        ).days > CUTOFF_GAP_DAYS
        # 끊긴 종목은 시장 거래일로 창을 센다 — 기준일 뒤 시장 거래일(시세 전체 마지막 날까지)
        시장일 = sorted(d for d in ix if series[i].date < d <= market_last) if 끊김 and market_last else []
        j = i + FWD_DAYS
        if j < len(series):
            i0, i1 = ix.get(series[i].date), ix.get(series[j].date)
            if i0 and i1:
                st.excess.append((series[j].close / base - 1) - (i1 / i0 - 1))
                st.fwd_at.append((op.stock_id, i))
        elif len(시장일) >= FWD_DAYS:
            i0, i1 = ix.get(series[i].date), ix.get(시장일[FWD_DAYS - 1])
            if i0 and i1:
                st.excess.append((series[-1].close / base - 1) - (i1 / i0 - 1))
                st.fwd_at.append((op.stock_id, i))
        window = series[i : i + TOUCH_DAYS + 1]
        # **120거래일이 다 찬 의견만 센다** (25.1148, 감사 재현). "이미 닿은 것" 도 세면 덜 찬 의견 가운데 닿은 것만
        # 분모·분자에 들어가 실제 20% 가 30.8% 로 보였다 — 최근 의견이 많은 증권사일수록 부풀어
        # `verdict.skilled` 가중이 틀어진다
        if len(window) == TOUCH_DAYS + 1 or len(시장일) >= TOUCH_DAYS:
            st.touched.append(any((b.high or b.close) >= op.target_price for b in window))
            st.touch_at.append((op.stock_id, i))
    return sorted((s.row() for s in stats.values()), key=lambda r: (-(r["n_fwd"] or 0), r["broker"]))


def cluster_summary(stats: dict[str, BrokerStat], gate: int = MIN_N) -> dict:
    """`compute` 가 채운 `stats` 로 증권사별 겹치지 않는 묶음 수와, 표본 문을 묶음 수로 보면 **문 밑으로 내려갈**
    증권사 수 — 기록만 (25.1361). 0 이면 이 진단을 접는다(docs/factors.md 12.25 P5)"""
    줄 = {b: {**s.clusters(), "n_fwd": len(s.excess), "n_touch": len(s.touched)} for b, s in stats.items()}
    return {
        "brokers": 줄,
        "fwd_gate_drop": sum(1 for r in 줄.values() if r["n_fwd"] >= gate > r["n_fwd_clusters"]),
        "touch_gate_drop": sum(1 for r in 줄.values() if r["n_touch"] >= gate > r["n_touch_clusters"]),
    }


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
