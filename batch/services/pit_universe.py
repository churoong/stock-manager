"""백테스트용 시점 유니버스 (docs/backtest.md 1.3).

**왜 있나.** 백테스트가 *오늘의* 유니버스를 과거 전 구간에 쓰면, 지금 시총·거래대금
문턱을 넘은 종목 — 즉 그동안 살아남아 커진 종목 — 만 과거에 고르게 된다.
docs/handoff.md 가 "가장 큰 편향" 으로 적어 둔 것이 이것이다. 상장폐지 종목이 없는
생존편향(1.2)과는 다른 문제다. 저 편향은 데이터가 없어 못 고치지만, **이 편향은
가지고 있는 가격으로 상당 부분 고칠 수 있다.**

여기서 하는 일은 하나다. 리밸런스 날짜 t 마다 "그때 기준으로 유니버스에 들었겠는가" 를
**t-1 까지의 값으로만** 판정한다.

미래를 보지 않는 것이 이 모듈의 전부이므로 DB 도 시각도 모른다. 전부 인자로 받는다.

무엇으로 판정하나 (docs/backtest.md 1.3 표와 같아야 한다)

  상장 경과일    stocks.listed_date 가 있으면 그것, 없으면 **관측된 첫 거래일**.
                 관측값이 가격 이력 시작에 붙어 있으면 상장일을 모르는 것이라 자르지 않는다 (25.403)
  거래대금       t-1 까지 20 거래일의 종가×거래량 평균
  시가총액       **현재 주식수 × 그날 종가**  ← 근사다. 아래 한계를 보라
  정적 결격      우선주·스팩 등. 오늘의 값이 과거에도 같았다고 본다

한계 (결과에 경고로 붙는다)

  - 주식수는 현재 값 하나뿐이다. 증자·감자·분할이 있었으면 과거 시총이 틀린다.
    그래도 "오늘 시총으로 과거를 자르는 것" 보다는 덜 틀린다
  - 상장폐지 종목이 애초에 DB 에 없다. 이 모듈은 그 편향을 고치지 못한다
  - 우선주·스팩 판정은 현재 상태다. 과거에 보통주였다가 바뀐 경우는 알 수 없다
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

# 판정에 쓰는 창. jobs/universe._avg_turnover_map 과 **같은 창**이다 — 시장의 최근 20 거래일, 행이 없는 날은 0.
# (운영은 25.518 부터 거래대금을 모르는 행을 빼고, 여기는 종가×거래량으로 채운다(25.99) —
#  모르는 값을 0 으로 세지 않는 것은 같다)
# 이력이 짧은 종목의 처리만 다르다(아래 judge, 25.278)
# (docs/infra.md 25.211·25.271). 2026-09-27 까지 이 주석은 "같은 창" 이라고 했지만
# 실제로는 **그 종목의 최근 20 행**이었다
TURNOVER_DAYS = 20

# 거래대금 표본이 이보다 적으면 판정하지 않고 통과시킨다.
# 창이 절반도 안 차면 "거래가 없다" 가 아니라 "우리가 모른다" 다. 모르는 것으로 자르지 않는다
MIN_TURNOVER_SAMPLES = 10

REASON_NOT_LISTED = "상장 전"
REASON_TOO_NEW = "상장 1년 미만"
REASON_SMALL_CAP = "시총 하한 미만"
REASON_THIN = "거래대금 하한 미만"
REASON_STATIC = "정적 결격(우선주·스팩 등)"

DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class Stock:
    """판정에 필요한 종목 정보. 전부 시점과 무관한 값이거나(정적), 현재 값이다."""

    stock_id: int
    #: 실제 상장일. 없으면 관측된 첫 거래일을 쓴다
    listed_date: str | None = None
    #: 현재 주식수. 과거 시총 근사에 쓴다
    listed_shares: int | None = None
    #: 오늘 유니버스에서 이미 탈락한 정적 사유(우선주·스팩 등). 있으면 과거에도 뺀다
    static_exclude: str | None = None
    #: `listed_date` 가 실제 상장일이 아니라 **관측된 첫 거래일**(상장일의 상한)인가 — 미국 (us_shares)
    listed_date_observed: bool = False


@dataclass
class Filters:
    min_market_cap: float
    min_avg_turnover_20d: float
    min_listed_days: int = DAYS_PER_YEAR
    #: 가격 이력이 시작한 날들. 관측 첫 거래일이 이 가운데 하나에 붙어 있으면 **실제 상장일을 모른다**
    #: (docs/infra.md 25.403·25.418). 하나가 아니라 여럿이다 — 시범 200종목을 먼저 받고 며칠 뒤 전체를 받으면
    #: 백필 시작이 둘이다. 25.403 은 가장 이른 날 하나만 봐서 나중 묶음이 다시 "상장 1년 미만" 으로 잘렸다
    history_starts: tuple[str, ...] = ()


#: 관측 첫 거래일이 이력 시작일에서 이 일수 안이면 "이력이 시작해서 처음 보인 것" 으로 본다.
#: 이력 시작일이 휴장일이거나 종목별로 하루이틀 늦게 잡히는 것을 흡수하는 여유다 (주말 + 연휴)
HISTORY_EDGE_DAYS = 7

#: 이만큼 많은 종목의 첫 거래일이 같은 주에 몰리면 그것은 상장이 아니라 **백필 시작**이다 (docs/infra.md 25.418).
#: 실제 신규 상장은 한 주에 유니버스 후보 열 종목씩 몰리지 않는다(시총·거래대금 하한을 넘는 새 종목은 드물다)
BACKFILL_CLUSTER_MIN = 10


def history_starts_of(first_dates: list[str]) -> tuple[str, ...]:
    """첫 거래일 목록에서 **백필 시작**으로 볼 날들. 가장 이른 날은 늘 넣는다 — 이력 전체의 시작이다.

    그 밖에는 한 날을 기준으로 `HISTORY_EDGE_DAYS` 안에 `BACKFILL_CLUSTER_MIN` 종목 이상이 몰린 날이다.
    """
    날들 = sorted(first_dates)
    if not 날들:
        return ()
    시작들 = [날들[0]]
    for i, d in enumerate(날들):
        j = bisect_right(날들, (date.fromisoformat(d) + timedelta(days=HISTORY_EDGE_DAYS)).isoformat())
        if j - i >= BACKFILL_CLUSTER_MIN and days_between(시작들[-1], d) > HISTORY_EDGE_DAYS:
            시작들.append(d)
    return tuple(시작들)


@dataclass
class Decision:
    included: bool
    reason: str | None = None
    market_cap: float | None = None
    avg_turnover: float | None = None


@dataclass
class Series:
    """한 종목의 시계열. 날짜는 오름차순이어야 한다."""

    dates: list[str] = field(default_factory=list)
    closes: dict[str, float] = field(default_factory=dict)
    turnover: dict[str, float] = field(default_factory=dict)

    def first_date(self) -> str | None:
        return self.dates[0] if self.dates else None


def days_between(start: str, end: str) -> int:
    """ISO 날짜 사이의 달력일. date 로 바꾸지 않고 세면 윤년에서 틀린다."""
    from datetime import date

    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _window(series: Series, cutoff: str, size: int | None = None) -> list[str]:
    """cutoff 이하의 거래일. size 를 주면 마지막 size 개만.

    날짜가 오름차순이라 이분 탐색으로 자른다. 리밸런스 60회 × 종목 900개마다 부르는
    함수라, 전체를 훑으면(1,250일) 6,700만 번을 비교하게 된다. 실제로 느렸다.
    """
    end = bisect_right(series.dates, cutoff)
    if end == 0:
        return []
    start = 0 if size is None else max(0, end - size)
    return series.dates[start:end]


def judge(
    stock: Stock,
    series: Series,
    cutoff: str,
    filters: Filters,
    market_window: list[str] | None = None,
) -> Decision:
    """t-1(cutoff)까지의 값으로 "그때 유니버스에 들었겠는가" 를 판정한다.

    Args:
        cutoff: 볼 수 있는 마지막 날짜. 리밸런스 날짜 t 의 전 거래일이다
    """
    if stock.static_exclude:
        return Decision(False, REASON_STATIC)

    window = _window(series, cutoff)
    if not window:
        return Decision(False, REASON_NOT_LISTED)

    start = stock.listed_date or series.first_date()
    # **관측 첫 거래일이 이력 시작에 붙어 있으면 상장일을 모르는 것이다** (docs/infra.md 25.403).
    # 그날은 "이력이 시작한 날" 이지 상장한 날이 아니다. 예전에는 그것을 상장일로 보고 이력 첫 1년을
    # 전 종목 "상장 1년 미만" 으로 잘라, 미국 백테스트의 첫 12개월이 통째로 현금이었다.
    # 모르는 것으로 자르지 않는다(docs/backtest.md 1.3)
    관측값 = stock.listed_date_observed or not stock.listed_date
    이력_가장자리 = 관측값 and start is not None and any(
        0 <= days_between(h, start) <= HISTORY_EDGE_DAYS for h in filters.history_starts
    )
    if start and not 이력_가장자리 and days_between(start, cutoff) < filters.min_listed_days:
        return Decision(False, REASON_TOO_NEW)

    last = window[-1]
    close = series.closes.get(last)

    # 시총 = 현재 주식수 × 그날 종가. 주식수를 모르면 이 조건으로 자르지 않는다
    cap: float | None = None
    if stock.listed_shares and close is not None:
        cap = stock.listed_shares * close
        if cap < filters.min_market_cap:
            return Decision(False, REASON_SMALL_CAP, market_cap=cap)

    # 거래대금 = 종가×거래량의 20일 평균. 표본이 모자라면 모르는 것이라 통과시킨다
    avg: float | None = None
    if market_window is not None:
        # **시장의 최근 20 거래일, 행이 없는 날은 0** (docs/infra.md 25.271) — 실제 유니버스 배치와 **같은 창**.
        # 종목의 최근 20 행으로 세면 오래 쉰 종목은 창이 옛날로 늘어나 쉰 날이 평균에서 빠지고 하한을 통과했다.
        # **다른 점 하나**(25.278 에서 바로잡은 주석): 이력이 짧을 때다. 유니버스 배치는 행이 20개 못 되면
        # 평균을 내지 않고 **뺀다**(REASON_NO_DATA). 여기는 행이 10개 못 되면 "모른다" 로 **통과**시키고,
        # 10~19개면 빈 날을 0 으로 채워 평균을 낸다.
        # 백테스트 초기 구간이 통째로 비지 않게 하려는 의도다(docs/backtest.md 1.3 "모르는 것으로 자르지 않는다")
        own = [d for d in _window(series, cutoff, TURNOVER_DAYS) if d in series.turnover]
        # **표본 하한은 시장 창 길이로 낮추지 않는다** (docs/infra.md 25.539, 감사 재현). 데이터가 막 시작된
        # 첫 리밸런스는 시장 창이 이틀뿐이라 이틀치 평균으로 '거래대금 하한 미만' 을 판정했다 —
        # 25.278 이 정한 "10일 미만은 모름" 과 달랐다
        # **가격 행은 있는데 거래대금을 모르는 날은 0 이 아니라 뺀다** (25.539) — 운영 유니버스(25.518)와 같다.
        # 행이 아예 없는 날(쉼)은 그대로 0 이다(25.271)
        모름 = [d for d in market_window if d in series.closes and d not in series.turnover]
        아는_날 = len(market_window) - len(모름)
        if len(own) >= MIN_TURNOVER_SAMPLES and 아는_날 > 0:
            avg = sum(series.turnover.get(d, 0.0) for d in market_window if d not in 모름) / 아는_날
    else:
        values = [series.turnover[d] for d in _window(series, cutoff, TURNOVER_DAYS) if d in series.turnover]
        if len(values) >= MIN_TURNOVER_SAMPLES:
            avg = sum(values) / len(values)
    if avg is not None and avg < filters.min_avg_turnover_20d:
        return Decision(False, REASON_THIN, market_cap=cap, avg_turnover=avg)

    return Decision(True, None, market_cap=cap, avg_turnover=avg)


def market_dates_of(series_by_stock: dict[int, Series]) -> list[str]:
    """모든 종목의 거래일을 합친 **시장의 거래일**(오름차순). 한 번만 내서 `members_at` 에 넘긴다."""
    return sorted({d for s in series_by_stock.values() for d in s.dates})


def members_at(
    stocks: list[Stock],
    series_by_stock: dict[int, Series],
    cutoff: str,
    filters: Filters,
    market_dates: list[str] | None = None,
) -> tuple[set[int], dict[str, int]]:
    """그 시점의 유니버스와 탈락 사유별 건수.

    `market_dates` 는 시장의 거래일(오름차순). 없으면 여기서 낸다 — 리밸런스마다 부르는 곳은 한 번 내서 넘긴다.
    """
    날들 = market_dates if market_dates is not None else market_dates_of(series_by_stock)
    끝 = bisect_right(날들, cutoff)
    창 = 날들[max(0, 끝 - TURNOVER_DAYS) : 끝]
    included: set[int] = set()
    reasons: dict[str, int] = {}
    for stock in stocks:
        series = series_by_stock.get(stock.stock_id, Series())
        decision = judge(stock, series, cutoff, filters, market_window=창)
        if decision.included:
            included.add(stock.stock_id)
        else:
            key = decision.reason or "알 수 없음"
            reasons[key] = reasons.get(key, 0) + 1
    return included, reasons
