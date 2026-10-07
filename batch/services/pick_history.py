"""건너뛴 추천 영수증 — 이 종목은 몇 번째 추천이고, 처음 봤을 때보다 얼마나 움직였나 (docs/reports.md 3.3, 25.950).

아침 리포트는 날마다 새것처럼 온다. 그런데 같은 종목이 열흘째 1부에 있으면 그것은 **열흘 동안 안 산 결정**이고,
그 결정의 값은 처음 본 날 종가와 오늘 종가의 차이다. 리포트는 그 차이를 한 번도 말해 주지 않았다 — 지난 리포트는
화면에 남지만(docs/reports.md) 사람이 날짜를 거슬러 세어야 했다. 여기서는 1부 종목마다 한 줄을 붙인다:

    첫 추천                                   ← 지난 리포트에 없던 종목
    3번째 추천 (연속 3일) — 첫 추천 09-29 종가 70,000원 대비 +3.1%

점수도 고른 종목도 바꾸지 않는다. **지난 리포트가 실제로 실은 것**(`report_items` recommend 절)만 센다 — 신호 표가
아니라 리포트 표를 보는 까닭은 "사용자가 봤던 것" 이 기준이기 때문이다(근거표가 없어 안 실린 날은 안 본 것이다).
처음 본 날 종가는 그날 항목의 `payload.close`(그때 적힌 값 그대로)이고, 오늘 값은 1부가 적는 종가와 같다. 둘 다 DB 의
실제 수치다.

연속 n일 = 그 시장의 가장 최근 리포트 날부터 거슬러 올라가며 빠짐없이 실린 날 수. 리포트가 없던 날(휴장·실패)은 세지
않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 첫 추천 종가 대비 비율을 적는 띠 (25.963). 밖이면 대비를 적지 않는다 — 리포트 항목의 종가는 **수정 전 종가**라,
#: 그 사이 액면분할·병합이 끼면 1:5 분할 하나로 "대비 −80%" 가 찍힌다. 국내 수정주가는 예약 없이 손으로만 돌아
#: (docs/infra.md 25.138) 다시 낸 종가로 바꿔 견줄 수도 없다. 밸류 분모 띠(scoring.MARKET_CAP_BAND, 25.954)와 같은 값
#: — 몇 주 사이 연속 추천된 종목이 정말로 반 토막·두 배가 될 수도 있지만, 그때 대비를 빼는 것은 덜 말하는 것이고
#: 분할을 −80% 로 적는 것은 틀리게 말하는 것이다
RATIO_BAND = (0.5, 2.0)


@dataclass(frozen=True)
class PickHistory:
    #: 지난 리포트에 실린 횟수(날 수). 오늘은 안 센다 — 오늘 줄은 "n+1 번째" 로 적는다
    times: int
    first_date: str | None
    first_close: float | None
    #: 가장 최근 리포트 날부터 거슬러 빠짐없이 실린 날 수 (오늘 제외)
    streak: int

    def as_payload(self) -> dict[str, Any]:
        return {"times": self.times, "first_date": self.first_date, "first_close": self.first_close,
                "streak": self.streak}  # fmt: skip


def summarize(rows: list[dict[str, Any]]) -> dict[int, PickHistory]:
    """`report_items`(recommend) 행 — {stock_id, trade_date, close} — 에서 종목마다 이력. 같은 날의 기간별 항목은 한
    날이다."""
    by_stock: dict[int, dict[str, float | None]] = {}
    all_dates: set[str] = set()
    for r in rows:
        sid = r.get("stock_id")
        d = r.get("trade_date")
        if sid is None or not d:
            continue
        sid, d = int(sid), str(d)
        all_dates.add(d)
        days = by_stock.setdefault(sid, {})
        close = r.get("close")
        # 같은 날 여러 기간 항목 — 종가는 같다. 먼저 온 값이 비었으면 뒤의 값으로 채운다
        if d not in days or days[d] is None:
            days[d] = float(close) if isinstance(close, int | float) else None
    ordered = sorted(all_dates, reverse=True)
    out: dict[int, PickHistory] = {}
    for sid, days in by_stock.items():
        dates = sorted(days)
        first = dates[0]
        streak = 0
        for d in ordered:
            if d in days:
                streak += 1
            else:
                break
        out[sid] = PickHistory(times=len(dates), first_date=first, first_close=days[first], streak=streak)
    return out


def history_line(h: dict[str, Any] | PickHistory | None, close_now: float | None, money: Any) -> str | None:
    """1부 한 줄. payload(dict)와 PickHistory 둘 다 받는다. `money(value)` 는 통화에 맞게 적는 함수."""
    if h is None:
        return "첫 추천"
    d = h.as_payload() if isinstance(h, PickHistory) else h
    times = d.get("times")
    if not isinstance(times, int) or times <= 0:
        return "첫 추천"
    streak = d.get("streak")
    연속 = f" (연속 {streak + 1}일)" if isinstance(streak, int) and streak >= 1 else ""
    line = f"{times + 1}번째 추천{연속}"
    first_date, first_close = d.get("first_date"), d.get("first_close")
    if isinstance(first_date, str) and first_date:
        line += f" — 첫 추천 {first_date[5:] if len(first_date) == 10 else first_date}"
        if isinstance(first_close, int | float) and first_close > 0:
            line += f" 종가 {money(first_close)}"
            if isinstance(close_now, int | float) and close_now > 0:
                ratio = close_now / first_close
                if RATIO_BAND[0] <= ratio <= RATIO_BAND[1]:
                    line += f" 대비 {round((ratio - 1) * 100, 1) + 0.0:+.1f}%"
                else:
                    line += " (가격 단위가 바뀐 듯해 대비는 적지 않음 — 분할·병합)"
    return line
