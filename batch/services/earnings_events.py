"""37회차 후보 B — 실적 발표 반응 EAR 의 사건 분류·값 (docs/factors.md 12.40 B·B′, docs/infra.md 25.1658).

Chan·Jegadeesh·Lakonishok(1996)·Brandt 외(2008, `[확인필요: 원문]`): 실적 발표 직후 시장 대비 오른 폭(EAR)이 큰 종목이
이어서
오른다(실적 발표 뒤 표류, PEAD). **정의·창·문턱은 12.40 에 결과를 보기 전에 적었다 — 바꾸지 않는다.** 실제 추천에는
쓰지 않는다.
계산만 한다 — DB 도 시각도 모른다. 사건 표·백필·IC·`pead_event` 는 11-01 Turso 리셋 뒤(12.40 순서).

사건 분류(12.40 B ①~⑤, 25.1648·25.1651·25.1655·25.1657 교차검증으로 닫음) — **기준일까지 접수된 앞쪽 경계만 본다**:
- 경계 = 정기보고서 (회계연도, 보고서 코드)의 처음 접수일 하나. 정정 행은 경계도 사건도 아니다(부르는 쪽이
`first_receipts` 로)
- 믿는 경계 = 법정 기한(회계 기간 끝 + 분기·반기 45일, 사업 90일 — `portfolio` 값, 말일이 휴장이면 다음 거래일) 안에
  처음 접수됐고, 바로 앞 보고서의 경계도 그보다 먼저 있다. 결산월을 확인하지 못한 회사는 믿지 않는다
- 실적 사건(잠정·손익구조) E: 앞 경계 P 를 믿고, P 다음 보고서 기한이 E 전에 지나지 않았고, (P, E] 를 거래소 공시(I)
수집이
  덮었고, (P, E) 에 앞선 실적 사건이 없으면 **사건**. 앞선 실적 사건이 있으면 **중복**. 그 밖은 **모름**
- 정기보고서 B: B 와 P 를 둘 다 믿고 (P, B] 가 덮였고 그 안에 실적 사건이 없으면 사건, 있으면 중복. B 를 못 믿으면 앞
실적
  사건이 있으면 중복·없으면 모름. 같은 날 낸 잠정은 그 보고서보다 먼저다
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from batch.services.disclosure_reaction import ACTION_TOLERANCE, classify
from batch.services.portfolio import ANNUAL_DEADLINE_DAYS, QUARTER_DEADLINE_DAYS
from batch.sources.dart import ANNUAL_REPORT_CODE

#: 보고서 코드 차례 — 1분기 → 반기 → 3분기 → 사업(→ 다음 해 1분기). DART 코드(`sources/dart.py`)
REPORT_ORDER = ("11013", "11012", "11014", ANNUAL_REPORT_CODE)
#: 12월 결산의 보고서별 회계 기간 끝(월) — 결산월이 12 가 아닌 회사는 회계연도 표기 규칙을 확인하지 못해 믿지 않는다
DEC_PERIOD_END_MONTH = {"11013": 3, "11012": 6, "11014": 9, ANNUAL_REPORT_CODE: 12}
#: 실적 유형(`disclosure_reaction` earnings)이어도 사건이 아닌 제목 낱말 — 앞으로의 전망·결산실적공시 예고는 실적이
#: 아니다
NOT_RESULT_WORDS = ("전망", "예고")
#: 값이 이만큼(거래일)보다 묵으면 NULL — 3개월 모멘텀 창과 같은 수(12.40, 새 숫자 없음)
FRESH_DAYS = 63
#: 매수 문턱 분포 창(거래일)과 백분위 — 문헌의 십분위 그대로(12.40 B′)
HISTORY_DAYS = 252
TOP_QUANTILE = 0.9

SourceKind = str  # "잠정" | "보고서"


@dataclass(frozen=True)
class Report:
    fiscal_year: int
    code: str  # REPORT_ORDER 가운데 하나
    receipt: date  # 처음 접수일


@dataclass(frozen=True)
class Classified:
    day: date  # 접수일
    source: SourceKind
    kind: str  # "사건" | "중복" | "모름"
    why: str


def is_result_title(title: str) -> bool:
    """실적(잠정)·손익구조 변경 공시인가 — `disclosure_reaction.classify` 의 earnings(정정 등 `EXCLUDE` 제외)에서
    전망·예고를 뺀다."""
    t = title.replace(" ", "")
    return classify(title) == "earnings" and not any(w in t for w in NOT_RESULT_WORDS)


def first_receipts(rows: list[tuple[int, str, date]]) -> list[Report]:
    """(회계연도, 보고서 코드, 접수일) 행들 → (회계연도, 코드)마다 처음 접수일 하나(정정 행은 경계가 아니다, 25.461
    규칙)."""
    처음: dict[tuple[int, str], date] = {}
    for fy, code, d in rows:
        if code in REPORT_ORDER and ((fy, code) not in 처음 or d < 처음[(fy, code)]):
            처음[(fy, code)] = d
    return sorted((Report(fy, c, d) for (fy, c), d in 처음.items()), key=lambda r: (r.receipt, r.fiscal_year, r.code))


def _앞_보고서(fy: int, code: str) -> tuple[int, str]:
    i = REPORT_ORDER.index(code)
    return (fy - 1, REPORT_ORDER[-1]) if i == 0 else (fy, REPORT_ORDER[i - 1])


def _다음_보고서(fy: int, code: str) -> tuple[int, str]:
    i = REPORT_ORDER.index(code)
    return (fy + 1, REPORT_ORDER[0]) if i == len(REPORT_ORDER) - 1 else (fy, REPORT_ORDER[i + 1])


def _달말(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)


def deadline(fiscal_year: int, code: str, fiscal_month: int | None, on_or_after: Callable[[date], date]) -> date | None:
    """그 보고서의 법정 제출 기한 — 회계 기간 끝 + 45일(분기·반기)·90일(사업). 말일이 휴장이면 다음 거래일(민법 161조).
    결산월이 12 가 아니거나 모르면 None(믿지 않는다)."""
    if fiscal_month != 12:
        return None
    끝 = _달말(fiscal_year, DEC_PERIOD_END_MONTH[code])
    일수 = ANNUAL_DEADLINE_DAYS if code == ANNUAL_REPORT_CODE else QUARTER_DEADLINE_DAYS
    return on_or_after(끝 + timedelta(days=일수))


def classify_events(
    reports: list[Report], results: list[date], covered: Callable[[date, date], bool], fiscal_month: int | None,
    on_or_after: Callable[[date], date],
) -> list[Classified]:
    """한 종목의 실적 공시(`results`, 접수일)와 정기보고서 경계(`reports`, `first_receipts`)를 시간 순서로 분류한다.

    `covered(a, b)` = (a, b] 의 모든 날을 거래소 공시(I) 수집이 덮었는가. `on_or_after(d)` = d 이상 첫 국내 거래일.
    각 항목은 **그 접수일까지 알 수 있는 것만** 본다 — 뒤 보고서·뒤 공시를 보지 않는다(시험이 미끼로 지킨다).
    """
    경계표 = {(r.fiscal_year, r.code): r for r in reports}

    def 믿음(r: Report) -> bool:
        기한 = deadline(r.fiscal_year, r.code, fiscal_month, on_or_after)
        앞 = 경계표.get(_앞_보고서(r.fiscal_year, r.code))
        return 기한 is not None and r.receipt <= 기한 and 앞 is not None and 앞.receipt < r.receipt

    # 같은 날이면 잠정이 보고서보다 먼저(같은 회계 기간)
    줄: list[tuple[date, int, Report | None]] = sorted(
        [(d, 0, None) for d in results] + [(r.receipt, 1, r) for r in reports], key=lambda x: (x[0], x[1]))
    out: list[Classified] = []
    for 날, 순, r in 줄:
        P = next((b for b in reversed(reports) if b.receipt < 날 and b is not r), None)
        앞선 = [c for c in out if c.source == "잠정" and P is not None and P.receipt < c.day <= 날]
        if r is None:  # 실적 공시
            if P is None:
                out.append(Classified(날, "잠정", "모름", "앞 경계 없음(첫 보고서 전)"))
            elif not 믿음(P):
                out.append(Classified(날, "잠정", "모름", "앞 경계를 믿지 못함"))
            elif (다음기한 := deadline(*_다음_보고서(P.fiscal_year, P.code), fiscal_month, on_or_after)) is not None \
                    and 다음기한 < 날:
                out.append(Classified(날, "잠정", "모름", "다음 보고서 기한이 지났는데 경계 없음"))
            elif not covered(P.receipt, 날):
                out.append(Classified(날, "잠정", "모름", "구간을 덮지 못함"))
            elif 앞선:
                out.append(Classified(날, "잠정", "중복", "같은 구간 앞선 실적 사건"))
            else:
                out.append(Classified(날, "잠정", "사건", "구간 첫 실적 사건"))
            continue
        assert 순 == 1
        if not 믿음(r):
            out.append(Classified(날, "보고서", "중복" if 앞선 else "모름",
                                  "보고서를 믿지 못함" + (" — 앞 실적 사건 있음" if 앞선 else "")))  # fmt: skip
        elif P is None or not 믿음(P):
            out.append(Classified(날, "보고서", "모름", "앞 경계 없음·믿지 못함"))
        elif not covered(P.receipt, 날):
            out.append(Classified(날, "보고서", "모름", "구간을 덮지 못함"))
        elif 앞선:
            out.append(Classified(날, "보고서", "중복", "같은 구간 앞선 실적 사건"))
        else:
            out.append(Classified(날, "보고서", "사건", "구간에 잠정 없음 — 보고서가 사건"))
    return out


def ear(
    axis: list[str], d: str, closes: dict[str, float], index: dict[str, float],
    factors: dict[str, float] | None = None,
) -> float | None:
    """EAR = (P(D+1)/P(D−2) − 1) − (I(D+1)/I(D−2) − 1). `axis` = 그 시장 거래일(오름차순), `d` = 사건일(거래일).
    값이 하나라도 없거나(정지), 창 안 수정계수(원 종가 ÷ 수정종가) 변화가 `ACTION_TOLERANCE` 를 넘으면 None."""
    if d not in axis:
        return None
    i = axis.index(d)
    if i < 2 or i + 1 >= len(axis):
        return None
    a, b = axis[i - 2], axis[i + 1]
    p0, p1, i0, i1 = closes.get(a), closes.get(b), index.get(a), index.get(b)
    if not (p0 and p1 and i0 and i1) or min(p0, p1, i0, i1) <= 0:
        return None
    if factors is not None:
        f0, f1 = factors.get(a), factors.get(b)
        if not (f0 and f1) or abs(f1 / f0 - 1) > ACTION_TOLERANCE:
            return None
    return (p1 / p0 - 1) - (i1 / i0 - 1)


def value_at(
    axis: list[str], t: str, events: list[tuple[str, str, float | None]], covered: Callable[[str, str], bool],
) -> float | None:
    """기준일 t 의 IC 값 — 기준일까지 보이는(D+1 ≤ t) 가장 최근 사건의 EAR.

    `events` = (사건일 D(거래일), 분류, EAR). 사건이면 값이 그 EAR 로, 중복이면 그대로, 모름이면 None 으로 바뀐다.
    값이 정해진 날(D+1)이 t 에서 `FRESH_DAYS` 거래일보다 앞이거나, 그 창(t 앞 `FRESH_DAYS` 거래일 ~ t)을 덮지 못하면
    None.
    """
    if t not in axis:
        return None
    k = axis.index(t)
    값: float | None = None
    정한날: int | None = None
    for d, 분류, e in sorted(events, key=lambda x: x[0]):
        if d not in axis or axis.index(d) + 1 > k:
            continue
        if 분류 == "사건":
            값, 정한날 = e, axis.index(d) + 1
        elif 분류 == "모름":
            값, 정한날 = None, axis.index(d) + 1
    if 값 is None or 정한날 is None or k - 정한날 > FRESH_DAYS:
        return None
    if k < FRESH_DAYS or not covered(axis[k - FRESH_DAYS], t):
        return None
    return 값


def top_cut(axis: list[str], t: str, known: list[tuple[str, float]], first_day: str) -> float | None:
    """매수 문턱 — 그 시장에서 t 앞 `HISTORY_DAYS` 거래일(t 포함) 안에 **보인**(D+1) EAR 들의 `TOP_QUANTILE` 분위(최근
    순위법:
    오름차순 ⌈0.9·n⌉ 번째). `first_day`(그 시장 사건 자료 시작)부터 `HISTORY_DAYS` 가 다 차기 전이면 None — 그 구간은
    사지 않는다."""
    if t not in axis or first_day not in axis:
        return None
    k, s = axis.index(t), axis.index(first_day)
    if k - s + 1 < HISTORY_DAYS:
        return None
    창 = set(axis[k - HISTORY_DAYS + 1 : k + 1])
    값 = sorted(e for 보인, e in known if 보인 in 창)
    if not 값:
        return None
    return 값[max(0, math.ceil(TOP_QUANTILE * len(값)) - 1)]
