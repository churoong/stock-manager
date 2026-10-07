"""종목 감성 집계 (docs/sentiment.md, Step 7). 순수 함수만. 소스·채점기와 무관하다.

기사마다 −1~+1 점수가 이미 매겨져 있다고 보고, 종목·날짜 단위로 합친다.
  - 최근 30일 기사만 (CLAUDE.md)
  - 시간 감쇠: 반감기 HALFLIFE_DAYS. 최근 기사 비중이 크다
  - 기사가 MIN_ARTICLES(5) 보다 적으면 점수를 내지 않는다(NULL). 한두 건으로 종목 분위기를 말하지 않는다
  - 7일 변화는 **7일 전에 알 수 있던 기사만으로** 7일 전 점수를 다시 내서 뺀다(미래 기사를 섞지 않는다)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

# 2: 창 안 가장 새 기사가 `NEWEST_MAX_DAYS` 보다 오래됐으면 값을 내지 않는다 (docs/infra.md 25.832)
CALC_VERSION = 2

WINDOW_DAYS = 30  # CLAUDE.md "종목별 최근 30일 뉴스"
# 반감기 7일: 한 주 전 기사는 절반, 한 달 전 기사는 약 1/20. 근거는 없다 [확인필요: 운영하며 조정]
HALFLIFE_DAYS = 7.0
# 5건 미만이면 점수를 내지 않는다 (사용자 결정 2026-09-17: 설계 제안 3건보다 보수적으로)
MIN_ARTICLES = 5
#: 창 안 **가장 새 기사**가 이보다 오래됐으면 값을 내지 않는다 (docs/infra.md 25.832, 감사). 감쇠 가중치는
#: 정규화(가중합 ÷ 가중치 합)라
#: 새 기사가 끊겨도 옛 기사 평균이 그대로 남고, 행은 매일 오늘 날짜로 써져 3일 묵음 검사도 통과했다 — 수집 대상(점수
#: 상위)에서
#: 빠진 종목의 −60 이 30일 동안 "오늘 감성" 으로 점수를 깎아 다시 상위에 못 들게 굳혔다. 반감기와 같은 7일
NEWEST_MAX_DAYS = 7
#: 창 안 기사 가운데 **채점되지 않은** 몫이 이보다 크면 값을 내지 않는다 (docs/infra.md 25.834) — 국내 채점이 멈춘 날
#: 옛 기사로만 낸 값을 막는다
UNSCORED_MAX_SHARE = 0.2

# 기사 하나를 긍정·부정으로 셀 때의 문턱. VADER 관례(compound ±0.05)를 따른다
POSITIVE_AT = 0.05
NEGATIVE_AT = -0.05

# 감성 급락 — 매도 플래그 황색. 사용자 결정 2026-09-17: 설계 제안(−30점·3건)보다 보수적으로
DROP_POINTS_7D = 40.0
DROP_MIN_NEGATIVE_7D = 5

#: 감성 점수가 이보다 오래됐으면 **쓰지 않는다.** 뉴스 수집이 멈췄는데 옛 분위기로
#: 판단하지 않기 위해서다. `scores` 가 처음부터 이 잣대를 걸고 있었는데 매도 플래그는
#: 안 걸고 있었다 — **같은 규칙이 두 곳에 있었고 한 곳만 지켰다**
#: (2026-09-23, docs/infra.md 25.161). 여기가 단일 정의처다.
MAX_AGE_DAYS = 3


@dataclass(frozen=True)
class ScoredArticle:
    published_at: datetime  # UTC aware
    score: float  # −1~+1


# 같은 사건의 판 표시 — 연합은 1보·2보·(종합)·(종합2보) 를 판마다 **다른 주소**로 내 주소 중복 제거를 지나간다.
# 사건 하나가 부정 7일을 네 건 채워 급락 문턱(5건)을 거의 혼자 넘겼다 (docs/infra.md 25.647, 감사)
_판_표시 = re.compile(r"\[(속보|\d+보|종합)\]|\(종합\d*보?\)|\(\d+보\)")


def 판_뗀_제목(title: str) -> str:
    """판 표시와 공백을 뗀 제목. 같은 종목에서 이 값이 같으면 같은 기사로 본다."""
    return re.sub(r"\s+", "", _판_표시.sub("", title))


def 판_하나만(rows: list[tuple[str, datetime, float]], tz: tzinfo | None = None) -> list[ScoredArticle]:
    """(제목, 발행시각, 점수) 에서 **같은 날(그 시장 현지)** 판 표시만 다른 기사는 가장 먼저 나온 것 하나만 남긴다.

    날짜는 그 시장의 현지 날짜다 (docs/infra.md 25.749, 감사) — 늘 KST 로 잘라,
    미국 ET 같은 날 09:00·12:00 판이 KST 로는 날이 갈려 두 건으로 셌다. `tz` 를 안 주면 예전처럼 KST.

    먼저 나온 것을 남겨야 7일 전 점수를 다시 낼 때 그때 알 수 없던 판이 끼지 않는다(look-ahead 방지).
    날이 다르면 같은 제목이어도 따로 센다 — "○○ 실적 호조" 같은 제목은 날마다 다른 기사일 수 있다.
    """
    first: dict[tuple[str, str], tuple[datetime, float]] = {}
    for title, published, score in sorted(rows, key=lambda r: r[1]):
        날 = (published.astimezone(tz).date() if tz else (published + timedelta(hours=9)).date()).isoformat()
        first.setdefault((판_뗀_제목(title) or title, 날), (published, score))
    return [ScoredArticle(p, sc) for p, sc in first.values()]


@dataclass
class Aggregate:
    sentiment: float | None
    article_count: int
    positive_count: int
    negative_count: int
    negative_count_7d: int
    #: 감쇠 가중치의 유효 기사 수(Kish) — **기록만 한다, 판정에 쓰지 않는다** (docs/sentiment.md 2장, infra 25.873).
    #: 값을 낸 날만
    effective_n: float | None = None
    #: 가장 큰 기사 가중치의 몫 (0~1). 한 기사가 점수를 얼마나 좌우하나
    max_weight_share: float | None = None


def effective_n(weights: list[float]) -> float | None:
    """Kish(1965) 유효 표본 수 (Σw)²/Σw². 가중치가 없으면 None."""
    제곱합 = sum(w * w for w in weights)
    return None if not weights or 제곱합 <= 0 else sum(weights) ** 2 / 제곱합


def aggregate(
    articles: list[ScoredArticle], as_of: datetime, tz: tzinfo | None = None, *, freshness: bool = True
) -> Aggregate:
    """as_of 시점에 알 수 있던(발행 시각 ≤ as_of) 최근 30일 기사로 −100~+100.

    `tz` 를 주면 30일·7일을 **그 시장 현지 달력**으로 뺀다 (25.749, 감사).
    UTC 에서 빼면 서머타임 경계에서 창 시작이 1시간 어긋났다(기준 11-10 EST 면 10-12 00:00~00:59 EDT 기사가 빠졌다).
    가중치(경과 시간)는 실제 시간이라 그대로다."""
    현지 = as_of.astimezone(tz) if tz else as_of
    start = 현지 - timedelta(days=WINDOW_DAYS)
    window = [a for a in articles if start < a.published_at <= as_of]
    recent = 현지 - timedelta(days=7)
    pos = sum(1 for a in window if a.score >= POSITIVE_AT)
    neg = sum(1 for a in window if a.score <= NEGATIVE_AT)
    neg7 = sum(1 for a in window if a.score <= NEGATIVE_AT and a.published_at > recent)
    if len(window) < MIN_ARTICLES:
        return Aggregate(None, len(window), pos, neg, neg7)
    if freshness and max(a.published_at for a in window) <= as_of - timedelta(days=NEWEST_MAX_DAYS):
        return Aggregate(None, len(window), pos, neg, neg7)
    weights = [0.5 ** ((as_of - a.published_at).total_seconds() / 86400 / HALFLIFE_DAYS) for a in window]
    total = sum(weights)
    value = sum(w * a.score for w, a in zip(weights, window, strict=True)) / total * 100
    n_eff = effective_n(weights)
    return Aggregate(round(value, 2), len(window), pos, neg, neg7,
                     None if n_eff is None else round(n_eff, 2), round(max(weights) / total, 3))  # fmt: skip


def deltas(
    articles: list[ScoredArticle], as_of: datetime, tz: tzinfo | None = None
) -> tuple[float | None, float | None]:
    """(7일 변화, 30일 변화). 과거 점수는 그때까지 발행된 기사로만 다시 낸다. `tz` 는 `aggregate` 와 같다(25.749)."""
    now = aggregate(articles, as_of, tz).sentiment
    현지 = as_of.astimezone(tz) if tz else as_of
    out: list[float | None] = []
    for days in (7, 30):
        # 과거 점수에는 신선도 규칙(25.832)을 걸지 않는다 — 걸면 그때 기사 6건이 있었는데도 "기사가 적던 종목" 으로
        # 읽혀 중립 0 출발
        # (25.638)이 엉뚱하게 발동해 거짓 감성 급락이 떴다 (25.838, 교차검증). 과거가 비는 것은 기사 5건 미만일 때뿐이다
        past = aggregate(articles, 현지 - timedelta(days=days), tz, freshness=False).sentiment
        out.append(None if now is None or past is None else round(now - past, 2))
    return out[0], out[1]


def drop_basis(delta_7d: float | None, current: float | None) -> float | None:
    """급락 판정에 쓰는 7일 변화. 7일 전 기사가 모자라 과거 점수가 없으면 **중립(0)에서 출발**한 것으로 본다.

    예전에는 과거가 없으면 변화도 없어 판정을 안 했다 (docs/infra.md 25.638, 감사). 조용하던 종목에 악재가
    몰리는 경우 — 이 플래그가 가장 잡아야 할 경우 — 를 놓쳤다. 오늘 점수가 있고 7일 변화가 없다는 것은 과거가
    없다는 뜻뿐이다(deltas). 부정 기사 5건 조건은 그대로라 기사 한두 건으로는 뜨지 않는다
    """
    if delta_7d is not None:
        return delta_7d
    return None if current is None else round(current, 2)


def is_sharp_drop(delta_7d: float | None, negative_count_7d: int, current: float | None = None) -> bool:
    """감성 급락: 7일 사이 40점 이상 하락 **그리고** 최근 7일 부정 기사 5건 이상 (사용자 결정 2026-09-17)."""
    basis = drop_basis(delta_7d, current)
    return basis is not None and basis <= -DROP_POINTS_7D and negative_count_7d >= DROP_MIN_NEGATIVE_7D


def effective_n_summary(values: list[float]) -> dict:
    """값을 낸 종목들의 유효 기사 수 분포 요약 (25.873). 종목별 값은 남기지 않는다(실행 기록이 커지지 않게)."""
    if not values:
        return {"n": 0}
    v = sorted(values)
    분위 = lambda q: v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]  # noqa: E731
    아래 = sum(1 for x in v if x < MIN_ARTICLES)
    return {"n": len(v), "below_min": 아래, "below_min_share": round(아래 / len(v), 3),
            "p10": 분위(0.1), "p50": 분위(0.5), "p90": 분위(0.9)}  # fmt: skip
