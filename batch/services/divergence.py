"""점수 × 수급 × 증권사 의견 엇갈림 (docs/reports.md 3.9, docs/infra.md 25.1001).

우리 점수가 매수라고 한 종목(아침 리포트 국내 추천)에서 **시장의 다른 두 목소리**가 반대쪽이면 알린다.

- 수급 = 최근 `FLOW_DAYS` 거래일 외국인 + 기관 순매수 대금 합(`kr_flows`, 백만원). 음수면 "판다"
- 의견 = 최근 `OPINION_DAYS` 일 증권사 목표가 변경 — 같은 증권사의 바로 앞 목표가보다 올렸나 내렸나(`kr_opinions`).
  내린 곳이 올린 곳보다 많으면 "내린다". 의견 코드(`opinion_code`)는 뜻이 [확인필요] 라 쓰지 않는다

표시일 뿐이다 — 점수·신호를 바꾸지 않는다. 수급·의견은 팩터가 아니다
(이력이 쌓인 뒤 docs/factors.md 에 식을 먼저 적는다).
반대가 아니라고 "확인됐다" 고 말하지 않는다 — 반대인 것만 적고 나머지는 수만 센다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 수급을 보는 거래일 수 — 한 주. 하루치는 잡음이 크다
FLOW_DAYS = 5
#: 이보다 적은 날의 수급만 있으면 판단하지 않는다(수집을 시작한 첫 주 등)
MIN_FLOW_DAYS = 3
#: 목표가 변경을 보는 달력일 수
OPINION_DAYS = 30
#: 목표가가 이만큼(비율) 넘게 바뀌어야 올림·내림으로 센다 — 반올림 차이를 올림으로 세지 않게
TARGET_TOLERANCE = 0.005


@dataclass(frozen=True)
class View:
    name: str
    ticker: str
    flow_days: int
    frgn_amt: int  # 백만원
    orgn_amt: int
    raises: int
    cuts: int

    @property
    def flow_against(self) -> bool:
        return self.flow_days >= MIN_FLOW_DAYS and self.frgn_amt + self.orgn_amt < 0

    @property
    def opinion_against(self) -> bool:
        return self.cuts > self.raises


def target_changes(rows: list[tuple[str, str, float | None]], since: str) -> tuple[int, int]:
    """(발표일, 증권사, 목표가) 를 날짜 순으로 받아 `since` 이후 변경의 (올림, 내림) 수.
    같은 증권사의 바로 앞 목표가와 비교한다. 앞 목표가가 없거나 목표가가 비면 세지 않는다."""
    last: dict[str, float] = {}
    up = down = 0
    for day, broker, target in sorted(rows, key=lambda r: (r[0], r[1])):
        if not target or target <= 0:
            continue
        prev = last.get(broker)
        if prev and day >= since:
            if target > prev * (1 + TARGET_TOLERANCE):
                up += 1
            elif target < prev * (1 - TARGET_TOLERANCE):
                down += 1
        last[broker] = target
    return up, down


def _억(백만원: int) -> str:
    """백만원 → 억. 10억 미만은 소수 한 자리 — 반올림으로 "-0억" 이 되지 않게."""
    억 = 백만원 / 100
    return f"{억:+.1f}억" if abs(억) < 10 else f"{억:+,.0f}억"


def against_lines(v: View) -> list[str]:
    """한 종목의 반대 목소리 문장 — 종목 분석(docs/analysis.md 5장)이 쓴다. 반대가 아니면 빈 목록."""
    out = []
    if v.flow_against:
        out.append(f"외국인·기관 {v.flow_days}거래일 순매도 (외국인 {_억(v.frgn_amt)}·기관 {_억(v.orgn_amt)})")
    if v.opinion_against:
        out.append(f"증권사 목표가 {OPINION_DAYS}일 내림 {v.cuts}·올림 {v.raises}")
    return out


def render(views: list[View]) -> list[str]:
    """반대인 종목만 줄로. 하나도 없으면 빈 목록(절을 싣지 않는다)."""
    lines = []
    for v in views:
        if not (v.flow_against or v.opinion_against):
            continue
        말 = []
        if v.flow_days >= MIN_FLOW_DAYS:
            표시 = "⚠ " if v.flow_against else ""
            말.append(f"{표시}외국인 {_억(v.frgn_amt)}·기관 {_억(v.orgn_amt)} ({v.flow_days}일)")
        if v.raises or v.cuts:
            표시 = "⚠ " if v.opinion_against else ""
            말.append(f"{표시}목표가 내림 {v.cuts}·올림 {v.raises} ({OPINION_DAYS}일)")
        둘다 = " — 둘 다 반대" if (v.flow_against and v.opinion_against) else ""
        lines.append(f"· {v.name}({v.ticker}) {' · '.join(말)}{둘다}")
    if not lines:
        return []
    rest = len(views) - len(lines)
    tail = [f"  나머지 {rest}종목은 반대 신호 없음(자료 없음 포함)"] if rest else []
    return [f"🔀 점수와 수급·증권사 의견의 엇갈림 (추천 {len(views)}종목 중 {len(lines)}, 참고만)", *lines, *tail]
