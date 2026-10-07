"""백테스트 엔진.

방법의 단일 정의처는 `docs/backtest.md` 다. 여기 있는 코드는 그 문서를 옮긴
것이고, 문서에 없는 방식으로 계산하지 않는다.

이 모듈은 **DB 를 모른다.** 가격 표와 판단 함수를 받아 자본곡선을 돌려준다.
같은 입력이면 같은 결과다. 네트워크 없이 검증할 수 있다.

두 가지를 구조로 막는다.
  look-ahead   판단 함수에는 t-1 까지 잘라 낸 데이터만 넘긴다.
               미래를 볼 방법이 없다
  생존편향     가격이 끊긴 종목은 마지막 종가로 매도한 것으로 보고 다시 사지
               않는다. 다만 상장폐지 종목이 DB 에 없으면 이 장치는 쓸 데가
               없고, 그 사실을 경고로 남긴다
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from batch.core import settings_range
from batch.services import metrics as m

# 2: 리스크 지표가 운영처럼 창 시작·끝을 보고 3Y 가 비면 1Y 로 내려간다 (2026-09-29, docs/infra.md 25.710)
# 3: 국내 매도 거래세를 체결일의 법정 세율로 (2026-09-30, docs/infra.md 25.783·25.787 — 비용 적용이 바뀌면 올린다)
# 4: 마지막 날은 리밸런스하지 않는다 (docs/infra.md 25.792·25.795) — 끝 날이 월초였던 결과의 회전·비용·횟수가 바뀐다
# 5: 표시통화가 다른 재무를 시총과 나누거나 해끼리 견주지 않는다 (2026-10-03, docs/infra.md 25.919 — 11회차 1)
CALC_VERSION = 5

# 기본 종목 수. 과거에 맞춰 고르면 과최적화이므로 문서에 적고 고정한다.
DEFAULT_TOP_N = 20

# 결과의 표본이 충분한지 보는 하한. 리밸런스가 이보다 적으면 경고한다.
MIN_REBALANCES = 12

WARN_SURVIVORSHIP = "생존편향: 상장폐지 종목 미포함. 실제보다 좋게 나온다"
WARN_FEW_REBALANCES = "표본 부족: 리밸런스 12회 미만"
WARN_NO_DIVIDENDS = "배당 재투자 없음. 가격 수익만 본다"
#: **미국은 배당이 들어 있다** (docs/infra.md 25.244). 미국 시세의 `adj_close` 는 야후 수정종가
#: (배당 재투자 포함 총수익)이고 백테스트는 `COALESCE(adj_close, close)` 를 쓴다(25.212 가 수익률 누적은
#: 총수익이 맞다고 정했다). 국내 수정주가는 분할만 조정하므로 국내만 가격 수익이다.
#: 예전에는 두 나라 모두에 위 경고를 붙여 미국 결과를 "가격 수익" 이라 불렀다
WARN_TOTAL_RETURN = "배당 재투자 포함(야후 수정종가, 총수익). 국내 결과(가격 수익)와 바로 견주지 말 것"


# ----------------------------------------------------------------------
# 비용
# ----------------------------------------------------------------------


#: 미국 매도에 붙는 거래세. **없다.** 0 을 상수로 둔 이유는 "안 넣은 것" 과 "없어서 0" 을
#: 구별하기 위해서다 — 근거표에 0% 가 뜨는 것이 맞고, 그것을 되돌려 놓는 실수를 막는다.
US_SELL_TAX_PCT = 0.0


def _pct(value: object) -> float | None:
    """설정에서 읽은 비율. 비어 있으면 None (0 과 구별한다)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


#: 국내 매도 거래세(증권거래세 + 코스피 농어촌특별세 합, 코스피·코스닥 합계가 같다)의 **시행일별 법정 세율** (%)
#: (docs/backtest.md 3장, docs/infra.md 25.783 — 기법 발굴 5회차 C①). 예전에는 전 구간 0.18%(2024년 세율)였다 —
#: 2021~22 년은
#: 0.23%, 2025 년은 0.15%, 2026 년은 0.20% 라 구간마다 틀렸다. 2019-06-03 전·2026-01-01 은 기사·증권사 공지로 확인했고
#: 법령 원문은 [확인필요] (law.go.kr 이 이 환경에서 막힘)
KR_SELL_TAX_SCHEDULE: tuple[tuple[str, float], ...] = (
    ("0001-01-01", 0.30),  # 2019-06-02 까지 [확인필요: 법령 원문]
    ("2019-06-03", 0.25),
    ("2021-01-01", 0.23),
    ("2023-01-01", 0.20),
    ("2024-01-01", 0.18),
    ("2025-01-01", 0.15),
    ("2026-01-01", 0.20),  # [확인필요: 법령 원문]
)


def tax_on(schedule: tuple[tuple[str, float], ...], day: str) -> float | None:
    """그날 시행 중인 세율. 표가 비었으면 None."""
    rate: float | None = None
    for start, pct in schedule:
        if start <= day:
            rate = pct
    return rate


@dataclass(frozen=True)
class Costs:
    """거래비용. 단위는 %. 기본값의 근거는 docs/backtest.md 3장.

    기본값에 [확인필요] 가 붙어 있다. 결과에 어떤 비용을 썼는지 그대로
    표시해야 한다. 숫자만 보여주고 근거를 숨기지 않는다.
    """

    commission_pct: float = 0.015  # 편도 수수료 [확인필요]
    #: 매도 거래세, 국내 — **지금 시행 중인** 세율. 리밸런스 날짜의 세율은 `tax_schedule` 이 정한다(비었으면 이 값 하나)
    tax_sell_pct: float = KR_SELL_TAX_SCHEDULE[-1][1]
    slippage_pct: float = 0.10  # 편도 슬리피지. 설계서 B5 제안값 [확인필요]
    #: 설정이 없어 **기본값을 쓴 항목** (docs/infra.md 25.358).
    #: `costs_json` 에 실려 화면이 "기본값" 을 그 항목에만 붙인다.
    #: 예전에는 화면이 늘 "(기본값은 아직 실측 아님)" 을 붙여, 설정의 수수료를 쓴 실행도 기본값처럼 읽혔다
    defaults: tuple[str, ...] = ()
    #: 시행일별 매도 거래세 ((시행일, %), …). 비었으면 `tax_sell_pct` 하나를 전 구간에 쓴다 (25.783)
    tax_schedule: tuple[tuple[str, float], ...] = ()

    def tax_at(self, day: str | None) -> float:
        """그날 매도에 붙는 거래세(%)."""
        if day is None or not self.tax_schedule:
            return self.tax_sell_pct
        rate = tax_on(self.tax_schedule, day)
        return self.tax_sell_pct if rate is None else rate

    @classmethod
    def zero(cls) -> Costs:
        """비용 0. 참고용으로만 나란히 둔다."""
        return cls(0.0, 0.0, 0.0)

    @classmethod
    def for_country(cls, country: str, fees: dict | None = None, taxes: dict | None = None) -> Costs:
        """그 나라의 거래비용 (docs/infra.md 25.126).

        **기본값은 국내 기준이다.** 미국 백테스트에 그대로 쓰면 **국내 증권거래세 0.18% 를
        미국 매도마다 물린다** — 미국에는 매매마다 붙는 세금이 없다(`portfolio.CostRates`
        가 같은 말을 이미 적어 두고 그렇게 하고 있다). 월 1회 리밸런스에 매도 비중 0.25 면
        연 0.5% 를 없는 세금으로 깎는다. 전략 비교가 그 폭에서 뒤집힌다.

        **설정이 있으면 설정을 쓴다.** `docs/backtest.md` 가 "`settings.taxes` 가 있으면
        그 값" 이라고 적어 두었는데 코드는 한 번도 읽지 않았다.

        `commission_pct` 는 **편도**라 `rebalance_cost` 가 ×2 로 왕복을 만든다. 설정은
        매수·매도를 따로 두므로 평균이 곧 편도 값이다 — 둘을 더하면 왕복이 되기 때문이다.
        한쪽만 채워져 있으면 그 값을 편도로 본다.
        """
        # **범위 밖 값은 모른다로 둔다** (docs/infra.md 25.171). 여기는 기본값으로
        # 떨어지므로 백테스트가 멈추지는 않는다 — `kr_buy_pct = 500` 을 그대로 쓰면
        # 회전마다 500% 를 물려 **어떤 전략이든 파산으로 보인다**
        미국 = country.upper() == "US"
        앞 = "us" if 미국 else "kr"
        수수료, _ = settings_range.잎마다(fees, (f"{앞}_buy_pct", f"{앞}_sell_pct"))
        세율, _ = settings_range.잎마다(taxes, ("kr_transaction_pct",))

        사이드 = [_pct(수수료.get(f"{앞}_{side}_pct")) for side in ("buy", "sell")]
        채워진 = [v for v in 사이드 if v is not None]
        commission = sum(채워진) / len(채워진) if 채워진 else cls.commission_pct
        기본값: list[str] = [] if 채워진 else ["commission"]

        일정: tuple[tuple[str, float], ...] = ()
        if 미국:
            # 미국은 매매마다 붙는 세금이 없다. SEC 수수료(매도 대금의 약 0.003%)와
            # FINRA TAF 가 있지만 자릿수가 달라 무시한다 [확인필요: 정확한 요율]
            tax = US_SELL_TAX_PCT
        else:
            # **국내는 체결일의 법정 세율** (25.783). 설정 `kr_transaction_pct` 는 **지금 시행 중인 구간**만 바꾼다 —
            # 설정 화면의 세율은 사용자가 지금 치르는 값(실현손익용)이라, 그것을 2021 년 매도에 물리면 과거 세율이
            # 틀린다.
            # 예전에는 설정이든 기본값이든 한 값이 전 구간을 덮었다 (5회차 검증 2 의 조건: 이 뜻 바꿈을 문서·기록에
            # 드러낸다)
            설정세율 = _pct(세율.get("kr_transaction_pct"))
            마지막_시행일 = KR_SELL_TAX_SCHEDULE[-1][0]
            if 설정세율 is None:
                tax = cls.tax_sell_pct
                기본값.append("tax")
                일정 = KR_SELL_TAX_SCHEDULE
            else:
                tax = 설정세율
                일정 = (*KR_SELL_TAX_SCHEDULE[:-1], (마지막_시행일, 설정세율))

        # 슬리피지는 설정에 항목이 없다. 나라를 가리지 않고 같은 [확인필요] 값을 쓴다
        기본값.append("slippage")
        return cls(
            commission_pct=commission, tax_sell_pct=tax, slippage_pct=cls.slippage_pct, defaults=tuple(기본값),
            tax_schedule=일정,
        )

    def rebalance_cost(self, turnover: float, sold: float, day: str | None = None) -> float:
        """리밸런스 한 번의 비용(자본 대비 비율).

        turnover 는 Σ|새 비중 - 옛 비중| / 2 (편도 회전율), sold 는 팔린 비중 합.
        수수료와 슬리피지는 사고팔 때 양쪽에 붙으므로 turnover×2 에 매긴다. 거래세는 `day`(체결일)의 세율(25.783).
        """
        if turnover < 0 or sold < 0:
            raise ValueError("회전율과 매도 비중은 음수일 수 없습니다")
        round_trip = (self.commission_pct + self.slippage_pct) / 100 * 2
        return turnover * round_trip + sold * self.tax_at(day) / 100


# ----------------------------------------------------------------------
# 판단 함수가 보는 창 — t-1 까지만
# ----------------------------------------------------------------------


class PriceView:
    """판단 함수에 넘기는 가격 창. cutoff 이후는 존재하지 않는다.

    잘라 낸 사본을 들고 있으므로 판단 함수가 어떤 방법으로도 미래를 볼 수
    없다. "조심해서 안 본다" 가 아니라 "볼 것이 없다" 여야 규칙이다.
    """

    def __init__(
        self,
        prices: dict[int, dict[str, float]],
        cutoff: str,
        turnover: dict[int, dict[str, float]] | None = None,
        levels: dict[int, dict[str, float]] | None = None,
    ) -> None:
        self.cutoff = cutoff
        # **가격 수준**(시총·PBR 용)을 따로 든다 (docs/infra.md 25.275). 미국 `prices` 는 야후 수정종가
        # (배당 재투자 포함)라 수익률에는 맞지만 **수준**은 그 뒤 배당만큼 낮다 — 5년 전 날짜면 배당 4% 종목의
        # 시총이 약 18% 작게 나와 밸류가 부풀고, 그 크기가 **미래의 배당 결정**에 달린다(look-ahead).
        # 없으면 `prices` 를 그대로 쓴다(국내는 분할만 조정)
        self._levels: dict[int, list[tuple[str, float]]] = {}
        for stock_id, by_date in (levels or {}).items():
            kept = sorted((d, c) for d, c in by_date.items() if d <= cutoff)
            if kept:
                self._levels[stock_id] = kept
        self._series: dict[int, list[tuple[str, float]]] = {}
        for stock_id, by_date in prices.items():
            kept = sorted((d, c) for d, c in by_date.items() if d <= cutoff)
            if kept:
                self._series[stock_id] = kept
        # 거래대금도 같은 창으로 잘라 둔다. Amihud 비유동성(docs/factors.md 10.1)이 쓴다.
        # 없으면 그 지표만 비고 나머지는 그대로다
        self._turnover = turnover or {}

    def broad_last(self, share: float = 0.5, lookback_days: int = 40) -> str | None:
        """cutoff 이하에서 **대부분의 종목이 시세를 가진** 마지막 날 (docs/infra.md 25.723).

        운영 `signal_outcomes.market_last_dates` 와 같은 규칙 — 최근 `lookback_days` 안에서 종목 수가 가장 많았던
        날의 `share` 이상이 들어온 마지막 날. 리스크 창의 끝을 cutoff 로 잡으면, 날짜 축이 모든 종목 날짜의 합집합이라
        한 종목만 시세가 있는 날이 cutoff 가 될 수 있고 나머지 전부가 끝 검사에 걸려 리스크가 None 이 됐다(25.717 회귀).
        한 번 계산해 둔다.
        """
        if getattr(self, "_broad", None) is not None or not self._series:
            return getattr(self, "_broad", None)
        from datetime import date as _d
        from datetime import timedelta as _td

        시작 = (_d.fromisoformat(self.cutoff[:10]) - _td(days=lookback_days)).isoformat()
        세기: dict[str, int] = {}
        for series in self._series.values():
            for d, _c in reversed(series):
                if d < 시작:
                    break
                세기[d] = 세기.get(d, 0) + 1
        if not 세기:
            return None
        최대 = max(세기.values())
        self._broad: str | None = max(d for d, n in 세기.items() if n >= 최대 * share)
        return self._broad

    def values(self, stock_id: int) -> list[float | None]:
        """closes() 와 같은 날짜 순서의 거래대금. 그날 값이 없으면 None."""
        by_date = self._turnover.get(stock_id, {})
        return [by_date.get(d) for d, _c in self._series.get(stock_id, [])]

    def stocks(self) -> list[int]:
        return sorted(self._series)

    def closes(self, stock_id: int) -> list[tuple[str, float]]:
        """날짜 오름차순 (날짜, 종가). 없으면 빈 목록."""
        return list(self._series.get(stock_id, []))

    def level_closes(self, stock_id: int) -> list[tuple[str, float]]:
        """시총·PBR 을 잴 가격 수준. 따로 받은 것이 없으면 `closes()` 와 같다 (25.275)."""
        if stock_id in self._levels:
            return list(self._levels[stock_id])
        return self.closes(stock_id)

    def last_close(self, stock_id: int) -> float | None:
        series = self._series.get(stock_id)
        return series[-1][1] if series else None

    def last_date(self, stock_id: int) -> str | None:
        series = self._series.get(stock_id)
        return series[-1][0] if series else None


# 판단 함수. 리밸런스 날짜 t 와 t-1 까지의 창을 받아 {stock_id: 비중} 을 돌려준다.
WeightsAt = Callable[[str, PriceView], dict[int, float]]


def top_n_equal_weight(
    scores: dict[int, float | None], n: int = DEFAULT_TOP_N
) -> dict[int, float]:
    """점수 상위 N 을 동일가중으로. 점수 없는 종목은 뽑지 않는다.

    동일가중인 이유: 점수가 순위 이상의 정보를 주는지 검증되지 않았다.
    """
    if n <= 0:
        raise ValueError("n 은 1 이상이어야 합니다")
    ranked = sorted(
        ((sid, s) for sid, s in scores.items() if s is not None),
        key=lambda pair: (-pair[1], pair[0]),
    )[:n]
    if not ranked:
        return {}
    weight = 1.0 / len(ranked)
    return {sid: weight for sid, _ in ranked}


# ----------------------------------------------------------------------
# 달력
# ----------------------------------------------------------------------


def month_starts(dates: list[str]) -> list[str]:
    """매월 첫 거래일. dates 는 거래일 목록(오름차순)이다."""
    out: list[str] = []
    seen: set[str] = set()
    for d in sorted(dates):
        key = d[:7]
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def previous_trading_day(dates: list[str], t: str) -> str | None:
    """t 직전 거래일. 판단은 여기까지의 데이터로 한다."""
    earlier = [d for d in dates if d < t]
    return earlier[-1] if earlier else None


def decision_cutoff(dates: list[str], prices: dict[int, dict[str, float]], t: str) -> str | None:
    """리밸런스 t 의 판단 기준일(t-1). 날짜 축에 없으면 **가격 자료에서** t 직전 날 (docs/infra.md 25.623·25.627).

    날짜 축(`dates`)은 시작일 이후만 담아 첫 리밸런스의 직전 거래일이 없다. 워밍업 가격(25.532)이 있으면 그 마지막 날로
    판단한다. 전략(`simulate`)과 팩터 IC(`jobs/backtest.factor_ics`)가 **같은 규칙**을 쓴다 — IC 만 첫 달을 건너뛰어
    한 달 어긋났다(25.627).
    """
    cutoff = previous_trading_day(dates, t)
    if cutoff is not None:
        return cutoff
    return max((day for by_date in prices.values() for day in by_date if day < t), default=None)


# ----------------------------------------------------------------------
# 시뮬레이션
# ----------------------------------------------------------------------


@dataclass
class Rebalance:
    date: str
    weights: dict[int, float]
    turnover: float
    cost: float
    dropped: list[int] = field(default_factory=list)  # 가격이 끊겨 뺀 종목


@dataclass
class BacktestResult:
    curve: list[tuple[str, float]]  # (날짜, 자본). 시작 1.0
    rebalances: list[Rebalance]
    costs: Costs
    warnings: list[str] = field(default_factory=list)

    @property
    def turnover_avg(self) -> float | None:
        if not self.rebalances:
            return None
        return sum(r.turnover for r in self.rebalances) / len(self.rebalances)

    def points(self) -> list[m.PricePoint]:
        """자본곡선을 metrics.compute 가 먹는 형태로."""
        return [
            m.PricePoint(date=date.fromisoformat(d), close=v) for d, v in self.curve
        ]


def simulate(
    prices: dict[int, dict[str, float]],
    dates: list[str],
    rebalance_dates: list[str],
    weights_at: WeightsAt,
    costs: Costs,
    *,
    has_delisted: bool = False,
    turnover: dict[int, dict[str, float]] | None = None,
    dividends_included: bool = False,
    levels: dict[int, dict[str, float]] | None = None,
) -> BacktestResult:
    """자본곡선을 만든다.

    Args:
        prices: {stock_id: {날짜: 종가}}
        turnover: {stock_id: {날짜: 거래대금}}. 판단 함수가 유동성 지표를 낼 때만 쓴다
        dates: 거래일 목록(오름차순). 자본곡선의 날짜 축
        rebalance_dates: 리밸런스 날짜. dates 의 부분집합
        weights_at: 판단 함수. t 와 t-1 까지의 창을 받는다
        costs: 거래비용
        has_delisted: 입력에 상장폐지 종목이 들어 있는가. 아니면 경고를 붙인다

    체결은 t 종가다. 아침에 판단하고 그날 종가에 산다고 본다.
    가격이 끊긴 종목은 마지막 종가에 묶인 채로 남고(= 그 값에 판 것과 같다),
    다음 리밸런스에서 뺀다.
    """
    dates = sorted(dates)
    if not dates:
        raise ValueError("거래일이 없습니다")
    rebalance_set = set(rebalance_dates)
    missing = rebalance_set - set(dates)
    if missing:
        raise ValueError(f"리밸런스 날짜가 거래일에 없습니다: {sorted(missing)[:3]}")

    last_date = {sid: max(by_date) for sid, by_date in prices.items() if by_date}

    equity = 1.0
    weights: dict[int, float] = {}  # 직전 리밸런스 시점의 비중
    entry: dict[int, float] = {}  # 그때의 체결가
    cash_w = 1.0  # 비중 합이 1 미만이면 나머지는 현금(수익 0). 2026-09-17 추세 오버레이용 (docs/backtest.md 2.4)
    curve: list[tuple[str, float]] = []
    rebalances: list[Rebalance] = []

    def carry(sid: int, d: str) -> float | None:
        """d 의 종가. 없으면 그 전 마지막 종가(거래정지·상장폐지)."""
        by_date = prices.get(sid, {})
        if d in by_date:
            return by_date[d]
        earlier = [k for k in by_date if k < d]
        return by_date[max(earlier)] if earlier else None

    def value_at(d: str) -> tuple[float, dict[int, float]]:
        """직전 리밸런스 이후 가격 변화를 반영한 자본과 종목별 가치."""
        if not weights:
            return equity, {}
        parts: dict[int, float] = {}
        for sid, w in weights.items():
            px = carry(sid, d)
            base = entry.get(sid)
            if px is None or base is None or base <= 0:
                parts[sid] = w * equity
            else:
                parts[sid] = w * equity * px / base
        return sum(parts.values()) + cash_w * equity, parts

    마지막 = dates[-1]
    for d in dates:
        # **마지막 날은 평가만 한다** (docs/infra.md 25.792, 백테스트 감사 #4). 날짜 축은 전 종목 날짜의 합집합이라,
        # 끝 날이 월초이고
        # 일부 종목만 시세가 들어와 있으면 나머지 보유가 "뒤 거래 없음 = 상장폐지" 로 읽혀 팔리고(회전·비용) 남은
        # 종목에 몰렸다.
        # 그날 사고팔아도 관찰이 끝나 수익에 닿지 않는다 — 비용만 붙는다. 날짜가 하나뿐이면 예전처럼 둔다
        if d in rebalance_set and (d != 마지막 or len(dates) == 1):
            cutoff = decision_cutoff(dates, prices, d)
            drifted_total, parts = value_at(d)
            drifted = (
                {sid: v / drifted_total for sid, v in parts.items()}
                if drifted_total > 0
                else {}
            )

            # 판단은 t-1 까지만 보고 한다. 창을 만들 때 잘라 낸다.
            # 첫 거래일이라 t-1 이 없으면 빈 창을 넘긴다. 무엇을 살지는 판단
            # 함수의 몫이다. 점수로 고르는 전략은 볼 것이 없어 아무것도 사지
            # 않을 것이고, 그것이 맞다. 엔진이 대신 비워 버리지 않는다
            view = PriceView(prices, cutoff, turnover, levels) if cutoff else PriceView({}, d)
            proposed = weights_at(d, view)

            # **들고 있는데 오늘 체결가가 없고 뒤에 다시 거래되는 종목은 거래정지다 — 팔 수 없다** (infra 25.471).
            # 예전에는 "살 수 없다" 목록에 섞여 정지 전 종가로 판 셈이 됐다 — 재개 뒤 갭(하락)을 피해 낙관적이었다.
            # 지금 비중 그대로 묶어 두고, 거래가 재개된 다음 리밸런스에서 판단대로 사고판다. 상장폐지(뒤 거래 없음)는
            # 그대로 마지막 종가로 매도한다(docs/backtest.md)
            frozen = {
                sid: w for sid, w in drifted.items()
                if w > 0 and d not in prices.get(sid, {}) and last_date.get(sid, "") > d
            }  # fmt: skip
            # 오늘 체결가가 없거나 가격이 끊긴 종목은 살 수 없다.
            dropped = [
                sid
                for sid in proposed
                if sid not in frozen and (sid not in prices or d not in prices[sid] or last_date.get(sid, "") < d)
            ]
            target = {sid: w for sid, w in proposed.items() if sid not in dropped and sid not in frozen}
            total_w = sum(target.values())
            if total_w > 0:
                # 판단 함수가 정한 투자 비율(비중 합)은 지키고, 살 수 없는 종목의 몫은 남은
                # 종목에 나눈다. 합이 1 미만이면 나머지가 현금이고, 1 을 넘으면 1 로 줄인다.
                # 묶인 정지 종목의 몫은 먼저 뺀다 — 그만큼은 이미 들고 있다
                invested = min(max(sum(proposed.values()), 0.0), 1.0)
                room = max(invested - sum(frozen.values()), 0.0)
                target = {sid: w / total_w * room for sid, w in target.items()}
            target = {**target, **frozen}

            keys = set(target) | set(drifted)
            # **거래대금 인자(`turnover`)와 다른 이름이어야 한다** (docs/infra.md 25.460). 같은 이름으로 덮어쓰면 다음
            # 리밸런스의 `PriceView` 가 이 숫자를 거래대금 표로 받아 0 이면 조용히 비고, 0 이 아니면 `.get` 에서 죽는다
            회전율 = sum(abs(target.get(k, 0.0) - drifted.get(k, 0.0)) for k in keys) / 2
            sold = sum(max(drifted.get(k, 0.0) - target.get(k, 0.0), 0.0) for k in keys)
            cost = costs.rebalance_cost(회전율, sold, d)

            equity = drifted_total * (1 - cost)
            weights = target
            cash_w = max(0.0, 1.0 - sum(target.values()))
            # 묶인 종목은 오늘 체결가가 없다 — 마지막 종가를 기준으로 이어 잰다(재개일 갭이 곡선에 들어간다)
            entry = {sid: (prices[sid][d] if d in prices.get(sid, {}) else carry(sid, d)) for sid in target}
            rebalances.append(
                Rebalance(date=d, weights=dict(target), turnover=회전율, cost=cost, dropped=dropped)
            )
            curve.append((d, equity))
            continue

        total, _ = value_at(d)
        curve.append((d, total))

    warnings = [WARN_TOTAL_RETURN if dividends_included else WARN_NO_DIVIDENDS]
    if not has_delisted:
        warnings.append(WARN_SURVIVORSHIP)
    if len(rebalances) < MIN_REBALANCES:
        warnings.append(WARN_FEW_REBALANCES)

    return BacktestResult(curve=curve, rebalances=rebalances, costs=costs, warnings=warnings)


# ----------------------------------------------------------------------
# 결과 요약
# ----------------------------------------------------------------------


@dataclass
class Summary:
    final_equity: float
    metrics: m.Metrics
    turnover_avg: float | None
    excess_cagr: float | None  # 벤치마크 대비
    win_rate: float | None  # 벤치마크를 이긴 달의 비율


def drawdown_curve(curve: list[tuple[str, float]]) -> list[float]:
    """날짜별 낙폭 = 자본 / 그때까지의 최고 자본 − 1. 0 이하이고 고점에서 0 이다.

    docs/metrics.md 의 MDD 정의(고점 대비 최대 하락률)를 날짜마다 푼 것이다. 최솟값이 MDD 다.
    """
    out: list[float] = []
    peak = 0.0
    for _d, equity in curve:
        peak = max(peak, equity)
        out.append(equity / peak - 1 if peak > 0 else 0.0)
    return out


def summarize(
    result: BacktestResult,
    benchmark: BacktestResult | None = None,
    risk_free_annual: float | None = None,
) -> Summary:
    """자본곡선에 docs/metrics.md 의 식을 그대로 적용한다. 새 식을 만들지 않는다."""
    points = result.points()
    window = _window_for(len(points))
    metrics = m.compute(window, points, risk_free_annual=risk_free_annual)

    excess = None
    win_rate = None
    if benchmark is not None and benchmark.curve:
        bench_metrics = m.compute(window, benchmark.points(), risk_free_annual=risk_free_annual)
        if metrics.cagr is not None and bench_metrics.cagr is not None:
            excess = metrics.cagr - bench_metrics.cagr
        win_rate = _monthly_win_rate(result.curve, benchmark.curve)

    return Summary(
        final_equity=result.curve[-1][1] if result.curve else 1.0,
        metrics=metrics,
        turnover_avg=result.turnover_avg,
        excess_cagr=excess,
        win_rate=win_rate,
    )


def _monthly_returns(curve: list[tuple[str, float]]) -> dict[str, float]:
    """월말 값 기준 월 수익률. {YYYY-MM: 수익률}"""
    by_month: dict[str, float] = {}
    for d, v in curve:
        by_month[d[:7]] = v  # 같은 달의 마지막 값이 남는다
    months = sorted(by_month)
    out: dict[str, float] = {}
    for prev, cur in zip(months, months[1:], strict=False):
        if by_month[prev] > 0:
            out[cur] = by_month[cur] / by_month[prev] - 1
    return out


def _monthly_win_rate(
    curve: list[tuple[str, float]], bench: list[tuple[str, float]]
) -> float | None:
    mine, theirs = _monthly_returns(curve), _monthly_returns(bench)
    common = sorted(set(mine) & set(theirs))
    if not common:
        return None
    wins = sum(1 for k in common if mine[k] > theirs[k])
    return wins / len(common)


def _window_for(count: int) -> str:
    """자본곡선 길이에 맞는 지표 창. metrics.MIN_POINTS 의 하한을 그대로 따른다.

    1,000일 넘으면 5Y, 600일 넘으면 3Y, 아니면 1Y. 표본이 200일에도 못 미치면
    compute 가 값을 비워 돌려준다. 짧은 곡선을 긴 창의 지표라고 부르지 않는다.
    """
    for window in ("5Y", "3Y"):
        if count >= m.MIN_POINTS[window]:
            return window
    return "1Y"
