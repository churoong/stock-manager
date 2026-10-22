"""텔레그램 메시지 포맷.

원칙
  - 숫자는 DB 에 저장된 값만 쓴다. 만들어 내지 않는다
  - 데이터 기준 시각을 항상 표시한다
  - 지연된 시세는 지연이라고 밝힌다
  - 마지막에 책임 고지를 붙인다 (telegram.send 가 처리한다)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from batch.core import calendar as cal


def _money(value: float | None, currency: str) -> str:
    if value is None or value != value:  # NaN 도 모름이다 (25.613)
        return "-"
    if currency == "KRW":
        return f"{round(value):,}원"  # 정수로 반올림 — "-0원" 이 나오지 않는다 (25.613)
    r = round(value, 2)
    return f"{'-' if r < 0 else ''}${abs(r):,.2f}"  # "$-1,234.50" 이 아니라 "-$1,234.50" (25.613)


#: 미국 종목 정식 이름에서 떼는 꼬리 — 리포트 한 줄에 "LTC Properties, Inc. Common Stock" 이 다 실려 주문 다섯 건이
#: 화면을 가득 채웠다(25.1721, 2026-10-22 사용자 지시 "텔레그램 리포트가 너무 가독성이 떨어진다").
#: 뒤에서부터 되풀이해 뗀다
_이름_꼬리 = re.compile(
    r"(,?\s+(Class [A-Z]\s+)?(Common Stock|Capital Stock|Ordinary Shares|American Depositary Shares|Depositary Shares"
    r"|Common Shares|Shares of Beneficial Interest|Class [A-Z]|Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?"
    r"|Ltd\.?|Limited|plc|N\.V\.|S\.A\.|L\.P\.|Holdings?|Group|\(The\)))+$",
    re.IGNORECASE,
)
#: 줄인 이름의 길이 상한(글자) — 넘으면 자르고 "…"
SHORT_NAME_MAX = 24


def short_name(name: Any, ticker: Any = None, currency: str = "KRW") -> str:
    """리포트에 싣는 짧은 종목 이름 — 국내는 이름 그대로, 미국은 법인 꼬리를 뗀 이름 + (티커).

    이름을 다 떼어 비면 티커만. 원래 이름은 웹(종목 상세)에 그대로 있다
    """
    이름 = str(name or "").strip()
    티커 = str(ticker or "").strip()
    if currency == "KRW":
        return 이름 or 티커
    짧게 = _이름_꼬리.sub("", 이름).strip(" ,") or 이름
    # 앞의 "The " 도 뗀다(25.1723) — "The Goldman Sachs Group, Inc." → "Goldman Sachs". 떼서 비면 그대로
    if 짧게.lower().startswith("the ") and len(짧게) > 4:
        짧게 = 짧게[4:]
    if len(짧게) > SHORT_NAME_MAX:
        짧게 = 짧게[: SHORT_NAME_MAX - 1].rstrip(" ,") + "…"
    if not 짧게:
        return 티커
    return f"{짧게}({티커})" if 티커 and 티커 != 짧게 else 짧게


def won_short(value: float | None) -> str:
    """큰 원화를 억·만 단위로 — 996,547,801 → "9억 9,655만원" (25.1721, 리포트 가독성). 1만 원 아래는 원 그대로"""
    if value is None or value != value:
        return "-"
    # 반올림은 늘 0.5 올림(25.1723 — 파이썬 `round` 는 짝수 쪽이라 15,000 → 2만, 25,000 → 2만 이었다)
    x = int(abs(float(value)) + 0.5)
    부호 = "-" if float(value) < 0 and x else ""
    if x < 10_000:
        return f"{부호}{x:,}원"
    만 = (x + 5_000) // 10_000
    억, 나머지 = divmod(만, 10_000)
    if not 억:
        return f"{부호}{만:,}만원"
    return f"{부호}{억:,}억" + (f" {나머지:,}만원" if 나머지 else "원")


def with_head(message: str, head: list[str]) -> str:
    """리포트 머리 블록(제목·생성·요약·지연 — 첫 빈 줄까지) 바로 아래에 `head` 를 끼운다.

    아침 운용 보고 (docs/desk.md 7장, 25.1510). 제목이 맨 위에 그대로 있어야 알림 미리보기·리포트 보관함에서 무슨
    리포트인지 보인다. 처음엔 두 줄 뒤에 끼워 요약·"예정보다 N분 늦게" 줄이 운용 보고 끝에 붙어 그 일부처럼 읽혔다
    (25.1512 검토 2) — 머리 블록이 끝나는 첫 빈 줄 뒤에, 끝에 빈 줄 하나를 두고 끼운다. 빈 줄이 없으면 끝에 붙인다
    """
    줄 = message.split("\n")
    i = 줄.index("") if "" in 줄 else len(줄)
    return "\n".join([*줄[: i + 1], *head, "", *줄[i + 1:]]).rstrip("\n")


#: 경고 한 줄의 상한 (25.613, 감사). 예외 문장을 그대로 싣는 경고가 3,900자를 넘으면 조각이 줄 가운데서 잘리고,
#: 경고는 1부보다 앞이라 1부를 둘째 조각으로 밀어냈다. 원문은 배치 기록에 있다
WARNING_LINE_MAX = 300


def _한_줄(text: str) -> str:
    return text if len(text) <= WARNING_LINE_MAX else text[:WARNING_LINE_MAX] + " … (잘림, 배치 기록 참고)"


def _change(pct: float | None) -> str:
    if pct is None or pct != pct:
        return "전일 대비 -"
    return f"{round(pct, 2) + 0.0:+.2f}%"


def _volume(value: int | None) -> str:
    if value is None:
        return "-"
    # 만 단위로 반올림한 값이 1만(=1억)에 닿으면 억으로 적는다 — 99,995,000주가 "10000만주" 로 찍혔다 (25.421)
    if value >= 100_000_000 or round(value / 10_000) >= 10_000:
        return f"{value / 100_000_000:.1f}억주"
    if value >= 10_000:
        return f"{value / 10_000:.0f}만주"
    return f"{value:,}주"


def late_warnings(warnings: list[str]) -> str:
    """리포트 본문을 만든 뒤에 생긴 경고 (docs/infra.md 25.1103). 머리 경고 절과 같은 모양으로 끝에 싣는다."""
    return "\n".join(["경고 (리포트를 마무리하며)", *(f"  - {_한_줄(w)}" for w in warnings)])


def daily_report(
    *,
    market: str,
    trade_date: str,
    rows: list[dict[str, Any]],
    started_at: datetime,
    delay_seconds: int | None = None,
    composed_at: datetime | None = None,
    warnings: list[str] | None = None,
    summary: str | None = None,
) -> str:
    """일일 리포트 본문을 만든다.

    Args:
        rows: 종목별 dict. name, close, change_pct, volume, currency, source 를 담는다
    """
    label = cal.MARKETS[market.upper()]["label"]
    lines = [
        f"[{label}] {trade_date} 종가",
        f"생성 {cal.format_local(started_at, market)}",
    ]
    # **본문을 다 만든 시각도 적는다** (docs/infra.md 25.756, 리포트 감사). "생성" 은 실행 시작이라, 약 10분 걸리는 날
    # 08:50 에 시작하면 개장 뒤에 도착해도 본문은 "생성 08:50" 만 말했다. 1분 넘게 차이 날 때만 붙인다
    if composed_at is not None and (composed_at - started_at).total_seconds() >= 60:
        lines[-1] += f" · 작성 끝 {cal.format_local(composed_at, market)}"

    if summary:
        lines.append(summary)

    if delay_seconds is not None and delay_seconds > 600:
        lines.append(f"예정보다 {delay_seconds // 60}분 늦게 실행됨")

    lines.append("")

    if not rows:
        lines.append("가져온 종목이 없습니다.")
    for row in rows:
        currency = row.get("currency", "KRW")
        # 그 종목의 마지막 시세가 리포트 날짜가 아니면 **그 날짜를 적는다** (docs/infra.md 25.326).
        # 예전에는 머리글의 "{trade_date} 종가" 아래에 지난 종가가 날짜 없이 실렸다
        그날 = row.get("trade_date")
        lines.append(f"{row['name']}" + (f" ({그날} 종가)" if 그날 and 그날 != trade_date else ""))
        lines.append(
            f"  {_money(row.get('close'), currency)}  {_change(row.get('change_pct'))}"
        )
        if row.get("volume") is not None:
            lines.append(f"  거래량 {_volume(row.get('volume'))}")

    if warnings:
        lines.append("")
        lines.append("경고")
        for warning in warnings:
            lines.append(f"  - {_한_줄(warning)}")

    sources = sorted({row.get("source", "") for row in rows if row.get("source")})
    if sources:
        lines.append("")
        lines.append(f"출처 {', '.join(sources)}")

    return "\n".join(lines)


#: 이번 재실행 본문을 웹에 저장하지 않았다는 표시 (daily 모름 경로, 25.574·25.575)
NOT_SAVED_MARK = "이번 재실행 본문은 웹에 저장하지 않았습니다"
#: 리포트를 웹에 **저장하지 못했다**는 표시 (25.672, 교차검증). 저장이 막힌 채 일부만 보냈거나 모르면 "웹의 오늘
#: 리포트를 보세요" 가
#: 거짓이다 — 웹에는 그날 리포트가 없다
WEB_SAVE_FAILED_MARK = "리포트를 웹에 저장하지 못했습니다"


def failure_alert(
    *,
    market: str,
    job_name: str,
    error_text: str,
    last_success: dict[str, Any] | None,
    title: str = "배치 실패",
    last_unread: bool = False,
    report_note: bool = True,
    head: str = "원인",
) -> str:
    """배치 실패 알림. `head` 는 본문 머리 — 실패가 아닌 알림(KIS모의투자 괴리)은 "내용" 으로(25.1654 — "원인" 머리가
    고장 알림처럼 읽혔다, 25.1631 감사 [권고]).

    `title` 이 기본이 아니면 **배치는 성공으로 닫힌 발송 문제**다 — 마지막 성공 줄을 싣지 않는다
    (docs/infra.md 25.504, 교차검증: 발송 모름·일부 발송이 "배치 실패 · 마지막 성공 어제" 로 와서 실패처럼 읽혔다).

    마지막 성공 리포트가 언제 것인지 함께 알려 준다.
    실패했다는 사실보다 "지금 내가 보는 데이터가 언제 것인가"가 중요하다.
    """
    label = cal.MARKETS.get(market.upper(), {}).get("label", market)
    lines = [
        f"[{label}] {title}",
        f"작업 {job_name}",
        "",
        head,
        f"  {error_text[:500]}" + (" … (잘림, 배치 기록 참고)" if len(error_text) > 500 else ""),
    ]

    if title != "배치 실패":
        if not report_note:
            # KIS모의투자 알림처럼 리포트 발송과 상관없는 알림 —
            # "리포트는 웹에" 줄이 내용과 맞지 않았다(25.1507, 검토 7)
            return "\n".join(lines)
        lines.append("")
        # 이번 본문을 웹에 저장하지 않은 경우(모름 재실행, 25.574)엔 "웹에 전체가 있다" 가 거짓이다 (25.575, 교차검증)
        if WEB_SAVE_FAILED_MARK in error_text:
            lines.append("배치는 끝났지만 리포트를 웹에 저장하지 못해 웹에서는 볼 수 없습니다. 받은 조각이 전부입니다.")
        elif NOT_SAVED_MARK in error_text:
            lines.append("배치는 끝났습니다. 웹의 오늘 리포트는 먼저 보낸 리포트입니다.")
        else:
            lines.append("배치는 끝났고 리포트는 웹의 오늘 리포트에 전체가 있습니다.")
        return "\n".join(lines)
    if last_success:
        lines.append("")
        lines.append(
            f"마지막 성공 {last_success.get('trade_date')} "
            f"({_finished_local(last_success.get('finished_at'), market)})"
        )
        lines.append("그때까지의 데이터는 그대로 남아 있습니다.")
    elif last_unread:
        # 못 읽은 것을 "이력 없음" 으로 적지 않는다 (docs/infra.md 25.818, 리포트 감사) — DB 가 막힌 날이 바로 이 경우다
        lines.append("")
        lines.append("마지막 성공 기록을 읽지 못했습니다(DB 접근 실패). 이전 데이터는 그대로일 수 있습니다.")
    else:
        lines.append("")
        lines.append("성공 이력이 아직 없습니다.")

    lines.append("")
    lines.append(f"다음 예정 {cal.next_scheduled_hint(market)}")
    return "\n".join(lines)


def skip_notice(*, market: str, reason: str, trade_date: str) -> str:
    """돌지 않고 건너뛴 이유. 평소에는 보내지 않고 수동 실행 때만 쓴다."""
    label = cal.MARKETS.get(market.upper(), {}).get("label", market)
    return f"[{label}] 실행하지 않음\n사유 {reason}\n기준 거래일 {trade_date}"


def _finished_local(finished_at: Any, market: str) -> str:
    """마지막 성공 시각을 리포트의 "생성" 줄과 같은 현지 시각으로 (docs/infra.md 25.339).

    예전에는 UTC 글자를 그대로 잘라 붙였다. 국내 배치가 09-26 08:31 KST 에 성공했으면
    "2026-09-25 23:31 UTC" 로 나와, 바로 앞의 거래일과 나란히 **하루 어긋나 보였다.**
    읽지 못하는 값이면 받은 그대로 둔다 — 실패 알림이 이것 때문에 무너지면 안 된다.
    """
    text = str(finished_at or "")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return f"{text[:16]} UTC" if text else "시각 모름"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return cal.format_local(moment, market)
