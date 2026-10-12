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
from datetime import date, timedelta

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
    # "분할결정" 은 액면분할(`주식분할결정`)도 잡았다 — 회사 분할만 (25.1153)
    ("merger", "합병·분할", ("합병", "회사분할")),
    # "타법인주식" 은 처분(`타법인주식및출자증권처분결정`)도 잡았다 — 취득만 (25.1153)
    ("acquire", "타법인 주식 취득", ("타법인주식및출자증권취득",)),
    ("facility", "유형자산 취득·시설투자", ("유형자산취득", "신규시설투자")),
    ("lawsuit", "소송", ("소송",)),
    ("fraud", "횡령·배임", ("횡령", "배임")),
    ("inquiry", "조회공시", ("조회공시",)),
    ("unfaithful", "불성실공시", ("불성실공시",)),
)


#: 이 낱말이 제목에 있으면 **어느 유형도 아니다** (25.1153, 감사 재현). 같은 낱말을 품은 반대·다른 사건이다:
#: 정정 공시, 신탁·계약 **해지**(자기주식취득신탁계약해지 → 매수의 반대), 이미 알린 일의 **결과 보고**,
#: 자회사의 사항(`유상증자결정(종속회사의주요경영사항)` — 그 회사가 증자하는 것이 아니다).
#: 웹(`web/lib/disclosureReaction.ts` `REACTION_EXCLUDE`)도 같은 목록이어야 한다(test_doc_constants)
EXCLUDE: tuple[str, ...] = ("정정", "해지", "결과보고", "종속회사", "자회사의주요경영사항")


def classify(title: str) -> str | None:
    """제목 → 유형 키. 빼는 낱말(`EXCLUDE`)이 있거나 맞는 유형이 없으면 None."""
    t = title.replace(" ", "")
    if any(w in t for w in EXCLUDE):
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


#: `car` 가 "5거래일째에 거래정지라 못 잼" 을 알리는 값 — None(아직 덜 지남·자료 없음)과 가른다 (25.1154)
HALTED = "halted"


def car(dates: list[str], closes: list[float], ratios: list[float | None], index: dict[str, float],
        day: str) -> float | str | None:  # fmt: skip
    """한 공시의 초과수익. 재지 못하면 None(전 거래일·5거래일째·지수가 없거나 기업행위), 5거래일째에 그 종목
    종가가 없으면(거래정지·상장폐지) `HALTED`.

    **창은 시장 거래일(지수 날짜)로 센다** (25.1154, 감사 재현). 예전엔 종목 자신의 거래일로 세어 정지 70거래일이
    끼면 "5거래일째" 가 104일 뒤가 되었고, 아직 정지 중이면 None 으로 빠졌다 — 횡령·배임·불성실공시처럼 정지가
    잦은 유형의 중앙값이 덜 나쁘게 나왔다. 전 거래일 종가는 그날까지의 마지막 종가다
    (그 전부터 정지였으면 정지 전 값)."""
    시장 = sorted(index)
    k = bisect.bisect_left(시장, day)  # 첫 "공시일 이후" 시장 거래일
    if k == 0 or k + WINDOW_DAYS - 1 >= len(시장):
        return None
    d0, d1 = 시장[k - 1], 시장[k + WINDOW_DAYS - 1]
    i0 = bisect.bisect_right(dates, d0) - 1  # d0 이하 마지막 종가
    if i0 < 0:
        return None
    i1 = bisect.bisect_left(dates, d1)
    if i1 >= len(dates) or dates[i1] != d1:
        return HALTED
    rs = [r for r in ratios[i0 : i1 + 1] if r]
    if rs and max(rs) / min(rs) - 1 > ACTION_TOLERANCE:
        return None
    a, b = index.get(d0), index.get(d1)
    if not a or not b or closes[i0] <= 0:
        return None
    return (closes[i1] / closes[i0] - 1) - (b / a - 1)


def summarize(values: dict[str, list[float]], halted: dict[str, int] | None = None) -> list[dict]:
    """유형 → 초과수익 목록 → 줄. 퍼센트(%, 소수 둘째 자리). `halted` 는 5거래일째 거래정지로 못 잰 건수."""
    out = []
    for key, label, words in TYPES:
        xs = values.get(key) or []
        out.append({
            "type": key, "label": label, "keywords": list(words), "n": len(xs),
            "halted": int((halted or {}).get(key) or 0),
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
    # 정지로 못 잰 건수를 말한다 (25.1154) — 그 사건들은 대개 나쁜 쪽인데 숫자에 없다
    정지 = int(row.get("halted") or 0)
    return (f"{row['label']} 공시: 전날 종가부터 {WINDOW_DAYS}거래일째까지(공시일 반응 포함) 지수 대비 중앙값 "
            f"{row['median_pct']:+.1f}% · "
            f"평균 {row['mean_pct']:+.1f}% · 오른 비율 {row['pos_pct']:.0f}% ({row['n']}건"
            + (f", 거래정지로 못 잰 {정지}건 빠짐" if 정지 else "") + ")")  # fmt: skip


# ----------------------------------------------------------------------
# 공시 위험 이력 1년 — 사실 줄 (docs/analysis.md 54장, 25.1157)
# ----------------------------------------------------------------------
#: 사실 줄에 넣는 유형 — 최대주주 변경·감자는 위험 신호가 아니다(18회차 검증 B)
GOVERNANCE_TYPES: tuple[str, ...] = ("fraud", "unfaithful")
GOVERNANCE_DAYS = 365
#: I 덮은 날이 계산일 며칠 전까지는 이어져야 하나 — 하루 수집이 늦거나 실패한 날을 넘기되 오래 비면 쓰지 않는다
COVERAGE_SLACK_DAYS = 7


def covered_span(days: list[str], today: date) -> tuple[str, str] | None:
    """덮은 날 목록 → 1년 전부터 끊김 없이 이어진 (시작, 끝). 1년 전을 못 덮었거나 끝이 `COVERAGE_SLACK_DAYS` 보다
    오래됐으면 None — 덮지 못한 날의 사건이 "없음" 으로 읽히면 안 된다."""
    have = {d[:10] for d in days}
    시작 = today - timedelta(days=GOVERNANCE_DAYS)
    끝: date | None = None
    d = 시작
    while d <= today and d.isoformat() in have:
        끝 = d
        d += timedelta(days=1)
    if 끝 is None or (today - 끝).days > COVERAGE_SLACK_DAYS:
        return None
    return 시작.isoformat(), 끝.isoformat()


def governance_line(events: list[dict]) -> str | None:
    """한 종목의 사건 → 사실 한 줄. 사건이 없으면 None(덮은 기간 안에서만 참인 "없음" 은 말하지 않는다)."""
    if not events:
        return None
    이름 = {key: label for key, label, _ in TYPES}
    조각 = []
    for kind in GOVERNANCE_TYPES:
        xs = sorted((e for e in events if e["type"] == kind), key=lambda e: str(e["day"]), reverse=True)
        if xs:
            나열 = ", ".join(f"{x['day']} \"{x['title']}\"" for x in xs[:3])
            나열 += f" 외 {len(xs) - 3}건" if len(xs) > 3 else ""
            조각.append(f"{이름[kind]} {len(xs)}건({나열})")
    기간 = f"{events[0]['covered_from']}~{events[0]['covered_to']}"
    return f"지난 1년 공시 위험 이력: {' · '.join(조각)} — 거래소 공시 {기간} 기준, 점수·결론에 넣지 않음"
