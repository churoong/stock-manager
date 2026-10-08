"""공시 반응 통계 (docs/disclosure_reaction.md, docs/infra.md 25.996).

공시 유형마다 "공시 전 거래일 종가 → 공시일부터 5거래일째 종가" 수익에서 같은 기간 지수 수익을 뺀 값(초과수익)을 모아
평균·중앙값·오른 비율을 낸다. 장중 공시 알림에
"이 유형 공시: 전날 종가부터 5거래일째까지(공시일 반응 포함) 지수 대비 중앙값 +x% (n건)"
로 붙인다(25.1013 — 알림을 받은 뒤 얻을 수 있는 몫이 아님을 밝힌다).

- 유형은 공시 제목의 낱말로 가른다(`TYPES` — 위에서부터 처음 맞는 것). 정정 공시("정정")는 뺀다 —
  같은 사건이 두 번 세진다
- 공시 시각을 모른다(장중·장 뒤). 그래서 **공시일 전 거래일 종가**에서 재기 시작한다 — 장 뒤 공시면 다음 날 반응까지,
  장중 공시면 당일 반응까지 함께 들어간다
- 기간 안 분할·병합(수정계수 2% 넘게 바뀜)이면 뺀다 — 가격 단위가 달라진다
- 같은 날 같은 종목에 여러 공시가 있으면 서로 독립이 아니다 — 유형마다 (종목, 날짜) 한 번만 센다
"""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass

#: 공시일부터 몇 번째 거래일 종가까지 재나 (공시일 = 1)
WINDOW_DAYS = 5
#: 화면에 통계를 보이는 최소 건수
MIN_N = 30
#: 기간 안 수정계수 변화가 이보다 크면 기업행위로 보고 뺀다
ACTION_TOLERANCE = 0.02

#: (키, 이름, 제목 낱말들). 위에서부터 처음 맞는 유형 하나. 앞의 것이 더 좁다(자기주식취득신탁 → 자사주)
TYPES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("buyback", "자기주식 취득", ("자기주식취득", "자기주식 취득")),
    ("buyback_sell", "자기주식 처분", ("자기주식처분", "자기주식 처분")),
    ("rights", "유상증자", ("유상증자",)),
    ("bonus", "무상증자", ("무상증자",)),
    ("cb", "전환사채·신주인수권부사채", ("전환사채", "신주인수권부사채", "교환사채")),
    ("supply", "단일판매·공급계약", ("단일판매", "공급계약")),
    ("earnings", "실적(잠정)·손익구조 변경", ("잠정)실적", "잠정실적", "손익구조")),
    ("dividend", "배당 결정", ("배당결정",)),
    ("control", "최대주주 변경", ("최대주주변경", "최대주주 변경")),
    ("reduction", "감자", ("감자결정", "감자 결정")),
    ("merger", "합병·분할", ("합병", "분할결정")),
    ("acquire", "타법인 주식 취득", ("타법인주식",)),
    ("facility", "유형자산 취득·시설투자", ("유형자산취득", "신규시설투자")),
    ("lawsuit", "소송", ("소송",)),
    ("fraud", "횡령·배임", ("횡령", "배임")),
    ("inquiry", "조회공시", ("조회공시",)),
    ("unfaithful", "불성실공시", ("불성실공시",)),
)


def classify(title: str) -> str | None:
    """제목 → 유형 키. 정정 공시이거나 맞는 유형이 없으면 None."""
    t = title.replace(" ", "")
    if "정정" in t:
        return None
    for key, _, words in TYPES:
        if any(w.replace(" ", "") in t for w in words):
            return key
    return None


@dataclass(frozen=True)
class Event:
    stock_id: int
    index_code: str
    day: str  # 공시일 YYYY-MM-DD
    type: str


def car(dates: list[str], closes: list[float], ratios: list[float | None], index: dict[str, float],
        day: str) -> float | None:  # fmt: skip
    """한 공시의 초과수익. 재지 못하면 None(전 거래일·5거래일째·지수가 없거나 기업행위)."""
    k = bisect.bisect_left(dates, day)  # 첫 "공시일 이후" 거래일
    if k == 0 or k + WINDOW_DAYS - 1 >= len(dates):
        return None
    i0, i1 = k - 1, k + WINDOW_DAYS - 1
    rs = [r for r in ratios[i0 : i1 + 1] if r]
    if rs and max(rs) / min(rs) - 1 > ACTION_TOLERANCE:
        return None
    a, b = index.get(dates[i0]), index.get(dates[i1])
    if not a or not b or closes[i0] <= 0:
        return None
    return (closes[i1] / closes[i0] - 1) - (b / a - 1)


def summarize(values: dict[str, list[float]]) -> list[dict]:
    """유형 → 초과수익 목록 → 줄. 퍼센트(%, 소수 둘째 자리)."""
    out = []
    for key, label, words in TYPES:
        xs = values.get(key) or []
        out.append({
            "type": key, "label": label, "keywords": list(words), "n": len(xs),
            "mean_pct": round(statistics.fmean(xs) * 100, 2) if xs else None,
            "median_pct": round(statistics.median(xs) * 100, 2) if xs else None,
            "pos_pct": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1) if xs else None,
        })  # fmt: skip
    return out


def line(row: dict | None) -> str | None:
    """알림에 붙일 한 줄. 표본이 적으면 None — 우연을 통계로 보이지 않는다."""
    if not row or (row.get("n") or 0) < MIN_N or row.get("median_pct") is None:
        return None
    # **공시일 반응이 들어 있다고 말한다** (25.1013, 14회차 교차검증). "공시 뒤 5거래일" 이라 적었는데 실제로는
    # 공시 전날 종가부터 잰다 — 알림을 받은 사람은 이미 지나간 공시일 몫을 앞으로 올 것처럼 읽었다
    # (악재면 바닥에서 팔게 만든다)
    return (f"{row['label']} 공시: 전날 종가부터 {WINDOW_DAYS}거래일째까지(공시일 반응 포함) 지수 대비 중앙값 "
            f"{row['median_pct']:+.1f}% · "
            f"평균 {row['mean_pct']:+.1f}% · 오른 비율 {row['pos_pct']:.0f}% ({row['n']}건)")  # fmt: skip
