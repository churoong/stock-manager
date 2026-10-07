"""자기주식 취득 결정 공시 — 종목선정 기법 발굴 루프 2회차 D (docs/factors.md 12.2, docs/infra.md 25.482).

기준일 전 1년 안에 "자기주식취득결정" 공시가 있었는가. Ikenberry, Lakonishok & Vermaelen (1995).
국내만, IC 와 참고 행만 쓴다(2회차 검증 조건).

계산만 한다. DB 도 시각도 모른다. 날짜는 ISO 문자열.
"""

from __future__ import annotations

from datetime import date, timedelta

KEYWORD = "자기주식취득결정"
#: 이 말이 제목에 있으면 사건으로 세지 않는다 — 정정은 원공시의 반복, 처분·신탁은 다른 사건, 자회사는 다른 회사다
#: 자회사의 주요경영사항은 모회사의 매입이 아니다 (25.484) [확인필요: 실제 제목]
#: "종속회사" 도 같은 이유로 뺀다 (25.739, 교차검증)
EXCLUDE = ("정정", "처분", "신탁", "자회사", "종속회사")
#: 창의 길이(일) — 1년 (docs/factors.md 12.2)
WINDOW_DAYS = 365


def is_buyback(title: str) -> bool:
    붙인 = title.replace(" ", "")
    # 제외어도 공백을 뺀 제목과 견준다 — "종속 회사" 처럼 띄어 쓴 제목이 빠져나갔다 (25.743, 교차검증. `dilution` 과
    # 같은 규칙)
    return KEYWORD in 붙인 and not any(w in 붙인 for w in EXCLUDE)


def covered(windows: list[tuple[str, str]], start: str, end: str) -> bool:
    """[start, end] 가 전 종목 수집 구간들의 합집합에 통째로 들어가는가. 구간은 (시작, 끝) ISO 날짜."""
    합친: list[list[str]] = []
    for a, b in sorted((w[0][:10], w[1][:10]) for w in windows):
        # 하루 차이로 이어지는 구간도 붙인다 — 날짜 문자열이라 다음 날을 계산해 본다
        if 합친 and a <= (date.fromisoformat(합친[-1][1]) + timedelta(days=1)).isoformat():
            합친[-1][1] = max(합친[-1][1], b)
        else:
            합친.append([a, b])
    return any(a <= start[:10] and end[:10] <= b for a, b in 합친)


def flag(event_dates: list[str], cutoff: str, windows: list[tuple[str, str]]) -> int | None:
    """(c − 365, c] 에 사건이 있으면 1, 없으면 0. **그 창 전체를 전 종목 수집이 덮지 못했으면 None** (25.484, 교차검증).

    예전에는 그 나라 공시의 가장 이른 접수일 하나로 "덮었다" 고 봤다 — 한 번 `--all` 을 돌린 뒤 지켜보는 종목만 7일씩
    쌓이면, 지켜보지 않는 종목의 이후 매입 공시가 없어 0 이 됐다(모르는 것이 "없음" 으로)."""
    창_시작 = (date.fromisoformat(cutoff[:10]) - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    if not covered(windows, 창_시작, cutoff):
        return None
    return 1 if any(창_시작 <= d[:10] <= cutoff[:10] for d in event_dates) else 0
