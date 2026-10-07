"""거래소 캘린더와 실행 시점 판단.

예약 실행이 지연되거나 조용히 건너뛸 수 있으므로(docs/infra.md 1번),
배치는 "지금이 내가 돌아야 할 때인가"를 스스로 판단해야 한다.

미국 배치는 서머타임 때문에 UTC 고정 시각으로 표현할 수 없다.
예약은 두 시각에 걸어 두고, 실제로 장 시작 전인지는 여기서 판정한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

MARKETS = {
    "KR": {
        "exchange": "XKRX",
        "timezone": "Asia/Seoul",
        "label": "국내",
    },
    "US": {
        "exchange": "XNYS",
        "timezone": "America/New_York",
        "label": "미국",
    },
}

# 장 시작 몇 분 전에 돌아야 하는가. 설계상 1시간 전이다.
LEAD_MINUTES = 60

# 실행을 허용하는 구간(개장까지 남은 분). 이 밖이면 이번 호출은 건너뛴다.
#
# 폭을 정하는 조건이 둘이다.
#   1. 예약이 늦어도 개장 전이면 돌아야 한다 -> 아래쪽을 넉넉히
#   2. 미국은 서머타임 때문에 60분 간격으로 예약 두 개를 건다.
#      둘 다 이 구간에 들면 같은 날 두 번 돈다 -> 폭이 120분을 넘으면 안 된다
#
# 실제 값은 서머타임 기간에 63분과 3분, 표준시에 123분과 63분이다.
# 아래 구간이면 매번 63분짜리 하나만 **정시 실행**으로 걸린다. (2026-10-03 정정, docs/infra.md 25.913: 25.845 부터
# 3분짜리도
# "늦은 실행" 으로 통과한다 — 같은 날 두 번째 실행을 막는 것은 이제 `db.has_successful_run`·`reports.sent_exists` 이고,
# 그 건너뜀은 할 일 없는 실행 표식(`NOTHING_DONE`)을 찍는다)
RUN_WINDOW_MIN_MINUTES = 25  # 개장 25분 전보다 늦으면 "늦은 실행" 이다(장 마감 전까지 돌고 머리에 표시, 25.845)
RUN_WINDOW_MAX_MINUTES = 100  # 개장 100분 전보다 이르면 다음 예약에 맡긴다

# 시장별로 아래쪽 문턱을 따로 둔다 (2026-09-17).
#
# 국내는 예약을 08:27 KST(개장 33분 전)로 늦췄다. 한국거래소가 전일 시세를
# 07:56 에는 주지 않고 08:17 에는 줬기 때문이다(docs/infra.md 16.1절).
# 25분 문턱을 그대로 두면 예약이 8분만 밀려도 건너뛴다. Actions 예약은 그
# 정도 지연이 흔하다. 국내 배치는 1~2분이면 끝나므로 개장 10분 전까지 허용한다.
#
# 미국은 25분을 유지한다. 위 2번 조건(예약 두 개 중 3분짜리를 걸러야 한다)
# 때문이고, 미국 공개 시각 문제는 없다.
RUN_WINDOW_MIN_BY_MARKET = {"KR": 10, "US": RUN_WINDOW_MIN_MINUTES}

#: 일일 배치의 **예약 시각** = 그날 정규장 시작 − 이만큼(분) (docs/infra.md 25.327).
#: 국내 08:27 KST(개장 09:00), 미국 08:27 ET(개장 09:30). 정의처는 워크플로의 cron 이고
#: `tests/test_scheduled_for.py` 가 두 값이 cron 과 맞는지 대 본다. 이것으로 `batch_runs.delay_seconds` 와
#: 리포트의 "예정보다 N분 늦게 실행됨" 을 낸다 — 예전에는 아무도 예약 시각을 넘기지 않아 늘 비었다
SCHEDULED_BEFORE_OPEN_MINUTES = {"KR": 33, "US": 63}


def scheduled_for(market: str, session_day: date) -> str | None:
    """그날 일일 배치가 돌았어야 하는 시각(UTC ISO). 휴장일이면 None.

    **그날의 개장 시각이 아니라 정규 개장 시각**에서 뺀다 (docs/infra.md 25.458). cron 은 날마다 같은 시각에 돈다 —
    새해 첫 거래일(10:00 개장)에 그날 개장으로 재면 예약이 09:27 로 밀려, 08:27 에 제때 돈 배치가 "−60분 지연" 이 됐다.
    """
    cal = exchange_calendar(market)
    minutes = SCHEDULED_BEFORE_OPEN_MINUTES.get(market.upper())
    if minutes is None or not cal.is_session(session_day.isoformat()):
        return None
    regular = cal.open_times[-1][1]  # 지금 적용되는 정규 개장(국내 09:00, 미국 09:30 현지)
    local = datetime.combine(session_day, regular, tzinfo=ZoneInfo(str(cal.tz)))
    return (local - timedelta(minutes=minutes)).astimezone(UTC).isoformat()


class UnknownMarket(ValueError):
    pass


def _market_config(market: str) -> dict[str, str]:
    conf = MARKETS.get(market.upper())
    if conf is None:
        raise UnknownMarket(f"알 수 없는 시장: {market}")
    return conf


def market_tz(market: str) -> ZoneInfo:
    """그 시장의 현지 시간대."""
    return ZoneInfo(_market_config(market)["timezone"])


def local_today(market: str) -> date:
    """그 시장의 현지 날짜. 거래일 기준은 항상 현지 날짜다."""
    return datetime.now(market_tz(market)).date()


#: 사용자가 사는 시간대. CLAUDE.md: "사용자는 개인 투자자 1명" 이고 앱 전체가 한국 시각을
#: 기준으로 말한다 — 조용시간·텔레그램 리포트 시각·장중 알림 묶음이 전부 KST 다.
USER_TIMEZONE = "Asia/Seoul"


def user_today() -> date:
    """**사용자에게 "오늘" 인 날짜** (docs/infra.md 25.125).

    거래일이 아니라 **사람의 달력**이다. 보유 기간(며칠째 들고 있나), 근거표의 "언제 기준",
    매도 플래그의 as_of 처럼 **시장이 아니라 사용자를 향한 날짜**가 이것을 쓴다.

    **UTC 날짜를 쓰면 안 된다.** 국내 일일 배치는 08:27 KST 에 도는데 그때 UTC 는 아직
    **전날 23:27** 이다. `datetime.now(UTC).date()` 는 국내 배치가 도는 **매 회** 하루
    전 날짜를 돌려준다. 예외가 아니라 상시다.

    `local_today(market)` 과 다르다. 그쪽은 "그 시장의 현지 날짜" 라 거래일 판정에 쓰고,
    이쪽은 나라가 섞인 보유 종목 하나의 달력이다. 둘을 한 이름으로 부르면 미국 보유분의
    보유 기간을 뉴욕 날짜로 세게 된다.

    덤으로 **경계에서 가장 멀다.** KST 자정은 15:00 UTC 라 국내(08:27 KST)·미국(21:30 KST)
    배치 어느 쪽에서도 멀다. 한 배치가 도는 중에 날짜가 바뀌는 일이 없다.
    """
    return datetime.now(ZoneInfo(USER_TIMEZONE)).date()


def is_session(market: str, day: date | None = None) -> bool:

    cal = exchange_calendar(market)
    target = day or local_today(market)
    return bool(cal.is_session(target.isoformat()))


def previous_session(market: str, before: date | None = None) -> date:
    """기준일보다 앞선 마지막 거래일. 배치가 다루는 데이터의 거래일이다.

    캘린더의 previous_session 은 거래일만 입력으로 받는다. 휴장일을 주면
    예외를 낸다. 휴장일에도 물어볼 수 있어야 하므로 한 단계를 거친다.
    """
    import pandas as pd

    cal = exchange_calendar(market)
    anchor = pd.Timestamp(before or local_today(market))

    # 기준일이 거래일이면 그대로, 휴장일이면 그 이전 거래일이 나온다
    landed = cal.date_to_session(anchor, direction="previous")

    if landed.date() == anchor.date():
        # 기준일 자신이 거래일이었으므로 한 칸 더 뒤로 간다
        return cal.previous_session(landed).date()
    return landed.date()


def default_as_of(market: str, today: date | None = None) -> str:
    """손으로 기준일을 안 줬을 때 계산 작업이 쓸 기준일 — **직전 거래일** (docs/infra.md 25.234).

    일일 배치가 넘기는 `trade_date`(`decide` 의 `previous_session(현지 오늘)`)와 **같은 정의**다.
    점수·신호·밴드는 기준일을 열쇠로 쌓고 화면·리포트가 `MAX(as_of_date)` 를 읽으므로, 따로 도는
    실행(D1 따라잡기·예비 워크플로)이 다른 날짜를 쓰면 **아침 배치보다 앞선 날짜**가 가장 새 것이 된다.

    2026-09-26 까지 기본값은 `datetime.now(UTC).date()` 였다. 매일 09:05 KST 에 도는 D1 따라잡기가
    토·일에도 그 날짜로 신호를 썼고, 월요일 아침 배치(기준 금요일)보다 **일요일 것이 MAX** 가 됐다.
    """
    return previous_session(market, today or local_today(market)).isoformat()


#: **라이브러리에 빠진 휴장일** — 손으로 더하는 자리 (docs/infra.md 25.449). exchange_calendars 는 사용자 기여로
#: 유지되어 임시공휴일 반영이 늦다(CLAUDE.md). 2026-09-28 에 재 보니 4.13.2 가 **2026-06-03 지방선거일**을
#: 거래일로 준다(2018·2020·2022·2024·2025 선거일은 휴장으로 맞다). 이런 날은 다음 날 아침 배치가
#: `trade_date=그날` 로 KRX 에 물어 0행을 받고 실패하며, 백필은 매 실행 "빠진 날" 로 되묻는다.
#: 새 임시공휴일이 지정되면 **여기에 날짜를 더한다** — 연 1회 거래소 공지와 대조(CLAUDE.md 휴장일 규칙)
#: `[확인필요: 2026-06-03 KRX 휴장 공지]` — 선거일은 법정 공휴일이고 역대 선거일은 모두 휴장이었다
EXTRA_HOLIDAYS: dict[str, frozenset[str]] = {
    "KR": frozenset({"2026-06-03"}),
    "US": frozenset(),
}

_patched: dict[str, Any] = {}


def exchange_calendar(market: str) -> Any:
    """그 시장의 거래소 달력 — **`EXTRA_HOLIDAYS` 를 더한 것** (25.449). 라이브러리 달력을 직접 부르지 말고 이것을 쓴다.

    라이브러리 달력 클래스를 상속해 `adhoc_holidays` 에 날짜를 더한다. 그러면 `is_session`·`previous_session`·
    `sessions_in_range`·`opens` 가 모두 같이 바뀐다 — 함수마다 따로 거르면 한 곳을 빠뜨린다.
    """
    import exchange_calendars as xcals
    import pandas as pd

    code = _market_config(market)["exchange"]
    extras = EXTRA_HOLIDAYS.get(market.upper(), frozenset())
    if not extras:
        return xcals.get_calendar(code)
    name = f"{code}_EXTRA"
    if name not in _patched:
        base = type(xcals.get_calendar(code))
        더할 = [pd.Timestamp(d) for d in sorted(extras)]

        def adhoc_holidays(self: Any) -> list:
            return list(base.adhoc_holidays.fget(self)) + 더할  # type: ignore[attr-defined]

        cls = type(name, (base,), {"name": name, "adhoc_holidays": property(adhoc_holidays)})
        xcals.register_calendar_type(name, cls, force=True)
        _patched[name] = xcals.get_calendar(name)
    return _patched[name]


def next_session(market: str, after: date) -> date:
    """기준일보다 뒤의 첫 거래일. 공시일 다음 거래일(시점 기준일)을 낼 때 쓴다.

    캘린더 범위 밖(대략 20년 전)이면 다음 평일로 갈음한다. 휴장일 하루 차이는 시점 판정에 영향이 작다.
    """
    import pandas as pd

    cal = exchange_calendar(market)
    anchor = after + timedelta(days=1)
    try:
        return cal.date_to_session(pd.Timestamp(anchor), direction="next").date()
    except Exception:  # noqa: BLE001 — 범위 밖 날짜
        while anchor.weekday() >= 5:
            anchor += timedelta(days=1)
        return anchor


def session_open_utc(market: str, day: date) -> datetime | None:
    """그 거래일의 정규장 시작 시각을 UTC 로 돌려준다.

    캘린더가 주는 값을 쓰므로 서머타임이 자동 반영된다.
    """

    cal = exchange_calendar(market)
    if not cal.is_session(day.isoformat()):
        return None
    opens = cal.opens
    key = opens.index[opens.index.date == day] if hasattr(opens.index, "date") else []
    if len(key) == 0:
        return None
    value = opens.loc[key[0]]
    stamp = value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
    return stamp.to_pydatetime()


def session_close_utc(market: str, day: date) -> datetime | None:
    """그 거래일의 정규장 마감 시각(UTC). 반일장·서머타임은 캘린더가 준다. 거래일이 아니면 None (25.611)."""
    cal = exchange_calendar(market)
    if not cal.is_session(day.isoformat()):
        return None
    closes = cal.closes
    key = closes.index[closes.index.date == day] if hasattr(closes.index, "date") else []
    if len(key) == 0:
        return None
    value = closes.loc[key[0]]
    stamp = value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
    return stamp.to_pydatetime()


@dataclass
class RunDecision:
    should_run: bool
    reason: str
    trade_date: str  # 다룰 데이터의 거래일 YYYY-MM-DD
    session_date: str  # 오늘 열리는 거래일 YYYY-MM-DD
    minutes_to_open: int | None = None
    #: 장 시작 전 창을 놓친 **늦은 실행**이면 개장 기준 분(음수면 개장 뒤). 리포트 머리에 "늦은 리포트" 를 단다 (25.845)
    late_minutes: int | None = None


def decide(
    market: str, now: datetime | None = None, force: bool = False
) -> RunDecision:
    """지금 이 배치를 돌려야 하는지 판단한다.

    판단 순서
      1. 오늘이 휴장이면 돌지 않는다
      2. force 면 시각 검사를 건너뛴다 (수동 실행)
      3. 장 시작 전 창을 놓쳤으면(개장 뒤·개장 직전) **장 마감 전까지는 "늦은 실행" 으로 돈다**
         (2026-10-01 사용자 결정, 25.845) — 예전에는 그날을 통째로 건너뛰어 손절 매도 플래그·시세 수집·
         리포트가 모두 빠졌다. 마감 뒤에는 건너뛴다. 같은 거래일이 이미 성공했으면 `daily.run` 이 건너뛴다
      4. 장 시작까지 너무 멀면 다음 예약에 맡긴다
    """
    conf = _market_config(market)
    current = now or datetime.now(UTC)
    tz = ZoneInfo(conf["timezone"])
    today_local = current.astimezone(tz).date()

    if not is_session(market, today_local):
        prev = previous_session(market, today_local)
        return RunDecision(
            should_run=False,
            reason=f"{conf['label']} 시장 휴장일",
            trade_date=prev.isoformat(),
            session_date=today_local.isoformat(),
        )

    # 장 시작 전에 도는 배치이므로, 다루는 데이터는 직전 거래일 것이다.
    prev = previous_session(market, today_local)
    decision_base = {
        "trade_date": prev.isoformat(),
        "session_date": today_local.isoformat(),
    }

    open_utc = session_open_utc(market, today_local)
    if open_utc is None:
        return RunDecision(
            should_run=True,
            reason="개장 시각을 알 수 없어 그대로 진행",
            **decision_base,
        )

    minutes_to_open = int((open_utc - current).total_seconds() // 60)

    if force:
        return RunDecision(
            should_run=True,
            reason=f"수동 실행 (개장까지 {minutes_to_open}분)",
            minutes_to_open=minutes_to_open,
            **decision_base,
        )

    늦음 = minutes_to_open < RUN_WINDOW_MIN_BY_MARKET.get(market.upper(), RUN_WINDOW_MIN_MINUTES)
    if 늦음:
        close_utc = session_close_utc(market, today_local)
        if close_utc is not None and current >= close_utc:
            return RunDecision(
                should_run=False,
                reason=f"이미 장이 끝났습니다 (개장 {-minutes_to_open}분 경과). 다음 거래일 배치가 이어받습니다",
                minutes_to_open=minutes_to_open,
                **decision_base,
            )
        언제 = f"개장 {-minutes_to_open}분 뒤" if minutes_to_open <= 0 else f"개장 {minutes_to_open}분 전"
        return RunDecision(
            should_run=True,
            reason=f"늦은 실행 ({언제}) — 장 마감 전이라 늦은 리포트로 보냅니다",
            minutes_to_open=minutes_to_open,
            late_minutes=minutes_to_open,
            **decision_base,
        )

    if minutes_to_open > RUN_WINDOW_MAX_MINUTES:
        return RunDecision(
            should_run=False,
            reason=f"개장까지 {minutes_to_open}분 남아 아직 이릅니다. 다음 예약에 맡깁니다",
            minutes_to_open=minutes_to_open,
            **decision_base,
        )

    return RunDecision(
        should_run=True,
        reason=f"개장 {minutes_to_open}분 전",
        minutes_to_open=minutes_to_open,
        **decision_base,
    )


def format_local(moment: datetime, market: str) -> str:
    """UTC 시각을 그 시장의 현지 시각 문자열로 바꾼다."""
    conf = _market_config(market)
    tz = ZoneInfo(conf["timezone"])
    suffix = "KST" if market.upper() == "KR" else "ET"
    return moment.astimezone(tz).strftime(f"%Y-%m-%d %H:%M {suffix}")


def next_scheduled_hint(market: str) -> str:
    """다음 실행 예정에 대한 설명. 화면과 리포트에 쓴다."""
    # **같은 날 예비 실행을 말한다** (docs/infra.md 25.913, 감사). 실패 알림 끝의 "다음 예정 매 거래일 08:27" 은
    # "내일" 로 읽혀
    # 오늘 리포트를 포기하게 했다 — 실제로는 국내 09:35 KST(daily-kr.yml 예비 cron), 미국 서머타임 기간 13:27 UTC 가
    # 한 번 더 돈다
    if market.upper() == "KR":
        return "매 거래일 08:27 KST — 실패하면 같은 날 09:35 KST 예비 실행이 한 번 더 시도합니다"
    return "매 거래일 미국 정규장 시작 1시간 전 — 서머타임 기간에는 개장 직전 예비 실행이 한 번 더 시도합니다"


def library_version() -> str:
    """판정에 쓴 캘린더 라이브러리와 버전.

    임시공휴일이 늦게 반영되는 경우를 나중에 추적하려면 어떤 버전이
    판정했는지 알아야 한다.
    """
    import exchange_calendars as xcals

    return f"exchange_calendars {getattr(xcals, '__version__', '버전미상')}"


def holiday_basis(market: str, day: date) -> str:
    """그날 판정이 어디서 왔나 — 수동 휴장일 목록(`EXTRA_HOLIDAYS`)인지 라이브러리 달력인지 (25.670)."""
    수동 = day.isoformat() in EXTRA_HOLIDAYS.get(market.upper(), frozenset())
    return "수동 휴장일(EXTRA_HOLIDAYS)" if 수동 else "라이브러리"


def record_decision(client, market: str, day: date, is_open: bool, note: str = "") -> None:
    """휴장일 판정을 남긴다.

    판정을 기록해 두고, 나중에 어긋난 날을 찾아낼 수 있게 한다. 2026-10-07 부터 국내는 KIS 공식 휴장일 조회와도
    매일 대조한다(`compare_with_kis`, docs/infra.md 25.985) — 그 전에는 대조할 상대가 없었다.

    **휴장일도 남긴다** (docs/infra.md 25.670, 감사). 예전에는 수집을 마친 뒤 개장일만 불러 `is_open=0` 행이
    한 번도 안 생겼고, 마감 시각은 NULL 로 박혀 반일장이 빠졌고, 근거(라이브러리·수동 목록)도 없어
    "연 1회 거래소 공지와 대조"(CLAUDE.md) 할 자료가 없었다. `source` 에 버전과 근거를 함께 적는다.
    """
    from batch.core.db import now_iso

    conf = _market_config(market)
    open_utc = session_open_utc(market, day) if is_open else None
    close_utc = session_close_utc(market, day) if is_open else None

    client.execute(
        "INSERT INTO market_calendar"
        " (exchange, date, is_open, open_utc, close_utc, note, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (exchange, date) DO UPDATE SET"
        "   is_open = excluded.is_open, open_utc = excluded.open_utc, close_utc = excluded.close_utc,"
        "   note = excluded.note, source = excluded.source,"
        "   fetched_at = excluded.fetched_at",
        [
            conf["exchange"],
            day.isoformat(),
            1 if is_open else 0,
            open_utc.isoformat() if open_utc else None,
            close_utc.isoformat() if close_utc else None,
            note or None,
            f"{library_version()} · {holiday_basis(market, day)}",
            now_iso(),
        ],
    )


#: KIS 휴장일 조회와 견줄 앞날 수 (docs/infra.md 25.985). 임시공휴일은 대개 1~2주 전에 정해진다 `[확인필요]`
KIS_COMPARE_DAYS = 14


def compare_with_kis(market: str, statuses: list, today: date, days: int = KIS_COMPARE_DAYS) -> list[str]:
    """KIS 개장 여부와 우리 달력이 다른 날 (docs/infra.md 25.985). `statuses` 는 `sources.kis.DayStatus` 목록.

    다르면 **우리 달력이 틀렸을 수 있다** — 임시공휴일이 라이브러리에 아직 없거나 `EXTRA_HOLIDAYS` 를 잘못 적었거나.
    달력을 저절로 고치지는 않는다(되돌릴 수 없는 휴장 판정을 바깥 응답 하나로 바꾸지 않는다).
    사람이 `EXTRA_HOLIDAYS` 를 고친다.
    """
    out = []
    for s in statuses:
        if not (today <= s.day < today + timedelta(days=days)):
            continue
        ours = is_session(market, s.day)
        if ours != s.is_open:
            우리, 그쪽 = ("개장" if ours else "휴장"), ("개장" if s.is_open else "휴장")
            out.append(f"{s.day.isoformat()} 우리 달력 {우리} · KIS {그쪽}")
    return out


def suspect_calendar(is_open: bool, rows_collected: int) -> str | None:
    """캘린더 판정과 실제 데이터가 어긋나는지 본다.

    개장일이라는데 시세가 하나도 없거나, 휴장일이라는데 데이터가 들어오면
    캘린더를 의심해야 한다. 임시공휴일 반영이 늦으면 이렇게 드러난다.
    """
    if is_open and rows_collected == 0:
        return "개장일로 판정했는데 시세가 한 건도 없습니다. 캘린더가 틀렸을 수 있습니다"
    if not is_open and rows_collected > 0:
        return "휴장일로 판정했는데 시세가 들어왔습니다. 캘린더가 틀렸을 수 있습니다"
    return None
