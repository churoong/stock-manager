"""단타·스윙 판정 — 그림자 장부와 (11월) 단타·스윙 가상 계좌가 함께 쓰는 순수 함수
(docs/paper.md 16장, docs/infra.md 25.1862).

2026-10-28 사용자 지시 "가상 계좌 두개를 추가해서 하나는 단타, 하나는 스윙 … 지금 가상계좌들은 너무 거래가
없어. 단기간에 성과를 확인할 수가 없어" → "너가 말한대로 진행해줘".
일봉(시가·고가·저가·종가)만으로 **정확히** 판정되는 꼴만 둔다:

- **당일 단타(`DAY`)**: 신호 다음 장 시가에 사서 그날 종가에 판다. 일봉 두 값으로 끝나 판정이 갈릴 데가 없다
- **스윙(`SWING`)**: 다음 장 시가에 사서 목표(+5%)·손절(−3%)·기한(10거래일) 가운데 먼저 닿는 것. 장중 순서를 모르는 날
  (같은 날 고가가 목표, 저가가 손절에 닿음)은 **손절로 본다**(보수 쪽). 다음 날 시가가 선을 건너뛰면 시가로 판다
- **종가 판정 스윙(`SWING_CLOSE`)**: 같은 선·기한을 **종가로만** 보고, 넘은 날의 **다음 장 시가**에 판다(25.1867).
  가상모의투자 체결 엔진은 일봉의 시가·종가만 보고(고가·저가 없음) 매도는 배치가 장 전에 낸 시장가가 다음 장 시가에
  찬다 — 11월 스윙 계좌가 실제로 돌릴 수 있는 꼴은 이것이다. 그림자 장부가 그 계좌 성적의 예고가 되도록 첫 집계
  (11-02) **전에** 함께 등록한다. `swing_v1`(장중 닿음)은 장중 감시선(분봉·KIS) 꼴의 상한 참고로 둔다

값(목표·손절·기한)은 **한 벌만 사전 등록**한다 — 여러 벌을 돌려 좋은 것을 고르면 과최적화다.
근거 없는 첫 값 `[확인필요]`.
진짜 장중 단타(장중 손절·익절)는 분봉이 있어야 판정된다 — 여기서는 하지 않는다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

#: 국내 시가가 전일 종가 대비 이만큼 이상이면 상한가 시가로 보고 사지 않는다(시장가 매수가 줄을 선다 —
#: 가상모의투자 25.1553 과 같은 보수 쪽). 호가 반올림 여유로 30% 대신 29.5%
KR_LIMIT_UP_OPEN = 0.295


@dataclass(frozen=True)
class Bar:
    """분할만 반영한 하루 시세(배당 미포함 — 신호 성적표와 같은 계열)."""

    date: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Rule:
    name: str
    kind: str  # "day" | "swing" | "swing_close"
    target_pct: float | None = None
    stop_pct: float | None = None
    max_sessions: int = 1


#: 사전 등록 판 1 (2026-10-28). 바꾸면 이름(판)을 바꾼다 — 섞어 집계하지 않는다
DAY = Rule("day_v1", "day")
SWING = Rule("swing_v1", "swing", target_pct=0.05, stop_pct=-0.03, max_sessions=10)
#: 판 1 과 같은 값 — 판정 꼴만 다르다(종가로 보고 다음 장 시가에 판다, 25.1867)
SWING_CLOSE = Rule("swing_close_v1", "swing_close", target_pct=0.05, stop_pct=-0.03, max_sessions=10)
RULES = (DAY, SWING, SWING_CLOSE)


@dataclass(frozen=True)
class Costs:
    """편도 %. 매수 수수료·매도 수수료·매도세·슬리피지(시장가에만). `tax_schedule` 이 있으면 **청산일**의 세율
    ((시행일, %) 오름차순 — 백테스트 `Costs.tax_schedule` 과 같은 꼴, 25.1863)."""

    buy_fee_pct: float
    sell_fee_pct: float
    sell_tax_pct: float
    slippage_pct: float
    tax_schedule: tuple[tuple[str, float], ...] = ()

    def tax_at(self, day: str | None) -> float:
        세율 = [r for d, r in self.tax_schedule if day is not None and d <= day]
        return 세율[-1] if 세율 else self.sell_tax_pct


@dataclass(frozen=True)
class Trade:
    stock_id: int
    signal_date: str
    rule: str
    status: str  # "closed" | "open"(아직 끝나지 않음) | "no_entry"
    entry_date: str | None = None
    entry_price: float | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    #: close · target · target_gap · stop · stop_gap · time · 끊김 ·
    #: (종가 판정 스윙) target_next_open · stop_next_open · time_next_open ·
    #: (체결 못 함) 상한가 시가 · 시세 없음 · 시세 끊김
    reason: str | None = None
    sessions: int = 0
    gross: float | None = None
    net: float | None = None  # 비용 뒤 수익률


def _net(entry: float, exit_: float, c: Costs, day: str | None = None) -> float:
    """비용 뒤 수익률. 매수 금액 = 진입가 × (1 + 매수 수수료), 매도 대금 = 청산가 × (1 − 매도 수수료 − 세)."""
    산 = entry * (1 + c.buy_fee_pct / 100)
    판 = exit_ * (1 - (c.sell_fee_pct + c.tax_at(day)) / 100)
    return round(판 / 산 - 1, 6)


def close_exit(entry_open: float, closes: list[float], rule: Rule = SWING_CLOSE) -> tuple[str, int] | None:
    """종가 판정 스윙의 넘음 — (`stop`·`target`·`time`, 넘은 종가의 자리). 아직 아니면 None.

    `closes` 는 **진입한 날부터** 그 종목 종가(오름차순 — 진입한 날이 첫째, 1거래일째). 선은 `entry_open`(슬리피지 전
    진입 시가) 기준. 선과 같은 종가는 넘은 것(등호 포함), 수익률을 % 여섯째 자리에서 반올림해 견준다 — 엔진 손절 판정
    (`sell_flags` `round(…, 6) <= stop_pct`)과 같은 꼴, 부동소수 찌꺼기(96.99999…)로 경계가 갈리지 않게(25.1870).
    그림자 `simulate` 와 가상모의투자 스윙 계좌(paper.md 16.3.1)가 **같이** 쓴다(25.1871) — 계좌 성적과 그림자 집계의
    차이가 판정 차이가 아니게"""
    assert rule.target_pct is not None and rule.stop_pct is not None
    if not entry_open or entry_open <= 0:
        return None
    아래, 위 = round(rule.stop_pct * 100, 6), round(rule.target_pct * 100, 6)
    for i, c in enumerate(closes[: rule.max_sessions]):
        수익 = round((c / entry_open - 1) * 100, 6)
        if 수익 <= 아래:
            return "stop", i
        if 수익 >= 위:
            return "target", i
        if i == rule.max_sessions - 1:
            return "time", i
    return None


def simulate(stock_id: int, signal_date: str, bars: list[Bar], rule: Rule, costs: Costs, country: str,
             prev_close: float | None = None, ended: bool = False) -> Trade:  # fmt: skip
    """`bars` 는 신호 날 **뒤**의 그 종목 시세(오름차순). `prev_close` 는 신호 날 종가(상한가 시가 판정용).

    `ended` — 그 종목 계열이 시장보다 먼저 끝났다(상장폐지·장기 정지). 청산 전에 시세가 끊기면 **마지막 종가로 닫는다**
    (`끊김`, 25.1863 교차검증 [권고]) — 열린 채 두면 끝난 거래 집계에서 빠져, 떨어진 뒤 끊기는 종목일수록 빠지는
    생존편향이 생긴다(신호 성적표 25.696 과 같은 꼴)"""
    if not bars or not bars[0].open or bars[0].open <= 0:
        if not bars and ended:
            return Trade(stock_id, signal_date, rule.name, "no_entry", reason="시세 끊김")
        return Trade(stock_id, signal_date, rule.name, "no_entry" if bars else "open",
                     reason="시세 없음" if bars else None)  # fmt: skip
    첫 = bars[0]
    if country == "KR" and prev_close and 첫.open / prev_close - 1 >= KR_LIMIT_UP_OPEN:
        return Trade(stock_id, signal_date, rule.name, "no_entry", entry_date=첫.date, reason="상한가 시가")
    slip = costs.slippage_pct / 100
    진입 = 첫.open * (1 + slip)  # 시가 시장가 매수 — 슬리피지 불리하게

    def 끝(bar: Bar, 값: float, 까닭: str, n: int) -> Trade:
        return Trade(stock_id, signal_date, rule.name, "closed", 첫.date, round(진입, 6), bar.date, round(값, 6),
                     까닭, n, round(값 / 진입 - 1, 6), _net(진입, 값, costs, bar.date))  # fmt: skip

    if rule.kind == "day":
        return 끝(첫, 첫.close * (1 - slip), "close", 1)
    assert rule.target_pct is not None and rule.stop_pct is not None
    기준 = 첫.open  # 선은 슬리피지 전 시가에서 — 주문을 낼 때 아는 값
    목표, 손절 = 기준 * (1 + rule.target_pct), 기준 * (1 + rule.stop_pct)
    if rule.kind == "swing_close":
        # 종가가 선을 넘은 날(또는 기한 날)의 **다음 장 시가** 시장가로 판다 — 넘은 날 다음 장이 아직 없으면 진행 중.
        # 판정은 가상모의투자 스윙 계좌와 같은 함수(`close_exit`, 25.1871)
        넘음 = close_exit(첫.open, [b.close for b in bars], rule)
        if 넘음 is not None and 넘음[1] + 1 < len(bars):
            다음 = bars[넘음[1] + 1]
            return 끝(다음, 다음.open * (1 - slip), f"{넘음[0]}_next_open", 넘음[1] + 2)
        if ended:
            return 끝(bars[-1], bars[-1].close * (1 - slip), "끊김", len(bars))
        return Trade(stock_id, signal_date, rule.name, "open", 첫.date, round(진입, 6), sessions=len(bars))
    for i, bar in enumerate(bars[: rule.max_sessions]):
        if i > 0 and bar.open <= 손절:
            return 끝(bar, bar.open * (1 - slip), "stop_gap", i + 1)  # 시가가 손절선 아래 — 시가 시장가
        if i > 0 and bar.open >= 목표:
            return 끝(bar, bar.open, "target_gap", i + 1)  # 지정가 매도가 시가에 찬다(더 좋은 값은 쳐 주지 않음)
        if bar.low <= 손절:  # 같은 날 목표에도 닿았어도 손절 먼저(장중 순서를 모른다 — 보수 쪽)
            return 끝(bar, 손절 * (1 - slip), "stop", i + 1)
        if bar.high >= 목표:
            return 끝(bar, 목표, "target", i + 1)
        if i == rule.max_sessions - 1:
            return 끝(bar, bar.close * (1 - slip), "time", i + 1)
    if ended:
        return 끝(bars[-1], bars[-1].close * (1 - slip), "끊김", len(bars))
    return Trade(stock_id, signal_date, rule.name, "open", 첫.date, round(진입, 6), sessions=len(bars))


@dataclass
class Stat:
    rule: str
    n: int = 0
    open: int = 0
    no_entry: int = 0
    win_rate: float | None = None
    avg_net: float | None = None
    median_net: float | None = None
    profit_factor: float | None = None  # 이긴 합 ÷ 진 합(절댓값). 진 것이 없으면 None
    avg_sessions: float | None = None
    #: 끝난 거래의 서로 다른 종목 수 — 단기 신호는 조건이 이어지는 동안 날마다 다시 나와 같은 종목의 거의 같은 거래가
    #: 겹친다. 거래 수(n)는 독립 표본보다 크다 (25.1863)
    stocks: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def summarize(trades: list[Trade], rule: str) -> Stat:
    """끝난 거래만 집계한다. 진행 중·체결 못 함은 수만 센다 — 끝난 것만 보면 늦게 끝나는 거래가 빠지는 쏠림이
    있다(알고 둔다)."""
    끝남 = [t for t in trades if t.rule == rule and t.status == "closed" and t.net is not None]
    s = Stat(rule, n=len(끝남), open=sum(t.rule == rule and t.status == "open" for t in trades),
             no_entry=sum(t.rule == rule and t.status == "no_entry" for t in trades))  # fmt: skip
    if not 끝남:
        return s
    nets = [float(t.net) for t in 끝남]  # type: ignore[arg-type]
    이김 = [x for x in nets if x > 0]
    짐 = [x for x in nets if x < 0]
    s.win_rate = round(len(이김) / len(nets), 4)
    s.avg_net = round(sum(nets) / len(nets), 6)
    s.median_net = round(statistics.median(nets), 6)
    s.profit_factor = round(sum(이김) / -sum(짐), 4) if 짐 else None
    s.avg_sessions = round(sum(t.sessions for t in 끝남) / len(끝남), 2)
    s.stocks = len({t.stock_id for t in 끝남})
    for t in 끝남:
        s.reasons[str(t.reason)] = s.reasons.get(str(t.reason), 0) + 1
    return s
