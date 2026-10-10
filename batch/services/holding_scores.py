"""보유 종목 점수 영수증 — 샀을 때 점수와 지금 점수를 2부에 나란히 (docs/reports.md 3.6, 25.953).

2부는 보유를 "현재 보유 평가 n원 (k종목)" 한 줄로만 적었다. 그 종목이 **지금 몇 점인지, 샀을 때 몇 점이었는지**는
웹 복기 화면에만 있다. 아침 리포트에서 추천 종목은 점수·순위·흔들기까지 보이는데 정작 내가 든 종목은 숫자가 없었다 —
추천과 보유를 같은 자로 재야 "바꿀까" 를 생각할 수 있다. 그래서 보유 종목마다 한 줄:

    보유 코스맥스: 지금 55점 (KOSPI 120위, 10-01) · 매수 때 62점 (09-20) → −7점

**표시일 뿐 매도 신호가 아니다**(CLAUDE.md "매도 플래그는 표시와 알림만. 자동 매도 절대 없음"). 매도 플래그는 따로 있고
규칙이 다르다(docs/sell_flags.md). 여기 숫자는 그 플래그를 만들지도 바꾸지도 않는다.

지금 점수 = `scores` 의 그 종목 가장 새 행(기준일 ≤ 신호 기준일, 그날의 가장 새 판) — 추천 화면과 같은 규칙.
매수 때 점수 = `trades.score_at_trade` 가운데 **점수가 있는 가장 이른 매수**(체결일 전 가장 최근 점수,
docs/portfolio.md).
스냅샷 전 매매라 비어 있으면 "매수 때 점수 없음" 이라 적는다 — 지어내지 않는다. ETF 는 점수가 없어 싣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from batch.core.rounding import fixed, half_up
from batch.services.accumulation import display_name

#: 2부에 싣는 보유 줄 상한. 보유가 많아도 한 조각(3,900자) 안에 들게
MAX_LINES = 8


@dataclass(frozen=True)
class HoldingScore:
    name: str
    market: str | None
    score_now: float | None
    rank_now: int | None
    as_of: str | None
    score_at_trade: float | None
    first_buy: str | None
    #: 두 점수가 같은 잣대(계산 판·팩터 가중치·센티먼트 가중치)인가. 다르면 차이를 적지 않는다 (25.962 — 판을 올린
    #: 10-06 리포트의 보유 9종목 전부가 판 8(매수 10-01)과 판 9(지금) 점수를 뺀 값이었다.
    #: 매도 플래그 25.209 와 같은 판정)
    same_yardstick: bool = True


def _short(d: str | None) -> str:
    return d[5:] if isinstance(d, str) and len(d) == 10 else (d or "")


def from_rows(rows: list[dict[str, Any]]) -> list[HoldingScore]:
    out: list[HoldingScore] = []
    for r in rows:
        if not r.get("name"):
            continue
        out.append(HoldingScore(
            name=str(r["name"]), market=r.get("market"),
            score_now=None if r.get("score_now") is None else float(r["score_now"]),
            rank_now=None if r.get("rank_in_market") is None else int(r["rank_in_market"]),
            as_of=r.get("as_of_date"),
            score_at_trade=None if r.get("score_at_trade") is None else float(r["score_at_trade"]),
            first_buy=r.get("first_buy"),
            same_yardstick=r.get("same_yardstick", True) is not False,
        ))  # fmt: skip
    return out


def line(h: HoldingScore) -> str:
    if h.score_now is None:
        지금 = "지금 점수 없음"
    else:
        자리 = f"{h.market} {h.rank_now}위" if h.market and h.rank_now else None
        안 = ", ".join(x for x in (자리, _short(h.as_of)) if x)
        지금 = f"지금 {fixed(h.score_now)}점" + (f" ({안})" if 안 else "")
    if h.score_at_trade is None:
        그때 = "매수 때 점수 없음"
        차이 = ""
    else:
        그때 = f"매수 때 {fixed(h.score_at_trade)}점" + (f" ({_short(h.first_buy)})" if h.first_buy else "")
        # **보이는 두 숫자의 차이**를 적는다 (25.959) — 반올림 전 값으로 빼면 "지금 60점 · 매수 때 61점 → +0점" 이
        # 됐다(10-06 리포트)
        if h.score_now is None:
            차이 = ""
        elif not h.same_yardstick:
            차이 = " (계산 판·가중치가 달라 차이는 적지 않음)"
        else:
            차이 = f" → {int(half_up(h.score_now) - half_up(h.score_at_trade)):+d}점"
    return f"보유 {display_name(h.name)}: {지금} · {그때}{차이}"


def render(scores: list[HoldingScore]) -> list[str]:
    """2부의 한 절. 보유가 없으면 빈 목록(아무 줄도 없다)."""
    if not scores:
        return []
    lines = ["보유 종목 점수 — 샀을 때와 지금 (표시일 뿐 매도 신호가 아닙니다)"]
    for h in scores[:MAX_LINES]:
        lines.append(f"  {line(h)}")
    if len(scores) > MAX_LINES:
        lines.append(f"  … 외 {len(scores) - MAX_LINES}종목")
    return lines
