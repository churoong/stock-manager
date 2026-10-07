"""국내 수정주가 (docs/adjust.md).

**무엇을 푸는가.** 한국거래소 원자료는 미조정이다. 1:10 액면분할이 있으면 종가가
하루 만에 10분의 1이 된다. 그대로 두면 -90% 수익률이 되어 모멘텀·리스크 팩터와
백테스트가 통째로 틀어진다. 실측(2026-09-17): 5년 국내 가격에서 일일 가격제한
±30% 를 넘는 움직임이 836번, 662종목에 있었다. 유니버스의 75% 다.

**어떻게 푸는가.** 한국거래소가 같은 응답에서 주는 등락률(FLUC_RT)은 기업행위를
반영한 기준가로 계산된다. 그래서 그날의 조정 계수를 되살릴 수 있다.

    계수(t) = (종가(t) / 종가(t-1)) / (1 + 등락률(t)/100)

분할이 없는 날은 1 에 가깝고, 1:10 분할이면 0.1 이다. 수정주가는 과거를 현재 기준으로
끌어내린 값이므로 **뒤에서 앞으로** 계수를 누적한다.

    수정종가(t) = 종가(t) × Π(계수(u), u > t)

새 데이터 소스를 들이지 않는다. 1순위 소스(KRX) 안에서 끝난다.

이 모듈은 DB 도 시각도 모른다. 날짜·종가·등락률만 받는다.
"""

from __future__ import annotations

from dataclasses import dataclass

CALC_VERSION = 1

#: 계수가 1 에서 이만큼 벗어나면 기업행위로 본다.
#:
#: 왜 0.02 인가: 등락률은 소수점 둘째 자리까지만 온다(예: -0.37). 종가가 작은 종목은
#: 반올림 오차만으로 계수가 1 에서 0.5% 안팎 흔들린다. 2% 면 그 오차를 덮으면서
#: 가장 작은 기업행위(2:1 분할 = 0.5)와도 멀다. 실측이 아니라 여유값이다 [확인필요]
ACTION_TOLERANCE = 0.02

#: 계수가 이 범위를 벗어나면 데이터가 이상한 것으로 보고 무시한다(1 로 둔다).
#: 1:100 분할(0.01)과 100:1 병합(100)까지는 받아들인다.
MIN_FACTOR = 0.005
MAX_FACTOR = 200.0

#: 두 행이 이보다 더 벌어져 있으면 **사이에 거래일이 빠져 있다** (docs/infra.md 25.132).
#:
#: `batch/services/metrics.MAX_SESSION_GAP_DAYS` 와 **같은 값이어야 한다** — 같은 사실을
#: 재는 자리이고, 그쪽이 실측한 값이다(`exchange_calendars` 로 2016~2026 의 거래일 사이
#: 최장 간격, XKRX 11일). 여기서 다시 재지 않고 가져다 쓴다 (25.0 「한 규칙이 두 곳에 있다」).
def _최장_휴장_간격() -> int:
    from batch.services.metrics import MAX_SESSION_GAP_DAYS

    return MAX_SESSION_GAP_DAYS


@dataclass(frozen=True)
class Day:
    date: str
    close: float
    change_pct: float | None


@dataclass
class Adjusted:
    date: str
    adj_close: float
    #: 그날 적용된 조정 계수. 1.0 이면 기업행위가 없었다
    factor: float
    #: 앞 행과의 **달력 일수**. 첫 행은 0.
    #:
    #: 이것이 최장 휴장 간격보다 크면 **사이에 거래일이 빠져 있다** — 계수를 되살리는 식이
    #: "앞 행 = 바로 전 거래일" 을 전제하므로, 그때의 계수는 기업행위가 아니라
    #: **빠진 구간의 수익률**일 수 있다 (docs/infra.md 25.132).
    gap_days: int = 0
    #: 앞 행의 날짜. 사이에 **빠진 거래일**을 세는 데 쓴다 (25.569). 첫 행은 None
    prev_date: str | None = None


def factor_of(prev_close: float, close: float, change_pct: float | None) -> float:
    """그날의 조정 계수. 알 수 없으면 1.0(조정 없음)을 돌려준다.

    1.0 을 돌려주는 경우를 넓게 잡은 이유: 모르는 날을 억지로 조정하면 **없던 기업행위를
    만들어 낸다.** 조정하지 않으면 그날 하나가 튈 뿐이지만, 잘못 조정하면 그 이전
    전체가 어긋난다. 틀리는 방향을 고른 것이다.
    """
    if change_pct is None or prev_close <= 0 or close <= 0:
        return 1.0
    ratio = 1 + change_pct / 100
    if ratio <= 0:
        # 등락률 -100% 이하. 정상 시세로는 나올 수 없는 값이다
        return 1.0
    factor = (close / prev_close) / ratio
    if factor < MIN_FACTOR or factor > MAX_FACTOR:
        return 1.0
    return factor


def is_action(factor: float) -> bool:
    """기업행위로 볼 만큼 1 에서 벗어났나."""
    return abs(factor - 1.0) > ACTION_TOLERANCE


def _사이_일수(앞: str, 뒤: str) -> int:
    from datetime import date as _date

    try:
        return (_date.fromisoformat(뒤) - _date.fromisoformat(앞)).days
    except ValueError:
        return 0


def is_suspect(a: Adjusted) -> bool:
    """**구멍 위에서 판정된 기업행위인가** (docs/infra.md 25.132).

    계수 식은 `앞 행 = 바로 전 거래일` 을 전제한다. 수집이 하루 빠지면 그 전제가 깨지고,
    계수가 **빠진 구간의 수익률**이 된다. 그것이 2% 를 넘으면 기업행위로 오인되고,
    수정계수는 **그 이전 전체**에 곱해지므로 종목의 역사가 통째로 밀린다.

    **여기서 계수를 바꾸지 않는다.** 국내 액면분할은 매매거래정지를 동반해 진짜 기업행위도
    구멍 위에 앉는다. 둘을 가르려면 한국거래소가 정지 종목의 행을 주는지 알아야 하는데
    확인되지 않았다 `[확인필요]`. 그래서 **표시만 한다** — 모르는 것을 아는 척하지 않는다.
    """
    if not is_action(a.factor):
        return False
    빠짐 = _빠진_거래일(a)
    # **날짜 간격이 아니라 빠진 거래일로 센다** (docs/infra.md 25.569, 감사 재현). 11일 문턱은 정상 연휴를 거르려는
    # 것이었는데, 25.132 가 든 바로 그 사례("수집이 하루 빠지면" — 간격 2~4일)를 한 번도 잡지 못했다. 달력을 못 쓰면
    # 예전처럼 날짜 간격으로 본다
    return 빠짐 > 0 if 빠짐 is not None else a.gap_days > _최장_휴장_간격()


def _빠진_거래일(a: Adjusted) -> int | None:
    """이 행과 앞 행 사이에 **거래일인데 행이 없는 날**의 수. 앞 행 날짜를 모르거나 달력을 못 쓰면 None."""
    if not a.prev_date:
        return None
    try:
        import pandas as pd

        from batch.core import calendar as cal

        사이 = cal.exchange_calendar("KR").sessions_in_range(pd.Timestamp(a.prev_date), pd.Timestamp(a.date))
        return max(0, len(사이) - 2)
    except Exception:  # noqa: BLE001 — 달력 문제로 판정을 막지 않는다. 날짜 간격으로 물러난다
        return None


def adjust(days: list[Day]) -> list[Adjusted]:
    """수정종가 시계열. 날짜 오름차순으로 받고 같은 순서로 돌려준다.

    가장 최근 날은 조정하지 않는다(계수 1). 과거로 갈수록 이후의 기업행위가 누적된다.
    """
    if not days:
        return []

    factors: list[float] = [1.0]
    for prev, cur in zip(days, days[1:], strict=False):
        raw = factor_of(prev.close, cur.close, cur.change_pct)
        # 기업행위가 아닌 날은 계수를 1 로 눌러 둔다. 반올림 오차가 쌓이면
        # 몇 해 뒤의 수정주가가 조용히 몇 % 씩 밀린다
        factors.append(raw if is_action(raw) else 1.0)

    gaps = [0] + [_사이_일수(prev.date, cur.date) for prev, cur in zip(days, days[1:], strict=False)]

    out: list[Adjusted] = []
    cumulative = 1.0
    # 뒤에서 앞으로: 어떤 날의 수정주가는 그 **다음** 날들의 계수를 모두 곱한 값이다
    for i in range(len(days) - 1, -1, -1):
        out.append(
            Adjusted(
                date=days[i].date,
                adj_close=days[i].close * cumulative,
                factor=factors[i],
                gap_days=gaps[i],
                prev_date=days[i - 1].date if i > 0 else None,
            )
        )
        cumulative *= factors[i]
    out.reverse()
    return out


def actions(days: list[Day]) -> list[tuple[str, float]]:
    """기업행위로 판정된 (날짜, 계수) 목록. 무엇을 고쳤는지 눈으로 보려고 남긴다."""
    return [(a.date, a.factor) for a in adjust(days) if is_action(a.factor)]


def suspect_actions(adjusted: list[Adjusted]) -> list[Adjusted]:
    """구멍 위에서 판정된 기업행위들 (docs/infra.md 25.132)."""
    return [a for a in adjusted if is_suspect(a)]
